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
  reasoningGraph: reasoningGraphSchema.default({ version: 1, nodes: [], edges: [] }),
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

const retrievalCandidateSchema = z.object({
  memoryId: z.string(),
  outcome: z.enum(['success', 'fail']),
  graphScore: z.number().min(0).max(1).default(0),
  lexicalScore: z.number().min(0).max(1).default(0),
  vectorScore: z.number().min(0).max(1).default(0),
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
})

export const retrievalRecordSchema = z.object({
  retrievalId: z.string(),
  taskId: z.string(),
  sourceSessionId: z.string(),
  queryHash: z.string(),
  route: z.enum(['auto', 'semantic', 'structure', 'graph', 'hybrid']),
  requestedK: z.number().int().positive(),
  positiveNegativeRatio: z.number().positive(),
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
  },
})
