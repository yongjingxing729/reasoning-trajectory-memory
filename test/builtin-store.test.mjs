import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BuiltinMemoryStore } from '../dist/builtin-store.js'

function memory(memoryId, overrides = {}) {
  const base = {
    memoryId,
    type: 'strategy',
    outcome: 'success',
    taskScope: 'code-review',
    preconditions: ['the deployment target is current'],
    content: '调用者提炼的可迁移推理策略（未独立验证）：validate the schema before applying a migration。',
    reasoning: 'validate the schema before applying a migration',
    title: 'Schema first',
    description: 'Applies before migrations',
    contraindications: ['skip for read-only checks'],
    reasoningGraph: { version: 1, nodes: [], edges: [] },
    feedback: { helpful: 0, harmful: 0, neutral: 0 },
    provenance: { taskId: 'task-a', sourceSessionId: 'session-1', eventSeqs: [1, 2] },
    evidence: { verifier: 'unit-suite', summary: 'passed', independentlyVerified: false },
    confidence: 0.55,
    trustLevel: 'unverified',
    extractorVersion: 'dsh-reasoning-memory/0.3.0',
    createdAt: 1000,
    updatedAt: 1000,
    semanticText: 'database schema migration',
    structureSignature: ['search', 'execute', 'verify'],
    extractionMethod: 'caller_insight',
    distillationModel: '',
    sourceMemoryIds: [],
  }
  return { ...base, ...overrides }
}

// Semantic-ish deterministic embedder: bag-of-token hashing into the vector
// dimensions, so texts sharing tokens score closer than unrelated texts.
function mockEmbed(text) {
  const dim = 4
  const vec = new Array(dim).fill(0)
  const tokens = text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []
  for (const token of tokens) {
    let hash = 0
    for (const ch of token) hash = (hash * 31 + ch.codePointAt(0)) >>> 0
    vec[hash % dim] += 1
  }
  const norm = Math.hypot(...vec) || 1
  return vec.map(value => value / norm)
}

function embeddingFetch() {
  const calls = []
  const fetcher = async (input, init = {}) => {
    if (!String(input).includes('/v1/embeddings')) throw new Error('unexpected fetch: ' + input)
    const body = JSON.parse(init.body)
    calls.push(body.input)
    return Response.json({ data: body.input.map((text, index) => ({ index, embedding: mockEmbed(text) })) })
  }
  return { calls, fetcher }
}

function storeOptions(overrides = {}) {
  return {
    embeddingUrl: 'http://embed.local/v1/embeddings',
    embeddingModel: 'test-embedder',
    embeddingDimension: 4,
    candidatePoolSize: 5,
    ...overrides,
  }
}

test('built-in store syncs structured JSON rows with dual hashes and skips unchanged rows', async () => {
  const { calls, fetcher } = embeddingFetch()
  const store = new BuiltinMemoryStore(storeOptions(), fetcher)
  const items = [
    memory('m1'),
    { ...memory('m2'), outcome: 'fail', type: 'failure', trustLevel: 'unknown', createdAt: 2000, updatedAt: 2000 },
  ]
  const first = await store.synchronize(items)
  assert.equal(first.synced, 2)
  assert.equal(first.embedded, 2)
  assert.equal(first.hasEmbeddings, true)
  const embedCallsAfterFirstSync = calls.length

  const rows = await store.queryStructured({ limit: 20, offset: 0, sortBy: 'created_at', sortOrder: 'desc' })
  assert.equal(rows.length, 2)
  assert.equal(rows[0].memoryId, 'm2')
  assert.equal(rows[0].outcome, 'fail')
  assert.equal(rows[0].trustLevel, 'unknown')
  assert.deepEqual(rows[0].contraindications, ['skip for read-only checks'])
  assert.equal(rows[0].provenance.taskId, 'task-a')
  assert.equal(rows[0].evidence.verifier, 'unit-suite')
  assert.deepEqual(rows[0].structureSignature, ['search', 'execute', 'verify'])

  // Identical re-sync: nothing re-written, nothing re-embedded.
  const second = await store.synchronize(items)
  assert.equal(second.synced, 0)
  assert.equal(second.embedded, 0)
  assert.equal(calls.length, embedCallsAfterFirstSync)

  // Feedback-only change: row refreshes but does NOT re-embed.
  const withFeedback = { ...items[0], feedback: { helpful: 1, harmful: 0, neutral: 0 } }
  const third = await store.synchronize([withFeedback, items[1]])
  assert.equal(third.synced, 1)
  assert.equal(third.embedded, 0)

  // Reasoning change: row refreshes AND re-embeds.
  const withReasoning = { ...withFeedback, reasoning: 'verify constraints before executing anything' }
  const fourth = await store.synchronize([withReasoning, items[1]])
  assert.equal(fourth.synced, 1)
  assert.equal(fourth.embedded, 1)

  await store.close()
})

