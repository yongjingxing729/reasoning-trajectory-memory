// Shared OpenAI-compatible embedding client used by every vector backend of
// this plugin. The provider contract: POST {model, input: string[]} returns
// {data: [{index, embedding}]}; each vector must be finite and match the
// configured dimension. Requests time out after 30s and never retry here:
// callers own their degradation policy.
export interface EmbeddingClientOptions {
  embeddingUrl: string
  embeddingModel: string
  embeddingApiKey?: string
  embeddingDimension: number
}

export class EmbeddingClient {
  private readonly fetcher: typeof fetch

  constructor(
    private readonly options: EmbeddingClientOptions,
    fetcher: typeof fetch = fetch,
  ) {
    this.fetcher = fetcher
    if (!Number.isInteger(options.embeddingDimension) || options.embeddingDimension < 1) {
      throw new Error('embedding dimension must be a positive integer')
    }
  }

  async embed(inputs: string[]): Promise<number[][]> {
    if (inputs.length === 0) return []
    const headers: Record<string, string> = { 'content-type': 'application/json' }
    if (this.options.embeddingApiKey) headers.authorization = `Bearer ${this.options.embeddingApiKey}`
    const response = await this.fetcher(this.options.embeddingUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify({ model: this.options.embeddingModel, input: inputs }),
      signal: AbortSignal.timeout(30_000),
    })
    if (!response.ok) throw new Error(`embedding provider request failed (${response.status})`)
    const payload = await response.json() as { data?: Array<{ index?: number; embedding?: number[] }> }
    if (!Array.isArray(payload.data) || payload.data.length !== inputs.length) {
      throw new Error('embedding provider returned an unexpected result count')
    }
    const ordered = [...payload.data].sort((left, right) => (left.index ?? 0) - (right.index ?? 0))
    return ordered.map(row => {
      const vector = row.embedding
      if (!Array.isArray(vector) || vector.length !== this.options.embeddingDimension
        || !vector.every(value => Number.isFinite(value))) {
        throw new Error(`embedding provider must return finite vectors of dimension ${this.options.embeddingDimension}`)
      }
      return vector
    })
  }
}
