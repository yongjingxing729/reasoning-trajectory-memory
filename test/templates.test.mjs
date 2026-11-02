// v0.4.0 strategy-template layer tests.
//
// Boots the built bundle in a mock Cordis host and drives the template
// lifecycle: seeding, plan, template tools, step-aligned retrieval, template
// and memory feedback (including the persistent harmful-task deprecation
// window across a simulated restart), the insight-confidence gate, and the
// additive domain compatibility of legacy records without the new fields.
// All tests are deterministic and network-free.
import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const bundleEntry = process.env.PLUGIN_BUNDLE_ENTRY
  ? fileURLToPath(new URL(`file://${process.env.PLUGIN_BUNDLE_ENTRY}`))
  : fileURLToPath(new URL('../dist/index.js', import.meta.url))
const { apply } = await import(pathToFileURL(bundleEntry).href)
const { memoryItemSchema, retrievalRecordSchema } = await import(pathToFileURL(fileURLToPath(new URL('../dist/domain.js', import.meta.url))).href)

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
  minInsightConfidence: 0.5,
  deprecatedAfterHarmfulFeedback: 2,
  templateMatchMin: 0.55,
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

function emit(listeners, sessionId, seq, type, data) {
  const session = { header: { id: sessionId } }
  for (const listener of listeners.get('session/event') ?? []) {
    listener(session, { seq, type, data })
  }
}