test('built-in store runs per-outcome pgvector search and excludes the active task', async () => {
  const { fetcher } = embeddingFetch()
  const store = new BuiltinMemoryStore(storeOptions({ candidatePoolSize: 3 }), fetcher)
  await store.synchronize([
    memory('self', { provenance: { taskId: 'active-task', sourceSessionId: 's', eventSeqs: [] } }),
    memory('good', { reasoning: 'validate the schema before applying a migration' }),
    memory('noise', { reasoning: 'prefer quiet interfaces over decoration', semanticText: 'unrelated' }),
    { ...memory('bad'), outcome: 'fail', reasoning: 'deploy without verifying the schema' },
  ])

  const results = await store.search('validate schema before migration', '', 'active-task')

  assert.ok(results.every(candidate => candidate.memoryId !== 'self'))
  const ids = new Set(results.map(candidate => candidate.memoryId))
  assert.ok(ids.has('good'))
  assert.ok(ids.has('bad'))
  assert.ok(results.every(candidate => candidate.vectorScore >= 0 && candidate.vectorScore <= 1))
  const outcomes = new Set(results.map(candidate => candidate.outcome))
  assert.deepEqual(outcomes, new Set(['success', 'fail']))
  const good = results.find(candidate => candidate.memoryId === 'good')
  const noise = results.find(candidate => candidate.memoryId === 'noise')
  assert.ok(good && noise && good.vectorScore > noise.vectorScore)
  await store.close()
})

test('built-in store works json-only without an embedding endpoint', async () => {
  const store = new BuiltinMemoryStore(storeOptions({ embeddingUrl: '' }))
  const result = await store.synchronize([memory('m1')])
  assert.equal(result.hasEmbeddings, false)
  assert.equal(result.embedded, 0)
  const rows = await store.queryStructured({ limit: 10, offset: 0, sortBy: 'created_at', sortOrder: 'desc' })
  assert.equal(rows.length, 1)
  assert.deepEqual(await store.search('anything', '', ''), [])
  await store.close()
})

test('embedding endpoint failure keeps structured rows and degrades to json-only', async () => {
  const failing = async () => { throw new Error('embedding down') }
  const store = new BuiltinMemoryStore(storeOptions(), failing)
  const result = await store.synchronize([memory('m1'), memory('m2')])
  assert.equal(result.hasEmbeddings, false)
  assert.equal(result.embedded, 0)
  const rows = await store.queryStructured({ limit: 10, offset: 0, sortBy: 'created_at', sortOrder: 'desc' })
  assert.equal(rows.length, 2)
  assert.deepEqual(await store.search('anything', '', ''), [])
  // A later write-through also survives without embeddings.
  await store.upsert(memory('m3'))
  const after = await store.queryStructured({ limit: 10, offset: 0, sortBy: 'created_at', sortOrder: 'desc' })
  assert.equal(after.length, 3)
  await store.close()
})

test('built-in store structured filters: outcome, type, trust, scope substring, limit/offset, sort', async () => {
  const { fetcher } = embeddingFetch()
  const store = new BuiltinMemoryStore(storeOptions(), fetcher)
  await store.synchronize([
    memory('a', { createdAt: 100, confidence: 0.2, taskScope: 'web-navigation' }),
    memory('b', { createdAt: 200, confidence: 0.9, type: 'failure', outcome: 'fail', taskScope: 'web-navigation' }),
    memory('c', { createdAt: 300, confidence: 0.5, taskScope: 'code-review' }),
  ])

  const byOutcome = await store.queryStructured({ outcome: 'fail', limit: 10, offset: 0, sortBy: 'created_at', sortOrder: 'desc' })
  assert.deepEqual(byOutcome.map(row => row.memoryId), ['b'])
  const byScope = await store.queryStructured({ taskScope: 'navigation', limit: 10, offset: 0, sortBy: 'created_at', sortOrder: 'desc' })
  assert.deepEqual(byScope.map(row => row.memoryId), ['b', 'a'])
  const byType = await store.queryStructured({ type: 'strategy', limit: 10, offset: 0, sortBy: 'created_at', sortOrder: 'asc' })
  assert.deepEqual(byType.map(row => row.memoryId), ['a', 'c'])
  const byConfidence = await store.queryStructured({ limit: 10, offset: 0, sortBy: 'confidence', sortOrder: 'desc' })
  assert.deepEqual(byConfidence.map(row => row.memoryId), ['b', 'c', 'a'])
  const paged = await store.queryStructured({ limit: 1, offset: 1, sortBy: 'created_at', sortOrder: 'asc' })
  assert.deepEqual(paged.map(row => row.memoryId), ['b'])
  await store.close()
})

test('built-in store persists across reopen and removes stale rows on sync', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'rm-store-test-'))
  try {
    const { calls, fetcher } = embeddingFetch()
    const store = new BuiltinMemoryStore(storeOptions({ dataDir: dir }), fetcher)
    await store.synchronize([memory('m1'), memory('m2')])
    await store.close()

    // Reopen with the SAME data: dual-hash sync skips every row.
    const reopened = new BuiltinMemoryStore(storeOptions({ dataDir: dir }), fetcher)
    const result = await reopened.synchronize([memory('m1')])
    assert.equal(result.synced, 0)
    assert.equal(result.staleRemoved, 1)
    assert.equal(calls.length, 1) // only the first open embedded anything (one batch call)
    const rows = await reopened.queryStructured({ limit: 10, offset: 0, sortBy: 'created_at', sortOrder: 'desc' })
    assert.deepEqual(rows.map(row => row.memoryId), ['m1'])
    await reopened.close()
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('built-in store delete removes rows', async () => {
  const { fetcher } = embeddingFetch()
  const store = new BuiltinMemoryStore(storeOptions(), fetcher)
  await store.synchronize([memory('m1'), memory('m2')])
  await store.delete(['m1'])
  const rows = await store.queryStructured({ limit: 10, offset: 0, sortBy: 'created_at', sortOrder: 'desc' })
  assert.deepEqual(rows.map(row => row.memoryId), ['m2'])
  await store.close()
})
