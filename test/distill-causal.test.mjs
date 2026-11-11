// v0.8.0 causal distillation tests.
//
// Boots the built bundle in a mock Cordis host and drives: the additive
// distill-causal MemoryItem defaults on the read path (legacy records read
// back without throwing), the reasoning_memory_distill_causal domain, and the
// additive config defaults. Deterministic and network-free.
import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const bundleEntry = process.env.PLUGIN_BUNDLE_ENTRY
  ? fileURLToPath(new URL(`file://${process.env.PLUGIN_BUNDLE_ENTRY}`))
  : fileURLToPath(new URL('../dist/index.js', import.meta.url))
const { apply } = await import(pathToFileURL(bundleEntry).href)

const config = {
  pythonExecutable: process.platform === 'win32' ? 'python' : 'python3',
  maxMemories: 50,
  maxRetrievalRecords: 50,
  maxTrajectoryEvents: 1000,
  positiveNegativeRatio: 1,
  vectorIndexEnabled: false,
  qdrantUrl: 'http://127.0.0.1:6333',
  qdrantCollection: 'reasoning_memories',
  qdrantApiKeyEnv: 'QDRANT_API_KEY',
  embeddingUrl: '',
  embeddingModel: 'unused',
  embeddingDimension: 8,
  embeddingApiKeyEnv: 'OPENAI_API_KEY',
  retrievalCandidatePoolSize: 100,
  vectorCandidatePoolSize: 100,
  enterpriseRuleRagEnabled: false,
  enterpriseRuleRagRequired: false,
  enterpriseRuleCollection: 'enterprise_reasoning_rules',
  enterpriseRuleSetId: '',
  enterpriseRuleCandidatePoolSize: 20,
  enterpriseRuleResultLimit: 5,
  enterpriseRuleScoreThreshold: 0.3,
  enterpriseRuleEmbeddingUrl: '',
  enterpriseRuleEmbeddingModel: '',
  enterpriseRuleEmbeddingDimension: 0,
  enterpriseRuleQdrantApiKeyEnv: 'ENTERPRISE_RULES_QDRANT_API_KEY',
  enterpriseRuleEmbeddingApiKeyEnv: 'ENTERPRISE_RULES_EMBEDDING_API_KEY',
  captureReasoningEnabled: true,
  maxReasoningTextChars: 4000,
  llmDistillationEnabled: false,
  llmDistillationTemperature: 1,
  distillationProvider: '',
  distillationModel: '',
  maxMemoryItemsPerOutcome: 3,
  deSpecificationThreshold: 0.9,
  relevanceThreshold: 0,
  memoryConsolidationEnabled: false,
  builtinStoreEnabled: false,
  builtinStoreDataDir: '',
  apiConfigPath: '',
  skillsExportDir: '',
  minInsightConfidence: 0,
  deprecatedAfterHarmfulFeedback: 0,
  templateMatchMin: 0.55,
  documentCollection: 'reasoning_documents',
  documentChunkTargetChars: 1200,
  documentChunkMinChars: 300,
  documentMaxHops: 2,
  quantumDecisionEnabled: false,
  maxSuperpositionSize: 3,
  phaseFromCausal: true,
  decoherenceRate: 0,
  distillGateEnabled: false,
  distillUpliftThreshold: 0,
  phaseFromDistillUplift: true,
  distillShrinkPrior: 20,
  distillMinArmSamples: 5,
}

function makeMockHost() {
  const tools = new Map()
  const listeners = new Map()
  const tables = new Map()
  const on = (event, listener) => {
    if (!listeners.has(event)) listeners.set(event, [])
    listeners.get(event).push(listener)
  }
  const makeTable = name => {
    if (!tables.has(name)) tables.set(name, new Map())
    const rows = tables.get(name)
    return {
      get: key => rows.get(key),
      has: key => rows.has(key),
      put: async (key, value) => { rows.set(key, value) },
      delete: async key => rows.delete(key),
      entries: () => rows.entries(),
      get size() { return rows.size },
    }
  }
  const ctx = {
    logger: { warn() {}, info() {}, error() {}, debug() {} },
    effect: fn => { fn(); return () => {} },
    on,
    get: () => undefined,
    storageDomain: {
      open: async () => ({
        table: name => makeTable(name),
        close: () => {},
      }),
    },
    tools: { register: tool => { tools.set(tool.name, tool) } },
  }
  return { ctx, tools, listeners, tables }
}

function execFor(sessionId) {
  return {
    agent: { session: { header: { id: sessionId } } },
    signal: new AbortController().signal,
  }
}

function emitBashEvents(listeners, sessionId) {
  const session = { header: { id: sessionId } }
  for (const listener of listeners.get('session/event') ?? []) {
    listener(session, { seq: 1, type: 'tool/call', data: { name: 'bash', callId: 'c1' } })
    listener(session, { seq: 2, type: 'tool/result', data: { message: { source: { callId: 'c1' } } } })
  }
}

test('distill causal domain and additive defaults stay compatible', { timeout: 60_000 }, async () => {
  const { ctx, tools, listeners, tables } = makeMockHost()
  await apply(ctx, config)
  assert.ok(tables.has('distill_causal_events'))

  const sessionId = 'session-distill'
  const begin = await tools.get('memory_begin_task').execute({ task_desc: '校验 schema', task_family: 'code' }, execFor(sessionId))
  emitBashEvents(listeners, sessionId)
  const saved = await tools.get('memory_save_outcome').execute({
    outcome: 'success', verifier: 'pytest', evidence: '27 passed',
    memory_items: [{ title: 't', description: 'd', content: '校验 schema 再发布', contraindications: [] }],
  }, execFor(sessionId))
  assert.equal(saved.stored, true)
  await tools.get('memory_end_task').execute({ task_id: begin.taskId }, execFor(sessionId))

  // Legacy records (no distill causal events supplied) read back with the
  // additive defaults without throwing. Inspect the canonical memories table
  // (memoryItemSchema.parse fills the defaults).
  const memoryRows = [...tables.get('memories').entries()].map(([, item]) => item)
  const stored = memoryRows.find(item => item.memoryId === saved.memoryIds[0])
  assert.ok(stored)
  assert.equal(stored.distillUplift, 0)
  assert.equal(stored.distillInherited, 0)
  assert.equal(stored.distillSamples, 0)
  assert.equal(stored.distillPhase, 0)
  assert.equal(stored.distillGate, 'na')
  assert.equal(stored.distillEstimand, '')
  assert.equal(stored.distillEvidenceProvenance, '')
  assert.equal(stored.distillFalsificationTest, '')
})
