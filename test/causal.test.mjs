// v0.6.0 causal utility engine tests.
//
// Boots the built bundle in a mock Cordis host and drives: observational
// event derivation at memory_save_outcome (treatment used/retrieved_unused
// from the feedback marks, stratified taskPattern, FIFO cap), paired
// counterfactual recording with per-task idempotency, retrieval audit causal
// fields, memory_query causal summaries, and zero-regression of the memory
// lifecycle. Deterministic and network-free.
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

test('causal events derive, pair, audit, cap, and keep the old paths intact', { timeout: 60_000 }, async () => {
  const { ctx, tools, listeners, tables } = makeMockHost()
  await apply(ctx, config)
  assert.ok(tools.has('memory_record_counterfactual'))

  const sessionId = 'session-causal'
  const causalTable = tables.get('causal_events')

  // Task 1 saves a memory for later tasks to retrieve.
  const begin1 = await tools.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor(sessionId))
  emitBashEvents(listeners, sessionId)
  const saved = await tools.get('memory_save_outcome').execute({
    outcome: 'success', verifier: 'pytest', evidence: '27 passed',
    memory_items: [{ title: 't', description: 'd', content: '先定位失败点再修改', contraindications: [] }],
  }, execFor(sessionId))
  assert.equal(saved.stored, true)
  const memoryId = saved.memoryIds[0]
  // No causal events for the saving task itself (nothing retrieved).
  assert.equal(causalTable.size, 0)
  await tools.get('memory_end_task').execute({ task_id: begin1.taskId }, execFor(sessionId))

  // Task 2 retrieves the memory, rates it helpful, and succeeds: the derived
  // observational event must carry treatment=used and the classified pattern.
  const begin2 = await tools.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor(sessionId))
  const retrieved = await tools.get('memory_retrieve').execute({ query: '重构解析函数', k: 4 }, execFor(sessionId))
  assert.ok(retrieved.memories.some(memory => memory.memoryId === memoryId))
  await tools.get('memory_feedback').execute({
    memory_ids: [memoryId], rating: 'helpful', evidence: 'worked',
  }, execFor(sessionId))
  emitBashEvents(listeners, sessionId)
  const saved2 = await tools.get('memory_save_outcome').execute({
    outcome: 'success', verifier: 'pytest', evidence: '27 passed',
    memory_items: [{ title: 't', description: 'd', content: '先定位失败点再修改', contraindications: [] }],
  }, execFor(sessionId))
  assert.equal(saved2.stored, true)
  await tools.get('memory_end_task').execute({ task_id: begin2.taskId }, execFor(sessionId))

  const eventsAfterTask2 = [...causalTable.entries()].map(([, event]) => event)
  assert.equal(eventsAfterTask2.length, 1)
  assert.equal(eventsAfterTask2[0].memoryId, memoryId)
  assert.equal(eventsAfterTask2[0].kind, 'memory')
  assert.equal(eventsAfterTask2[0].treatment, 'used')
  assert.equal(eventsAfterTask2[0].outcome, 'success')
  assert.equal(eventsAfterTask2[0].taskPattern, 'code_synthesis')
  assert.equal(eventsAfterTask2[0].taskId, begin2.taskId)

  // Task 3 retrieves without feedback: treatment=retrieved_unused.
  const begin3 = await tools.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor(sessionId))
  await tools.get('memory_retrieve').execute({ query: '重构解析函数', k: 4 }, execFor(sessionId))
  emitBashEvents(listeners, sessionId)
  await tools.get('memory_save_outcome').execute({
    outcome: 'fail', verifier: 'pytest', evidence: '2 failed',
    memory_items: [{ title: 't', description: 'd', content: '先定位失败点再修改', contraindications: [] }],
  }, execFor(sessionId))
  await tools.get('memory_end_task').execute({ task_id: begin3.taskId }, execFor(sessionId))
  const eventsAfterTask3 = [...causalTable.entries()].map(([, event]) => event)
  const task3Events = eventsAfterTask3.filter(event => event.taskId === begin3.taskId)
  // Task 3 retrieved both the task-1 and the task-2 memory (task 2 saved a
  // second one), so two observational events derive, both unrated.
  assert.equal(task3Events.length, 2)
  assert.ok(task3Events.every(event => event.treatment === 'retrieved_unused'))
  assert.ok(task3Events.every(event => event.outcome === 'fail'))

  // Paired counterfactual recording: one pair, both arms, per-task idempotency.
  const beginCf = await tools.get('memory_begin_task').execute({ task_desc: '对照实验', task_family: 'code' }, execFor(sessionId))
  const pairArgs = { memory_id: memoryId, pair_id: 'pair-x' }
  const withArm = await tools.get('memory_record_counterfactual').execute(
    { ...pairArgs, arm: 'with', outcome: 'success', task_pattern: 'code_synthesis' }, execFor(sessionId),
  )
  assert.equal(withArm.recorded, true)
  assert.equal(withArm.pairId, 'pair-x')
  const withoutArm = await tools.get('memory_record_counterfactual').execute(
    { ...pairArgs, arm: 'without', outcome: 'fail', task_pattern: 'code_synthesis' }, execFor(sessionId),
  )
  assert.equal(withoutArm.recorded, true)
  assert.ok(withoutArm.note.includes('Both arms'))
  const duplicate = await tools.get('memory_record_counterfactual').execute(
    { ...pairArgs, arm: 'with', outcome: 'success', task_pattern: 'code_synthesis' }, execFor(sessionId),
  )
  assert.equal(duplicate.recorded, false)
  await tools.get('memory_end_task').execute({ task_id: beginCf.taskId }, execFor(sessionId))

  // memory_query causal summaries.
  const queried = await tools.get('memory_query').execute({ include_causal: true, limit: 5 }, execFor(sessionId))
  const causalRow = queried.items.find(item => item.memoryId === memoryId)
  assert.ok(causalRow)
  assert.equal(typeof causalRow.causal.applied, 'boolean')
  assert.equal(typeof causalRow.causal.nEff, 'number')
  assert.equal(typeof causalRow.causal.estimatorVersion, 'string')

  // Retrieval audit carries the causal fields on candidates and the record.
  const begin4 = await tools.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor(sessionId))
  await tools.get('memory_retrieve').execute({ query: '重构解析函数', k: 4 }, execFor(sessionId))
  const auditRows = [...tables.get('retrievals').entries()].map(([, row]) => row)
  const lastAudit = auditRows[auditRows.length - 1]
  assert.equal(typeof lastAudit.estimatorVersion, 'string')
  assert.ok(Array.isArray(lastAudit.candidates))
  const candidate = lastAudit.candidates.find(row => row.memoryId === memoryId)
  if (candidate) {
    assert.equal(typeof candidate.causalApplied, 'boolean')
    assert.equal(typeof candidate.causalUplift, 'number')
    assert.equal(typeof candidate.causalSamples, 'number')
    assert.equal(typeof candidate.simpsonParadox, 'boolean')
  }
  await tools.get('memory_end_task').execute({ task_id: begin4.taskId }, execFor(sessionId))

  // FIFO cap: seed >200 events for one unit and confirm the oldest drop.
  const now = Date.now()
  for (let index = 0; index < 210; index++) {
    causalTable.set(`bulk-${index}`, {
      eventId: `bulk-${index}`,
      memoryId,
      kind: 'memory',
      taskId: 'bulk-task',
      taskPattern: 'general',
      treatment: 'used',
      outcome: 'success',
      stepIndex: 0,
      retrievalIntent: '',
      modelVersion: '',
      pairId: '',
      createdAt: now + index,
    })
  }
  // One more write through the guarded path triggers the FIFO trim.
  const begin5 = await tools.get('memory_begin_task').execute({ task_desc: '无关任务', task_family: 'general' }, execFor(sessionId))
  const bulkRecord = await tools.get('memory_record_counterfactual').execute(
    { memory_id: memoryId, arm: 'with', outcome: 'success', pair_id: 'bulk-pair', task_pattern: 'general' },
    execFor(sessionId),
  )
  assert.equal(bulkRecord.recorded, true)
  await tools.get('memory_end_task').execute({ task_id: begin5.taskId }, execFor(sessionId))
  const bulkEvents = [...causalTable.entries()]
    .map(([, event]) => event)
    .filter(event => event.memoryId === memoryId)
    .sort((left, right) => left.createdAt - right.createdAt)
  assert.equal(bulkEvents.length, 200)
  // The FIFO trim dropped the pre-bulk history: everything kept was created
  // at or after the bulk seed window.
  assert.ok(bulkEvents.every(event => event.createdAt >= now))
})
