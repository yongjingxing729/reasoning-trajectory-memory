import { createHash } from 'node:crypto'
import type { MemoryItem } from './domain.js'
import { memorySearchText, querySearchText } from './search-text.js'
import { EmbeddingClient } from './embedding-client.js'

export interface QdrantMemoryIndexOptions {
  qdrantUrl: string
  collection: string
  qdrantApiKey?: string
  embeddingUrl: string
  embeddingModel: string
  embeddingApiKey?: string
  embeddingDimension: number
  candidatePoolSize: number
}

export interface VectorCandidate {
  memoryId: string
  outcome: 'success' | 'fail'
  vectorScore: number
}

type Outcome = VectorCandidate['outcome']
type QdrantPoint = {
  id: string | number
  score?: number
  payload?: Record<string, unknown>
  vector?: number[]
}

class HttpStatusError extends Error {
  constructor(readonly status: number) {
    super(`HTTP ${status}`)
  }
}

export class QdrantMemoryIndex {
  private readonly baseUrl: string
  private readonly collectionPath: string
  private readonly fetcher: typeof fetch
  private readonly embedder: EmbeddingClient

  constructor(
    private readonly options: QdrantMemoryIndexOptions,
    fetcher: typeof fetch = fetch,
  ) {
    this.baseUrl = options.qdrantUrl.replace(/\/+$/, '')
    this.collectionPath = `/collections/${encodeURIComponent(options.collection)}`
    this.fetcher = fetcher
    this.embedder = new EmbeddingClient({
      embeddingUrl: options.embeddingUrl,
      embeddingModel: options.embeddingModel,
      embeddingApiKey: options.embeddingApiKey,
      embeddingDimension: options.embeddingDimension,
    }, fetcher)
  }

  async synchronize(memories: MemoryItem[]): Promise<void> {
    await this.ensureCollection()
    const existing = await this.scrollAllPoints()
    const desired = new Map(memories.map(item => [item.memoryId, this.contentHash(item)]))
    const pending = memories.filter(item => existing.get(item.memoryId) !== desired.get(item.memoryId))
    const stale = [...existing.keys()].filter(memoryId => !desired.has(memoryId))

    for (let offset = 0; offset < pending.length; offset += 32) {
      await this.upsertBatch(pending.slice(offset, offset + 32))
    }
    for (let offset = 0; offset < stale.length; offset += 256) {
      await this.delete(stale.slice(offset, offset + 256))
    }
  }

  async upsert(memory: MemoryItem): Promise<void> {
    await this.ensureCollection()
    await this.upsertBatch([memory])
  }

  async delete(memoryIds: string[]): Promise<void> {
    if (memoryIds.length === 0) return
    await this.request(`${this.collectionPath}/points/delete?wait=true`, {
      method: 'POST',
      body: JSON.stringify({ points: memoryIds }),
    })
  }

  async search(query: string, reasoningGraphJson: string, excludeTaskId: string): Promise<VectorCandidate[]> {
    const [queryVector] = await this.embedder.embed([querySearchText(query, reasoningGraphJson)])
    const outcomes: Outcome[] = ['success', 'fail']
    const batches = await Promise.all(outcomes.map(async outcome => {
      const filter: Record<string, unknown> = {
        must: [{ key: 'outcome', match: { value: outcome } }],
      }
      if (excludeTaskId) {
        filter.must_not = [{ key: 'taskId', match: { value: excludeTaskId } }]
      }
      const response = await this.request(`${this.collectionPath}/points/query`, {
        method: 'POST',
        body: JSON.stringify({
          query: queryVector,
          limit: this.options.candidatePoolSize,
          filter,
          with_payload: ['memoryId', 'outcome'],
          with_vector: false,
        }),
      }) as { result?: { points?: QdrantPoint[] } }
      return (response.result?.points ?? []).flatMap(point => {
        const memoryId = typeof point.payload?.memoryId === 'string'
          ? point.payload.memoryId
          : String(point.id)
        const score = Number(point.score)
        if (!memoryId || !Number.isFinite(score)) return []
        return [{
          memoryId,
          outcome,
          vectorScore: Math.max(0, Math.min(1, score)),
        }]
      })
    }))
    return batches.flat()
  }

