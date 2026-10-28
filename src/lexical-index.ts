import type { MemoryItem } from './domain.js'
import { querySearchText, memorySearchText } from './search-text.js'

export interface LexicalCandidate {
  memoryId: string
  outcome: 'success' | 'fail'
  lexicalScore: number
}

type Document = {
  outcome: LexicalCandidate['outcome']
  taskId: string
  length: number
  frequencies: Map<string, number>
}

function tokens(text: string): string[] {
  const output: string[] = []
  for (const token of text.toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []) {
    if (/\p{Script=Han}/u.test(token)) {
      const chars = Array.from(token)
      output.push(...chars)
      for (let index = 0; index + 1 < chars.length; index++) {
        output.push(chars[index] + chars[index + 1])
      }
    } else {
      output.push(token)
    }
  }
  return output
}

export class LexicalMemoryIndex {
  private readonly documents = new Map<string, Document>()
  private readonly postings = new Map<string, Map<string, number>>()
  private totalLength = 0

  constructor(memories: MemoryItem[] = []) {
    for (const memory of memories) this.upsert(memory)
  }

  upsert(memory: MemoryItem): void {
    this.delete([memory.memoryId])
    const documentTokens = tokens(memorySearchText(memory))
    const frequencies = new Map<string, number>()
    for (const token of documentTokens) frequencies.set(token, (frequencies.get(token) ?? 0) + 1)
    const document: Document = {
      outcome: memory.outcome,
      taskId: memory.provenance.taskId,
      length: documentTokens.length,
      frequencies,
    }
    this.documents.set(memory.memoryId, document)
    this.totalLength += document.length
    for (const [token, frequency] of frequencies) {
      let posting = this.postings.get(token)
      if (!posting) {
        posting = new Map()
        this.postings.set(token, posting)
      }
      posting.set(memory.memoryId, frequency)
    }
  }

  delete(memoryIds: string[]): void {
    for (const memoryId of memoryIds) {
      const document = this.documents.get(memoryId)
      if (!document) continue
      this.documents.delete(memoryId)
      this.totalLength -= document.length
      for (const token of document.frequencies.keys()) {
        const posting = this.postings.get(token)
        posting?.delete(memoryId)
        if (posting?.size === 0) this.postings.delete(token)
      }
    }
  }

  search(query: string, reasoningGraphJson: string, excludeTaskId: string, limit: number): LexicalCandidate[] {
    const queryTokens = [...new Set(tokens(querySearchText(query, reasoningGraphJson)))]
    if (queryTokens.length === 0 || this.documents.size === 0) return []
    const averageLength = this.totalLength / this.documents.size || 1
    const scores = new Map<string, number>()
    const k1 = 1.2
    const b = 0.75
    for (const token of queryTokens) {
      const posting = this.postings.get(token)
      if (!posting?.size) continue
      const documentFrequency = posting.size
      const inverseDocumentFrequency = Math.log1p(
        (this.documents.size - documentFrequency + 0.5) / (documentFrequency + 0.5),
      )
      for (const [memoryId, frequency] of posting) {
        const document = this.documents.get(memoryId)
        if (!document || (excludeTaskId && document.taskId === excludeTaskId)) continue
        const norm = frequency + k1 * (1 - b + b * document.length / averageLength)
        const score = inverseDocumentFrequency * (frequency * (k1 + 1)) / norm
        scores.set(memoryId, (scores.get(memoryId) ?? 0) + score)
      }
    }

    const candidates: LexicalCandidate[] = []
    for (const outcome of ['success', 'fail'] as const) {
      const ranked = [...scores.entries()]
        .filter(([memoryId]) => this.documents.get(memoryId)?.outcome === outcome)
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
        .slice(0, limit)
      const maxScore = ranked[0]?.[1] ?? 0
      for (const [memoryId, score] of ranked) {
        candidates.push({
          memoryId,
          outcome,
          lexicalScore: maxScore > 0 ? score / maxScore : 0,
        })
      }
    }
    return candidates
  }
}
