/** 记忆库: the storageDomain-backed repository layer.
 *
 * Owns every typed domain the plugin opens (memories, retrievals, templates,
 * skills, documents+chunks, causal/quantum/distill-causal event logs), the
 * in-memory lexical indexes built from them, the bounded event-log writers,
 * the structured scan fallback, and the idempotent builtin-template seeding.
 * It never talks to a retrieval service or to the model; tool modules get it
 * through the runtime container. */
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-session'
import type {} from '@deepseek-ai/dsh-storage-domain'
import { runReasoningCore } from './python.js'
import type { Config } from './config.js'
import { reasoningMemoryDomain, strategyTemplateSchema, type StrategyTemplate } from './domain.js'
import { reasoningMemorySkillsDomain } from './skill-domain.js'
import { reasoningMemoryDocumentsDomain } from './document-domain.js'
import { reasoningMemoryCausalDomain, type CausalEvent } from './causal-domain.js'
import { reasoningMemoryQuantumDomain, type QuantumEvent } from './quantum-domain.js'
import { reasoningMemoryDistillCausalDomain, type DistillCausalEvent } from './distill-causal-domain.js'
import { LexicalMemoryIndex } from './lexical-index.js'
import { DocumentLexicalIndex } from './document-index.js'
import type { StructuredMemoryRow, StructuredQueryFilter } from './builtin-store.js'

/** FIFO cap for the per-memory causal event log; it bounds storage growth, not
 *  the estimator sample window (the Python core applies its own minimums). */
export const MAX_CAUSAL_EVENTS_PER_UNIT = 200

