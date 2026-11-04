import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { z } from 'zod'

// Reasoning-aware document retrieval lives in its own storage domain so the
// reasoning_memory domain (v1) and the skills domain stay untouched: user
// document corpora can grow to hundreds of chunks per document and deserve an
// isolated memory/rollback surface, mirroring the skill-domain precedent.
// Additive law: once data exists, new fields must carry zod defaults.
export const taskPatternEnum = z.enum([
  'multi_hop_qa',
  'code_synthesis',
  'tool_orchestration',
  'gui_operation',
  'long_horizon_planning',
  'numeric_search',
  'general',
])

export const documentSchema = z.object({
  docId: z.string(),
  name: z.string(),
  source: z.string().default(''),
  version: z.string().default('1'),
  taskPatterns: z.array(taskPatternEnum).default(['general']),
  chunkCount: z.number().int().nonnegative(),
  charCount: z.number().int().nonnegative(),
  importedAt: z.number().int().nonnegative(),
  updatedAt: z.number().int().nonnegative(),
})

export const documentChunkSchema = z.object({
  chunkId: z.string(),
  docId: z.string(),
  seq: z.number().int().positive(),
  headingPath: z.array(z.string()),
  text: z.string(),
  prevChunkId: z.string().default(''),
  nextChunkId: z.string().default(''),
  parentChunkId: z.string().default(''),
  refs: z.array(z.string()).default([]),
})

export type DocumentRecord = z.infer<typeof documentSchema>
export type DocumentChunk = z.infer<typeof documentChunkSchema>

export const reasoningMemoryDocumentsDomain = defineDomain({
  name: 'reasoning_memory_documents',
  version: 1,
  layout: 'per-record',
  tables: {
    documents: domainTable<string, DocumentRecord>(documentSchema),
    chunks: domainTable<string, DocumentChunk>(documentChunkSchema),
  },
})
