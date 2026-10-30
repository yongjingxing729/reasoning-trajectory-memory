import { readFileSync, watch, type FSWatcher } from 'node:fs'
import { dirname, basename } from 'node:path'
import { parse } from 'yaml'
import { fetch as undiciFetch, ProxyAgent } from 'undici'
import z from 'zod'
import { EmbeddingClient, type EmbeddingClientOptions } from './embedding-client.js'

// User-editable API endpoint configuration.
//
// A local YAML file (pointed to by the profile option `apiConfigPath`) declares
// named API endpoints the plugin may call. Each endpoint declares the contract
// it satisfies (`kind`) so the plugin can validate that a feature's request
// matches the endpoint the user actually configured. Keys come from
// environment variables (preferred) or an inline value (plaintext, flagged in
// status). A global proxy plus per-endpoint overrides make foreign endpoints
// reachable; Node's fetch does not read the system proxy, so outbound calls
// route through an undici ProxyAgent built from this file.
//
// Reload semantics: the file is watched and re-validated on change; an invalid
// file keeps the last good configuration and records the error. Proxy and
// no-proxy changes hot-apply to every request. Endpoint model/dimension
// changes require a plugin restart (the pgvector table dimension and the
// store's sync hashes are bound at startup).

export const API_CONFIG_VERSION = 1

const endpointNameSchema = z.string().min(1).max(64).regex(/^[a-zA-Z0-9._-]+$/, 'endpoint names allow letters, digits, dot, underscore and dash')

const proxySchema = z.object({
  url: z.string().default(''),
  noProxy: z.array(z.string().min(1)).default([]),
}).default({ url: '', noProxy: [] })

const defaultsSchema = z.object({
  timeoutMs: z.number().int().positive().max(300_000).default(30_000),
}).default({ timeoutMs: 30_000 })

const endpointBaseSchema = z.object({
  kind: z.enum(['openai-embeddings', 'openai-chat', 'http-json']),
  baseUrl: z.string().min(1),
  apiKeyEnv: z.string().default(''),
  apiKey: z.string().default(''),
  headers: z.record(z.string().min(1), z.string()).default({}),
  // '' inherits the global proxy, 'direct' bypasses it, anything else is a URL.
  proxy: z.string().default(''),
  timeoutMs: z.number().int().positive().max(300_000).optional(),
}).superRefine((value, context) => {
  if (value.apiKeyEnv && value.apiKey) {
    context.addIssue({
      code: 'custom',
      message: "set either apiKeyEnv or apiKey, not both",
    })
  }
})

const embeddingsEndpointSchema = endpointBaseSchema.safeExtend({
  kind: z.literal('openai-embeddings'),
  model: z.string().min(1),
  dimension: z.number().int().positive(),
})

const chatEndpointSchema = endpointBaseSchema.safeExtend({
  kind: z.literal('openai-chat'),
  model: z.string().min(1),
  temperature: z.number().min(0).max(2).optional(),
})

const httpEndpointSchema = endpointBaseSchema.safeExtend({
  kind: z.literal('http-json'),
})

const endpointSchema = z.union([embeddingsEndpointSchema, chatEndpointSchema, httpEndpointSchema])

export const apiConfigFileSchema = z.object({
  version: z.literal(API_CONFIG_VERSION),
  proxy: proxySchema,
  defaults: defaultsSchema,
  endpoints: z.record(endpointNameSchema, endpointSchema).default({}),
})

export type ApiEndpointKind = z.infer<typeof endpointBaseSchema>['kind']
export type ApiEndpoint = z.infer<typeof endpointSchema>
export type ApiConfigFile = z.infer<typeof apiConfigFileSchema>
export type ApiProxy = z.infer<typeof proxySchema>

export interface ApiStatusView {
  path: string
  loaded: boolean
  lastError: string
  proxy: { url: string; noProxy: string[] }
  endpoints: Array<{
    name: string
    kind: ApiEndpointKind
    baseUrl: string
    model?: string
    dimension?: number
    temperature?: number
    keySource: 'env' | 'inline' | 'none'
    proxy: 'inherit' | 'direct' | 'custom'
  }>
}

