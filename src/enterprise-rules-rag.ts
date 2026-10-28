import { querySearchText } from './search-text.js'

export interface EnterpriseRuleRagOptions {
  qdrantUrl: string
  collection: string
  qdrantApiKey?: string
  embeddingUrl: string
  embeddingModel: string
  embeddingApiKey?: string
  embeddingDimension: number
  ruleSetId: string
  candidatePoolSize: number
  resultLimit: number
  scoreThreshold: number
}

export interface EnterpriseRule {
  ruleId: string
  ruleSetId: string
  title: string
  text: string
  source: string
  version: string
  priority: number
  validFrom: number
  validUntil: number
  score: number
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

function safeText(value: unknown, maxLength: number): string {
  if (typeof value !== 'string') return ''
  return value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').trim().slice(0, maxLength)
}

export class EnterpriseRuleRag {
  private readonly baseUrl: string
  private readonly collectionPath: string
  private readonly fetcher: typeof fetch
  private collectionValidation?: Promise<void>

  constructor(
    private readonly options: EnterpriseRuleRagOptions,
    fetcher: typeof fetch = fetch,
  ) {
    this.baseUrl = options.qdrantUrl.replace(/\/+$/, '')
    this.collectionPath = `/collections/${encodeURIComponent(options.collection)}`
    this.fetcher = fetcher
    if (!Number.isInteger(options.embeddingDimension) || options.embeddingDimension < 1) {
      throw new Error('enterprise rule embedding dimension must be a positive integer')
    }
    if (!Number.isInteger(options.candidatePoolSize) || options.candidatePoolSize < 1
      || !Number.isInteger(options.resultLimit) || options.resultLimit < 1) {
      throw new Error('enterprise rule candidate and result limits must be positive integers')
    }
    if (!Number.isFinite(options.scoreThreshold) || options.scoreThreshold < 0 || options.scoreThreshold > 1) {
      throw new Error('enterprise rule score threshold must be between 0 and 1')
    }
  }

  async search(query: string, reasoningGraphJson = ''): Promise<EnterpriseRule[]> {
    await this.validateCollection()
    const [queryVector] = await this.embed([querySearchText(query, reasoningGraphJson)])
    const now = Date.now()
    const response = await this.request(`${this.collectionPath}/points/query`, {
      method: 'POST',
      body: JSON.stringify({
        query: queryVector,
        limit: this.options.candidatePoolSize,
        filter: {
          must: [
            { key: 'ruleSetId', match: { value: this.options.ruleSetId } },
            { key: 'status', match: { value: 'active' } },
            { key: 'validFrom', range: { lte: now } },
            { key: 'validUntil', range: { gte: now } },
          ],
        },
        with_payload: [
          'ruleId', 'ruleSetId', 'title', 'text', 'source', 'version',
          'priority', 'validFrom', 'validUntil', 'status',
        ],
        with_vector: false,
      }),
    }) as { result?: { points?: QdrantPoint[] } }

    return (response.result?.points ?? []).flatMap(point => {
      const payload = point.payload
      if (!payload || payload.ruleSetId !== this.options.ruleSetId || payload.status !== 'active') return []
      const ruleId = safeText(payload.ruleId, 160)
      const text = safeText(payload.text, 4000)
      const source = safeText(payload.source, 500)
      const validFrom = Number(payload.validFrom)
      const validUntil = Number(payload.validUntil)
      const priority = Number(payload.priority ?? 0)
      const score = Number(point.score)
      if (!ruleId || !text || !source
        || !Number.isFinite(validFrom) || !Number.isFinite(validUntil) || validFrom > now || validUntil < now
        || !Number.isFinite(priority) || !Number.isFinite(score) || score < this.options.scoreThreshold) {
        return []
      }
      return [{
        ruleId,
        ruleSetId: this.options.ruleSetId,
        title: safeText(payload.title, 240) || ruleId,
        text,
        source,
        version: safeText(payload.version, 100) || 'unspecified',
        priority,
        validFrom,
        validUntil,
        score: Math.max(0, Math.min(1, score)),
      }]
    }).sort((left, right) => right.score - left.score || right.priority - left.priority || left.ruleId.localeCompare(right.ruleId))
      .slice(0, this.options.resultLimit)
  }

  private async validateCollection(): Promise<void> {
    const validation = this.collectionValidation ??= (async () => {
      const response = await this.request(this.collectionPath, { method: 'GET' }) as {
        result?: { config?: { params?: { vectors?: { size?: number; distance?: string } | Record<string, unknown> } } }
      }
      const vectors = response.result?.config?.params?.vectors
      if (!vectors || !('size' in vectors) || vectors.size !== this.options.embeddingDimension) {
        throw new Error(`enterprise rule collection vector size does not match configured dimension ${this.options.embeddingDimension}`)
      }
      if ('distance' in vectors && vectors.distance !== 'Cosine') {
        throw new Error('enterprise rule collection must use cosine distance')
      }
    })()
    try {
      await validation
    } catch (error) {
      if (this.collectionValidation === validation) this.collectionValidation = undefined
      throw error
    }
  }

  private async embed(inputs: string[]): Promise<number[][]> {
    const headers: Record<string, string> = { 'content-type': 'application/json' }
    if (this.options.embeddingApiKey) headers.authorization = `Bearer ${this.options.embeddingApiKey}`
    const response = await this.fetcher(this.options.embeddingUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify({ model: this.options.embeddingModel, input: inputs }),
      signal: AbortSignal.timeout(30_000),
    })
    if (!response.ok) throw new Error(`enterprise rule embedding request failed (${response.status})`)
    const payload = await response.json() as { data?: Array<{ index?: number; embedding?: number[] }> }
    if (!Array.isArray(payload.data) || payload.data.length !== inputs.length) {
      throw new Error('enterprise rule embedding provider returned an unexpected result count')
    }
    const ordered = [...payload.data].sort((left, right) => (left.index ?? 0) - (right.index ?? 0))
    return ordered.map(row => {
      const vector = row.embedding
      if (!Array.isArray(vector) || vector.length !== this.options.embeddingDimension
        || !vector.every(value => Number.isFinite(value))) {
        throw new Error(`enterprise rule embedding provider must return finite vectors of dimension ${this.options.embeddingDimension}`)
      }
      return vector
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