  private contentHash(item: MemoryItem): string {
    return createHash('sha256').update(JSON.stringify({
      embeddingUrl: this.options.embeddingUrl,
      embeddingModel: this.options.embeddingModel,
      embeddingDimension: this.options.embeddingDimension,
      text: memorySearchText(item),
    })).digest('hex')
  }

  private async ensureCollection(): Promise<void> {
    const path = this.collectionPath
    try {
      const response = await this.request(path, { method: 'GET' }) as {
        result?: { config?: { params?: { vectors?: { size?: number } | Record<string, { size?: number }> } } }
      }
      const vectors = response.result?.config?.params?.vectors
      const size = vectors && 'size' in vectors
        ? vectors.size
        : undefined
      if (size !== this.options.embeddingDimension) {
        throw new Error(`Qdrant collection vector size does not match configured dimension ${this.options.embeddingDimension}`)
      }
    } catch (error) {
      if (!(error instanceof HttpStatusError) || error.status !== 404) throw error
      try {
        await this.request(path, {
          method: 'PUT',
          body: JSON.stringify({
            vectors: { size: this.options.embeddingDimension, distance: 'Cosine' },
          }),
        })
      } catch (createError) {
        if (!(createError instanceof HttpStatusError) || createError.status !== 409) throw createError
      }
    }
    for (const field of ['outcome', 'taskId']) {
      try {
        await this.request(`${this.collectionPath}/index?wait=true`, {
          method: 'PUT',
          body: JSON.stringify({ field_name: field, field_schema: 'keyword' }),
        })
      } catch (error) {
        if (!(error instanceof HttpStatusError) || error.status !== 409) throw error
      }
    }
  }

  private async scrollAllPoints(): Promise<Map<string, string>> {
    const points = new Map<string, string>()
    let offset: string | number | undefined
    while (true) {
      const response = await this.request(`${this.collectionPath}/points/scroll`, {
        method: 'POST',
        body: JSON.stringify({
          limit: 256,
          ...(offset !== undefined ? { offset } : {}),
          with_payload: ['contentHash', 'memoryId'],
          with_vector: false,
        }),
      }) as { result?: { points?: QdrantPoint[]; next_page_offset?: string | number | null } }
      for (const point of response.result?.points ?? []) {
        const memoryId = typeof point.payload?.memoryId === 'string' ? point.payload.memoryId : String(point.id)
        const hash = typeof point.payload?.contentHash === 'string' ? point.payload.contentHash : ''
        points.set(memoryId, hash)
      }
      const next = response.result?.next_page_offset
      if (next === undefined || next === null || next === offset) break
      offset = next
    }
    return points
  }

  private async upsertBatch(memories: MemoryItem[]): Promise<void> {
    if (memories.length === 0) return
    const vectors = await this.embedder.embed(memories.map(memorySearchText))
    const points = memories.map((item, index) => ({
      id: item.memoryId,
      vector: vectors[index],
      payload: {
        memoryId: item.memoryId,
        outcome: item.outcome,
        taskId: item.provenance.taskId,
        contentHash: this.contentHash(item),
      },
    }))
    await this.request(`${this.collectionPath}/points?wait=true`, {
      method: 'PUT',
      body: JSON.stringify({ points }),
    })
  }

  private async request(path: string, init: RequestInit): Promise<unknown> {
    const headers: Record<string, string> = { 'content-type': 'application/json' }
    if (this.options.qdrantApiKey) headers['api-key'] = this.options.qdrantApiKey
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      ...init,
      headers: { ...headers, ...(init.headers as Record<string, string> | undefined) },
      signal: init.signal ?? AbortSignal.timeout(15_000),
    })
    if (!response.ok) throw new HttpStatusError(response.status)
    if (response.status === 204) return {}
    return response.json()
  }
}
