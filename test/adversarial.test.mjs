// T-debug-2 adversarial regressions for the v0.4.0 fix round.
//
// Covers the adjudicated fixes end-to-end where they are observable from the
// plugin surface: B20 (CJK trigger phrasing), B24 (Python version gate),
// B25 (noProxy host matching), P13 (builtin-template seeding retry plus the
// empty-library plan form), P14 (publish name follows the tool description),
// and P16 (deprecated memories leave the pool before quota selection, with
// the deprecatedFilteredCount audit field). All tests are deterministic and
// network-free; the Python worker runs through the sandbox file-io fallback
// where pipes are unavailable.
import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const dist = name => pathToFileURL(fileURLToPath(new URL(`../dist/${name}`, import.meta.url))).href
const bundleEntry = process.env.PLUGIN_BUNDLE_ENTRY
  ? fileURLToPath(new URL(`file://${process.env.PLUGIN_BUNDLE_ENTRY}`))
  : fileURLToPath(new URL('../dist/index.js', import.meta.url))
const { apply } = await import(pathToFileURL(bundleEntry).href)
const { validateSkillInput } = await import(dist('skill-format.js'))
const { hostMatchesRule } = await import(dist('api-config.js'))
const { checkPythonVersion } = await import(dist('python.js'))

function memory(memoryId, overrides = {}) {
  return {
    memoryId,
    type: 'strategy',
    outcome: 'success',
    taskScope: 'code-review',
    preconditions: ['the deployment target is current'],
    content: '调用者提炼的可迁移推理策略（未独立验证）：validate the schema before applying a migration。',
    reasoning: 'validate the schema before applying a migration',
    title: 'Schema first',
    description: 'Applies before migrations',
    contraindications: [],
    reasoningGraph: { version: 1, nodes: [], edges: [] },
    feedback: { helpful: 0, harmful: 0, neutral: 0 },
    provenance: { taskId: 'seed-task', sourceSessionId: 'seed-session', eventSeqs: [1, 2] },
    evidence: { verifier: 'unit-suite', summary: 'passed', independentlyVerified: false },
    confidence: 0.55,
    trustLevel: 'unverified',
    extractorVersion: 'dsh-reasoning-memory/0.4.0',
    createdAt: 1,
    updatedAt: 1,
    semanticText: 'database schema migration',
    structureSignature: ['search', 'execute', 'verify'],
    extractionMethod: 'caller_insight',
    distillationModel: '',
    sourceMemoryIds: [],
    strategyTemplateId: '',
    reasoningLevel: 'path',
    insightConfidence: 0,
    status: 'active',
    ...overrides,
  }
}

function makeMockHost(seedTables = {}, options = {}) {
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
      put: async (key, value) => {
        if (name === 'templates' && options.hostileTemplates?.() && String(key).startsWith('memory_tpl_builtin_')) {
          throw new Error('simulated template table write failure')
        }
        rows.set(key, value)
      },
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
  return { agent: { session: { header: { id: sessionId } } }, signal: new AbortController().signal }
}

function emit(listeners, sessionId, seq, type, data) {
  const session = { header: { id: sessionId } }
  for (const listener of listeners.get('session/event') ?? []) {
    listener(session, { seq, type, data })
  }
}

const pythonExecutable = process.platform === 'win32' ? 'python' : 'python3'

// B20: bilingual trigger phrasing. The CJK tokens must match outside the
// word-boundary group so a pure-Chinese description passes the skill guard.
test('skill guard accepts Chinese trigger phrasing and still bans em-dashes', () => {
  const validInput = {
    name: 'schema-check',
    description: '当任务涉及发布产物时使用本技能',
    body: 'Step 1. Validate.',
    resources: [],
    sourceMemoryIds: [],
  }
  assert.deepEqual(validateSkillInput(validInput), [])
  const stillBanned = validateSkillInput({ ...validInput, description: '当任务涉及发布产物时，使用本技能——谨慎执行' })
  assert.ok(stillBanned.some(issue => issue.field === 'description' && /em-dash/.test(issue.reason)))
})

