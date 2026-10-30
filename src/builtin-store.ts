import { createHash } from 'node:crypto'
import { PGlite } from '@electric-sql/pglite'
import { vector } from '@electric-sql/pglite/vector'
import type { MemoryItem } from './domain.js'
import { memorySearchText, querySearchText } from './search-text.js'
import { EmbeddingClient } from './embedding-client.js'

// Built-in, in-process PostgreSQL (PGlite) store with the pgvector extension.
// It keeps the full structured JSON memory rows plus a dense embedding per row
// and an HNSW cosine index. It is a DERIVED store: the DSH storageDomain
// remains the canonical fact source, and this store re-syncs from it by dual
// content hashes on startup, mirroring the Qdrant adapter's contract.
//
// Two hashes per row:
//  - contentHash: embedding input (search text + embedding config) — drives
//    re-embedding only when the vector-relevant text changes;
//  - rowHash: the full structured JSON row — drives row refresh (feedback,
//    trust, metadata) without re-embedding.
//
// dataDir === undefined runs fully in memory (rebuilt from storageDomain on
// every boot); a configured dataDir persists the database files on disk.

export interface BuiltinMemoryStoreOptions {
  dataDir?: string
  embeddingUrl: string
  embeddingModel: string
  embeddingApiKey?: string
  embeddingDimension: number
  candidatePoolSize: number
}

export interface BuiltinVectorCandidate {
  memoryId: string
  outcome: 'success' | 'fail'
  vectorScore: number
}

// Lifecycle states surfaced to the retrieval layer:
//  - disabled: the profile keeps the store off;
//  - initializing: schema/sync still running (BM25 covers the gap);
//  - ready: rows + embeddings available for recall;
//  - json-only: rows available, embeddings unavailable (no embedder);
//  - degraded: init/sync/write failed; the caller falls back to BM25.
export type BuiltinStoreStatus = 'disabled' | 'initializing' | 'ready' | 'json-only' | 'degraded'

export interface StructuredMemoryRow {
  memoryId: string
  type: string
  outcome: string
  taskScope: string
  preconditions: string[]
  content: string
  reasoning: string
  title: string
  description: string
  contraindications: string[]
  reasoningGraph: unknown
  feedback: { helpful: number; harmful: number; neutral: number }
  provenance: { taskId: string; sourceSessionId: string; eventSeqs: number[] }
  evidence: { verifier: string; summary: string; independentlyVerified: boolean }
  confidence: number
  trustLevel: string
  extractorVersion: string
  createdAt: number
  updatedAt: number
  semanticText: string
  structureSignature: string[]
  extractionMethod: string
  distillationModel: string
  sourceMemoryIds: string[]
}

export interface StructuredQueryFilter {
  outcome?: 'success' | 'fail'
  type?: 'strategy' | 'structure' | 'failure'
  trustLevel?: 'verified' | 'unverified' | 'unknown'
  taskScope?: string
  limit: number
  offset: number
  sortBy: 'created_at' | 'updated_at' | 'confidence'
  sortOrder: 'asc' | 'desc'
}

export interface SyncResult {
  synced: number
  embedded: number
  staleRemoved: number
  hasEmbeddings: boolean
}

const SORT_COLUMNS = {
  created_at: 'created_at',
  updated_at: 'updated_at',
  confidence: 'confidence',
} as const

const ROW_SELECT = `
  memory_id, type, outcome, task_scope, preconditions, content, reasoning,
  title, description, contraindications, reasoning_graph, feedback, provenance,
  evidence, confidence, trust_level, extractor_version, created_at, updated_at,
  semantic_text, structure_signature, extraction_method, distillation_model,
  source_memory_ids`

export class BuiltinMemoryStore {
  readonly embedderConfigured: boolean
  private db: PGlite | undefined
  private embedder: EmbeddingClient | undefined
  private schemaReady: Promise<void> | undefined
  private embeddingsDisabled = false
  private closed = false
  // All database operations flow through one queue so concurrent callers
  // (startup sync, write-through, retrieval, queries, close) serialize
  // deterministically and close always runs after every in-flight op.
  private operationQueue: Promise<unknown> = Promise.resolve()