type Logger = { warn(message: string): void; info(message: string): void }

const WATCH_DEBOUNCE_MS = 300

function redactUrl(raw: string): string {
  try {
    const url = new URL(raw)
    if (url.username || url.password) {
      url.username = url.username ? '***' : ''
      url.password = url.password ? '***' : ''
    }
    return url.toString().replace(/\/$/, '')
  } catch {
    return raw ? '***' : ''
  }
}

function normalizeBaseUrl(raw: string): string {
  return raw.trim().replace(/\/+$/, '')
}

function hostMatchesRule(host: string, rule: string): boolean {
  const normalized = rule.trim().toLowerCase()
  if (!normalized) return false
  const candidate = host.toLowerCase()
  if (candidate === normalized) return true
  if (normalized.startsWith('.')) return candidate.endsWith(normalized)
  if (normalized.startsWith('*.')) return candidate.endsWith(normalized.slice(1))
  return candidate.includes(normalized)
}

export class ApiConfigService {
  private config: ApiConfigFile | undefined
  private lastError = ''
  private watcher: FSWatcher | undefined
  private reloadTimer: ReturnType<typeof setTimeout> | undefined
  private readonly proxyAgents = new Map<string, ProxyAgent>()

  constructor(
    readonly path: string,
    private readonly logger: Logger,
  ) {}

  // Synchronously read, parse, and validate the file. On any failure the
  // previous good configuration is kept and lastError records the cause.
  load(): boolean {
    let raw = ''
    try {
      raw = readFileSync(this.path, 'utf8')
    } catch (error) {
      this.lastError = `cannot read config file: ${error instanceof Error ? error.message : String(error)}`
      return false
    }
    let parsed: unknown
    try {
      parsed = parse(raw)
    } catch (error) {
      this.lastError = `invalid YAML: ${error instanceof Error ? error.message : String(error)}`
      return false
    }
    const result = apiConfigFileSchema.safeParse(parsed)
    if (!result.success) {
      const detail = result.error.issues
        .slice(0, 3)
        .map(issue => `${issue.path.join('.') || 'root'}: ${issue.message}`)
        .join('; ')
      this.lastError = `config validation failed: ${detail}`
      return false
    }
    this.config = result.data
    this.lastError = ''
    this.logger.info(`reasoning-memory: loaded API config from ${this.path} (${Object.keys(result.data.endpoints).length} endpoint(s))`)
    return true
  }

  start(): void {
    this.load()
    const directory = dirname(this.path)
    const file = basename(this.path)
    try {
      this.watcher = watch(directory, (_event, filename) => {
        if (filename !== file) return
        if (this.reloadTimer) clearTimeout(this.reloadTimer)
        this.reloadTimer = setTimeout(() => this.reload(), WATCH_DEBOUNCE_MS)
      })
      this.watcher.on('error', () => {
        // Some filesystems do not support watching; the config stays usable
        // through the manual memory_api_status reload flag.
      })
    } catch {
      this.logger.warn('reasoning-memory: cannot watch the API config file; changes need a manual reload.')
    }
  }

  reload(): void {
    const wasLoaded = Boolean(this.config)
    const ok = this.load()
    if (!ok && wasLoaded) {
      this.logger.warn(`reasoning-memory: keeping the last good API config; ${this.lastError}`)
    } else if (!ok) {
      this.logger.warn(`reasoning-memory: API config unavailable; ${this.lastError}`)
    }
  }

  stop(): void {
    if (this.reloadTimer) clearTimeout(this.reloadTimer)
    this.watcher?.close()
  }

  get loaded(): boolean {
    return Boolean(this.config)
  }

  hasEndpoint(name: string): boolean {
    return Boolean(this.config?.endpoints[name])
  }

  endpoint(name: string): ApiEndpoint | undefined {
    return this.config?.endpoints[name]
  }

  endpointKind(name: string): ApiEndpointKind | undefined {
    return this.endpoint(name)?.kind
  }

  // Resolve one endpoint's API key without ever exposing its value.
  private keyOf(endpoint: ApiEndpoint): string | undefined {
    if (endpoint.apiKeyEnv) return process.env[endpoint.apiKeyEnv] || undefined
    if (endpoint.apiKey) return endpoint.apiKey
    return undefined
  }