// B25: noProxy host matching is whole-host equality plus suffix wildcards;
// substring rules no longer over-match.
test('noProxy rules require whole-host equality and keep suffix wildcards', () => {
  assert.equal(hostMatchesRule('127.0.0.1', '127.0.0.1'), true)
  assert.equal(hostMatchesRule('localhost', 'localhost'), true)
  assert.equal(hostMatchesRule('badexample.com', 'example.com'), false)
  assert.equal(hostMatchesRule('1.2.3.4', '1.2'), false)
  assert.equal(hostMatchesRule('example.com', 'example.com'), true)
  assert.equal(hostMatchesRule('sub.example.com', '.example.com'), true)
  assert.equal(hostMatchesRule('sub.example.com', '*.example.com'), true)
  assert.equal(hostMatchesRule('badexample.com', '.example.com'), false)
  assert.equal(hostMatchesRule('example.com', ''), false)
})

// B24: the startup version gate reports readable outcomes without capturing
// stdout (works in sandboxes that deny pipe stdio).
test('python version gate accepts the interpreter and reports readable failures', () => {
  const ok = checkPythonVersion(pythonExecutable)
  assert.equal(ok.ok, true, ok.message)
  const missing = checkPythonVersion('definitely-missing-python-interpreter-xyz')
  assert.equal(missing.ok, false)
  assert.match(missing.message, /cannot run the configured python executable/)
})

// P13 + empty-library plan form: a failing template-table write blocks the
// startup seeding, memory_plan_task must still answer readably with an empty
// library, and once the table recovers the next template call retries the
// seeding (the failed promise is not cached).
test('seeding failure degrades readably and the next template call retries', { timeout: 60_000 }, async () => {
  let hostile = true
  const { ctx, tools, tables } = makeMockHost({}, { hostileTemplates: () => hostile })
  await apply(ctx, {
    pythonExecutable,
    maxMemories: 50, maxRetrievalRecords: 50, maxTrajectoryEvents: 1000, positiveNegativeRatio: 1,
    vectorIndexEnabled: false, qdrantUrl: 'http://127.0.0.1:6333', qdrantCollection: 'reasoning_memories',
    qdrantApiKeyEnv: 'QDRANT_API_KEY', embeddingUrl: '', embeddingModel: 'unused', embeddingDimension: 8,
    embeddingApiKeyEnv: 'OPENAI_API_KEY', retrievalCandidatePoolSize: 100, vectorCandidatePoolSize: 100,
    enterpriseRuleRagEnabled: false, enterpriseRuleRagRequired: false,
    enterpriseRuleCollection: 'enterprise_reasoning_rules', enterpriseRuleSetId: '',
    enterpriseRuleCandidatePoolSize: 20, enterpriseRuleResultLimit: 5, enterpriseRuleScoreThreshold: 0.3,
    enterpriseRuleEmbeddingUrl: '', enterpriseRuleEmbeddingModel: '', enterpriseRuleEmbeddingDimension: 0,
    enterpriseRuleQdrantApiKeyEnv: 'ENTERPRISE_RULES_QDRANT_API_KEY',
    enterpriseRuleEmbeddingApiKeyEnv: 'ENTERPRISE_RULES_EMBEDDING_API_KEY',
    captureReasoningEnabled: true, maxReasoningTextChars: 4000, llmDistillationEnabled: false,
    llmDistillationTemperature: 1, distillationProvider: '', distillationModel: '',
    maxMemoryItemsPerOutcome: 3, deSpecificationThreshold: 0.9, relevanceThreshold: 0,
    memoryConsolidationEnabled: false, builtinStoreEnabled: false, builtinStoreDataDir: '',
    apiConfigPath: '', skillsExportDir: '', minInsightConfidence: 0, deprecatedAfterHarmfulFeedback: 0,
    templateMatchMin: 0.55,
  })

  // The startup seed failed (hostile table); plan still answers readably with
  // an empty template library instead of throwing.
  const plan = await tools.get('memory_plan_task').execute(
    { task_desc: 'refactor the parser', task_family: 'code' }, execFor('s1'),
  )
  assert.ok(['multi_hop_qa', 'code_synthesis', 'tool_orchestration', 'gui_operation', 'long_horizon_planning', 'numeric_search', 'general'].includes(plan.pattern))
  assert.equal(plan.matchedTemplates.length, 0)
  assert.match(plan.note, /general mode/i)

  // Table recovers: the next template call retries the seeding.
  hostile = false
  const listed = await tools.get('memory_list_templates').execute({}, execFor('s1'))
  assert.equal(listed.count, 5)
  assert.ok(tables.get('templates').size >= 5)
})

