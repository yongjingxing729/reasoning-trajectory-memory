// MemoryLifecycleTools — model-facing tool surface.

import type { Context } from '@deepseek-ai/cordis'
import type { Runtime } from '../runtime.js'
import type { ActiveTask } from '../capture.js'
import { causalEventSchema } from '../causal-domain.js'
import type { CausalEvent } from '../causal-domain.js'
import { distillSystemPrompt, distillUserText, distillWithLlm, parseMemoryItems } from '../distillation-llm.js'
import type { DistilledMemoryItem } from '../distillation-llm.js'
import { memoryItemSchema, strategyTemplateSchema } from '../domain.js'
import type { MemoryItem, StrategyTemplate } from '../domain.js'
import { clipText, getSessionIdFromAgent, isRecord, renderJson } from '../helpers.js'
import { runReasoningCore } from '../python.js'
import { BEGIN_OUTPUT_SCHEMA, END_OUTPUT_SCHEMA, FEEDBACK_OUTPUT_SCHEMA, SAVE_OUTPUT_SCHEMA } from '../tool-schemas.js'
import type { ClassifyResponse, DistillResponse } from '../types.js'
import type { LlmRuntime } from '@deepseek-ai/dsh-llm'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { randomUUID } from 'node:crypto'

export function registerMemoryLifecycleTools(ctx: Context, rt: Runtime): void {
  const { config } = rt
  const { causalEvents, lexicalIndex, memories, templates, writeCausalEvents } = rt.storage
  const { apiConfig, builtinWrite, vectorIndex, vectorIndexReady } = rt.indexes
  const { activeBySession, activeByTaskId } = rt.capture
  // Distinct-task harmful feedback window for the conservative deprecation
  // mark (final spec 5): persisted per memory as a bounded recent-task list, so
  // the count survives restarts.
  const harmfulDeprecationLimit = Number(config.deprecatedAfterHarmfulFeedback ?? 0)
  const MAX_HARMFUL_TASK_IDS = 8
  // Cap on the causal events a single save_outcome may derive for one task: a
  // tool-level guard so one long task cannot flood the causal log.
  const MAX_CAUSAL_EVENTS_PER_TASK = 100

  ctx.tools.register(defineTool({
    name: 'memory_begin_task',
    description: 'Start a reasoning-memory episode. Call once before the task work so subsequent DSH tool-call/result events can be associated with this task. Only abstract tool names and event sequence numbers are captured; arguments, outputs, prompts, and model reasoning text are not stored.',
    parameters: {
      task_desc: { type: 'string', required: true, description: 'Short task description used as the semantic retrieval key.' },
      goal: { type: 'string', description: 'Intended result, if distinct from the task description.' },
      task_family: { type: 'string', description: 'Optional task family, repository, or benchmark split label.' },
    },
    output: { schema: BEGIN_OUTPUT_SCHEMA, render: renderJson },
    async execute(args, exec) {
      const sessionId = getSessionIdFromAgent(exec.agent)
      if (!sessionId) {
        return { started: false, taskId: '', captureStatus: 'No calling DSH session was available.' }
      }
      if (activeBySession.has(sessionId)) {
        const existing = activeBySession.get(sessionId)!
        return { started: false, taskId: existing.taskId, captureStatus: 'This session already has an active task; save its outcome and end it first.' }
      }
      const taskDesc = clipText(args.task_desc, 4000)
      if (!taskDesc) throw new Error('task_desc must not be empty')
      const taskId = randomUUID()
      const active: ActiveTask = {
        taskId,
        sessionId,
        taskFamily: clipText(args.task_family ?? '', 200) || 'general',
        semanticText: clipText([taskDesc, args.goal ?? ''].filter(Boolean).join('\n'), 4000),
        startedAt: Date.now(),
        events: [],
        captureTruncated: false,
        pendingToolNames: new Map(),
        retrievedMemoryIds: new Set(),
        feedbackMemoryIds: new Set(),
        planTemplateIds: new Set(),
        templateFeedbackRecorded: new Set(),
        causalEventsRecorded: false,
        counterfactualRecorded: new Set(),
        reasoningText: '',
        reasoningCaptureStatus: config.captureReasoningEnabled ? 'not-provided' : 'disabled',
        capturedModel: null,
        usage: { inputTokens: 0, outputTokens: 0, reasoningTokens: 0 },
      }
      activeBySession.set(sessionId, active)
      activeByTaskId.set(taskId, active)
      return {
        started: true,
        taskId,
        captureStatus: config.captureReasoningEnabled
          ? 'Capturing tool names, event order, token usage, and model-provided reasoning text (in-memory only; raw arguments, outputs, and prompts are excluded).'
          : 'Capturing tool names, event order, and token usage only; raw arguments, outputs, prompts, and model reasoning text are excluded.',
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_feedback',
    description: 'Give one bounded usefulness signal for memories retrieved or templates planned in the active task. Memory ratings slightly adjust future ranking; template ratings feed the template utility statistics. Both are self-reported, not proof of correctness. The evidence text is discarded.',
    parameters: {
      memory_ids: { type: 'array', items: { type: 'string' }, description: 'Up to 20 memory IDs returned by memory_retrieve in this active task. Optional when rating a template.' },
      template_id: { type: 'string', description: 'Optional strategy template id returned by memory_plan_task in this active task. Each template can be rated once per task.' },
      step_index: { type: 'integer', description: 'Optional template step index this rating refers to. Discarded, like the evidence text.' },
      rating: { type: 'string', required: true, enum: ['helpful', 'harmful', 'neutral'], description: 'Whether applying the reasoning lesson or the template plan helped, misled, or had no clear effect.' },
      evidence: { type: 'string', required: true, description: 'Short basis for the rating. It is used for this call only and is not stored.' },
    },
    output: { schema: FEEDBACK_OUTPUT_SCHEMA, render: renderJson },
    async execute(args, exec) {
      const sessionId = getSessionIdFromAgent(exec.agent) ?? ''
      const active = activeBySession.get(sessionId)
      if (!active) {
        return { updated: 0, skipped: (args.memory_ids ?? []).length, templateUpdated: 0, note: 'Start an active task and retrieve memories or plan templates before feedback.' }
      }
      if (!clipText(args.evidence, 1000)) throw new Error('evidence must not be empty')
      const notes: string[] = []

      let templateUpdated = 0
      const templateId = typeof args.template_id === 'string' && args.template_id.trim()
        ? clipText(args.template_id, 100)
        : ''
      if (templateId) {
        if (!active.planTemplateIds.has(templateId)) {
          notes.push(`template '${templateId}' was not returned by memory_plan_task in this task and cannot be rated.`)
        } else if (active.templateFeedbackRecorded.has(templateId)) {
          notes.push(`template '${templateId}' was already rated in this task; each template can be rated once per task.`)
        } else {
          const template = templates.get(templateId)
          if (!template) {
            notes.push(`template '${templateId}' no longer exists in the template bank.`)
          } else {
            const templateResult = await runReasoningCore<{ templateId: string; utility: StrategyTemplate['utility'] }>(
              config.pythonExecutable!, {
                operation: 'template_feedback',
                template,
                verdict: args.rating,
              }, exec.signal,
            )
            const nextTemplate = strategyTemplateSchema.parse({
              ...template,
              utility: templateResult.utility,
              updatedAt: Date.now(),
            })
            await templates.put(templateId, nextTemplate)
            active.templateFeedbackRecorded.add(templateId)
            templateUpdated++
          }
        }
      }

      const uniqueIds = [...new Set(args.memory_ids ?? [])].slice(0, 20)
      const eligibleIds = uniqueIds.filter(id =>
        active.retrievedMemoryIds.has(id) && !active.feedbackMemoryIds.has(id),
      )
      const items = eligibleIds
        .map(id => memories.get(id))
        .filter((item): item is MemoryItem => Boolean(item))
      let skipped = uniqueIds.length - items.length
      let updated = 0
      if (items.length === 0) {
        if (templateUpdated === 0 && templateId) {
          return { updated: 0, skipped, templateUpdated, note: notes.join(' ') || 'Only memories retrieved in this task can be rated, and each can be rated once per task.' }
        }
        if (templateUpdated === 0) {
          return { updated: 0, skipped, templateUpdated, note: 'Only memories retrieved in this task can be rated, and each can be rated once per task.' }
        }
      } else {
        const updateResult = await runReasoningCore<{
          updates: Array<{ memoryId: string; feedback: MemoryItem['feedback'] }>
        }>(config.pythonExecutable!, {
          operation: 'feedback',
          memories: items,
          rating: args.rating,
        }, exec.signal)
        for (const update of updateResult.updates) {
          const item = memories.get(update.memoryId)
          if (!item) continue
          let status = item.status ?? 'active'
          let harmfulTaskIds = item.harmfulTaskIds ?? []
          if (harmfulDeprecationLimit > 0 && args.rating === 'harmful') {
            if (!harmfulTaskIds.includes(active.taskId)) {
              // Bounded recent-task window: drop the oldest entry when full.
              harmfulTaskIds = [...harmfulTaskIds, active.taskId].slice(-MAX_HARMFUL_TASK_IDS)
            }
            if (status === 'active' && harmfulTaskIds.length >= harmfulDeprecationLimit) {
              status = 'deprecated'
              notes.push(`memory '${update.memoryId}' received harmful feedback from ${harmfulTaskIds.length} distinct tasks and was marked deprecated (it still participates in retrieval).`)
            }
          }
          const next = memoryItemSchema.parse({ ...item, feedback: update.feedback, status, harmfulTaskIds, updatedAt: Date.now() })
          await memories.put(update.memoryId, next)
          builtinWrite(store => store.upsert(next))
          active.feedbackMemoryIds.add(update.memoryId)
          updated++
        }
        skipped += items.length - updated
      }
      return {
        updated,
        skipped,
        templateUpdated,
        note: [...notes, 'Aggregate self-reported counts persist; the evidence text was discarded and cannot promote trustLevel.']
          .filter(Boolean).join(' '),
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_save_outcome',
    description: 'Distill the active task into reusable reasoning memories. Provide an actual verifier name and concise evidence. For failure, include failure_mode only when the cause is attributable; otherwise do not guess. You may submit up to 3 structured memory_items (title/description/content/contraindications) instead of a single insight, or request LLM distillation of the captured trajectory when the profile enables it. The plugin cannot independently run the verifier, so every accepted item is stored as unverified.',
    parameters: {
      outcome: { type: 'string', required: true, enum: ['success', 'fail', 'partial', 'unknown'], description: 'Task outcome. Partial and unknown outcomes are not admitted to positive/negative memory.' },
      verifier: { type: 'string', required: true, description: 'Verifier or evaluation source, such as a test suite, benchmark checker, or explicit user confirmation.' },
      evidence: { type: 'string', required: true, description: 'Short evidence summary from the verifier or evaluator.' },
      reasoning_insight: { type: 'string', description: 'For success: one concise transferable strategy and why it helped, not a task summary or full chain-of-thought. Ignored when memory_items are provided.' },
      failure_mode: { type: 'string', description: 'Evidence-supported failure cause. Leave absent if attribution is uncertain. Ignored when memory_items are provided.' },
      memory_items: {
        type: 'array', description: 'Optional up to 3 structured memory items. Each must generalize beyond this task: do not embed specific task strings or identifiers. Items restating the task description or duplicating each other are rejected.',
        items: {
          type: 'object', additionalProperties: false,
          properties: {
            title: { type: 'string', required: true, description: 'Brief label of the strategy or lesson.' },
            description: { type: 'string', required: true, description: 'One sentence: when this item applies or does NOT apply.' },
            content: { type: 'string', required: true, description: '1-3 sentences of concrete, actionable, transferable insights.' },
            contraindications: { type: 'array', items: { type: 'string' }, description: 'Conditions under which the item must NOT be applied.' },
          },
        },
      },
      distill_with_llm: { type: 'boolean', description: 'LLM distillation of the captured trajectory (abstract actions, optional reasoning record, evidence) into structured memory items. Only runs when the profile enables llmDistillationEnabled; pass false to opt out for this call and submit your own insight or memory_items instead.' },
      reasoning_graph_json: { type: 'string', description: 'Optional compact DAG of premises, inferences, actions, evidence, and conclusion using the documented version-1 node/edge schema. This is a concise rationale graph, not hidden chain-of-thought; invalid/cyclic graphs are rejected. Allowed only for a single memory item; if omitted, the Python core creates a provenance-labeled fallback graph per item.' },
      insight_confidence: { type: 'number', description: 'Optional self-assessed insight confidence (0 to 1) applied to every item of this call. When the profile sets minInsightConfidence above 0, items below that gate are rejected; no value counts as 0. This is a write policy, not a calibrated quality verdict.' },
    },
    output: { schema: SAVE_OUTPUT_SCHEMA, render: renderJson },
    async execute(args, exec) {
      const sessionId = getSessionIdFromAgent(exec.agent) ?? ''
      const active = activeBySession.get(sessionId)
      if (!active) {
        return { stored: false, reason: 'No active task. Call memory_begin_task before collecting an outcome.', memoryIds: [], trustLevel: 'not-applicable' as const, gateRejectedCount: 0, distillation: { method: 'not-applicable' as const, model: '' } }
      }
      const outcome = args.outcome as 'success' | 'fail' | 'partial' | 'unknown'
      const admittedOutcome = outcome === 'success' || outcome === 'fail' ? outcome : null
      const verifier = clipText(args.verifier, 200)
      const evidence = clipText(args.evidence, 2000)
      const graphJson = clipText(args.reasoning_graph_json ?? '', 30000)
      const insightConfidence = typeof args.insight_confidence === 'number' && Number.isFinite(args.insight_confidence)
        ? Math.max(0, Math.min(1, args.insight_confidence))
        : 0
      const minInsightConfidence = Math.max(0, Math.min(1, Number(config.minInsightConfidence ?? 0)))

      const callerItems: DistilledMemoryItem[] = []
      if (Array.isArray(args.memory_items)) {
        for (const raw of args.memory_items) {
          if (!isRecord(raw)) continue
          const content = typeof raw.content === 'string' ? clipText(raw.content, 4000) : ''
          if (!content) continue
          const contraindications = Array.isArray(raw.contraindications)
            ? raw.contraindications
              .filter((entry): entry is string => typeof entry === 'string')
              .map(entry => clipText(entry, 200))
              .filter(Boolean)
              .slice(0, 3)
            : []
          callerItems.push({
            title: typeof raw.title === 'string' ? clipText(raw.title, 120) : '',
            description: typeof raw.description === 'string' ? clipText(raw.description, 300) : '',
            content,
            contraindications,
            // Structured items inherit the task outcome; the Python core rejects
            // items without a success/fail outcome label.
            ...(admittedOutcome ? { outcome: admittedOutcome } : {}),
          })
          if (callerItems.length >= config.maxMemoryItemsPerOutcome!) break
        }
      }

      const wantLlm = config.llmDistillationEnabled! && args.distill_with_llm !== false
      let items: DistilledMemoryItem[] | undefined = callerItems.length > 0 ? callerItems : undefined
      let extractionMethod: 'caller_insight' | 'llm_distillation' = 'caller_insight'
      let distillationModel = ''
      let distillationNote = ''
      if (args.distill_with_llm === true && !config.llmDistillationEnabled) {
        distillationNote = 'LLM distillation was requested but the profile disables llmDistillationEnabled; the caller-provided insight is used instead.'
      } else if (!items && wantLlm && admittedOutcome) {
        const actions = active.events
          .filter(event => event.type === 'tool_result')
          .map(event => event.toolName ? `${event.toolName}${event.success === false ? ' (failed)' : ''}` : '(unnamed tool)')
          .join(' 鈫?')
        const systemPrompt = distillSystemPrompt(admittedOutcome, config.maxMemoryItemsPerOutcome!)
        const userPrompt = distillUserText({ query: active.semanticText, actions, evidence, reasoningText: active.reasoningText }, admittedOutcome)
        // API-config route first when the profile left the DSH distillation
        // model unset: the user's own chat endpoint handles distillation.
        const apiChatAvailable = !config.distillationProvider!.trim()
          && apiConfig?.endpointKind('distillation') === 'openai-chat'
        if (apiChatAvailable && apiConfig) {
          try {
            const text = await apiConfig.openAiChat(
              'distillation',
              systemPrompt,
              userPrompt,
              config.llmDistillationTemperature!,
              exec.signal,
            )
            const parsed = parseMemoryItems(text, false)
            if (parsed.length === 0) {
              distillationNote = 'API-config distillation produced no parseable memory items; the caller-provided insight is used instead.'
            } else {
              items = parsed.map(item => ({ ...item, outcome: admittedOutcome }))
              extractionMethod = 'llm_distillation'
              distillationModel = apiConfig.chatOptions('distillation')?.model ?? 'api-config:distillation'
            }
          } catch (error) {
            distillationNote = `API-config distillation failed (${error instanceof Error ? error.message : String(error)}); the caller-provided insight is used instead.`
          }
        } else {
          const llm = ctx.get('llm') as LlmRuntime | undefined
          const provider = config.distillationProvider!.trim() || active.capturedModel?.provider || ''
          const model = config.distillationModel!.trim() || active.capturedModel?.model || ''
          if (!llm) {
            distillationNote = 'LLM distillation was requested but the llm service is unavailable; the caller-provided insight is used instead.'
          } else if (!provider || !model) {
            distillationNote = 'LLM distillation was requested but no distillation model is configured and no task model was captured; the caller-provided insight is used instead.'
          } else {
            try {
              const parsed = await distillWithLlm(
                llm,
                provider,
                model,
                systemPrompt,
                userPrompt,
                config.llmDistillationTemperature!,
                exec.signal,
              )
              if (parsed.length === 0) {
                distillationNote = 'LLM distillation produced no parseable memory items; the caller-provided insight is used instead.'
              } else {
                items = parsed.map(item => ({ ...item, outcome: admittedOutcome }))
                extractionMethod = 'llm_distillation'
                distillationModel = model
              }
            } catch (error) {
              distillationNote = `LLM distillation failed (${error instanceof Error ? error.message : String(error)}); the caller-provided insight is used instead.`
            }
          }
        }
      }

      const result = await runReasoningCore<DistillResponse>(config.pythonExecutable!, {
        operation: 'distill',
        taskId: active.taskId,
        sourceSessionId: active.sessionId,
        taskFamily: active.taskFamily,
        semanticText: active.semanticText,
        events: active.events,
        outcome: args.outcome,
        verifier,
        evidence,
        reasoningInsight: clipText(args.reasoning_insight ?? '', 1000),
        failureMode: clipText(args.failure_mode ?? '', 500),
        reasoningGraphJson: items && items.length > 1 ? '' : graphJson,
        extractionMethod,
        distillationModel,
        sourceMemoryIds: [],
        items,
        insightConfidence,
        minInsightConfidence,
        maxItemsPerOutcome: config.maxMemoryItemsPerOutcome,
        deSpecificationThreshold: config.deSpecificationThreshold,
        distillGateEnabled: config.distillGateEnabled,
        distillUpliftThreshold: config.distillUpliftThreshold,
        phaseFromDistillUplift: config.phaseFromDistillUplift,
        distillShrinkPrior: config.distillShrinkPrior,
        distillMinArmSamples: config.distillMinArmSamples,
      }, exec.signal)

      // v0.6.0 observational causal events: one record per memory retrieved
      // in this task, with the treatment defined by whether feedback marked
      // it used. Written once per task, only for success/fail outcomes; the
      // Beta feedback semantics stay untouched (zero regression).
      if (outcome === 'success' || outcome === 'fail') {
        if (!active.causalEventsRecorded) {
          active.causalEventsRecorded = true
          try {
            const classification = await runReasoningCore<ClassifyResponse>(config.pythonExecutable!, {
              operation: 'classify_task_pattern',
              taskDesc: active.semanticText,
              taskFamily: active.taskFamily,
              events: active.events,
            }, exec.signal)
            const now = Date.now()
            const pattern = classification.pattern
            const events: CausalEvent[] = [...active.retrievedMemoryIds]
              .slice(0, MAX_CAUSAL_EVENTS_PER_TASK)
              .map(memoryId => causalEventSchema.parse({
                eventId: `${memoryId}:${active.taskId}:${now}`,
                memoryId,
                kind: 'memory' as const,
                taskId: active.taskId,
                taskPattern: pattern,
                treatment: active.feedbackMemoryIds.has(memoryId) ? 'used' : 'retrieved_unused',
                outcome,
                stepIndex: 0,
                retrievalIntent: '',
                modelVersion: active.capturedModel?.model ?? '',
                pairId: '',
                createdAt: now,
              }))
            await writeCausalEvents(events)
          } catch (error) {
            // Causal recording is best effort: a classification failure must
            // never block the outcome save.
            ctx.logger.warn(`reasoning-memory: causal event derivation failed: ${error instanceof Error ? error.message : String(error)}`)
          }
        }
      }
      if (!result.stored || !result.memories || result.memories.length === 0) {
        return {
          stored: false,
          reason: [result.reason, distillationNote].filter(Boolean).join(' '),
          memoryIds: [],
          trustLevel: 'not-applicable' as const,
          gateRejectedCount: result.gateRejectedCount ?? 0,
          distillation: { method: extractionMethod, model: distillationModel },
        }
      }

      const storedItems = result.memories.map(item => memoryItemSchema.parse(item))
      for (const item of storedItems) {
        await memories.put(item.memoryId, item)
        lexicalIndex.upsert(item)
        builtinWrite(store => store.upsert(item))
      }
      const overflow = memories.size - config.maxMemories!
      const expiredIds: string[] = []
      if (overflow > 0) {
        const oldest = [...memories.entries()]
          .sort((left, right) => left[1].createdAt - right[1].createdAt)
          .slice(0, overflow)
        for (const [key] of oldest) {
          await memories.delete(key)
          expiredIds.push(key)
        }
        lexicalIndex.delete(expiredIds)
        builtinWrite(store => store.delete(expiredIds))
      }
      if (vectorIndex && !rt.state.vectorIndexDegraded) {
        void vectorIndexReady.then(async () => {
          if (rt.state.vectorIndexDegraded || !vectorIndex) return
          if (expiredIds.length > 0) await vectorIndex.delete(expiredIds)
          for (const item of storedItems) {
            if (!expiredIds.includes(item.memoryId)) await vectorIndex.upsert(item)
          }
        }).catch(() => {
          rt.state.vectorIndexDegraded = true
        })
      }
      return {
        stored: true,
        reason: [result.reason, distillationNote, 'the submitted verifier/evidence were not independently checked by this plugin.']
          .filter(Boolean).join(' '),
        memoryIds: storedItems.map(item => item.memoryId),
        trustLevel: 'unverified' as const,
        gateRejectedCount: result.gateRejectedCount ?? 0,
        distillation: { method: extractionMethod, model: distillationModel },
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_record_counterfactual',
    description: 'Record one arm of a paired continuation experiment (the strongest causal evidence): run the SAME task prefix twice, once with this memory retrieved and once without, then report each arm. Both arms with the same pair_id form a pair; the causal estimator computes the paired effect from complete pairs. Each task can record at most one with-arm and one without-arm per memory. Without an active task, pair_id is required so unrelated standalone arms never pair up.',
    parameters: {
      memory_id: { type: 'string', required: true, description: 'The memory id whose effect is being measured.' },
      arm: { type: 'string', required: true, enum: ['with', 'without'], description: 'Whether this continuation arm used the memory (with) or skipped it (without).' },
      outcome: { type: 'string', required: true, enum: ['success', 'fail'], description: 'The environment/verifier outcome of this arm.' },
      pair_id: { type: 'string', description: 'Pair identifier shared by the two arms; defaults to one pair per task and memory inside an active task, and is REQUIRED when no active task exists.' },
      task_pattern: { type: 'string', enum: ['multi_hop_qa', 'code_synthesis', 'tool_orchestration', 'gui_operation', 'long_horizon_planning', 'numeric_search', 'general'], description: 'Stratification label for this experiment; defaults to the classified active task pattern.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          recorded: { type: 'boolean', required: true },
          eventId: { type: 'string', required: true },
          pairId: { type: 'string', required: true },
          note: { type: 'string', required: true },
        },
      } as const,
      render: renderJson,
    },
    async execute(args, exec) {
      const sessionId = getSessionIdFromAgent(exec.agent) ?? ''
      const active = activeBySession.get(sessionId)
      const memory = memories.get(args.memory_id)
      if (!memory) {
        return { recorded: false, eventId: '', pairId: '', note: `no memory with id '${args.memory_id}'` }
      }
      const arm = args.arm as 'with' | 'without'
      const explicitPair = typeof args.pair_id === 'string' && args.pair_id.trim()
        ? clipText(args.pair_id, 100)
        : ''
      // Standalone arms (no active task) must declare their pair explicitly:
      // the task-scoped default pair cannot exist here, and a global default
      // would silently pair unrelated experiments and poison the paired ATE.
      if (!active && !explicitPair) {
        return {
          recorded: false, eventId: '', pairId: '',
          note: 'standalone counterfactual arms require an explicit pair_id; without an active task the two arms of the same pair share that id.',
        }
      }
      const pairId = explicitPair || `${active!.taskId}:${memory.memoryId}`
      if (active && active.counterfactualRecorded.has(`${pairId}|${arm}`)) {
        return { recorded: false, eventId: '', pairId, note: 'this task already recorded this arm for this pair; each pair accepts one with-arm and one without-arm.' }
      }
      let pattern = args.task_pattern as CausalEvent['taskPattern'] | undefined
      if (!pattern) {
        try {
          const classification = await runReasoningCore<ClassifyResponse>(config.pythonExecutable!, {
            operation: 'classify_task_pattern',
            taskDesc: active?.semanticText ?? memory.semanticText,
            taskFamily: active?.taskFamily ?? '',
            events: active?.events ?? [],
          }, exec.signal)
          pattern = classification.pattern
        } catch {
          pattern = 'general'
        }
      }
      const now = Date.now()
      const event = causalEventSchema.parse({
        eventId: `${memory.memoryId}:${pairId}:${arm}:${now}`,
        memoryId: memory.memoryId,
        kind: 'memory' as const,
        taskId: active?.taskId ?? '',
        taskPattern: pattern,
        treatment: arm === 'with' ? 'counterfactual_with' : 'counterfactual_without',
        outcome: args.outcome as 'success' | 'fail',
        stepIndex: 0,
        retrievalIntent: '',
        modelVersion: active?.capturedModel?.model ?? '',
        pairId,
        createdAt: now,
      })
      await writeCausalEvents([event])
      active?.counterfactualRecorded.add(`${pairId}|${arm}`)
      const pairedArms = [...causalEvents.entries()]
        .map(([, entry]) => entry)
        .filter(entry => entry.pairId === pairId && entry.memoryId === memory.memoryId)
        .map(entry => entry.treatment)
      const note = pairedArms.includes('counterfactual_with') && pairedArms.includes('counterfactual_without')
        ? 'Both arms are recorded: the pair now feeds the paired causal estimate.'
        : 'Arm recorded; record the opposite arm with the same pair_id to complete the pair.'
      return { recorded: true, eventId: event.eventId, pairId, note }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_end_task',
    description: 'End the active reasoning-memory task and clear its temporary trajectory capture. Call memory_save_outcome first if a reusable outcome should be distilled.',
    parameters: {
      task_id: { type: 'string', required: true, description: 'The task_id returned by memory_begin_task.' },
    },
    output: { schema: END_OUTPUT_SCHEMA, render: renderJson },
    async execute(args, exec) {
      const active = activeByTaskId.get(args.task_id)
      const callerSessionId = getSessionIdFromAgent(exec.agent)
      if (!active || active.sessionId !== callerSessionId) {
        return {
          ended: false,
          taskId: args.task_id,
          observedEvents: 0,
          captureTruncated: false,
          reasoningCaptureStatus: 'disabled' as const,
          usage: { inputTokens: 0, outputTokens: 0, reasoningTokens: 0 },
        }
      }
      activeByTaskId.delete(active.taskId)
      activeBySession.delete(active.sessionId)
      return {
        ended: true,
        taskId: active.taskId,
        observedEvents: active.events.length,
        captureTruncated: active.captureTruncated,
        reasoningCaptureStatus: active.reasoningCaptureStatus,
        usage: active.usage,
      }
    },
  }))
}
