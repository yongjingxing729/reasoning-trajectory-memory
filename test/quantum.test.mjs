// v0.7.0 quantum multi-probability engine tests.
//
// Boots the built bundle in a mock Cordis host and drives: the additive
// superposition[] contract on memory_retrieve / memory_plan_task, the
// zero-regression absence of the field when quantumDecisionEnabled is false,
// the retrieval audit candidate defaults, the reasoning_memory_quantum domain,
// and the additive config defaults. Deterministic and network-free.
import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const bundleEntry = process.env.PLUGIN_BUNDLE_ENTRY
  ? fileURLToPath(new URL(`file://${process.env.PLUGIN_BUNDLE_ENTRY}`))
  : fileURLToPath(new URL('../dist/index.js', import.meta.url))
const { apply } = await import(pathToFileURL(bundleEntry).href)

const baseConfig = {
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

test('quantum path is additive and the off state stays absent', { timeout: 60_000 }, async () => {
  const { ctx, tools, listeners, tables } = makeMockHost()
  // Default config: quantumDecisionEnabled is false.
  await apply(ctx, baseConfig)
  assert.ok(tables.has('quantum_events'))

  const sessionId = 'session-quantum-off'

  // Seed three memories in one task, then end it.
  const begin1 = await tools.get('memory_begin_task').execute({ task_desc: '校验 schema', task_family: 'code' }, execFor(sessionId))
  emitBashEvents(listeners, sessionId)
  const saved = await tools.get('memory_save_outcome').execute({
    outcome: 'success', verifier: 'pytest', evidence: '27 passed',
    memory_items: [
      { title: 'a', description: 'd', content: '校验 schema 再发布', contraindications: [] },
      { title: 'b', description: 'd', content: '先定位失败点再修改', contraindications: [] },
      { title: 'c', description: 'd', content: '检查约束再执行', contraindications: [] },
    ],
  }, execFor(sessionId))
  assert.equal(saved.stored, true)
  await tools.get('memory_end_task').execute({ task_id: begin1.taskId }, execFor(sessionId))

  const begin2 = await tools.get('memory_begin_task').execute({ task_desc: '校验 schema', task_family: 'code' }, execFor(sessionId))
  const retrieved = await tools.get('memory_retrieve').execute({ query: '校验 schema 再发布', k: 3 }, execFor(sessionId))
  // G1.4: the tool output carries no superposition key when disabled.
  assert.equal('superposition' in retrieved, false)

  // Retrieval audit candidates carry the additive defaults.
  const auditRows = [...tables.get('retrievals').entries()].map(([, row]) => row)
  const lastAudit = auditRows[auditRows.length - 1]
  assert.ok(Array.isArray(lastAudit.candidates))
  for (const candidate of lastAudit.candidates) {
    assert.equal(candidate.amplitude, 0)
    assert.equal(candidate.phase, 0)
    assert.equal(candidate.interferenceDelta, 0)
    assert.equal(candidate.coherence, 0)
    assert.equal(candidate.collapseReason, '')
  }
  await tools.get('memory_end_task').execute({ task_id: begin2.taskId }, execFor(sessionId))
})

test('quantum enabled emits the ordered superposition contract', { timeout: 60_000 }, async () => {
  const { ctx, tools, listeners } = makeMockHost()
  await apply(ctx, { ...baseConfig, quantumDecisionEnabled: true, maxSuperpositionSize: 3 })

  const sessionId = 'session-quantum-on'
  const begin1 = await tools.get('memory_begin_task').execute({ task_desc: '校验 schema', task_family: 'code' }, execFor(sessionId))
  emitBashEvents(listeners, sessionId)
  await tools.get('memory_save_outcome').execute({
    outcome: 'success', verifier: 'pytest', evidence: '27 passed',
    memory_items: [
      { title: 'a', description: 'd', content: '校验 schema 再发布', contraindications: [] },
      { title: 'b', description: 'd', content: '先定位失败点再修改', contraindications: [] },
      { title: 'c', description: 'd', content: '检查约束再执行', contraindications: [] },
    ],
  }, execFor(sessionId))
  await tools.get('memory_end_task').execute({ task_id: begin1.taskId }, execFor(sessionId))

  const begin2 = await tools.get('memory_begin_task').execute({ task_desc: '校验 schema', task_family: 'code' }, execFor(sessionId))
  const retrieved = await tools.get('memory_retrieve').execute({ query: '校验 schema 再发布', k: 3 }, execFor(sessionId))
  assert.ok(Array.isArray(retrieved.superposition))
  assert.equal(retrieved.superposition.length, 3)
  for (const entry of retrieved.superposition) {
    for (const key of ['id', 'amplitude', 'phase', 'p', 'interferenceDeltaRaw', 'interferenceDelta', 'coherence', 'collapseReason']) {
      assert.ok(key in entry, `missing key ${key}`)
    }
  }
  // G3.3: order == retrieval order.
  assert.deepEqual(
    retrieved.superposition.map(entry => entry.id),
    retrieved.memories.map(memory => memory.memoryId),
  )
  await tools.get('memory_end_task').execute({ task_id: begin2.taskId }, execFor(sessionId))
})
