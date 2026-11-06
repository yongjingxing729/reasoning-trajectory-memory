import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { z } from 'zod'

const structureTag = z.enum([
  'decompose',
  'constrain',
  'search',
  'execute',
  'verify',
  'rollback',
])

const graphNodeSchema = z.object({
  id: z.string().min(1).max(40),
  kind: z.enum(['context', 'premise', 'inference', 'action', 'evidence', 'conclusion', 'failure']),
  text: z.string().min(1).max(400),
  source: z.enum(['agent_input', 'task_input', 'tool_trace', 'verifier_input', 'outcome_input', 'derived']),
})

const graphEdgeSchema = z.object({
  sourceId: z.string().min(1).max(40),
  targetId: z.string().min(1).max(40),
  relation: z.enum(['supports', 'requires', 'leads_to', 'precedes', 'frames', 'guides', 'explains', 'qualifies', 'contradicts']),
  source: z.enum(['agent_input', 'task_input', 'tool_trace', 'verifier_input', 'outcome_input', 'derived']),
})

export const reasoningGraphSchema = z.object({
  version: z.literal(1),
  nodes: z.array(graphNodeSchema).max(24),
  edges: z.array(graphEdgeSchema).max(48),
})

export type ReasoningGraph = z.infer<typeof reasoningGraphSchema>

export const memoryItemSchema = z.object({
  memoryId: z.string(),
  type: z.enum(['strategy', 'structure', 'failure']),
  outcome: z.enum(['success', 'fail']),
  taskScope: z.string(),
  preconditions: z.array(z.string()),
  content: z.string(),
  // Additive defaults keep the existing v1 storage records readable.
  reasoning: z.string().default(''),
  // ReasoningBank-style structured distillation fields (title / description /
  // content / when-NOT-to-use), plus extraction provenance. All additive.
  title: z.string().default(''),
  description: z.string().default(''),
  contraindications: z.array(z.string()).default([]),
  extractionMethod: z.enum(['caller_insight', 'llm_distillation', 'llm_self_contrast']).default('caller_insight'),
  distillationModel: z.string().default(''),
  sourceMemoryIds: z.array(z.string()).default([]),
  reasoningGraph: reasoningGraphSchema.default({ version: 1, nodes: [], edges: [] }),
  // v0.4.0 strategy-template alignment fields. All additive with defaults;
  // a deprecated memory still participates in retrieval and is only flagged.
  strategyTemplateId: z.string().default(''),
  reasoningLevel: z.enum(['analysis', 'path', 'step']).default('path'),
  insightConfidence: z.number().min(0).max(1).default(0),
  status: z.enum(['active', 'deprecated']).default('active'),
  // Recent distinct tasks that rated this memory harmful. A bounded window,
  // not a complete history; used by the conservative deprecation mark.
  harmfulTaskIds: z.array(z.string()).default([]),
  feedback: z.object({
    helpful: z.number().int().nonnegative(),
    harmful: z.number().int().nonnegative(),
    neutral: z.number().int().nonnegative(),
  }).default({ helpful: 0, harmful: 0, neutral: 0 }),
  provenance: z.object({
    taskId: z.string(),
    sourceSessionId: z.string(),
    eventSeqs: z.array(z.number().int().nonnegative()),
  }),
  evidence: z.object({
    verifier: z.string(),
    summary: z.string(),
    independentlyVerified: z.literal(false),
  }),
  confidence: z.number().min(0).max(1),
  trustLevel: z.enum(['verified', 'unverified', 'unknown']),
  extractorVersion: z.string(),
  createdAt: z.number().int().nonnegative(),
  updatedAt: z.number().int().nonnegative(),
  semanticText: z.string(),
  structureSignature: z.array(structureTag),
})

export type MemoryItem = z.infer<typeof memoryItemSchema>

// Strategy templates (final spec 2.1): the four-layer mapping's procedural
// memory. The table is added to the existing v1 domain, so every field with a
// default below must keep its default once real data exists.
export const templateModuleDemandSchema = z.object({
  kind: z.enum([
    'retrieve_memory',
    'retrieve_template',
    'use_skill',
    'invoke_tool',
    'verify',
    'rollback_check',
    'retrieve_document',
  ]),
  intent: z.string().default(''),
})

export const templateStepSchema = z.object({
  index: z.number().int().positive(),
  title: z.string(),
  moduleDemands: z.array(templateModuleDemandSchema),
})

export const strategyTemplateSchema = z.object({
  templateId: z.string(),
  name: z.string(),
  description: z.string(),
  taskPatterns: z.array(z.enum([
    'multi_hop_qa', 'code_synthesis', 'tool_orchestration',
    'gui_operation', 'long_horizon_planning', 'numeric_search', 'general',
  ])),
  steps: z.array(templateStepSchema),
  structureSignature: z.array(structureTag),
  preconditions: z.array(z.string()).default([]),
  provenance: z.object({
    author: z.enum(['builtin', 'mined', 'caller']),
    sourceMemoryIds: z.array(z.string()).default([]),
    sourceTaskIds: z.array(z.string()).default([]),
  }),
  utility: z.object({
    helpful: z.number().int().nonnegative().default(0),
    harmful: z.number().int().nonnegative().default(0),
    neutral: z.number().int().nonnegative().default(0),
  }),
  version: z.number().int().positive().default(1),
  status: z.enum(['active', 'deprecated']).default('active'),
  createdAt: z.number().int().nonnegative(),
  updatedAt: z.number().int().nonnegative(),
})

