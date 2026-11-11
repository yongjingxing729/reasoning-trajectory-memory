// MemoryConsolidationTools — model-facing tool surface.

import type { Context } from '@deepseek-ai/cordis'
import type { Runtime } from '../runtime.js'
import { collectStreamText, consolidateSystemPrompt, consolidateUserText, parseMemoryItems } from '../distillation-llm.js'
import type { ConsolidateExperience, DistilledMemoryItem } from '../distillation-llm.js'
import { memoryItemSchema } from '../domain.js'
import type { MemoryItem } from '../domain.js'
import { clipText, getSessionIdFromAgent, renderJson } from '../helpers.js'
import { runReasoningCore } from '../python.js'
import { CONSOLIDATE_OUTPUT_SCHEMA } from '../tool-schemas.js'
import type { DistillResponse } from '../types.js'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import type { LlmRuntime } from '@deepseek-ai/dsh-llm'
import { defineTool } from '@deepseek-ai/dsh-tools'

export function registerMemoryConsolidationTools(ctx: Context, rt: Runtime): void {
  const { config } = rt
  const { lexicalIndex, memories } = rt.storage
  const { apiConfig, builtinWrite, vectorIndex, vectorIndexReady } = rt.indexes

  ctx.tools.register(defineTool({
    name: 'memory_consolidate',
    description: 'Distill several reasoning memories from repeated attempts of the same kind of task into higher-quality transferable strategies via self-contrast reasoning (Memory-aware Test-Time Scaling). Success and failure experiences are compared together; each new item is labeled success or fail and stored as unverified. The source memories stay in the bank. Requires the profile to enable memoryConsolidationEnabled plus either a distillationProvider/distillationModel pair or a distillation endpoint in the API config file.',
    parameters: {
      memory_ids: { type: 'array', required: true, items: { type: 'string' }, description: '2 to 10 memory IDs from repeated attempts of the same task kind (retrieved or saved earlier).' },
      goal: { type: 'string', description: 'Optional shared task description used as the consolidated context.' },
    },
    output: { schema: CONSOLIDATE_OUTPUT_SCHEMA, render: renderJson },
    async execute(args, exec) {
      const notApplicable = (reason: string) => ({ stored: false as const, reason, memoryIds: [], trustLevel: 'not-applicable' as const })
      if (!config.memoryConsolidationEnabled) return notApplicable('memory_consolidate is disabled in this profile; set memoryConsolidationEnabled.')
      // Same precedence rule as memory_save_outcome: an explicit profile
      // distillationProvider wins; the API-config chat endpoint only takes
      // over when the profile left that model unset.
      const apiChatAvailable = !config.distillationProvider!.trim()
        && apiConfig?.endpointKind('distillation') === 'openai-chat'
      const llm = ctx.get('llm') as LlmRuntime | undefined
      if (!apiChatAvailable && !llm) return notApplicable('memory_consolidate requires the llm service or a distillation endpoint in the API config file.')
      const provider = config.distillationProvider!.trim()
      const model = config.distillationModel!.trim()
      if (!apiChatAvailable && (!provider || !model)) return notApplicable('memory_consolidate requires distillationProvider and distillationModel in the plugin profile, or a distillation endpoint in the API config file.')
      const ids = [...new Set(args.memory_ids)].slice(0, 10)
      if (ids.length < 2) return notApplicable('provide at least two distinct memory IDs from repeated attempts of the same task kind.')
      const sources = ids
        .map(id => memories.get(id))
        .filter((item): item is MemoryItem => Boolean(item))
      if (sources.length < 2) return notApplicable('at least two of the provided memory IDs must exist in the memory bank.')

      const experiences: ConsolidateExperience[] = sources.map(item => ({
        outcome: item.outcome,
        context: item.semanticText,
        reasoning: item.reasoning || item.content,
        actions: item.structureSignature.join(' 鈫?'),
        evidence: item.evidence.summary,
        title: item.title,
      }))
      const goal = clipText(args.goal ?? '', 4000)

      let parsed: DistilledMemoryItem[]
      let usedModel = ''
      try {
        const system = consolidateSystemPrompt(config.maxMemoryItemsPerOutcome!)
        const user = consolidateUserText(goal, experiences)
        let text: string
        if (apiChatAvailable && apiConfig) {
          text = await apiConfig.openAiChat('distillation', system, user, config.llmDistillationTemperature!, exec.signal)
          usedModel = apiConfig.chatOptions('distillation')?.model ?? 'api-config:distillation'
        } else {
          const prepared = await llm!.prepareCall({ provider, model, temperature: config.llmDistillationTemperature! }, exec.signal)
          const message = createUserMessage({
            content: [{ type: 'text', text: user }],
            source: { kind: 'plugin', plugin: 'reasoning-memory' },
          })
          text = await collectStreamText(prepared.stream({
            provider: prepared.config.provider,
            model: prepared.config.model,
            temperature: prepared.config.temperature,
            messages: [message],
            system,
            signal: exec.signal,
          }))
          usedModel = model
        }
        parsed = parseMemoryItems(text, true)
      } catch (error) {
        return notApplicable(`memory consolidation failed: ${error instanceof Error ? error.message : String(error)}`)
      }
      if (parsed.length === 0) return notApplicable('self-contrast distillation produced no parseable memory items with an outcome label.')

      const result = await runReasoningCore<DistillResponse>(config.pythonExecutable!, {
        operation: 'distill',
        taskId: '',
        sourceSessionId: getSessionIdFromAgent(exec.agent) ?? '',
        taskFamily: 'consolidated',
        semanticText: goal || sources.map(item => item.semanticText).join('\n').slice(0, 4000),
        events: [],
        outcome: 'success',
        verifier: 'self-contrast',
        evidence: `contrast over ${sources.length} source memories; not independently verified`,
        reasoningInsight: '',
        failureMode: '',
        reasoningGraphJson: '',
        extractionMethod: 'llm_self_contrast' as const,
        distillationModel: usedModel,
        sourceMemoryIds: ids,
        items: parsed,
        maxItemsPerOutcome: config.maxMemoryItemsPerOutcome,
        deSpecificationThreshold: config.deSpecificationThreshold,
        distillGateEnabled: config.distillGateEnabled,
        distillUpliftThreshold: config.distillUpliftThreshold,
        phaseFromDistillUplift: config.phaseFromDistillUplift,
        distillShrinkPrior: config.distillShrinkPrior,
        distillMinArmSamples: config.distillMinArmSamples,
      }, exec.signal)
      if (!result.stored || !result.memories || result.memories.length === 0) {
        return { stored: false, reason: result.reason, memoryIds: [], trustLevel: 'not-applicable' as const }
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
        reason: `stored ${storedItems.length} unverified consolidated memories distilled from ${sources.length} source memories; the sources remain in the bank.`,
        memoryIds: storedItems.map(item => item.memoryId),
        trustLevel: 'unverified' as const,
      }
    },
  }))
}