export async function createStorageLayer(ctx: Context, config: Config) {
  const domain = await ctx.storageDomain.open(reasoningMemoryDomain)
  const memories = domain.table('memories')
  const retrievals = domain.table('retrievals')
  const templates = domain.table('templates')
  const skillsDomain = await ctx.storageDomain.open(reasoningMemorySkillsDomain)
  const skills = skillsDomain.table('skills')
  const lexicalIndex = new LexicalMemoryIndex([...memories.entries()].map(([, item]) => item))

  // Reasoning-aware document retrieval: an isolated domain, its own BM25
  // postings, and an optional vector collection. Deleting documents can
  // never touch the memory or skill surfaces.
  const documentsDomain = await ctx.storageDomain.open(reasoningMemoryDocumentsDomain)
  const documents = documentsDomain.table('documents')
  const documentChunks = documentsDomain.table('chunks')
  const documentLexicalIndex = new DocumentLexicalIndex()
  for (const [, chunk] of documentChunks.entries()) {
    const record = documents.get(chunk.docId)
    if (!record) continue
    documentLexicalIndex.upsert(record.name, record.taskPatterns, chunk)
  }

  // Causal event log for the v0.6.0 causal utility engine: its own domain,
  // written by memory_save_outcome (observational events) and
  // memory_record_counterfactual (paired arms). The estimator runs in the
  // Python core; this layer only records and forwards.
  const causalDomain = await ctx.storageDomain.open(reasoningMemoryCausalDomain)
  const causalEvents = causalDomain.table('causal_events')

  const writeCausalEvents = async (events: CausalEvent[]): Promise<void> => {
    for (const event of events) await causalEvents.put(event.eventId, event)
    // FIFO guard per unit: drop the oldest events past the cap.
    for (const unitId of new Set(events.map(event => event.memoryId))) {
      const unitEvents = [...causalEvents.entries()]
        .map(([, event]) => event)
        .filter(event => event.memoryId === unitId)
        .sort((left, right) => left.createdAt - right.createdAt || left.eventId.localeCompare(right.eventId))
      const overflow = unitEvents.length - MAX_CAUSAL_EVENTS_PER_UNIT
      for (const event of unitEvents.slice(0, overflow)) {
        await causalEvents.delete(event.eventId)
      }
    }
  }

  // v0.7.0 quantum collapse audit: its own domain, written once per collapse
  // point (rank_cutoff when the selected candidates exceed the superposition
  // cap). The coherent state itself is emitted as the additive superposition[]
  // field; this log records the collapse metadata for auditability.
  const quantumDomain = await ctx.storageDomain.open(reasoningMemoryQuantumDomain)
  const quantumEvents = quantumDomain.table('quantum_events')
  const MAX_QUANTUM_EVENTS = 2000
  const writeQuantumEvent = async (event: QuantumEvent): Promise<void> => {
    await quantumEvents.put(event.eventId, event)
    const overflow = quantumEvents.size - MAX_QUANTUM_EVENTS
    if (overflow > 0) {
      const oldest = [...quantumEvents.entries()]
        .map(([, record]) => record)
        .sort((left, right) => left.createdAt - right.createdAt || left.eventId.localeCompare(right.eventId))
        .slice(0, overflow)
      for (const record of oldest) await quantumEvents.delete(record.eventId)
    }
  }

  // v0.8.0 causal distillation event log: its own domain, holding the
  // do(distill)/do(not-distill) observational events and paired restored-state
  // rollout arms. The uplift estimator runs in the Python core; this layer
  // records and forwards.
  const distillCausalDomain = await ctx.storageDomain.open(reasoningMemoryDistillCausalDomain)
  const distillCausalEvents = distillCausalDomain.table('distill_causal_events')
  const MAX_DISTILL_EVENTS_PER_UNIT = 200
  const writeDistillCausalEvents = async (events: DistillCausalEvent[]): Promise<void> => {
    for (const event of events) await distillCausalEvents.put(event.eventId, event)
    for (const unitId of new Set(events.map(event => event.memoryId))) {
      const unitEvents = [...distillCausalEvents.entries()]
        .map(([, event]) => event)
        .filter(event => event.memoryId === unitId)
        .sort((left, right) => left.createdAt - right.createdAt || left.eventId.localeCompare(right.eventId))
      const overflow = unitEvents.length - MAX_DISTILL_EVENTS_PER_UNIT
      for (const event of unitEvents.slice(0, overflow)) {
        await distillCausalEvents.delete(event.eventId)
      }
    }
  }

  // Idempotent seeding of the five builtin templates (final spec 4.4). The
  // records come from the Python core so the step/module-demand derivation
  // stays single-sourced. A failed attempt is NOT cached: the promise is
  // reset so the next plan/list/load call retries the seeding, keeping a
  // transient python failure from emptying the template layer until restart.
  let templatesSeeded: Promise<void> | undefined
  const seedTemplates = async (): Promise<void> => {
    try {
      const result = await runReasoningCore<{ templates: StrategyTemplate[] }>(
        config.pythonExecutable!, { operation: 'builtin_templates' }, new AbortController().signal,
      )
      for (const record of result.templates) {
        if (templates.get(record.templateId)) continue
        await templates.put(record.templateId, strategyTemplateSchema.parse(record))
      }
    } catch (error) {
      templatesSeeded = undefined
      ctx.logger.warn(`reasoning-memory: builtin template seeding failed (will retry on the next template call): ${error instanceof Error ? error.message : String(error)}`)
    }
  }
  const ensureTemplatesSeeded = (): Promise<void> => {
    templatesSeeded ??= seedTemplates()
    return templatesSeeded
  }
  // The first seeding call is made by the runtime right after the Python
  // version gate, preserving the original startup order (open -> gate -> seed).

  // Canonical-store scan used when the built-in store is disabled, degraded,
  // or still initializing. Applies the exact same typed filter semantics as
  // BuiltinMemoryStore.queryStructured so both sources answer identically.
  const scanMemories = (filter: StructuredQueryFilter): StructuredMemoryRow[] => {
    const limit = Math.max(1, Math.min(100, filter.limit || 20))
    const offset = Math.max(0, filter.offset || 0)
    const sortKey = filter.sortBy === 'confidence' ? 'confidence'
      : filter.sortBy === 'updated_at' ? 'updatedAt'
      : 'createdAt'
    const direction = filter.sortOrder === 'asc' ? 1 : -1
    const rows = [...memories.entries()]
      .map(([, item]) => item)
      .filter(item =>
        (!filter.outcome || item.outcome === filter.outcome)
        && (!filter.type || item.type === filter.type)
        && (!filter.trustLevel || item.trustLevel === filter.trustLevel)
        && (!filter.taskScope || (item.taskScope || '').includes(filter.taskScope))
        && (!filter.status || (item.status ?? 'active') === filter.status)
        && (!filter.strategyTemplateId || (item.strategyTemplateId ?? '') === filter.strategyTemplateId)
        && (!filter.reasoningLevel || (item.reasoningLevel ?? 'path') === filter.reasoningLevel))
      .sort((left, right) => {
        const difference = (Number(left[sortKey] ?? 0) - Number(right[sortKey] ?? 0)) * direction
        return difference !== 0 ? difference : left.memoryId.localeCompare(right.memoryId)
      })
      .slice(offset, offset + limit)
    return rows.map(item => ({
      memoryId: item.memoryId,
      type: item.type,
      outcome: item.outcome,
      taskScope: item.taskScope,
      preconditions: item.preconditions ?? [],
      content: item.content,
      reasoning: item.reasoning ?? '',
      title: item.title ?? '',
      description: item.description ?? '',
      contraindications: item.contraindications ?? [],
      reasoningGraph: item.reasoningGraph ?? { version: 1, nodes: [], edges: [] },
      feedback: item.feedback ?? { helpful: 0, harmful: 0, neutral: 0 },
      provenance: item.provenance,
      evidence: item.evidence,
      confidence: item.confidence,
      trustLevel: item.trustLevel,
      extractorVersion: item.extractorVersion,
      createdAt: item.createdAt,
      updatedAt: item.updatedAt,
      semanticText: item.semanticText,
      structureSignature: item.structureSignature,
      extractionMethod: item.extractionMethod ?? 'caller_insight',
      distillationModel: item.distillationModel ?? '',
      sourceMemoryIds: item.sourceMemoryIds ?? [],
      strategyTemplateId: item.strategyTemplateId ?? '',
      reasoningLevel: item.reasoningLevel ?? 'path',
      insightConfidence: item.insightConfidence ?? 0,
      status: item.status ?? 'active',
    }))
  }

  return {
    memories,
    retrievals,
    templates,
    skills,
    documents,
    documentChunks,
    causalEvents,
    quantumEvents,
    distillCausalEvents,
    lexicalIndex,
    documentLexicalIndex,
    writeCausalEvents,
    writeQuantumEvent,
    writeDistillCausalEvents,
    scanMemories,
    ensureTemplatesSeeded,
    close: () => {
      void domain.close()
      void skillsDomain.close()
      void documentsDomain.close()
      void causalDomain.close()
      void quantumDomain.close()
      void distillCausalDomain.close()
    },
  }
}

export type StorageLayer = Awaited<ReturnType<typeof createStorageLayer>>