export type StrategyTemplate = z.infer<typeof strategyTemplateSchema>

const retrievalCandidateSchema = z.object({
  memoryId: z.string(),
  outcome: z.enum(['success', 'fail']),
  graphScore: z.number().min(0).max(1).default(0),
  lexicalScore: z.number().min(0).max(1).default(0),
  vectorScore: z.number().min(0).max(1).default(0),
  templateScore: z.number().min(0).max(1).default(0),
  contextScore: z.number().min(0).max(1).default(0),
  reasoningScore: z.number().min(0).max(1).default(0),
  semanticScore: z.number().min(0).max(1),
  structureScore: z.number().min(0).max(1),
  combinedScore: z.number().min(0).max(1),
  utilityMultiplier: z.number().min(0.8).max(1.2).default(1),
  contextRank: z.number().int().positive().nullable().default(null),
  reasoningRank: z.number().int().positive().nullable().default(null),
  structureRank: z.number().int().positive().nullable(),
  graphRank: z.number().int().positive().nullable().default(null),
  lexicalRank: z.number().int().positive().nullable().default(null),
  vectorRank: z.number().int().positive().nullable().default(null),
  combinedRank: z.number().int().positive(),
  selectedRank: z.number().int().positive().nullable(),
  // v0.6.0 causal utility audit per candidate. All additive with defaults.
  causalApplied: z.boolean().default(false),
  causalUplift: z.number().min(-1).max(1).default(0),
  causalSamples: z.number().int().nonnegative().default(0),
  simpsonParadox: z.boolean().default(false),
  causalStrata: z.string().default(''),
  estimatorVersion: z.string().default(''),
})

export const retrievalRecordSchema = z.object({
  retrievalId: z.string(),
  taskId: z.string(),
  sourceSessionId: z.string(),
  queryHash: z.string(),
  route: z.enum(['auto', 'semantic', 'structure', 'graph', 'hybrid', 'document']),
  requestedK: z.number().int().positive(),
  positiveNegativeRatio: z.number().positive(),
  // v0.4.0 step-aligned retrieval audit. All additive with defaults.
  activeTemplateId: z.string().default(''),
  stepIndex: z.number().int().nonnegative().default(0),
  retrievalIntent: z.string().default(''),
  templateChannelWeight: z.number().min(0).max(1).default(0),
  deprecatedFilteredCount: z.number().int().nonnegative().default(0),
  // v0.5.0 document retrieval audit: document results live in their own
  // fields and never enter selectedMemoryIds or the memory quota.
  documentIds: z.array(z.string()).default([]),
  documentPatternFallback: z.boolean().default(false),
  // v0.6.0 causal utility audit (record-level summary of the selected rows).
  causalUplift: z.number().min(-1).max(1).default(0),
  causalSamples: z.number().int().nonnegative().default(0),
  simpsonParadox: z.boolean().default(false),
  causalStrata: z.string().default(''),
  estimatorVersion: z.string().default(''),
  fusionWeights: z.object({
    // Defaults admit legacy v1 audits written with { semantic, structure }.
    context: z.number().min(0).max(1).default(0.45),
    reasoning: z.number().min(0).max(1).default(0),
    structure: z.number().min(0).max(1),
    graph: z.number().min(0).max(1).default(0),
    vector: z.number().min(0).max(1).default(0),
    lexical: z.number().min(0).max(1).default(0),
  }),
  fusionMode: z.enum(['weighted', 'rrf']).default('weighted'),
  relevanceThreshold: z.number().min(0).max(1).default(0),
  thresholdFilteredCount: z.number().int().nonnegative().default(0),
  enterpriseRuleIds: z.array(z.string()).default([]),
  enterpriseRuleRagStatus: z.enum([
    'disabled', 'ready', 'no_matches', 'unavailable', 'invalid_configuration',
  ]).default('disabled'),
  enterpriseRuleRagRequired: z.boolean().default(false),
  selectedMemoryIds: z.array(z.string()),
  candidates: z.array(retrievalCandidateSchema),
  createdAt: z.number().int().nonnegative(),
})

export type RetrievalRecord = z.infer<typeof retrievalRecordSchema>

export const reasoningMemoryDomain = defineDomain({
  name: 'reasoning_memory',
  version: 1,
  layout: 'per-record',
  tables: {
    memories: domainTable<string, MemoryItem>(memoryItemSchema),
    retrievals: domainTable<string, RetrievalRecord>(retrievalRecordSchema),
    templates: domainTable<string, StrategyTemplate>(strategyTemplateSchema),
  },
})