function emitBashEvents(listeners, sessionId) {
  emit(listeners, sessionId, 1, 'tool/call', { name: 'bash', callId: 'c1' })
  emit(listeners, sessionId, 2, 'tool/result', { message: { source: { callId: 'c1' } } })
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

function makeLegacyMemory(memoryId, taskId) {
  return {
    memoryId,
    type: 'strategy',
    outcome: 'success',
    taskScope: 'general',
    preconditions: [],
    content: '调用者提炼的可迁移推理策略（未独立验证）：先定位失败点再修改',
    reasoning: '先定位失败点再修改',
    provenance: { taskId, sourceSessionId: 'legacy-session', eventSeqs: [1, 2] },
    evidence: { verifier: 'pytest', summary: '通过', independentlyVerified: false },
    confidence: 0.55,
    trustLevel: 'unverified',
    extractorVersion: 'dsh-reasoning-memory/0.3.0',
    createdAt: 1,
    updatedAt: 1,
    semanticText: '重构解析函数',
    structureSignature: ['search', 'execute', 'verify'],
  }
}

test('template layer boots, plans, rates, gates, and stays additive-compatible', { timeout: 60_000 }, async () => {
  // A legacy record without any v0.4.0 field is seeded at boot so it takes
  // the normal index/retrieve path; the zod schema must fill the defaults.
  const legacySeed = makeLegacyMemory('legacy-1', 'legacy-task')
  const { ctx, tools, listeners, tables } = makeMockHost({ memories: [['legacy-1', legacySeed]] })
  await apply(ctx, config)
  for (const name of ['memory_plan_task', 'memory_list_templates', 'memory_load_template', 'memory_publish_template']) {
    assert.ok(tools.has(name), `tool ${name} should be registered`)
  }

  // Idempotent builtin seeding.
  const listed = await pollUntil(async () => {
    const result = await tools.get('memory_list_templates').execute({}, execFor('s1'))
    return result.count >= 5 ? result : null
  })
  assert.equal(listed.count, 5)
  assert.ok(listed.templates.every(template => template.name.startsWith('memory_tpl_')))

  // Progressive disclosure: layer 2 load.
  const loaded = await tools.get('memory_load_template').execute({ template_id: 'memory_tpl_builtin_tot' }, execFor('s1'))
  assert.equal(loaded.found, true)
  assert.equal(loaded.name, 'memory_tpl_tot-decompose-backtrack')
  assert.ok(loaded.steps.length >= 4)
  assert.equal(loaded.status, 'active')
  const missing = await tools.get('memory_load_template').execute({ template_id: 'nope' }, execFor('s1'))
  assert.equal(missing.found, false)

  // Caller publish, validation guards, and version bump.
  const publishArgs = {
    name: 'memory_tpl_custom-refactor',
    description: 'Use when refactoring an existing repository with a test suite',
    task_patterns: ['code_synthesis'],
    steps: [
      { index: 1, title: 'locate failures', module_demands: [{ kind: 'retrieve_memory', intent: 'find refactor lessons' }] },
      { index: 2, title: 'edit', module_demands: [{ kind: 'invoke_tool', intent: '' }] },
      { index: 3, title: 'verify', module_demands: [{ kind: 'verify', intent: '' }] },
    ],
    preconditions: ['a test suite exists'],
  }
  const published = await tools.get('memory_publish_template').execute(publishArgs, execFor('s1'))
  assert.equal(published.published, true)
  assert.equal(published.version, 1)
  assert.deepEqual(published.structureSignature, ['search', 'execute', 'verify'])
  const republished = await tools.get('memory_publish_template').execute(publishArgs, execFor('s1'))
  assert.equal(republished.version, 2)
  assert.equal(republished.templateId, published.templateId)
  const badName = await tools.get('memory_publish_template').execute({ ...publishArgs, name: 'custom-refactor' }, execFor('s1'))
  assert.equal(badName.published, false)
  assert.ok(badName.note.includes('memory_tpl_'))
  const badDescription = await tools.get('memory_publish_template').execute(
    { ...publishArgs, name: 'memory_tpl_no-trigger', description: 'a plain description without triggers' },
    execFor('s1'),
  )
  assert.equal(badDescription.published, false)
  assert.ok(badDescription.note.includes('trigger'))

  // Plan within an active task records planTemplateIds for feedback checks.
  const begin = await tools.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor('s1'))
  assert.ok(begin.started)
  emitBashEvents(listeners, 's1')
  const plan = await tools.get('memory_plan_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor('s1'))
  assert.ok(['multi_hop_qa', 'code_synthesis', 'tool_orchestration', 'gui_operation', 'long_horizon_planning', 'numeric_search', 'general'].includes(plan.pattern))
  const plannedTemplate = plan.matchedTemplates[0]
  assert.ok(plannedTemplate)
  assert.ok(Array.isArray(plannedTemplate.steps))
  assert.ok(Array.isArray(plannedTemplate.activeRetrievalPoints))

  // Step-aligned retrieval with the template channel and audit fields.
  const retrieve = await tools.get('memory_retrieve').execute({
    query: '重构代码库',
    k: 4,
    active_template_id: 'memory_tpl_builtin_tot',
    step_index: 2,
    retrieval_intent: 'retrieve_memory',
  }, execFor('s1'))
  assert.ok(Array.isArray(retrieve.memories))
  for (const memory of retrieve.memories) {
    assert.equal(typeof memory.templateScore, 'number')
    assert.equal(typeof memory.deprecated, 'boolean')
  }
  const retrievalRows = [...tables.get('retrievals').entries()].map(([, row]) => row)
  const lastAudit = retrievalRows[retrievalRows.length - 1]
  assert.equal(lastAudit.activeTemplateId, 'memory_tpl_builtin_tot')
  assert.equal(lastAudit.stepIndex, 2)
  assert.equal(lastAudit.retrievalIntent, 'retrieve_memory')
  assert.ok(lastAudit.templateChannelWeight > 0)

  // Template feedback: planned once, rejected otherwise.
  const templateFeedback = await tools.get('memory_feedback').execute({
    memory_ids: [], template_id: plannedTemplate.templateId, rating: 'helpful', evidence: 'guided the work',
  }, execFor('s1'))
  assert.equal(templateFeedback.templateUpdated, 1)
  const repeatFeedback = await tools.get('memory_feedback').execute({
    memory_ids: [], template_id: plannedTemplate.templateId, rating: 'helpful', evidence: 'again',
  }, execFor('s1'))
  assert.equal(repeatFeedback.templateUpdated, 0)
  assert.ok(repeatFeedback.note.includes('already rated'))
  const unplannedFeedback = await tools.get('memory_feedback').execute({
    memory_ids: [], template_id: 'memory_tpl_builtin_react', rating: 'neutral', evidence: 'x',
  }, execFor('s1'))
  assert.equal(unplannedFeedback.templateUpdated, 0)
  assert.ok(unplannedFeedback.note.includes('memory_plan_task'))

  // Insight-confidence write gate.
  const gated = await tools.get('memory_save_outcome').execute({
    outcome: 'success', verifier: 'pytest', evidence: '27 passed',
    memory_items: [{ title: 't', description: 'd', content: '先定位失败点再修改', contraindications: [] }],
    insight_confidence: 0.2,
  }, execFor('s1'))
  assert.equal(gated.stored, false)
  assert.ok(gated.gateRejectedCount >= 1)
  const saved = await tools.get('memory_save_outcome').execute({
    outcome: 'success', verifier: 'pytest', evidence: '27 passed',
    memory_items: [{ title: 't', description: 'd', content: '先定位失败点再修改', contraindications: [] }],
    insight_confidence: 0.9,
  }, execFor('s1'))
  assert.equal(saved.stored, true)
  assert.equal(saved.gateRejectedCount, 0)
  const savedMemoryId = saved.memoryIds[0]
  await tools.get('memory_end_task').execute({ task_id: begin.taskId }, execFor('s1'))

  // Structured query filters for the new fields.
  const queried = await tools.get('memory_query').execute({ reasoning_level: 'path', status: 'active' }, execFor('s1'))
  assert.ok(queried.count >= 1)

  // Legacy record without any v0.4.0 field: read, retrieve, and rate without
  // throwing; the zod schema fills every additive default.
  const legacy = makeLegacyMemory('legacy-1', 'legacy-task')
  const parsed = memoryItemSchema.parse(legacy)
  assert.equal(parsed.strategyTemplateId, '')
  assert.equal(parsed.reasoningLevel, 'path')
  assert.equal(parsed.insightConfidence, 0)
  assert.equal(parsed.status, 'active')
  assert.deepEqual(parsed.harmfulTaskIds, [])

  const begin2 = await tools.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor('s1'))
  const retrieveLegacy = await tools.get('memory_retrieve').execute({ query: '重构解析函数', k: 4 }, execFor('s1'))
  const legacyRow = retrieveLegacy.memories.find(memory => memory.memoryId === 'legacy-1')
  assert.ok(legacyRow, 'the legacy memory should be retrievable')
  assert.equal(legacyRow.deprecated, false)
  assert.equal(legacyRow.templateScore, 0)
  const legacyFeedback = await tools.get('memory_feedback').execute({
    memory_ids: ['legacy-1'], rating: 'helpful', evidence: 'worked',
  }, execFor('s1'))
  assert.equal(legacyFeedback.updated, 1)
  await tools.get('memory_end_task').execute({ task_id: begin2.taskId }, execFor('s1'))

  // Retrieval audits from legacy shapes keep parsing.
  const legacyAudit = retrievalRecordSchema.parse({
    retrievalId: 'legacy-audit',
    taskId: 'legacy-task',
    sourceSessionId: 's1',
    queryHash: 'abc',
    route: 'auto',
    requestedK: 4,
    positiveNegativeRatio: 1,
    fusionWeights: { semantic: 1, structure: 0 },
    selectedMemoryIds: ['legacy-1'],
    candidates: [],
    createdAt: 1,
  })
  assert.equal(legacyAudit.activeTemplateId, '')
  assert.equal(legacyAudit.templateChannelWeight, 0)
})