  private keySourceOf(endpoint: ApiEndpoint): 'env' | 'inline' | 'none' {
    if (endpoint.apiKeyEnv) return 'env'
    if (endpoint.apiKey) return 'inline'
    return 'none'
  }

  private effectiveTimeout(endpoint: ApiEndpoint): number {
    return endpoint.timeoutMs ?? this.config?.defaults.timeoutMs ?? 30_000
  }

  // Proxy decision for one host, resolved against the CURRENT config (so
  // proxy/noProxy edits hot-apply to every subsequent request).
  private proxyFor(host: string): ProxyAgent | undefined {
    const proxy = this.config?.proxy
    if (!proxy?.url) return undefined
    if (proxy.noProxy.some(rule => hostMatchesRule(host, rule))) return undefined
    let agent = this.proxyAgents.get(proxy.url)
    if (!agent) {
      agent = new ProxyAgent({ uri: proxy.url })
      this.proxyAgents.set(proxy.url, agent)
    }
    return agent
  }

  private proxyForEndpoint(endpoint: ApiEndpoint, host: string): ProxyAgent | undefined {
    if (endpoint.proxy === 'direct') return undefined
    if (endpoint.proxy) {
      let agent = this.proxyAgents.get(endpoint.proxy)
      if (!agent) {
        agent = new ProxyAgent({ uri: endpoint.proxy })
        this.proxyAgents.set(endpoint.proxy, agent)
      }
      return agent
    }
    return this.proxyFor(host)
  }

