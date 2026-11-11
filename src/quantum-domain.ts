import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { z } from 'zod'

// v0.7.0 quantum multi-probability engine collapse audit. One event per
// collapse point (rank_cutoff / sample_insufficient / explicit_commit /
// config_disabled); the state itself is emitted as the additive
// `superposition[]` field on retrieval / plan outputs. The domain lives on its
// own so the reasoning memory, causal, and document domains stay untouched.
// Additive law: new fields must carry zod defaults once real data exists.
export const quantumEventSchema = z.object({
  eventId: z.string(),
  retrievalId: z.string().default(''),
  kind: z.enum(['memory', 'template']),
  unitIds: z.array(z.string()),
  amplitudes: z.array(z.number()),
  phases: z.array(z.number()),
  coherence: z.number(),
  coherenceAfter: z.number(),
  collapseReason: z.enum(['rank_cutoff', 'sample_insufficient', 'explicit_commit', 'config_disabled']),
  estimatorVersion: z.string(),
  createdAt: z.number().int().nonnegative(),
})

export type QuantumEvent = z.infer<typeof quantumEventSchema>

export const reasoningMemoryQuantumDomain = defineDomain({
  name: 'reasoning_memory_quantum',
  version: 1,
  layout: 'per-record',
  tables: {
    quantum_events: domainTable<string, QuantumEvent>(quantumEventSchema),
  },
})
