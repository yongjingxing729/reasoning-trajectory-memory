/** Shared response and transport types exchanged between the plugin layers. */
import type { JsonValue } from '@deepseek-ai/dsh-session'
import type { DocumentChunk } from './document-domain.js'
import type { MemoryItem, StrategyTemplate } from './domain.js'

export type Route = 'auto' | 'semantic' | 'structure' | 'graph' | 'hybrid'
export type EnterpriseRuleRagStatus = 'disabled' | 'ready' | 'no_matches' | 'unavailable' | 'invalid_configuration'

export type DistillResponse = {
  stored: boolean
  reason: string
  memories?: MemoryItem[]
  gateRejectedCount?: number
}
export type RetrievalRow = {
  item: { reasoning?: string; reasoningGraph?: MemoryItem['reasoningGraph'] }
  memoryId: string
  outcome: 'success' | 'fail'
  contextScore: number
  reasoningScore: number
  semanticScore: number
  structureScore: number
  graphScore: number
  lexicalScore: number
  vectorScore: number
  templateScore: number
  deprecated: boolean
  combinedScore: number
  utilityMultiplier: number
}
export type RetrievalResponse = {
  results: RetrievalRow[]
  candidateScores: Array<{
    memoryId: string
    outcome: 'success' | 'fail'
    contextScore: number
    reasoningScore: number
    semanticScore: number
    structureScore: number
    graphScore: number
    lexicalScore: number
    vectorScore: number
    templateScore: number
    combinedScore: number
    utilityMultiplier: number
    contextRank: number | null
    reasoningRank: number | null
    structureRank: number | null
    graphRank: number | null
    lexicalRank: number | null
    vectorRank: number | null
    combinedRank: number
    selectedRank: number | null
    causalApplied: boolean
    causalUplift: number
    causalSamples: number
    simpsonParadox: boolean
    causalStrata: string
    estimatorVersion: string
  }>
  queryHash: string
  balance: {
    positiveCount: number
    negativeCount: number
    ratio: number
    fallbackUsed: boolean
    thresholdFilteredCount: number
  }
  fusionWeights: { context: number; reasoning: number; structure: number; graph: number; vector: number; lexical: number; template?: number }
  fusionMode: 'weighted' | 'rrf'
  templateChannelWeight?: number
  deprecatedFilteredCount?: number
  simpsonNote?: string
  superposition?: Array<{
    id: string
    amplitude: number
    phase: number
    p: number
    interferenceDeltaRaw: number
    interferenceDelta: number
    coherence: number
    collapseReason: string
  }>
  quantum?: {
    coherence: number
    coherenceBefore: number
    coherenceAfter: number
    collapseReason: string
    estimatorVersion: string
  }
  reason: string
}

export type TaskPattern = 'multi_hop_qa' | 'code_synthesis' | 'tool_orchestration' | 'gui_operation' | 'long_horizon_planning' | 'numeric_search' | 'general'

export type PlanResponse = {
  pattern: TaskPattern
  confidence: number
  evidence: Array<{ signal: string; hit: JsonValue; weight: number }>
  scores: Record<string, number>
  fallback: boolean
  matchedTemplates: Array<{
    templateId: string
    name: string
    description: string
    taskPatterns: string[]
    steps: StrategyTemplate['steps']
    structureSignature: string[]
    preconditions: string[]
    activeRetrievalPoints: number[]
    score: number
    breakdown: { struct: number; lexical: number; pattern: number; quality: number }
    strongMatch: boolean
    injectable: boolean
    deprecated: boolean
    utility: StrategyTemplate['utility']
  }>
  plan: string
  audit: {
    matchThreshold: number
    evaluatedTemplates: number
    querySignature: string[]
  }
  superposition?: Array<{
    id: string
    amplitude: number
    phase: number
    p: number
    interferenceDeltaRaw: number
    interferenceDelta: number
    coherence: number
    collapseReason: string
  }>
  quantum?: {
    coherence: number
    coherenceBefore: number
    coherenceAfter: number
    collapseReason: string
    estimatorVersion: string
  }
}

export type PublishValidateResponse = {
  valid: boolean
  issues: string[]
  normalized: {
    name: string
    description: string
    taskPatterns: string[]
    steps: Array<{ index: number; title: string; moduleDemands: Array<{ kind: string; intent: string }> }>
    preconditions: string[]
    structureSignature: string[]
  }
}

export type DocumentRetrieveResponse = {
  results: Array<{
    chunkId: string
    docId: string
    seq: number
    headingPath: string[]
    text: string
    hop: number
    viaChunkId: string
    docName: string
    source: string
    version: string
    links: { prevChunkId: string; nextChunkId: string; parentChunkId: string }
    score: number
    breakdown: { lexical: number; vector: number; pattern: number; link: number }
  }>
  pattern: string
  patternFallback: boolean
  expansion: {
    graphNodeCount: number
    stepBackTerms: string[]
    intentTexts: string[]
    pseudoFeedbackUsed: boolean
  }
  fusionMode: 'rrf' | 'weighted'
  audit: { eligibleChunks: number; visitedChunks: number; seeds: number; maxHops: number; expandCap: number }
  reason: string
}

export type DocumentImportResponse = {
  docId: string
  name: string
  charCount: number
  chunkCount: number
  chunks: DocumentChunk[]
  audit: {
    truncated: boolean
    headingsDetected: boolean
    unresolvedRefs: number
    oversizedParagraphs: number
    contentTruncated: boolean
  }
}

export type DocumentDeletePlanResponse = {
  docId: string
  chunkIds: string[]
}

export type CausalEstimateResponse = {
  causalApplied: boolean
  causalMultiplier: number
  ate: number
  ateVariance: number
  se: number
  ciLower: number
  ciUpper: number
  ateShrunk: number
  crude: number
  crudeSe: number
  simpsonFlip: boolean
  iptwAte: number
  iptwDisagreement: boolean
  pairedAte: number
  pairedPairs: number
  nEff: number
  nTreated: number
  nControl: number
  cate: Array<{ stratum: string; n1: number; n0: number; y1: number; y0: number; cate: number }>
  droppedStrata: string[]
  estimatorVersion: string
  reason: string
}

export type ClassifyResponse = {
  pattern: TaskPattern
  confidence: number
  evidence: Array<{ signal: string; hit: JsonValue; weight: number }>
  scores: Record<string, number>
  fallback: boolean
}
