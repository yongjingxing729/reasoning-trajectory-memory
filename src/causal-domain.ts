import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { z } from 'zod'

// Causal event log for the v0.6.0 causal utility engine. One event per
// (unit, task, treatment arm) observation; units are memories or templates
// (kind discriminates). The log lives in its own domain so the reasoning
// memory, skills, and documents domains stay untouched. Additive law: once
// data exists, new fields must carry zod defaults.
export const causalEventSchema = z.object({
  eventId: z.string(),
  memoryId: z.string(),
  kind: z.enum(['memory', 'template']),
  taskId: z.string(),
  taskPattern: z.enum([
    'multi_hop_qa',
    'code_synthesis',
    'tool_orchestration',
    'gui_operation',
    'long_horizon_planning',
    'numeric_search',
    'general',
  ]),
  treatment: z.enum(['used', 'retrieved_unused', 'counterfactual_with', 'counterfactual_without']),
  outcome: z.enum(['success', 'fail']),
  stepIndex: z.number().int().nonnegative(),
  retrievalIntent: z.string().default(''),
  modelVersion: z.string().default(''),
  pairId: z.string().default(''),
  createdAt: z.number().int().nonnegative(),
})

export type CausalEvent = z.infer<typeof causalEventSchema>

export const reasoningMemoryCausalDomain = defineDomain({
  name: 'reasoning_memory_causal',
  version: 1,
  layout: 'per-record',
  tables: {
    causal_events: domainTable<string, CausalEvent>(causalEventSchema),
  },
})
