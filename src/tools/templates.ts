// TemplateTools — model-facing tool surface.

import type { Context } from '@deepseek-ai/cordis'
import type { Runtime } from '../runtime.js'
import { strategyTemplateSchema } from '../domain.js'
import type { StrategyTemplate } from '../domain.js'
import { clipText, getSessionIdFromAgent, renderJson } from '../helpers.js'
import { runReasoningCore } from '../python.js'
import { PLAN_OUTPUT_SCHEMA, TEMPLATE_LIST_OUTPUT_SCHEMA, TEMPLATE_LOAD_OUTPUT_SCHEMA, TEMPLATE_PUBLISH_OUTPUT_SCHEMA } from '../tool-schemas.js'
import type { PlanResponse, PublishValidateResponse } from '../types.js'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { randomUUID } from 'node:crypto'

export function registerTemplateTools(ctx: Context, rt: Runtime): void {
  const { config } = rt
  const { ensureTemplatesSeeded, skills, templates } = rt.storage
  const { activeBySession } = rt.capture

  ctx.tools.register(defineTool({
    name: 'memory_plan_task',
    description: 'Classify the current task into a task pattern and return the best-matching strategy templates with their step-level module demands and proactive retrieval points. This is the joint retrieval and activation planning surface; templates are guidance, not orders. Matching templates are recorded for this task so memory_feedback can rate them.',
    parameters: {
      task_desc: { type: 'string', required: true, description: 'Short task description, the same text you would pass to memory_begin_task.' },
      goal: { type: 'string', description: 'Intended result, if distinct from the task description.' },
      task_family: { type: 'string', description: 'Optional task family or repository label; it strongly informs the pattern classification.' },
      max_templates: { type: 'integer', description: 'Maximum matched templates to return; defaults to 2 and is capped at 10.' },
    },
    output: { schema: PLAN_OUTPUT_SCHEMA, render: renderJson },
    async execute(args, exec) {
      const sessionId = getSessionIdFromAgent(exec.agent) ?? ''
      const active = activeBySession.get(sessionId)
      await ensureTemplatesSeeded()
      const templateList = [...templates.entries()].map(([, record]) => record)
      const taskDesc = clipText(args.task_desc, 4000)
      if (!taskDesc) throw new Error('task_desc must not be empty')
      const maxTemplates = Number.isInteger(args.max_templates) ? Math.max(1, Math.min(10, Number(args.max_templates))) : 2
      const templateMatchMin = Math.max(0, Math.min(1, Number(config.templateMatchMin ?? 0.55)))
      const result = await runReasoningCore<PlanResponse>(config.pythonExecutable!, {
        operation: 'plan_task',
        taskDesc,
        goal: clipText(args.goal ?? '', 4000),
        taskFamily: clipText(args.task_family ?? '', 200),
        events: active?.events ?? [],
        hasGraph: false,
        graphNodeKinds: [],
        templates: templateList,
        maxTemplates,
        templateMatchMin,
        quantumDecisionEnabled: config.quantumDecisionEnabled,
        maxSuperpositionSize: config.maxSuperpositionSize,
        phaseFromCausal: config.phaseFromCausal,
        decoherenceRate: config.decoherenceRate,
      }, exec.signal)
      const matchedIds = result.matchedTemplates.map(template => template.templateId)
      if (active) {
        active.planTemplateIds.clear()
        for (const templateId of matchedIds) active.planTemplateIds.add(templateId)
      }
      // Recommended skills: read-only keyword match against the published
      // skill list using the classified pattern and the top template name.
      const keywordParts = new Set<string>([
        result.pattern.replace(/_/g, '-'),
        ...result.matchedTemplates.slice(0, 1).flatMap(template =>
          template.name.replace(/^memory_tpl_/, '').split('-'),
        ),
      ].filter(part => part.length >= 4))
      const recommendedSkills = [...skills.entries()]
        .map(([, record]) => record)
        .filter(record => [...keywordParts].some(part =>
          record.name.includes(part) || record.description.toLocaleLowerCase().includes(part)))
        .sort((left, right) => left.name.localeCompare(right.name))
        .slice(0, 3)
        .map(record => ({ name: record.name, description: record.description }))
      const belowThreshold = result.matchedTemplates.length === 0
        || result.matchedTemplates[0].score < templateMatchMin
      const documentSteps = [...new Set(result.matchedTemplates.flatMap(template =>
        (template.steps ?? []).flatMap(step =>
          (step.moduleDemands ?? []).some(demand => demand.kind === 'retrieve_document')
            ? [step.index]
            : [],
        ),
      ))].sort((left, right) => left - right)
      const documentHint = documentSteps.length > 0
        ? `Steps ${documentSteps.join(', ')} declare a retrieve_document demand: call memory_retrieve_document for those steps.`
        : ''
      const { quantum: _quantum, ...planResult } = result
      return {
        ...planResult,
        matchedTemplates: result.matchedTemplates.map(template => ({
          templateId: template.templateId,
          name: template.name,
          description: template.description,
          taskPatterns: template.taskPatterns,
          steps: template.steps,
          structureSignature: template.structureSignature,
          activeRetrievalPoints: template.activeRetrievalPoints,
          score: template.score,
          breakdown: template.breakdown,
          strongMatch: template.strongMatch,
          injectable: template.injectable,
          deprecated: template.deprecated,
          utility: template.utility,
        })),
        recommendedSkills,
        note: [
          belowThreshold
            ? 'No template cleared the match threshold; proceed in general mode instead of forcing a template onto this task.'
            : 'Template steps and module demands are guidance; re-verify every result in the current environment and rate the template with memory_feedback afterwards.',
          documentHint,
        ].filter(Boolean).join(' '),
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_list_templates',
    description: 'List strategy templates (layer 1 of progressive disclosure: metadata only). Use memory_load_template to see a full template with its steps and module demands.',
    parameters: {
      pattern: { type: 'string', enum: ['multi_hop_qa', 'code_synthesis', 'tool_orchestration', 'gui_operation', 'long_horizon_planning', 'numeric_search', 'general'], description: 'Only templates declaring this task pattern.' },
      include_deprecated: { type: 'boolean', description: 'Include deprecated templates; defaults to true.' },
    },
    output: { schema: TEMPLATE_LIST_OUTPUT_SCHEMA, render: renderJson },
    async execute(args) {
      await ensureTemplatesSeeded()
      const includeDeprecated = args.include_deprecated !== false
      const pattern = args.pattern as string | undefined
      const records = [...templates.entries()]
        .map(([, record]) => record)
        .filter(record =>
          (includeDeprecated || record.status !== 'deprecated')
          && (!pattern || record.taskPatterns.includes(pattern as StrategyTemplate['taskPatterns'][number])))
        .sort((left, right) => left.name.localeCompare(right.name))
      return {
        count: records.length,
        templates: records.map(record => ({
          templateId: record.templateId,
          name: record.name,
          description: record.description,
          taskPatterns: record.taskPatterns,
          version: record.version,
          status: record.status,
          utility: record.utility,
        })),
        note: 'Layer 1 of progressive disclosure: metadata only. Load a template with memory_load_template for its full steps and module demands.',
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_load_template',
    description: 'Load one strategy template by id (layer 2 of progressive disclosure): full steps, module demands, structure signature, preconditions, provenance summary, and utility. Deprecated templates stay loadable with their status flag.',
    parameters: {
      template_id: { type: 'string', required: true, description: 'The template id from memory_list_templates or memory_plan_task.' },
    },
    output: { schema: TEMPLATE_LOAD_OUTPUT_SCHEMA, render: renderJson },
    async execute(args) {
      await ensureTemplatesSeeded()
      const record = templates.get(args.template_id)
      if (!record) {
        return {
          found: false,
          templateId: args.template_id,
          name: '',
          description: '',
          taskPatterns: [],
          steps: [],
          structureSignature: [],
          preconditions: [],
          provenance: { author: 'builtin' as const, sourceMemoryIds: [], sourceTaskIds: [] },
          utility: { helpful: 0, harmful: 0, neutral: 0 },
          version: 0,
          status: 'active' as const,
          note: `no template named '${args.template_id}'; run memory_list_templates for the available ids.`,
        }
      }
      return {
        found: true,
        templateId: record.templateId,
        name: record.name,
        description: record.description,
        taskPatterns: record.taskPatterns,
        steps: record.steps,
        structureSignature: record.structureSignature,
        preconditions: record.preconditions,
        provenance: record.provenance,
        utility: record.utility,
        version: record.version,
        status: record.status,
        note: record.status === 'deprecated'
          ? 'Layer 2 of progressive disclosure. This template is deprecated: re-verify each step in the current environment before following it.'
          : 'Layer 2 of progressive disclosure. Template steps are guidance; verify results in the current environment.',
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_publish_template',
    description: 'Publish a caller-authored strategy template. Names must keep the memory_tpl_ prefix with a kebab-case body; descriptions must carry trigger phrasing and no em-dashes. Republishing the same name bumps its version instead of deleting the previous one.',
    parameters: {
      name: { type: 'string', required: true, description: 'Template name: memory_tpl_ prefix followed by kebab-case (lowercase letters, digits, hyphens), at most 80 characters.' },
      description: { type: 'string', required: true, description: 'Model-facing trigger description (when/if/for, or 褰?濡傛灉/鎻愬埌/闇€瑕?鏃?; no em-dashes; at most 400 characters.' },
      task_patterns: { type: 'array', required: true, items: { type: 'string', enum: ['multi_hop_qa', 'code_synthesis', 'tool_orchestration', 'gui_operation', 'long_horizon_planning', 'numeric_search', 'general'] }, description: 'One or more task patterns this template applies to.' },
      steps: {
        type: 'array', required: true, description: '1 to 8 steps in execution order.',
        items: {
          type: 'object', additionalProperties: false,
          properties: {
            index: { type: 'integer', required: true, description: 'Step number, starting at 1.' },
            title: { type: 'string', required: true, description: 'Short step label.' },
            module_demands: {
              type: 'array', required: true, description: '1 to 4 module demands for this step.',
              items: {
                type: 'object', additionalProperties: false,
                properties: {
                  kind: { type: 'string', required: true, enum: ['retrieve_memory', 'retrieve_template', 'use_skill', 'invoke_tool', 'verify', 'rollback_check', 'retrieve_document'] },
                  intent: { type: 'string', description: 'What this demand should accomplish; used as a retrieval query hint.' },
                },
              },
            },
          },
        },
      },
      preconditions: { type: 'array', items: { type: 'string' }, description: 'Conditions that should hold before applying this template.' },
    },
    output: { schema: TEMPLATE_PUBLISH_OUTPUT_SCHEMA, render: renderJson },
    async execute(args) {
      await ensureTemplatesSeeded()
      const validation = await runReasoningCore<PublishValidateResponse>(config.pythonExecutable!, {
        operation: 'publish_template_validate',
        draft: {
          name: args.name,
          description: args.description,
          task_patterns: args.task_patterns,
          steps: args.steps,
          preconditions: args.preconditions ?? [],
        },
      }, new AbortController().signal)
      if (!validation.valid) {
        return {
          published: false,
          templateId: '',
          name: typeof args.name === 'string' ? clipText(args.name, 80) : '',
          version: 0,
          structureSignature: [],
          note: validation.issues.join('; '),
        }
      }
      const normalized = validation.normalized
      const now = Date.now()
      const existing = [...templates.entries()]
        .map(([, record]) => record)
        .find(record => record.name === normalized.name)
      const record = strategyTemplateSchema.parse({
        templateId: existing?.templateId ?? randomUUID(),
        name: normalized.name,
        description: normalized.description,
        taskPatterns: normalized.taskPatterns,
        steps: normalized.steps,
        structureSignature: normalized.structureSignature,
        preconditions: normalized.preconditions,
        provenance: {
          author: 'caller' as const,
          sourceMemoryIds: [],
          sourceTaskIds: [],
        },
        utility: existing?.utility ?? { helpful: 0, harmful: 0, neutral: 0 },
        version: (existing?.version ?? 0) + 1,
        status: 'active' as const,
        createdAt: existing?.createdAt ?? now,
        updatedAt: now,
      })
      await templates.put(record.templateId, record)
      return {
        published: true,
        templateId: record.templateId,
        name: record.name,
        version: record.version,
        structureSignature: record.structureSignature,
        note: 'The template is caller-authored and carries no independently verified evidence; treat its steps as guidance.',
      }
    },
  }))
}