// P14 + P16 integration: publishing a name that follows the tool description
// succeeds; deprecated memories leave the pool before quota selection when
// include_deprecated is false and the audit records deprecatedFilteredCount.
test('publish name follows the description and deprecated filtering precedes the quota', { timeout: 60_000 }, async () => {
  const active = memory('active-1')
  const deprecated = memory('deprecated-1', { status: 'deprecated', harmfulTaskIds: ['t1'] })
  const { ctx, tools, tables, listeners } = makeMockHost({
    memories: [['active-1', active], ['deprecated-1', deprecated]],
  })
  await apply(ctx, {
    pythonExecutable,
    maxMemories: 50, maxRetrievalRecords: 50, maxTrajectoryEvents: 1000, positiveNegativeRatio: 1,
    vectorIndexEnabled: false, qdrantUrl: 'http://127.0.0.1:6333', qdrantCollection: 'reasoning_memories',
    qdrantApiKeyEnv: 'QDRANT_API_KEY', embeddingUrl: '', embeddingModel: 'unused', embeddingDimension: 8,
    embeddingApiKeyEnv: 'OPENAI_API_KEY', retrievalCandidatePoolSize: 100, vectorCandidatePoolSize: 100,
    enterpriseRuleRagEnabled: false, enterpriseRuleRagRequired: false,
    enterpriseRuleCollection: 'enterprise_reasoning_rules', enterpriseRuleSetId: '',
    enterpriseRuleCandidatePoolSize: 20, enterpriseRuleResultLimit: 5, enterpriseRuleScoreThreshold: 0.3,
    enterpriseRuleEmbeddingUrl: '', enterpriseRuleEmbeddingModel: '', enterpriseRuleEmbeddingDimension: 0,
    enterpriseRuleQdrantApiKeyEnv: 'ENTERPRISE_RULES_QDRANT_API_KEY',
    enterpriseRuleEmbeddingApiKeyEnv: 'ENTERPRISE_RULES_EMBEDDING_API_KEY',
    captureReasoningEnabled: true, maxReasoningTextChars: 4000, llmDistillationEnabled: false,
    llmDistillationTemperature: 1, distillationProvider: '', distillationModel: '',
    maxMemoryItemsPerOutcome: 3, deSpecificationThreshold: 0.9, relevanceThreshold: 0,
    memoryConsolidationEnabled: false, builtinStoreEnabled: false, builtinStoreDataDir: '',
    apiConfigPath: '', skillsExportDir: '', minInsightConfidence: 0, deprecatedAfterHarmfulFeedback: 0,
    templateMatchMin: 0.55,
  })

  // P14: a name written exactly as the tool description prescribes passes.
  const published = await tools.get('memory_publish_template').execute({
    name: 'memory_tpl_validate-before-publish',
    description: 'Use when a task publishes generated artifacts',
    task_patterns: ['general'],
    steps: [{ index: 1, title: 'validate', module_demands: [{ kind: 'verify', intent: '' }] }],
  }, execFor('s1'))
  assert.equal(published.published, true, published.note)

  await tools.get('memory_begin_task').execute({ task_desc: 'database schema migration' }, execFor('s1'))
  emit(listeners, 's1', 1, 'tool/call', { name: 'bash', callId: 'c1' })
  emit(listeners, 's1', 2, 'tool/result', { message: { source: { callId: 'c1' } } })

  // Default: both memories participate; the deprecated one carries the flag.
  const withDeprecated = await tools.get('memory_retrieve').execute({ query: 'database schema migration', k: 4 }, execFor('s1'))
  assert.ok(withDeprecated.memories.some(row => row.memoryId === 'deprecated-1'))
  assert.equal(withDeprecated.memories.find(row => row.memoryId === 'deprecated-1').deprecated, true)

  // Opt-out: the deprecated memory leaves the pool BEFORE quota selection and
  // the audit records the filtered count.
  const withoutDeprecated = await tools.get('memory_retrieve').execute(
    { query: 'database schema migration', k: 4, include_deprecated: false }, execFor('s1'),
  )
  assert.ok(!withoutDeprecated.memories.some(row => row.memoryId === 'deprecated-1'))
  const audits = [...tables.get('retrievals').entries()].map(([, row]) => row)
  const lastAudit = audits[audits.length - 1]
  assert.equal(lastAudit.deprecatedFilteredCount, 1)
  // The quota counts match the returned rows now that filtering precedes it.
  assert.equal(withoutDeprecated.balance.positiveCount, withoutDeprecated.memories.filter(row => row.outcome === 'success').length)
  assert.ok(withoutDeprecated.note.includes('excluded before quota selection'))
})

