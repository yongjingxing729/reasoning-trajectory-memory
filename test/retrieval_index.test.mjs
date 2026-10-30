import assert from 'node:assert/strict'
import test from 'node:test'
import { LexicalMemoryIndex } from '../dist/lexical-index.js'
import { QdrantMemoryIndex } from '../dist/vector-index.js'
import { EnterpriseRuleRag } from '../dist/enterprise-rules-rag.js'
import { distillWithLlm, parseMemoryItems } from '../dist/distillation-llm.js'

function memory(memoryId, outcome = 'success', taskId = 'old-task') {
  return {
    memoryId,
    outcome,
    semanticText: 'database schema migration',
    reasoning: 'validate the schema before applying a migration',
    content: '',
    preconditions: ['the deployment target is current'],
    structureSignature: ['search', 'verify', 'execute'],
    reasoningGraph: { version: 1, nodes: [], edges: [] },
    provenance: { taskId },
  }
}

test('BM25 candidate index ranks lexical matches, keeps both outcomes, and excludes the active task', () => {
  const index = new LexicalMemoryIndex([
    memory('success-1', 'success'),
    { ...memory('fail-1', 'fail'), semanticText: 'database migration skipped validation' },
    { ...memory('self', 'success', 'current-task'), semanticText: 'database schema migration validation' },
    { ...memory('unrelated', 'success'), semanticText: 'write a poem about winter' },
  ])

  const results = index.search('database schema migration validation', '', 'current-task', 5)

  assert.deepEqual(new Set(results.map(row => row.outcome)), new Set(['success', 'fail']))
  assert.ok(results.some(row => row.memoryId === 'success-1'))
  assert.ok(results.every(row => row.memoryId !== 'self'))
  assert.ok(results.find(row => row.memoryId === 'success-1').lexicalScore > 0)
  index.delete(['success-1'])
  assert.ok(index.search('database schema migration', '', '', 5).every(row => row.memoryId !== 'success-1'))
})

test('Qdrant adapter syncs content hashes and searches outcome-filtered vector candidates', async () => {
  const calls = []
  const fetchMock = async (input, init = {}) => {
    const url = String(input)
    const method = init.method ?? 'GET'
    calls.push({ url, method, body: init.body ? JSON.parse(init.body) : undefined })

    if (url.endsWith('/v1/embeddings')) {
      const body = JSON.parse(init.body)
      return Response.json({ data: body.input.map((_, index) => ({ index, embedding: [1, 0, 0] })) })
    }
    if (method === 'GET' && url.endsWith('/collections/reasoning_test')) {
      return new Response('{}', { status: 404 })
    }
    if (method === 'POST' && url.endsWith('/points/scroll')) {
      return Response.json({ result: { points: [], next_page_offset: null } })
    }
    if (method === 'POST' && url.endsWith('/points/query')) {
      const body = JSON.parse(init.body)
      const outcome = body.filter.must[0].match.value
      const points = outcome === 'success'
        ? [{ id: 'success-1', score: 0.94, payload: { memoryId: 'success-1', outcome } }]
        : []
      return Response.json({ result: { points } })
    }
    return Response.json({ result: { status: 'ok' } })
  }
  const index = new QdrantMemoryIndex({
    qdrantUrl: 'http://qdrant.local',
    collection: 'reasoning_test',
    embeddingUrl: 'http://embed.local/v1/embeddings',
    embeddingModel: 'test-embedder',
    embeddingDimension: 3,
    candidatePoolSize: 10,
  }, fetchMock)

  await index.synchronize([memory('success-1')])
  const candidates = await index.search('database migration', '', 'active-task')

  assert.deepEqual(candidates, [{ memoryId: 'success-1', outcome: 'success', vectorScore: 0.94 }])
  assert.ok(calls.some(call => call.method === 'PUT' && call.url.endsWith('/collections/reasoning_test')))
  assert.ok(calls.some(call => call.method === 'PUT' && call.url.endsWith('/points?wait=true')))
  const searches = calls.filter(call => call.url.endsWith('/points/query'))
  assert.equal(searches.length, 2)
  assert.ok(searches.every(call => call.body.filter.must_not[0].match.value === 'active-task'))
})

