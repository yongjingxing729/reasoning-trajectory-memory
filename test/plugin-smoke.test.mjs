// Plugin deployment smoke test.
//
// Loads the BUILT bundle (dist/index.js, or PLUGIN_BUNDLE_ENTRY pointing at an
// extracted npm tarball) inside a minimal mock Cordis host, then drives the
// full memory lifecycle — begin, capture, save, end, retrieve, query,
// feedback, status — including a real Python worker and the in-process
// PGlite/pgvector store. This proves the shipped artifact actually boots as a
// DSH plugin outside the desktop app, which is the deployment contract the
// CD pipeline verifies.
import assert from 'node:assert/strict'
import test from 'node:test'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const bundleEntry = process.env.PLUGIN_BUNDLE_ENTRY
  ? resolve(process.env.PLUGIN_BUNDLE_ENTRY)
  : fileURLToPath(new URL('../dist/index.js', import.meta.url))
const { apply } = await import(pathToFileURL(bundleEntry).href)

// A complete config object: Cordis normally fills the z defaults; the mock
// host passes a plain object, so every field must be present here.
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
  embeddingUrl: '', // no embedder: the built-in store runs json-only, no network
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
  builtinStoreEnabled: process.env.PLUGIN_SMOKE_NO_BUILTIN !== '1',
  builtinStoreDataDir: '',
  apiConfigPath: '',
}

function makeMockHost() {
  const tools = new Map()
  const listeners = new Map()
  const tables = new Map()
  const disposers = []
  const on = (event, listener) => {
    if (!listeners.has(event)) listeners.set(event, [])
    listeners.get(event).push(listener)
  }
  const makeTable = () => {
    const rows = new Map()
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
    effect: fn => {
      const disposer = fn()
      if (typeof disposer === 'function') disposers.push(disposer)
      return () => {}
    },
    on,
    get: () => undefined,
    storageDomain: {
      open: async () => ({
        table: name => {
          if (!tables.has(name)) tables.set(name, makeTable())
          return tables.get(name)
        },
        close: () => {},
      }),
    },
    tools: {
      register: tool => { tools.set(tool.name, tool) },
    },
  }
  return { ctx, tools, listeners, disposers }
}

function execFor(sessionId) {
  return {
    agent: { session: { header: { id: sessionId } } },
    signal: new AbortController().signal,
  }
}

function emit(listeners, sessionId, seq, type, data) {
  const session = { header: { id: sessionId } }
  for (const listener of listeners.get('session/event') ?? []) {
    listener(session, { seq, type, data })
  }
}

async function pollUntil(check, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await check()
    if (value) return value
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  return null
}

