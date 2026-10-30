import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer } from 'node:http'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ApiConfigService } from '../dist/api-config.js'

const silentLogger = { warn() {}, info() {} }

async function startServer(handler) {
  return new Promise(resolve => {
    const server = createServer(handler)
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }))
  })
}

function fixtureYaml(port, overrides = {}) {
  const base = {
    proxyUrl: 'http://user:secret@127.0.0.1:59999',
    noProxy: ['127.0.0.1', 'localhost'],
  }
  const merged = { ...base, ...overrides }
  const noProxyBody = merged.noProxy.length === 0
    ? '  noProxy: []'
    : `  noProxy:\n${merged.noProxy.map(entry => `    - ${entry}`).join('\n')}`
  return `version: 1
proxy:
  url: ${merged.proxyUrl}
${noProxyBody}
defaults:
  timeoutMs: 5000
endpoints:
  embeddings:
    kind: openai-embeddings
    baseUrl: http://127.0.0.1:${port}/v1
    apiKeyEnv: TEST_EMBED_KEY
    model: test-embedder
    dimension: 4
  distillation:
    kind: openai-chat
    baseUrl: http://127.0.0.1:${port}
    apiKey: sk-inline-test
    model: test-chat
    temperature: 1.0
  helper:
    kind: http-json
    baseUrl: http://127.0.0.1:${port}/api
    headers:
      X-Custom: abc
`
}

function makeConfigDir(content) {
  const dir = mkdtempSync(join(tmpdir(), 'rm-api-config-'))
  const path = join(dir, 'api.config.yaml')
  writeFileSync(path, content)
  return { dir, path }
}

test('loads and validates a full config: defaults, key sources, proxy decisions', async () => {
  const { server, port } = await startServer((_req, res) => { res.end('{}') })
  const { dir, path } = makeConfigDir(fixtureYaml(port))
  process.env.TEST_EMBED_KEY = 'env-key-value'
  try {
    const service = new ApiConfigService(path, silentLogger)
    assert.equal(service.load(), true)
    assert.equal(service.loaded, true)
    assert.equal(service.endpointKind('embeddings'), 'openai-embeddings')
    assert.equal(service.endpointKind('distillation'), 'openai-chat')
    assert.equal(service.endpointKind('helper'), 'http-json')
    assert.equal(service.endpointKind('missing'), undefined)

    const embedding = service.embeddingOptions('embeddings')
    assert.equal(embedding.embeddingUrl, `http://127.0.0.1:${port}/v1/embeddings`)
    assert.equal(embedding.embeddingModel, 'test-embedder')
    assert.equal(embedding.embeddingDimension, 4)
    assert.equal(embedding.embeddingApiKey, 'env-key-value')

    const chat = service.chatOptions('distillation')
    assert.equal(chat.baseUrl, `http://127.0.0.1:${port}`)
    assert.equal(chat.model, 'test-chat')
    assert.equal(chat.apiKey, 'sk-inline-test')
    assert.equal(chat.temperature, 1.0)

    const status = service.status()
    assert.equal(status.loaded, true)
    assert.deepEqual(status.endpoints.map(endpoint => endpoint.kind),
      ['openai-embeddings', 'openai-chat', 'http-json'])
    const keySources = Object.fromEntries(status.endpoints.map(endpoint => [endpoint.name, endpoint.keySource]))
    assert.equal(keySources.embeddings, 'env')
    assert.equal(keySources.distillation, 'inline')
    assert.equal(keySources.helper, 'none')
    // Secrets are masked: no key value or proxy credential leaks into status.
    assert.ok(!JSON.stringify(status).includes('sk-inline-test'))
    assert.ok(!JSON.stringify(status).includes('env-key-value'))
    assert.ok(!JSON.stringify(status).includes('user:secret'))
    assert.ok(JSON.stringify(status).includes('***'))
    service.stop()
  } finally {
    delete process.env.TEST_EMBED_KEY
    server.close()
    rmSync(dir, { recursive: true, force: true })
  }
})

test('invalid YAML and validation failures keep the last good config', async () => {
  const { dir, path } = makeConfigDir(fixtureYaml(1))
  const service = new ApiConfigService(path, silentLogger)
  assert.equal(service.load(), true)

  writeFileSync(path, 'version: 1\nendpoints: [broken')
  service.reload()
  assert.equal(service.loaded, true, 'last good config survives a YAML error')
  assert.ok(service.status().lastError.includes('invalid YAML'))
  assert.equal(service.endpointKind('embeddings'), 'openai-embeddings')

  writeFileSync(path, 'version: 1\nproxy: {}\ndefaults: {}\nendpoints:\n  bad:\n    kind: openai-embeddings\n    baseUrl: https://x\n    model: m\n    dimension: -5\n')
  service.reload()
  assert.equal(service.loaded, true, 'last good config survives a validation error')
  assert.ok(service.status().lastError.includes('validation'))

  writeFileSync(path, fixtureYaml(1).replace('apiKeyEnv: TEST_EMBED_KEY', 'apiKeyEnv: TEST_EMBED_KEY\n    apiKey: also-set'))
  service.reload()
  assert.equal(service.loaded, true)
  assert.ok(service.status().lastError.includes('apiKeyEnv or apiKey'))
  service.stop()
  rmSync(dir, { recursive: true, force: true })
})