  private enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.operationQueue.then(operation, operation)
    this.operationQueue = result.catch(() => {})
    return result
  }

  constructor(
    private readonly options: BuiltinMemoryStoreOptions,
    fetcher: typeof fetch = fetch,
  ) {
    if (!Number.isInteger(options.embeddingDimension) || options.embeddingDimension < 1) {
      throw new Error('built-in store embedding dimension must be a positive integer')
    }
    if (!Number.isInteger(options.candidatePoolSize) || options.candidatePoolSize < 1) {
      throw new Error('built-in store candidate pool size must be a positive integer')
    }
    this.embedderConfigured = Boolean(options.embeddingUrl.trim())
    if (this.embedderConfigured) {
      this.embedder = new EmbeddingClient({
        embeddingUrl: options.embeddingUrl,
        embeddingModel: options.embeddingModel,
        embeddingApiKey: options.embeddingApiKey,
        embeddingDimension: options.embeddingDimension,
      }, fetcher)
    }
  }

  private open(): PGlite {
    if (this.closed) throw new Error('the built-in store is closed')
    if (this.db) return this.db
    this.db = this.options.dataDir
      ? new PGlite(this.options.dataDir, { extensions: { vector } })
      : new PGlite({ extensions: { vector } })
    return this.db
  }

  private ensureSchema(): Promise<void> {
    if (this.schemaReady) return this.schemaReady
    this.schemaReady = (async () => {
      const db = this.open()
      await db.exec('CREATE EXTENSION IF NOT EXISTS vector')
      await db.exec('CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
      const meta = await db.query<{ value: string }>(
        "SELECT value FROM store_meta WHERE key = 'embedding_dimension'",
      )
      const storedDimension = meta.rows[0]?.value
      if (storedDimension !== undefined && Number(storedDimension) !== this.options.embeddingDimension) {
        // A derived store: a dimension/embedding-config change rebuilds the
        // table instead of migrating in place.
        await db.exec('DROP TABLE IF EXISTS memories')
      }
      const dimension = this.options.embeddingDimension
      await db.exec(`
        CREATE TABLE IF NOT EXISTS memories (
          memory_id TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          outcome TEXT NOT NULL,
          task_scope TEXT NOT NULL,
          preconditions JSONB NOT NULL,
          content TEXT NOT NULL,
          reasoning TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT NOT NULL,
          contraindications JSONB NOT NULL,
          reasoning_graph JSONB NOT NULL,
          feedback JSONB NOT NULL,
          provenance JSONB NOT NULL,
          evidence JSONB NOT NULL,
          confidence REAL NOT NULL,
          trust_level TEXT NOT NULL,
          extractor_version TEXT NOT NULL,
          created_at BIGINT NOT NULL,
          updated_at BIGINT NOT NULL,
          semantic_text TEXT NOT NULL,
          structure_signature JSONB NOT NULL,
          extraction_method TEXT NOT NULL,
          distillation_model TEXT NOT NULL,
          source_memory_ids JSONB NOT NULL,
          content_hash TEXT NOT NULL,
          row_hash TEXT NOT NULL,
          embedding vector(${dimension})
        )`)
      await db.exec('CREATE INDEX IF NOT EXISTS memories_outcome_idx ON memories (outcome)')
      await db.exec('CREATE INDEX IF NOT EXISTS memories_type_idx ON memories (type)')
      await db.exec('CREATE INDEX IF NOT EXISTS memories_trust_level_idx ON memories (trust_level)')
      await db.exec('CREATE INDEX IF NOT EXISTS memories_task_scope_idx ON memories (task_scope)')
      await db.exec('CREATE INDEX IF NOT EXISTS memories_embedding_idx ON memories USING hnsw (embedding vector_cosine_ops)')
      await db.query(
        "INSERT INTO store_meta (key, value) VALUES ('embedding_dimension', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        [String(this.options.embeddingDimension)],
      )
    })()
    return this.schemaReady
  }

  private contentHashOf(item: MemoryItem): string {
    return createHash('sha256').update(JSON.stringify({
      embeddingUrl: this.options.embeddingUrl,
      embeddingModel: this.options.embeddingModel,
      embeddingDimension: this.options.embeddingDimension,
      text: memorySearchText(item),
    })).digest('hex')
  }

  private rowHashOf(item: MemoryItem): string {
    const row = {
      memoryId: item.memoryId,
      type: item.type,
      outcome: item.outcome,
      taskScope: item.taskScope,
      preconditions: item.preconditions,
      content: item.content,
      reasoning: item.reasoning,
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
    }
    return createHash('sha256').update(JSON.stringify(row)).digest('hex')
  }

  private rowParams(item: MemoryItem): unknown[] {
    return [
      item.memoryId,
      item.type,
      item.outcome,
      item.taskScope,
      JSON.stringify(item.preconditions ?? []),
      item.content,
      item.reasoning,
      item.title ?? '',
      item.description ?? '',
      JSON.stringify(item.contraindications ?? []),
      JSON.stringify(item.reasoningGraph ?? { version: 1, nodes: [], edges: [] }),
      JSON.stringify(item.feedback ?? { helpful: 0, harmful: 0, neutral: 0 }),
      JSON.stringify(item.provenance),
      JSON.stringify(item.evidence),
      item.confidence,
      item.trustLevel,
      item.extractorVersion,
      item.createdAt,
      item.updatedAt,
      item.semanticText,
      JSON.stringify(item.structureSignature ?? []),
      item.extractionMethod ?? 'caller_insight',
      item.distillationModel ?? '',
      JSON.stringify(item.sourceMemoryIds ?? []),
      this.contentHashOf(item),
      this.rowHashOf(item),
    ]
  }

  private static readonly UPSERT_SQL = `
    INSERT INTO memories (
      memory_id, type, outcome, task_scope, preconditions, content, reasoning,
      title, description, contraindications, reasoning_graph, feedback,
      provenance, evidence, confidence, trust_level, extractor_version,
      created_at, updated_at, semantic_text, structure_signature,
      extraction_method, distillation_model, source_memory_ids,
      content_hash, row_hash
    ) VALUES (
      $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
      $17, $18, $19, $20, $21, $22, $23, $24, $25, $26
    ) ON CONFLICT (memory_id) DO UPDATE SET
      type = EXCLUDED.type,
      outcome = EXCLUDED.outcome,
      task_scope = EXCLUDED.task_scope,
      preconditions = EXCLUDED.preconditions,
      content = EXCLUDED.content,
      reasoning = EXCLUDED.reasoning,
      title = EXCLUDED.title,
      description = EXCLUDED.description,
      contraindications = EXCLUDED.contraindications,
      reasoning_graph = EXCLUDED.reasoning_graph,
      feedback = EXCLUDED.feedback,
      provenance = EXCLUDED.provenance,
      evidence = EXCLUDED.evidence,
      confidence = EXCLUDED.confidence,
      trust_level = EXCLUDED.trust_level,
      extractor_version = EXCLUDED.extractor_version,
      created_at = EXCLUDED.created_at,
      updated_at = EXCLUDED.updated_at,
      semantic_text = EXCLUDED.semantic_text,
      structure_signature = EXCLUDED.structure_signature,
      extraction_method = EXCLUDED.extraction_method,
      distillation_model = EXCLUDED.distillation_model,
      source_memory_ids = EXCLUDED.source_memory_ids,
      content_hash = EXCLUDED.content_hash,
      row_hash = EXCLUDED.row_hash`

  // Embedding is an enhancement of the derived store, never a precondition:
  // when the endpoint is unreachable, rows still commit and the store degrades
  // to json-only instead of failing the whole sync.
  private async embedOrNull(texts: string[]): Promise<number[][] | null> {
    if (!this.embedder || this.embeddingsDisabled) return null
    try {
      return await this.embedder.embed(texts)
    } catch {
      this.embeddingsDisabled = true
      return null
    }
  }

  // Reconcile the store with the canonical storageDomain snapshot.
  synchronize(memories: MemoryItem[]): Promise<SyncResult> {
    return this.enqueue(() => this.synchronizeInner(memories))
  }

  private async synchronizeInner(memories: MemoryItem[]): Promise<SyncResult> {
    await this.ensureSchema()
    const db = this.open()
    const existing = new Map<string, { contentHash: string; rowHash: string }>()
    const rows = await db.query<{ memory_id: string; content_hash: string; row_hash: string }>(
      'SELECT memory_id, content_hash, row_hash FROM memories',
    )
    for (const row of rows.rows) existing.set(row.memory_id, { contentHash: row.content_hash, rowHash: row.row_hash })
    const desired = new Map(memories.map(item => [item.memoryId, item]))
    const stale = [...existing.keys()].filter(memoryId => !desired.has(memoryId))
    for (const memoryId of stale) await db.query('DELETE FROM memories WHERE memory_id = $1', [memoryId])

    let synced = 0
    const toEmbed: MemoryItem[] = []
    for (const item of memories) {
      const contentHash = this.contentHashOf(item)
      const rowHash = this.rowHashOf(item)
      const previous = existing.get(item.memoryId)
      const rowChanged = !previous || previous.rowHash !== rowHash
      const contentChanged = !previous || previous.contentHash !== contentHash
      if (!rowChanged && !contentChanged) continue
      await db.query(BuiltinMemoryStore.UPSERT_SQL, this.rowParams(item))
      synced++
      if (contentChanged && this.embedder) toEmbed.push(item)
    }
    let embedded = 0
    if (this.embedder && toEmbed.length > 0) {
      const texts = toEmbed.map(memorySearchText)
      const vectors = await this.embedOrNull(texts)
      if (vectors) {
        for (let index = 0; index < toEmbed.length; index++) {
          await db.query(
            'UPDATE memories SET embedding = $2 WHERE memory_id = $1',
            [toEmbed[index].memoryId, JSON.stringify(vectors[index])],
          )
          embedded++
        }
      }
    }
    return {
      synced,
      embedded,
      staleRemoved: stale.length,
      hasEmbeddings: Boolean(this.embedder) && !this.embeddingsDisabled,
    }
  }

  // Write-through for a single accepted/updated memory item.
  upsert(item: MemoryItem): Promise<void> {
    return this.enqueue(() => this.upsertInner(item))
  }

  private async upsertInner(item: MemoryItem): Promise<void> {
    await this.ensureSchema()
    const db = this.open()
    const previous = await db.query<{ content_hash: string; row_hash: string }>(
      'SELECT content_hash, row_hash FROM memories WHERE memory_id = $1',
      [item.memoryId],
    )
    const contentHash = this.contentHashOf(item)
    const rowHash = this.rowHashOf(item)
    const prev = previous.rows[0]
    const contentChanged = !prev || prev.content_hash !== contentHash
    await db.query(BuiltinMemoryStore.UPSERT_SQL, this.rowParams(item))
    if (contentChanged) {
      const [embedding] = await this.embedOrNull([memorySearchText(item)]) ?? []
      if (embedding) {
        await db.query(
          'UPDATE memories SET embedding = $2 WHERE memory_id = $1',
          [item.memoryId, JSON.stringify(embedding)],
        )
      }
    }
  }

  delete(memoryIds: string[]): Promise<void> {
    return this.enqueue(() => this.deleteInner(memoryIds))
  }

  private async deleteInner(memoryIds: string[]): Promise<void> {
    if (memoryIds.length === 0) return
    await this.ensureSchema()
    const db = this.open()
    for (const memoryId of memoryIds) {
      await db.query('DELETE FROM memories WHERE memory_id = $1', [memoryId])
    }
  }

  // Dense retrieval: per-outcome nearest neighbors via pgvector, excluding the
  // active task. Mirrors QdrantMemoryIndex.search's contract.
  search(query: string, reasoningGraphJson: string, excludeTaskId: string): Promise<BuiltinVectorCandidate[]> {
    return this.enqueue(() => this.searchInner(query, reasoningGraphJson, excludeTaskId))
  }

  private async searchInner(query: string, reasoningGraphJson: string, excludeTaskId: string): Promise<BuiltinVectorCandidate[]> {
    if (!this.embedder || this.embeddingsDisabled) return []
    await this.ensureSchema()
    const db = this.open()
    const [queryVector] = await this.embedOrNull([querySearchText(query, reasoningGraphJson)]) ?? []
    if (!queryVector) return []
    const literal = JSON.stringify(queryVector)
    const outcomes: Array<'success' | 'fail'> = ['success', 'fail']
    const batches = await Promise.all(outcomes.map(async outcome => {
      const result = await db.query<{ memory_id: string; distance: number }>(`
        SELECT memory_id, embedding <=> $4::vector AS distance
        FROM memories
        WHERE outcome = $1
          AND embedding IS NOT NULL
          AND ($2::text = '' OR provenance->>'taskId' <> $2)
        ORDER BY distance
        LIMIT $3::int`,
        [outcome, excludeTaskId, this.options.candidatePoolSize, literal],
      )
      return result.rows.flatMap(row => {
        const distance = Number(row.distance)
        if (!row.memory_id || !Number.isFinite(distance)) return []
        return [{
          memoryId: row.memory_id,
          outcome,
          vectorScore: Math.max(0, Math.min(1, 1 - distance)),
        }]
      })
    }))
    return batches.flat()
  }

  // Safe structured JSON query: whitelisted enums, parameterized values, and
  // substring matching via position() (no LIKE escaping surface). No raw SQL
  // ever reaches the database from callers.
  queryStructured(filter: StructuredQueryFilter): Promise<StructuredMemoryRow[]> {
    return this.enqueue(() => this.queryStructuredInner(filter))
  }

  private async queryStructuredInner(filter: StructuredQueryFilter): Promise<StructuredMemoryRow[]> {
    await this.ensureSchema()
    const db = this.open()
    const conditions: string[] = []
    const params: unknown[] = []
    const push = (sql: string, value: unknown) => {
      params.push(value)
      conditions.push(sql.replace('$?', `$${params.length}`))
    }
    if (filter.outcome) push('outcome = $?', filter.outcome)
    if (filter.type) push('type = $?', filter.type)
    if (filter.trustLevel) push('trust_level = $?', filter.trustLevel)
    if (filter.taskScope) push("position($? in task_scope) > 0", filter.taskScope)
    const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : ''
    const sortColumn = SORT_COLUMNS[filter.sortBy] ?? 'created_at'
    const sortOrder = filter.sortOrder === 'asc' ? 'ASC' : 'DESC'
    const limit = Math.max(1, Math.min(100, Math.floor(filter.limit) || 20))
    const offset = Math.max(0, Math.floor(filter.offset) || 0)
    params.push(limit, offset)
    const sql = `
      SELECT ${ROW_SELECT}
      FROM memories
      ${where}
      ORDER BY ${sortColumn} ${sortOrder}, memory_id ASC
      LIMIT $${params.length - 1}::int OFFSET $${params.length}::int`
    const result = await db.query<Record<string, unknown>>(sql, params)
    return result.rows.map(row => this.toStructuredRow(row))
  }

  private toStructuredRow(row: Record<string, unknown>): StructuredMemoryRow {
    return {
      memoryId: String(row.memory_id ?? ''),
      type: String(row.type ?? ''),
      outcome: String(row.outcome ?? ''),
      taskScope: String(row.task_scope ?? ''),
      preconditions: this.parseJsonArray(row.preconditions),
      content: String(row.content ?? ''),
      reasoning: String(row.reasoning ?? ''),
      title: String(row.title ?? ''),
      description: String(row.description ?? ''),
      contraindications: this.parseJsonArray(row.contraindications),
      reasoningGraph: this.parseJson(row.reasoning_graph),
      feedback: this.parseJson(row.feedback),
      provenance: this.parseJson(row.provenance),
      evidence: this.parseJson(row.evidence),
      confidence: Number(row.confidence ?? 0),
      trustLevel: String(row.trust_level ?? ''),
      extractorVersion: String(row.extractor_version ?? ''),
      createdAt: Number(row.created_at ?? 0),
      updatedAt: Number(row.updated_at ?? 0),
      semanticText: String(row.semantic_text ?? ''),
      structureSignature: this.parseJsonArray(row.structure_signature),
      extractionMethod: String(row.extraction_method ?? ''),
      distillationModel: String(row.distillation_model ?? ''),
      sourceMemoryIds: this.parseJsonArray(row.source_memory_ids),
    }
  }

  private parseJson(value: unknown): any {
    if (typeof value !== 'string' && !Array.isArray(value) && !(typeof value === 'object' && value !== null)) return null
    if (typeof value === 'string') {
      try {
        return JSON.parse(value)
      } catch {
        return null
      }
    }
    return value
  }

  private parseJsonArray(value: unknown): string[] {
    const parsed = this.parseJson(value)
    return Array.isArray(parsed) ? parsed.filter((entry): entry is string => typeof entry === 'string') : []
  }

  async close(): Promise<void> {
    // Serialized after every in-flight operation; a closed store rejects
    // further database use cleanly instead of racing the shutdown or
    // silently resurrecting an in-memory database.
    await this.enqueue(async () => {
      if (this.closed) return
      this.closed = true
      if (this.db) {
        await this.db.close()
        this.db = undefined
      }
      this.schemaReady = undefined
    })
  }
}
