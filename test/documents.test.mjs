// v0.5.0 reasoning-aware document retrieval tests.
//
// Boots the built bundle in a mock Cordis host and drives the document
// lifecycle: import (with version bump and rejection guards), list, load,
// step-aligned retrieve (pattern filter, multi-hop, retrieval audit), and
// delete. A decoupling regression keeps the memory surface working across
// all document operations. Deterministic and network-free.
import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const bundleEntry = process.env.PLUGIN_BUNDLE_ENTRY
  ? fileURLToPath(new URL(`file://${process.env.PLUGIN_BUNDLE_ENTRY}`))
  : fileURLToPath(new URL('../dist/index.js', import.meta.url))
const { apply } = await import(pathToFileURL(bundleEntry).href)
const { retrievalRecordSchema } = await import(pathToFileURL(fileURLToPath(new URL('../dist/domain.js', import.meta.url))).href)

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

function makeMockHost(seedTables = {}) {
  const tools = new Map()
  const listeners = new Map()
  const tables = new Map()
  for (const [name, rows] of Object.entries(seedTables)) {
    const seeded = new Map()
    for (const [key, value] of rows) seeded.set(key, value)
    tables.set(name, seeded)
  }
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

const DEV_GUIDE = `# 开发规范

## 2.1 命名
函数命名使用 snake_case，见 §2.2。

## 2.2 测试要求
提交前必须跑单测，依据 2.1。
`

test('document lifecycle: import, list, load, retrieve, version bump, delete', { timeout: 60_000 }, async () => {
  const { ctx, tools, listeners, tables } = makeMockHost()
  await apply(ctx, config)
  for (const name of ['memory_import_document', 'memory_list_documents', 'memory_load_document', 'memory_retrieve_document', 'memory_delete_document']) {
    assert.ok(tools.has(name), `tool ${name} should be registered`)
  }

  // Import guards.
  const badName = await tools.get('memory_import_document').execute(
    { name: 'dev-guide', content: 'content' }, execFor('s1'),
  ).catch(error => String(error))
  assert.ok(String(badName).includes('memory_doc_'))
  const oversized = await tools.get('memory_import_document').execute(
    { name: 'memory_doc_huge', content: 'x'.repeat(200_001) }, execFor('s1'),
  ).catch(error => String(error))
  assert.ok(String(oversized).includes('import cap'))

  const imported = await tools.get('memory_import_document').execute({
    name: 'memory_doc_dev-guide',
    content: DEV_GUIDE,
    task_patterns: ['code_synthesis'],
    source: 'company-wiki',
  }, execFor('s1'))
  assert.equal(imported.imported, true)
  assert.equal(imported.version, '1')
  assert.equal(imported.chunkCount, 3)

  // List (layer 1) and load (whole small document).
  const listed = await tools.get('memory_list_documents').execute({}, execFor('s1'))
  assert.equal(listed.count, 1)
  assert.equal(listed.documents[0].name, 'memory_doc_dev-guide')
  const filtered = await tools.get('memory_list_documents').execute({ pattern: 'gui_operation' }, execFor('s1'))
  assert.equal(filtered.count, 0)

  const loaded = await tools.get('memory_load_document').execute({ doc_id: imported.docId }, execFor('s1'))
  assert.equal(loaded.found, true)
  assert.equal(loaded.chunkCount, 3)
  assert.ok(loaded.chunks.some(chunk => chunk.headingPath.includes('2.1 命名')))
  const missing = await tools.get('memory_load_document').execute({ doc_id: 'nope' }, execFor('s1'))
  assert.equal(missing.found, false)

  // Step-aligned retrieval with pattern filter and audit fields.
  const retrieved = await tools.get('memory_retrieve_document').execute({
    query: '函数命名规范',
    k: 4,
    task_pattern: 'code_synthesis',
    active_template_id: '',
    step_index: 2,
    retrieval_intent: 'retrieve_document',
  }, execFor('s1'))
  assert.ok(Array.isArray(retrieved.documents))
  assert.equal(retrieved.pattern, 'code_synthesis')
  assert.equal(retrieved.patternFallback, false)
  assert.ok(retrieved.documents.length >= 1)
  assert.ok(retrieved.documents[0].docName.startsWith('memory_doc_'))
  assert.ok(retrieved.note.includes('external content'))

  const retrievalRows = [...tables.get('retrievals').entries()].map(([, row]) => row)
  const lastAudit = retrievalRows[retrievalRows.length - 1]
  assert.equal(lastAudit.route, 'document')
  assert.equal(lastAudit.stepIndex, 2)
  assert.equal(lastAudit.retrievalIntent, 'retrieve_document')
  assert.deepEqual(lastAudit.documentIds, [imported.docId])
  assert.deepEqual(lastAudit.selectedMemoryIds, [])
  assert.equal(lastAudit.documentPatternFallback, false)

  // Pattern mismatch triggers the soft fallback audit flag.
  const fallback = await tools.get('memory_retrieve_document').execute({
    query: '函数命名规范',
    k: 4,
    task_pattern: 'numeric_search',
  }, execFor('s1'))
  assert.equal(fallback.patternFallback, true)
  const fallbackRows = [...tables.get('retrievals').entries()].map(([, row]) => row)
  assert.equal(fallbackRows[fallbackRows.length - 1].documentPatternFallback, true)

  // Re-import bumps the version and replaces the chunks.
  const reimported = await tools.get('memory_import_document').execute({
    name: 'memory_doc_dev-guide',
    content: '# 开发规范\n\n## 2.1 命名\n新内容。\n',
    task_patterns: ['code_synthesis'],
  }, execFor('s1'))
  assert.equal(reimported.imported, true)
  assert.equal(reimported.version, '2')
  assert.equal(reimported.docId, imported.docId)
  assert.equal(reimported.chunkCount, 2)
  assert.equal(tables.get('chunks').size, 2)

  // Decoupling regression: the memory lifecycle still works afterwards.
  const begin = await tools.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor('s1'))
  assert.ok(begin.started)
  emitBashEvents(listeners, 's1')
  const saved = await tools.get('memory_save_outcome').execute({
    outcome: 'success', verifier: 'pytest', evidence: '27 passed',
    memory_items: [{ title: 't', description: 'd', content: '先定位失败点再修改', contraindications: [] }],
  }, execFor('s1'))
  assert.equal(saved.stored, true)
  const memoryRetrieve = await tools.get('memory_retrieve').execute({ query: '重构解析函数', k: 4 }, execFor('s1'))
  assert.ok(Array.isArray(memoryRetrieve.memories))
  await tools.get('memory_end_task').execute({ task_id: begin.taskId }, execFor('s1'))

  // Delete removes the document, its chunks, and keeps memories intact.
  const deleted = await tools.get('memory_delete_document').execute({ doc_id: imported.docId }, execFor('s1'))
  assert.equal(deleted.deleted, true)
  assert.equal(deleted.removedChunks, 2)
  assert.equal(tables.get('documents').size, 0)
  assert.equal(tables.get('chunks').size, 0)
  assert.ok(tables.get('memories').size >= 1)
  const gone = await tools.get('memory_load_document').execute({ doc_id: imported.docId }, execFor('s1'))
  assert.equal(gone.found, false)

  // Legacy retrieval audits still parse with the new additive fields.
  const legacyAudit = retrievalRecordSchema.parse({
    retrievalId: 'legacy-doc-audit',
    taskId: 'legacy-task',
    sourceSessionId: 's1',
    queryHash: 'abc',
    route: 'auto',
    requestedK: 4,
    positiveNegativeRatio: 1,
    fusionWeights: { semantic: 1, structure: 0 },
    selectedMemoryIds: [],
    candidates: [],
    createdAt: 1,
  })
  assert.deepEqual(legacyAudit.documentIds, [])
  assert.equal(legacyAudit.documentPatternFallback, false)
})
