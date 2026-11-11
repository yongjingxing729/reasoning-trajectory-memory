import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { z } from 'zod'

// v0.8.0 causal distillation event log: the do(distill) treatment vs
// do(not-distill) control, plus paired restored-state rollout arms. One event
// per (candidate, task, arm) observation. The log lives in its own domain so
// the reasoning memory, causal, quantum, and document domains stay untouched.
// Additive law: new fields must carry zod defaults once real data exists.
export const distillCausalEventSchema = z.object({
  eventId: z.string(),
  memoryId: z.string(),
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
  treatment: z.enum(['distilled', 'not_distilled', 'counterfactual_with', 'counterfactual_without']),
  outcome: z.enum(['success', 'fail']),
  stepIndex: z.number().int().nonnegative().default(0),
  modelVersion: z.string().default(''),
  pairId: z.string().default(''),
  createdAt: z.number().int().nonnegative(),
})

export type DistillCausalEvent = z.infer<typeof distillCausalEventSchema>

export const reasoningMemoryDistillCausalDomain = defineDomain({
  name: 'reasoning_memory_distill_causal',
  version: 1,
  layout: 'per-record',
  tables: {
    distill_causal_events: domainTable<string, DistillCausalEvent>(distillCausalEventSchema),
  },
})