// T-rag-debug R1: a document query with zero text overlap returns empty
// results (never full-score rows manufactured from pattern/link alone), and
// the tool note says so instead of presenting fabricated matches.
test('document retrieval with zero text signal returns empty with a readable note', { timeout: 60_000 }, async () => {
  const { ctx, tools, tables } = makeMockHost()
  await apply(ctx, {
    pythonExecutable,
    maxMemories: 50, maxRetrievalRecords: 50, maxTrajectoryEvents: 1000, positiveNegativeRatio: 1,
    vectorIndexEnabled: false, qdrantUrl: 'http://127.0.0.1:6333', qdrantCollection: 'reasoning_memories',
    qdrantApiKeyEnv: 'QDRANT_API_KEY', embeddingUrl: '', embeddingModel: 'unused', embeddingDimension: 8,
    embeddingApiKeyEnv: 'OPENAI_API_KEY', retrievalCandidatePoolSize: 100, vectorCandidatePoolSize: 100,
    enterpriseRuleRagEnabled: false, enterpriseRuleRagRequired: false,
    enterpriseRuleCollection: 'enterprise_reasoning_rules', enterpriseRuleSetId: '',
    enterpriseRuleCandidatePoolSize: 20, enterpriseRuleResultLimit: 5, enterpriseRuleScoreThreshold: 0.3,
    enterpriseRuleEmbeddingUrl: '', enterpriseRuleEmbeddingModel: '', enterpriseRuleEmbeddingDimension: 0,
    enterpriseRuleQdrantApiKeyEnv: 'ENTERPRISE_RULES_QDRANT_API_KEY',
    enterpriseRuleEmbeddingApiKeyEnv: 'ENTERPRISE_RULES_EMBEDDING_API_KEY',
    captureReasoningEnabled: true, maxReasoningTextChars: 4000, llmDistillationEnabled: false,
    llmDistillationTemperature: 1, distillationProvider: '', distillationModel: '',
    maxMemoryItemsPerOutcome: 3, deSpecificationThreshold: 0.9, relevanceThreshold: 0,
    memoryConsolidationEnabled: false, builtinStoreEnabled: false, builtinStoreDataDir: '',
    apiConfigPath: '', skillsExportDir: '', minInsightConfidence: 0, deprecatedAfterHarmfulFeedback: 0,
    templateMatchMin: 0.55,
  })

  const imported = await tools.get('memory_import_document').execute({
    name: 'memory_doc_standards',
    content: '# Code Standards\n\nEvery function name uses snake_case.\n',
    task_patterns: ['code_synthesis'],
  }, execFor('s1'))
  assert.equal(imported.imported, true)

  // A query with no token overlap with the corpus must yield an empty list,
  // not chunks carrying a perfect score.
  const miss = await tools.get('memory_retrieve_document').execute(
    { query: 'zzzz qqqq wwww', k: 4 }, execFor('s1'),
  )
  assert.deepEqual(miss.documents, [])
  assert.ok(miss.note.includes('No document chunk matched'))

  // The audit row for the document retrieval stays parseable and isolated
  // from the memory quota fields.
  const audits = [...tables.get('retrievals').entries()].map(([, row]) => row)
  const documentAudit = audits.find(row => row.route === 'document')
  assert.ok(documentAudit)
  assert.deepEqual(documentAudit.selectedMemoryIds, [])
  assert.deepEqual(documentAudit.documentIds, [])

  const hit = await tools.get('memory_retrieve_document').execute(
    { query: 'snake case function naming', k: 4 }, execFor('s1'),
  )
  assert.equal(hit.documents.length >= 1, true)
  assert.equal(hit.documents[0].docName, 'memory_doc_standards')
})
