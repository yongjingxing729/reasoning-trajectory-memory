import type { DocumentChunk } from './document-domain.js'

// Standalone BM25 index for the document retrieval surface. It reuses the
// same scoring shape as LexicalMemoryIndex (k1=1.2, b=0.75, Chinese
// char+bigram tokens, word tokens otherwise) but keeps its own postings:
// document chunks never mix with memory postings, so deleting a document
// cannot touch the memory index.
export interface DocumentLexicalCandidate {
  chunkId: string
  lexicalScore: number
}

type IndexDocument = {
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

export function documentChunkSearchText(
  docName: string,
  taskPatterns: string[],
  chunk: DocumentChunk,
): string {
  return [
    `doc: ${docName}`,
    `patterns: ${taskPatterns.join(' ')}`,
    `heading: ${chunk.headingPath.join(' / ')}`,
    `text: ${chunk.text}`,
  ].filter(part => part.trim()).join('\n')
}

export class DocumentLexicalIndex {
  private readonly documents = new Map<string, IndexDocument>()
  private readonly postings = new Map<string, Map<string, number>>()
  private totalLength = 0

  upsert(docName: string, taskPatterns: string[], chunk: DocumentChunk): void {
    this.delete([chunk.chunkId])
    const documentTokens = tokens(documentChunkSearchText(docName, taskPatterns, chunk))
    const frequencies = new Map<string, number>()
    for (const token of documentTokens) frequencies.set(token, (frequencies.get(token) ?? 0) + 1)
    const document: IndexDocument = { length: documentTokens.length, frequencies }
    this.documents.set(chunk.chunkId, document)
    this.totalLength += document.length
    for (const [token, frequency] of frequencies) {
      let posting = this.postings.get(token)
      if (!posting) {
        posting = new Map()
        this.postings.set(token, posting)
      }
      posting.set(chunk.chunkId, frequency)
    }
  }

  delete(chunkIds: string[]): void {
    for (const chunkId of chunkIds) {
      const document = this.documents.get(chunkId)
      if (!document) continue
      this.documents.delete(chunkId)
      this.totalLength -= document.length
      for (const token of document.frequencies.keys()) {
        const posting = this.postings.get(token)
        posting?.delete(chunkId)
        if (posting?.size === 0) this.postings.delete(token)
      }
    }
  }

  search(query: string, limit: number): DocumentLexicalCandidate[] {
    const queryTokens = [...new Set(tokens(query))]
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
      for (const [chunkId, frequency] of posting) {
        const document = this.documents.get(chunkId)
        if (!document) continue
        const norm = frequency + k1 * (1 - b + b * document.length / averageLength)
        const score = inverseDocumentFrequency * (frequency * (k1 + 1)) / norm
        scores.set(chunkId, (scores.get(chunkId) ?? 0) + score)
      }
    }
    const ranked = [...scores.entries()]
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
      .slice(0, limit)
    const maxScore = ranked[0]?.[1] ?? 0
    return ranked.map(([chunkId, score]) => ({
      chunkId,
      lexicalScore: maxScore > 0 ? score / maxScore : 0,
    }))
  }
}