test('harmful feedback deprecates across distinct tasks and survives a restart', { timeout: 60_000 }, async () => {
  const { ctx: ctxA, tools: toolsA, listeners: listenersA, tables: tablesA } = makeMockHost()
  await apply(ctxA, config)

  // Task 1 saves a memory.
  const begin1 = await toolsA.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor('sa'))
  emitBashEvents(listenersA, 'sa')
  const saved = await toolsA.get('memory_save_outcome').execute({
    outcome: 'success', verifier: 'pytest', evidence: '27 passed',
    memory_items: [{ title: 't', description: 'd', content: '先定位失败点再修改', contraindications: [] }],
    insight_confidence: 0.9,
  }, execFor('sa'))
  const memoryId = saved.memoryIds[0]
  await toolsA.get('memory_end_task').execute({ task_id: begin1.taskId }, execFor('sa'))

  // Task 2 retrieves and rates harmful (first distinct task).
  const begin2 = await toolsA.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor('sa'))
  const retrieved2 = await toolsA.get('memory_retrieve').execute({ query: '重构解析函数', k: 4 }, execFor('sa'))
  assert.ok(retrieved2.memories.some(memory => memory.memoryId === memoryId))
  const harmful1 = await toolsA.get('memory_feedback').execute({
    memory_ids: [memoryId], rating: 'harmful', evidence: 'misled the change',
  }, execFor('sa'))
  assert.equal(harmful1.updated, 1)
  await toolsA.get('memory_end_task').execute({ task_id: begin2.taskId }, execFor('sa'))
  const afterTask2 = tablesA.get('memories').get(memoryId)
  assert.deepEqual(afterTask2.harmfulTaskIds, [begin2.taskId])
  assert.equal(afterTask2.status, 'active')

  // Simulated restart: boot host B from a snapshot of host A's tables.
  const snapshot = {}
  for (const name of ['memories', 'retrievals', 'templates', 'skills']) {
    snapshot[name] = [...tablesA.get(name).entries()]
  }
  const { ctx: ctxB, tools: toolsB, tables: tablesB } = makeMockHost(snapshot)
  await apply(ctxB, config)

  // Task 3 in host B: the second distinct harmful task flips the status.
  const begin3 = await toolsB.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor('sb'))
  const retrieved3 = await toolsB.get('memory_retrieve').execute({ query: '重构解析函数', k: 4 }, execFor('sb'))
  assert.ok(retrieved3.memories.some(memory => memory.memoryId === memoryId))
  const harmful2 = await toolsB.get('memory_feedback').execute({
    memory_ids: [memoryId], rating: 'harmful', evidence: 'misled again',
  }, execFor('sb'))
  assert.equal(harmful2.updated, 1)
  await toolsB.get('memory_end_task').execute({ task_id: begin3.taskId }, execFor('sb'))
  const afterTask3 = tablesB.get('memories').get(memoryId)
  assert.deepEqual(afterTask3.harmfulTaskIds, [begin2.taskId, begin3.taskId])
  assert.equal(afterTask3.status, 'deprecated')

  // Deprecated memories stay retrievable with the flag and respect the
  // include_deprecated opt-out.
  const begin4 = await toolsB.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor('sb'))
  const withDeprecated = await toolsB.get('memory_retrieve').execute({ query: '重构解析函数', k: 4 }, execFor('sb'))
  const deprecatedRow = withDeprecated.memories.find(memory => memory.memoryId === memoryId)
  assert.ok(deprecatedRow)
  assert.equal(deprecatedRow.deprecated, true)
  const withoutDeprecated = await toolsB.get('memory_retrieve').execute(
    { query: '重构解析函数', k: 4, include_deprecated: false }, execFor('sb'),
  )
  assert.ok(!withoutDeprecated.memories.some(memory => memory.memoryId === memoryId))
  await toolsB.get('memory_end_task').execute({ task_id: begin4.taskId }, execFor('sb'))

  // The harmful-task window stays bounded at 8 entries. Boot a third host
  // seeded with a crowded record so it takes the normal index path.
  const crowded = { ...makeLegacyMemory('crowded-1', 'crowded-task'), harmfulTaskIds: ['t1', 't2', 't3', 't4', 't5', 't6', 't7', 't8'] }
  const snapshotC = {}
  for (const name of ['memories', 'retrievals', 'templates', 'skills']) {
    snapshotC[name] = [...tablesB.get(name).entries()]
  }
  snapshotC.memories.push(['crowded-1', crowded])
  const { ctx: ctxC, tools: toolsC, tables: tablesC } = makeMockHost(snapshotC)
  await apply(ctxC, config)
  const begin5 = await toolsC.get('memory_begin_task').execute({ task_desc: '重构解析函数', task_family: 'code' }, execFor('sc'))
  const retrieved5 = await toolsC.get('memory_retrieve').execute({ query: '重构解析函数', k: 20 }, execFor('sc'))
  assert.ok(retrieved5.memories.some(memory => memory.memoryId === 'crowded-1'))
  await toolsC.get('memory_feedback').execute({
    memory_ids: ['crowded-1'], rating: 'harmful', evidence: 'window check',
  }, execFor('sc'))
  const crowdedAfter = tablesC.get('memories').get('crowded-1')
  assert.equal(crowdedAfter.harmfulTaskIds.length, 8)
  assert.deepEqual(crowdedAfter.harmfulTaskIds, ['t2', 't3', 't4', 't5', 't6', 't7', 't8', begin5.taskId])
  assert.equal(crowdedAfter.status, 'deprecated')
  await toolsC.get('memory_end_task').execute({ task_id: begin5.taskId }, execFor('sc'))
})