test('the built bundle boots in a mock DSH host and runs the full memory lifecycle', { timeout: 60_000 }, async () => {
  const { ctx, tools, listeners, disposers } = makeMockHost()
  await apply(ctx, config)

  try {
    const expectedTools = [
      'memory_begin_task',
      'memory_retrieve',
      'memory_feedback',
      'memory_save_outcome',
      'memory_consolidate',
      'memory_query',
      'memory_api_status',
      'memory_end_task',
    ]
    for (const name of expectedTools) assert.ok(tools.has(name), `tool ${name} should be registered`)

    const sessionId = 'session-smoke'

    // Second begin while one is active must be rejected.
    const begin = await tools.get('memory_begin_task').execute({
      task_desc: 'validate the schema before publishing generated artifacts',
    }, execFor(sessionId))
    assert.equal(begin.started, true)
    assert.ok(begin.taskId)
    assert.ok(begin.captureStatus.includes('reasoning'))
    const secondBegin = await tools.get('memory_begin_task').execute({ task_desc: 'nested' }, execFor(sessionId))
    assert.equal(secondBegin.started, false)

    // Simulate the DSH session stream: model identity, reasoning deltas, usage,
    // and one successful tool round-trip.
    emit(listeners, sessionId, 1, 'assistant/message', {
      message: { source: { provider: 'smoke', model: 'smoke-model' }, content: [] },
    })
    emit(listeners, sessionId, 2, 'assistant/chunk', {
      chunk: { type: 'reasoning-delta', index: 0, text: 'First validate the source schema.' },
    })
    emit(listeners, sessionId, 3, 'assistant/chunk', {
      chunk: { type: 'usage', usage: { inputTokens: 10, outputTokens: 4, reasoningTokens: 2 } },
    })
    emit(listeners, sessionId, 4, 'tool/call', { name: 'bash', callId: 'c1' })
    emit(listeners, sessionId, 5, 'tool/result', {
      message: { source: { callId: 'c1' }, isError: false },
    })

    const saved = await tools.get('memory_save_outcome').execute({
      outcome: 'success',
      verifier: 'smoke-suite',
      evidence: 'the suite passed after schema validation',
      reasoning_insight: 'Validate the source schema before publishing generated artifacts.',
    }, execFor(sessionId))
    assert.equal(saved.stored, true)
    assert.equal(saved.memoryIds.length, 1)
    assert.equal(saved.trustLevel, 'unverified')
    assert.equal(saved.distillation.method, 'caller_insight')
    const savedMemoryId = saved.memoryIds[0]

    const ended = await tools.get('memory_end_task').execute({ task_id: begin.taskId }, execFor(sessionId))
    assert.equal(ended.ended, true)
    assert.equal(ended.observedEvents, 2)
    assert.equal(ended.reasoningCaptureStatus, 'provided')
    assert.equal(ended.usage.inputTokens, 10)
    assert.equal(ended.usage.reasoningTokens, 2)

    // A fresh task retrieves the saved memory; the active-task exclusion and the
    // positive quota both hold.
    const begin2 = await tools.get('memory_begin_task').execute({ task_desc: 'check schema rules before a release' }, execFor(sessionId))
    assert.equal(begin2.started, true)
    const retrieved = await tools.get('memory_retrieve').execute({
      query: 'schema validation before publishing',
      k: 4,
    }, execFor(sessionId))
    assert.equal(retrieved.route, 'auto')
    assert.equal(retrieved.memories.length, 1)
    assert.equal(retrieved.memories[0].memoryId, savedMemoryId)
    assert.equal(retrieved.memories[0].title.length > 0, true)
    assert.equal(retrieved.balance.positiveCount, 1)
    assert.equal(retrieved.balance.negativeCount, 0)
    assert.ok(retrieved.note.includes('explicitly state'))

    // Structured JSON query: first from the canonical scan, then (after the
    // built-in store finishes its startup sync) served by PostgreSQL.
    const queryImmediately = await tools.get('memory_query').execute({}, execFor(sessionId))
    assert.equal(queryImmediately.count, 1)
    assert.equal(queryImmediately.items[0].memoryId, savedMemoryId)
    const queryFromStore = await pollUntil(async () => {
      const result = await tools.get('memory_query').execute({}, execFor(sessionId))
      return result.note.includes('PostgreSQL') ? result : null
    })
    assert.ok(queryFromStore, 'the built-in PostgreSQL store should serve structured queries after sync')
    assert.equal(queryFromStore.count, 1)
    assert.equal(queryFromStore.items[0].provenance.taskId, begin.taskId)
    assert.deepEqual(queryFromStore.items[0].contraindications, [])

    // Bounded self-reported feedback persists on the retrieved memory.
    const feedback = await tools.get('memory_feedback').execute({
      memory_ids: [savedMemoryId],
      rating: 'helpful',
      evidence: 'the lesson matched the current subproblem',
    }, execFor(sessionId))
    assert.equal(feedback.updated, 1)

    // API config is disabled in this profile; consolidation is off.
    const apiStatus = await tools.get('memory_api_status').execute({}, execFor(sessionId))
    assert.equal(apiStatus.enabled, false)
    assert.ok(apiStatus.note.includes('apiConfigPath'))
    const consolidated = await tools.get('memory_consolidate').execute({ memory_ids: [savedMemoryId] }, execFor(sessionId))
    assert.equal(consolidated.stored, false)

    await tools.get('memory_end_task').execute({ task_id: begin2.taskId }, execFor(sessionId))
  } finally {
    for (const disposer of disposers.splice(0)) await disposer()
  }
})