test('enterprise rule RAG pins the rule-set filter and rejects out-of-scope or expired payloads', async () => {
  const calls = []
  const now = Date.now()
  const valid = {
    ruleId: 'retention-7',
    ruleSetId: 'org-prod',
    title: 'Retention policy',
    text: 'Retain audit events for the configured legal period.',
    source: 'https://policy.example/rules/retention-7',
    version: '2026-04',
    priority: 90,
    validFrom: now - 60_000,
    validUntil: now + 60_000,
    status: 'active',
  }
  const fetchMock = async (input, init = {}) => {
    const url = String(input)
    const method = init.method ?? 'GET'
    calls.push({ url, method, headers: init.headers, body: init.body ? JSON.parse(init.body) : undefined })
    if (url.endsWith('/v1/embeddings')) {
      return Response.json({ data: [{ index: 0, embedding: [1, 0, 0] }] })
    }
    if (method === 'GET' && url.endsWith('/collections/enterprise_rules')) {
      return Response.json({ result: { config: { params: { vectors: { size: 3, distance: 'Cosine' } } } } })
    }
    if (method === 'POST' && url.endsWith('/points/query')) {
      return Response.json({ result: { points: [
        { id: 'rule-1', score: 0.91, payload: valid },
        { id: 'rule-2', score: 0.99, payload: { ...valid, ruleId: 'other-org', ruleSetId: 'org-other' } },
        { id: 'rule-3', score: 0.98, payload: { ...valid, ruleId: 'expired', validUntil: now - 1 } },
        { id: 'rule-4', score: 0.1, payload: { ...valid, ruleId: 'low-score' } },
      ] } })
    }
    return Response.json({ result: { status: 'ok' } })
  }
  const rag = new EnterpriseRuleRag({
    qdrantUrl: 'http://qdrant.local',
    collection: 'enterprise_rules',
    qdrantApiKey: 'rule-read-only-key',
    embeddingUrl: 'http://embed.local/v1/embeddings',
    embeddingModel: 'policy-embedder',
    embeddingApiKey: 'embed-key',
    embeddingDimension: 3,
    ruleSetId: 'org-prod',
    candidatePoolSize: 20,
    resultLimit: 5,
    scoreThreshold: 0.2,
  }, fetchMock)

  const rules = await rag.search('retain audit information')

  assert.deepEqual(rules.map(rule => rule.ruleId), ['retention-7'])
  const query = calls.find(call => call.url.endsWith('/points/query'))
  const must = query.body.filter.must
  assert.deepEqual(must[0], { key: 'ruleSetId', match: { value: 'org-prod' } })
  assert.deepEqual(must[1], { key: 'status', match: { value: 'active' } })
  assert.ok(must[2].range.lte >= now && must[2].range.lte - now < 5000)
  assert.ok(must[3].range.gte >= now && must[3].range.gte - now < 5000)
  assert.equal(query.headers['api-key'], 'rule-read-only-key')
  assert.ok(calls.every(call => call.method === 'GET'
    || call.url.endsWith('/points/query')
    || call.url.endsWith('/v1/embeddings')))
})

test('distillation parser extracts structured items, contraindications, and outcome labels', () => {
  const text = `Here is the analysis.

# Memory Item 1
## Title: Validate before publish
## Description: Applies before publishing generated artifacts
## Content: Run the schema validator against every generated artifact.
Also re-check after every edit.
## Contraindications
- Not for third-party sources
- Not for drafts
## Outcome: success

# Memory Item 2
## Title Avoided pitfall
## Description When forms are involved
## Content Do not submit before verifying the form state.
## Contraindications
## Outcome: fail
`

  const withoutOutcome = parseMemoryItems(text, false)
  assert.equal(withoutOutcome.length, 2)
  assert.equal(withoutOutcome[0].title, 'Validate before publish')
  assert.equal(withoutOutcome[0].outcome, 'success')
  assert.deepEqual(withoutOutcome[0].contraindications, ['Not for third-party sources', 'Not for drafts'])
  assert.ok(withoutOutcome[0].content.includes('re-check after every edit'))
  assert.equal(withoutOutcome[1].title, 'Avoided pitfall')
  assert.equal(withoutOutcome[1].outcome, 'fail')

  const strict = parseMemoryItems('# Memory Item 1\n## Content: no outcome here\n', true)
  assert.equal(strict.length, 0)
})

test('LLM distillation resolves the model, streams text, and parses items', async () => {
  const calls = []
  const generated = `# Memory Item 1
## Title: Filter first
## Description: Before searching
## Content: Filter the candidate set before running the search.
## Contraindications
## Outcome: success
`
  const llm = {
    async prepareCall(config, signal) {
      calls.push({ config, hasSignal: signal !== undefined })
      return {
        config: { provider: config.provider, model: config.model, temperature: config.temperature },
        stream(options) {
          calls.push({ streamOptions: options })
          assert.equal(options.system, 'system prompt')
          assert.equal(options.messages[0].role, 'user')
          assert.equal(options.messages[0].content[0].type, 'text')
          assert.equal(options.messages[0].content[0].text, 'user prompt')
          return (async function* () {
            yield { type: 'block-start', index: 0, blockType: 'text' }
            yield { type: 'text-delta', index: 0, text: generated.slice(0, 20) }
            yield { type: 'text-delta', index: 0, text: generated.slice(20) }
            yield { type: 'finish', reason: { kind: 'stop' } }
          })()
        },
      }
    },
  }
  const controller = new AbortController()
  const items = await distillWithLlm(
    llm,
    'test-provider',
    'test-model',
    'system prompt',
    'user prompt',
    1.0,
    controller.signal,
  )

  assert.equal(items.length, 1)
  assert.equal(items[0].title, 'Filter first')
  assert.equal(items[0].outcome, 'success')
  assert.equal(calls[0].config.temperature, 1.0)
  assert.equal(calls[1].streamOptions.temperature, 1.0)
  assert.equal(calls[1].streamOptions.model, 'test-model')
})