  // Proxy-aware fetch bound to the CURRENT config at call time.
  readonly fetchFn: typeof fetch = (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url)
    const endpoint = this.endpointForUrl(url)
    const dispatcher = endpoint ? this.proxyForEndpoint(endpoint, url.hostname) : this.proxyFor(url.hostname)
    const requestInput = input as unknown as Parameters<typeof undiciFetch>[0]
    const requestInit = init as unknown as Parameters<typeof undiciFetch>[1]
    return (dispatcher
      ? undiciFetch(requestInput, { ...requestInit, dispatcher })
      : undiciFetch(requestInput, requestInit)) as unknown as Promise<Response>
  }

  // Match a URL back to its configured endpoint by baseUrl prefix; used so
  // endpoint-level proxy overrides apply to derived request URLs.
  private endpointForUrl(url: URL): ApiEndpoint | undefined {
    const urlBase = url.toString().replace(/\/$/, '')
    for (const endpoint of Object.values(this.config?.endpoints ?? {})) {
      if (urlBase.startsWith(normalizeBaseUrl(endpoint.baseUrl))) return endpoint
    }
    return undefined
  }

  // OpenAI-compatible embeddings endpoint → EmbeddingClient options.
  embeddingOptions(name = 'embeddings'): EmbeddingClientOptions | null {
    const endpoint = this.endpoint(name)
    if (!endpoint || endpoint.kind !== 'openai-embeddings') return null
    return {
      embeddingUrl: `${normalizeBaseUrl(endpoint.baseUrl)}/embeddings`,
      embeddingModel: endpoint.model,
      embeddingApiKey: this.keyOf(endpoint),
      embeddingDimension: endpoint.dimension,
    }
  }

  // OpenAI-compatible chat endpoint settings for LLM distillation.
  chatOptions(name = 'distillation'): { baseUrl: string; model: string; apiKey?: string; temperature?: number } | null {
    const endpoint = this.endpoint(name)
    if (!endpoint || endpoint.kind !== 'openai-chat') return null
    return {
      baseUrl: normalizeBaseUrl(endpoint.baseUrl),
      model: endpoint.model,
      apiKey: this.keyOf(endpoint),
      temperature: endpoint.temperature,
    }
  }

  // One non-streaming chat completion over a configured openai-chat endpoint.
  async openAiChat(name: string, system: string, user: string, temperature: number, signal: AbortSignal): Promise<string> {
    const endpoint = this.endpoint(name)
    if (!endpoint || endpoint.kind !== 'openai-chat') {
      throw new Error(`endpoint '${name}' is not an openai-chat endpoint`)
    }
    const url = `${normalizeBaseUrl(endpoint.baseUrl)}/chat/completions`
    const headers: Record<string, string> = { 'content-type': 'application/json', ...endpoint.headers }
    const key = this.keyOf(endpoint)
    if (key) headers.authorization = `Bearer ${key}`
    const response = await this.fetchFn(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        model: endpoint.model,
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
        temperature: Math.min(2, Math.max(0, temperature)),
      }),
      signal,
    })
    if (!response.ok) throw new Error(`chat endpoint '${name}' failed (${response.status})`)
    const payload = await response.json() as { choices?: Array<{ message?: { content?: unknown } }> }
    const content = payload.choices?.[0]?.message?.content
    if (typeof content !== 'string') throw new Error(`chat endpoint '${name}' returned an unexpected shape`)
    return content.trim()
  }

  // Embeddings over a configured openai-embeddings endpoint. The client is
  // rebuilt per call so proxy/key changes apply without a restart; the
  // model/dimension are still bound to the startup-time store schema.
  async embeddings(name: string, inputs: string[]): Promise<number[][]> {
    const options = this.embeddingOptions(name)
    if (!options) throw new Error(`endpoint '${name}' is not an openai-embeddings endpoint`)
    return new EmbeddingClient(options, this.fetchFn).embed(inputs)
  }

  // Generic JSON request against an http-json endpoint. This is the extension
  // point for future plugin features that call external APIs: validate the
  // endpoint kind, then issue a parameterized request through the shared
  // proxy-aware fetch.
  async request(
    name: string,
    options: { path?: string; method?: string; body?: unknown; headers?: Record<string, string>; timeoutMs?: number },
  ): Promise<unknown> {
    const endpoint = this.endpoint(name)
    if (!endpoint) throw new Error(`unknown endpoint '${name}'`)
    if (endpoint.kind !== 'http-json') {
      throw new Error(`endpoint '${name}' has kind '${endpoint.kind}'; use the dedicated client for that contract`)
    }
    const path = options.path ?? ''
    const url = `${normalizeBaseUrl(endpoint.baseUrl)}${path.startsWith('/') ? path : `/${path}`}`
    const headers: Record<string, string> = { ...endpoint.headers, ...(options.headers ?? {}) }
    const key = this.keyOf(endpoint)
    if (key) headers.authorization = `Bearer ${key}`
    if (options.body !== undefined) headers['content-type'] = 'application/json'
    const method = options.method ?? (options.body === undefined ? 'GET' : 'POST')
    const response = await this.fetchFn(url, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: AbortSignal.timeout(options.timeoutMs ?? this.effectiveTimeout(endpoint)),
    })
    if (!response.ok) throw new Error(`endpoint '${name}' request failed (${response.status})`)
    if (response.status === 204) return null
    return response.json()
  }

  // Masked status for the memory_api_status tool: names, kinds, key SOURCES,
  // and proxy decisions — never key values or proxy credentials.
  status(): ApiStatusView {
    const endpoints = Object.entries(this.config?.endpoints ?? {}).map(([name, endpoint]) => ({
      name,
      kind: endpoint.kind,
      baseUrl: redactUrl(endpoint.baseUrl),
      ...(endpoint.kind === 'openai-embeddings'
        ? { model: endpoint.model, dimension: endpoint.dimension }
        : endpoint.kind === 'openai-chat'
          ? { model: endpoint.model, ...(endpoint.temperature === undefined ? {} : { temperature: endpoint.temperature }) }
          : {}),
      keySource: this.keySourceOf(endpoint),
      proxy: endpoint.proxy === 'direct' ? 'direct' as const
        : endpoint.proxy ? 'custom' as const
        : 'inherit' as const,
    }))
    return {
      path: this.path,
      loaded: Boolean(this.config),
      lastError: this.lastError,
      proxy: {
        url: redactUrl(this.config?.proxy.url ?? ''),
        noProxy: this.config?.proxy.noProxy ?? [],
      },
      endpoints,
    }
  }
}

export function joinUrl(base: string, path: string): string {
  return `${normalizeBaseUrl(base)}${path.startsWith('/') ? path : `/${path}`}`
}