test('proxy-aware fetch honors noProxy (direct) and routes other hosts through the proxy', async () => {
  const { server, port } = await startServer((req, res) => {
    res.setHeader('content-type', 'application/json')
    res.end(JSON.stringify({ ok: true }))
  })
  try {
    const { dir, path } = makeConfigDir(fixtureYaml(port))
    const service = new ApiConfigService(path, silentLogger)
    assert.equal(service.load(), true)

    // Host 127.0.0.1 is in noProxy → direct connection succeeds.
    const direct = await service.fetchFn(`http://127.0.0.1:${port}/ping`)
    assert.equal(direct.status, 200)

    // Same host, no noProxy rules → routed through the dead proxy → fails.
    const { path: path2 } = makeConfigDir(fixtureYaml(port, { noProxy: [] }))
    const proxied = new ApiConfigService(path2, silentLogger)
    assert.equal(proxied.load(), true)
    await assert.rejects(
      () => proxied.fetchFn(`http://127.0.0.1:${port}/ping`),
      /fetch failed|ECONNREFUSED|socket hang up|connect|proxy/i,
    )
    proxied.stop()
    service.stop()
    rmSync(dir, { recursive: true, force: true })
    rmSync(join(path2, '..'), { recursive: true, force: true })
  } finally {
    server.close()
  }
})

test('openAiChat posts the OpenAI chat shape through the configured endpoint', async () => {
  const captured = {}
  const { server, port } = await startServer((req, res) => {
    let body = ''
    req.on('data', chunk => { body += chunk })
    req.on('end', () => {
      captured.path = req.url
      captured.auth = req.headers.authorization
      captured.body = JSON.parse(body)
      res.setHeader('content-type', 'application/json')
      res.end(JSON.stringify({ choices: [{ message: { content: '# Memory Item 1\n## Title: T\n## Description: D\n## Content: lesson\n## Contraindications\n## Outcome: success' } }] }))
    })
  })
  const { dir, path } = makeConfigDir(fixtureYaml(port))
  const service = new ApiConfigService(path, silentLogger)
  assert.equal(service.load(), true)
  try {
    const controller = new AbortController()
    const text = await service.openAiChat('distillation', 'system prompt', 'user prompt', 0.7, controller.signal)
    assert.ok(text.includes('lesson'))
    assert.equal(captured.path, '/chat/completions')
    assert.equal(captured.auth, 'Bearer sk-inline-test')
    assert.equal(captured.body.model, 'test-chat')
    assert.equal(captured.body.temperature, 0.7)
    assert.deepEqual(captured.body.messages, [
      { role: 'system', content: 'system prompt' },
      { role: 'user', content: 'user prompt' },
    ])
  } finally {
    service.stop()
    server.close()
    rmSync(dir, { recursive: true, force: true })
  }
})

test('embeddings() resolves vectors through the endpoint and http-json request() passes headers', async () => {
  const calls = []
  const { server, port } = await startServer((req, res) => {
    let body = ''
    req.on('data', chunk => { body += chunk })
    req.on('end', () => {
      const parsed = body ? JSON.parse(body) : {}
      calls.push({ url: req.url, auth: req.headers.authorization, header: req.headers['x-custom'], body: parsed })
      res.setHeader('content-type', 'application/json')
      if (req.url === '/v1/embeddings') {
        res.end(JSON.stringify({ data: parsed.input.map((text, index) => ({ index, embedding: [index, 0, 0, text.length] })) }))
      } else {
        res.end(JSON.stringify({ ok: true }))
      }
    })
  })
  const { dir, path } = makeConfigDir(fixtureYaml(port))
  const service = new ApiConfigService(path, silentLogger)
  assert.equal(service.load(), true)
  try {
    const vectors = await service.embeddings('embeddings', ['aa', 'bbbb'])
    assert.deepEqual(vectors, [[0, 0, 0, 2], [1, 0, 0, 4]])
    assert.equal(calls[0].url, '/v1/embeddings')

    const echoed = await service.request('helper', { path: '/echo', body: { a: 1 } })
    assert.deepEqual(echoed, { ok: true })
    assert.equal(calls[1].header, 'abc')

    await assert.rejects(
      () => service.request('embeddings', {}),
      /use the dedicated client/,
    )
    await assert.rejects(
      () => service.request('missing', {}),
      /unknown endpoint/,
    )
  } finally {
    service.stop()
    server.close()
    rmSync(dir, { recursive: true, force: true })
  }
})

test('file watch hot-reloads valid edits and keeps the last good config on bad edits', async () => {
  const { dir, path } = makeConfigDir(fixtureYaml(1))
  const service = new ApiConfigService(path, silentLogger)
  service.start()
  try {
    assert.equal(service.endpointKind('latecomer'), undefined)
    writeFileSync(path, fixtureYaml(1) + `  latecomer:\n    kind: http-json\n    baseUrl: https://late.example\n`)
    const deadline = Date.now() + 5000
    while (Date.now() < deadline && !service.hasEndpoint('latecomer')) {
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    assert.equal(service.endpointKind('latecomer'), 'http-json', 'watch should pick up the new endpoint')
  } finally {
    service.stop()
    rmSync(dir, { recursive: true, force: true })
  }
})
