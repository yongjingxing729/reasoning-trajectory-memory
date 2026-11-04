import { createHash } from 'node:crypto'
import type { DocumentChunk } from './document-domain.js'
import { documentChunkSearchText } from './document-index.js'
import { EmbeddingClient } from './embedding-client.js'

// Optional dense-vector index for the document retrieval surface. It runs in
// its OWN Qdrant collection (reasoning_documents by default): memories, the
// enterprise rule collection, and documents never share points. The canonical
// document domain stays the fact source and the index re-syncs by content
// hash, mirroring the memory vector adapter's contract.
export interface QdrantDocumentIndexOptions {
  qdrantUrl: string
  collection: string
  qdrantApiKey?: string
  embeddingUrl: string
  embeddingModel: string
  embeddingApiKey?: string
  embeddingDimension: number
  candidatePoolSize: number
}

export interface DocumentVectorCandidate {
  chunkId: string
  vectorScore: number
}

type QdrantPoint = {
  id: string | number
  score?: number
  payload?: Record<string, unknown>
}

class HttpStatusError extends Error {
  constructor(readonly status: number) {
    super(`HTTP ${status}`)
  }
}

export class QdrantDocumentIndex {
  private readonly baseUrl: string
  private readonly collectionPath: string
  private readonly fetcher: typeof fetch
  private readonly embedder: EmbeddingClient

  constructor(
    private readonly options: QdrantDocumentIndexOptions,
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

  async synchronize(
    entries: Array<{ docName: string; taskPatterns: string[]; chunk: DocumentChunk }>,
  ): Promise<void> {
    await this.ensureCollection()
    const existing = await this.scrollAllPoints()
    const desired = new Map(entries.map(entry => [entry.chunk.chunkId, this.contentHash(entry)]))
    const pending = entries.filter(entry => existing.get(entry.chunk.chunkId) !== desired.get(entry.chunk.chunkId))
    const stale = [...existing.keys()].filter(chunkId => !desired.has(chunkId))
    for (let offset = 0; offset < pending.length; offset += 32) {
      await this.upsertBatch(pending.slice(offset, offset + 32))
    }
    for (let offset = 0; offset < stale.length; offset += 256) {
      await this.delete(stale.slice(offset, offset + 256))
    }
  }

  async upsert(entry: { docName: string; taskPatterns: string[]; chunk: DocumentChunk }): Promise<void> {
    await this.ensureCollection()
    await this.upsertBatch([entry])
  }

  async delete(chunkIds: string[]): Promise<void> {
    if (chunkIds.length === 0) return
    await this.request(`${this.collectionPath}/points/delete?wait=true`, {
      method: 'POST',
      body: JSON.stringify({ points: chunkIds }),
    })
  }

  async search(query: string): Promise<DocumentVectorCandidate[]> {
    const [queryVector] = await this.embedder.embed([query])
    const response = await this.request(`${this.collectionPath}/points/query`, {
      method: 'POST',
      body: JSON.stringify({
        query: queryVector,
        limit: this.options.candidatePoolSize,
        with_payload: ['chunkId'],
        with_vector: false,
      }),
    }) as { result?: { points?: QdrantPoint[] } }
    return (response.result?.points ?? []).flatMap(point => {
      const chunkId = typeof point.payload?.chunkId === 'string'
        ? point.payload.chunkId
        : String(point.id)
      const score = Number(point.score)
      if (!chunkId || !Number.isFinite(score)) return []
      return [{ chunkId, vectorScore: Math.max(0, Math.min(1, score)) }]
    })
  }

  private contentHash(entry: { docName: string; taskPatterns: string[]; chunk: DocumentChunk }): string {
    return createHash('sha256').update(JSON.stringify({
      embeddingUrl: this.options.embeddingUrl,
      embeddingModel: this.options.embeddingModel,
      embeddingDimension: this.options.embeddingDimension,
      text: documentChunkSearchText(entry.docName, entry.taskPatterns, entry.chunk),
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
          with_payload: ['contentHash', 'chunkId'],
          with_vector: false,
        }),
      }) as { result?: { points?: QdrantPoint[]; next_page_offset?: string | number | null } }
      for (const point of response.result?.points ?? []) {
        const chunkId = typeof point.payload?.chunkId === 'string' ? point.payload.chunkId : String(point.id)
        const hash = typeof point.payload?.contentHash === 'string' ? point.payload.contentHash : ''
        points.set(chunkId, hash)
      }
      const next = response.result?.next_page_offset
      if (next === undefined || next === null || next === offset) break
      offset = next
    }
    return points
  }

  private async upsertBatch(entries: Array<{ docName: string; taskPatterns: string[]; chunk: DocumentChunk }>): Promise<void> {
    if (entries.length === 0) return
    const vectors = await this.embedder.embed(entries.map(entry =>
      documentChunkSearchText(entry.docName, entry.taskPatterns, entry.chunk),
    ))
    const points = entries.map((entry, index) => ({
      id: entry.chunk.chunkId,
      vector: vectors[index],
      payload: {
        chunkId: entry.chunk.chunkId,
        contentHash: this.contentHash(entry),
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
