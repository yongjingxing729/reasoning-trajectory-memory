/** 检索层: every derived retrieval index and outbound API surface.
 *
 * Builds the optional Qdrant vector indexes, the in-process PGlite/pgvector
 * built-in store, and the enterprise rule RAG client; applies the hot-editable
 * API config; and tracks degraded status in the runtime state so tool results
 * can tell the model which channel actually answered. storageDomain stays the
 * canonical memory source: every index here is rebuildable. */
import type { Context } from '@deepseek-ai/cordis'
import { ApiConfigService } from './api-config.js'
import { BuiltinMemoryStore } from './builtin-store.js'
import { EnterpriseRuleRag } from './enterprise-rules-rag.js'
import { QdrantDocumentIndex } from './document-vector.js'
import { QdrantMemoryIndex } from './vector-index.js'
import type { DocumentChunk } from './document-domain.js'
import type { Config } from './config.js'
import type { StorageLayer } from './storage-layer.js'
import type { RuntimeState } from './runtime.js'

export async function createIndexLayer(ctx: Context, config: Config, storage: StorageLayer, state: RuntimeState) {
  const { memories, documents, documentChunks } = storage
  // User-editable API endpoint configuration. When the profile sets
  // apiConfigPath, endpoints declared there override the profile-level
  // embedding settings and can drive LLM distillation directly. The shared
  // fetch routes every adapter through the configured proxy (hot-applied);
  // endpoint model/dimension changes take effect on restart.
  let apiConfig: ApiConfigService | undefined
  if (config.apiConfigPath!.trim()) {
    apiConfig = new ApiConfigService(config.apiConfigPath!.trim(), ctx.logger)
    apiConfig.start()
  }
  const apiFetch: typeof fetch = apiConfig ? apiConfig.fetchFn : fetch
  const embeddingInputs = (): { url: string; model: string; key?: string; dimension: number } => {
    const configured = apiConfig?.embeddingOptions('embeddings')
    if (configured) {
      return {
        url: configured.embeddingUrl,
        model: configured.embeddingModel,
        key: configured.embeddingApiKey,
        dimension: configured.embeddingDimension,
      }
    }
    return {
      url: config.embeddingUrl!,
      model: config.embeddingModel!,
      key: process.env[config.embeddingApiKeyEnv!] || undefined,
      dimension: config.embeddingDimension!,
    }
  }
  const embeddings = embeddingInputs()
  let vectorIndex: QdrantMemoryIndex | undefined
  let vectorIndexReady: Promise<void> = Promise.resolve()
  if (config.vectorIndexEnabled && !config.builtinStoreEnabled) {
    try {
      vectorIndex = new QdrantMemoryIndex({
        qdrantUrl: config.qdrantUrl!,
        collection: config.qdrantCollection!,
        qdrantApiKey: process.env[config.qdrantApiKeyEnv!] || undefined,
        embeddingUrl: embeddings.url,
        embeddingModel: embeddings.model,
        embeddingApiKey: embeddings.key,
        embeddingDimension: embeddings.dimension,
        candidatePoolSize: Math.floor(config.vectorCandidatePoolSize!),
      }, apiFetch)
      const existingMemories = [...memories.entries()].map(([, item]) => item)
      vectorIndexReady = vectorIndex.synchronize(existingMemories).then(() => {
        state.vectorIndexSynchronized = true
      }).catch(() => {
        state.vectorIndexDegraded = true
      })
    } catch {
      state.vectorIndexDegraded = true
    }
  }

  // Optional document vector index: its own Qdrant collection, gated by the
  // shared vectorIndexEnabled switch (final spec 5). Degradation falls back
  // to the BM25 document index without touching the memory surfaces.
  let documentVectorIndex: QdrantDocumentIndex | undefined
  if (config.vectorIndexEnabled) {
    try {
      documentVectorIndex = new QdrantDocumentIndex({
        qdrantUrl: config.qdrantUrl!,
        collection: config.documentCollection!,
        qdrantApiKey: process.env[config.qdrantApiKeyEnv!] || undefined,
        embeddingUrl: embeddings.url,
        embeddingModel: embeddings.model,
        embeddingApiKey: embeddings.key,
        embeddingDimension: embeddings.dimension,
        candidatePoolSize: Math.floor(config.vectorCandidatePoolSize!),
      }, apiFetch)
      void documentVectorIndex.synchronize([...documentChunks.entries()].flatMap(([, chunk]) => {
        const record = documents.get(chunk.docId)
        return record ? [{ docName: record.name, taskPatterns: record.taskPatterns, chunk }] : []
      })).catch(() => {
        state.documentVectorDegraded = true
      })
    } catch {
      state.documentVectorDegraded = true
    }
  }
  const documentVectorWrite = (
    operation: 'upsert' | 'delete',
    entries: Array<{ docName: string; taskPatterns: string[]; chunk: DocumentChunk }>,
    chunkIds: string[],
  ): void => {
    if (!documentVectorIndex || state.documentVectorDegraded) return
    void (async () => {
      try {
        if (operation === 'upsert') {
          for (const entry of entries) await documentVectorIndex.upsert(entry)
        } else {
          await documentVectorIndex.delete(chunkIds)
        }
      } catch {
        state.documentVectorDegraded = true
      }
    })()
  }

  // Built-in PostgreSQL (PGlite) + pgvector store: structured JSON rows with
  // dense embeddings and HNSW. It takes over the vector-recall role from the
  // external Qdrant index when enabled; storageDomain stays canonical and the
  // store re-syncs from it by dual content hashes.
  let builtinStore: BuiltinMemoryStore | undefined
  let builtinStoreReady: Promise<void> = Promise.resolve()
  if (config.builtinStoreEnabled) {
    if (config.vectorIndexEnabled) {
      ctx.logger.warn('reasoning-memory: builtinStoreEnabled and vectorIndexEnabled are both on; the built-in store owns vector retrieval and the Qdrant index stays idle.')
    }
    try {
      builtinStore = new BuiltinMemoryStore({
        dataDir: config.builtinStoreDataDir!.trim() || undefined,
        embeddingUrl: embeddings.url,
        embeddingModel: embeddings.model,
        embeddingApiKey: embeddings.key,
        embeddingDimension: embeddings.dimension,
        candidatePoolSize: Math.floor(config.vectorCandidatePoolSize!),
      }, apiFetch)
      state.builtinStoreStatus = 'initializing'
      const existingMemories = [...memories.entries()].map(([, item]) => item)
      builtinStoreReady = builtinStore.synchronize(existingMemories).then((result) => {
        state.builtinStoreStatus = result.hasEmbeddings ? 'ready' : 'json-only'
      }).catch(() => {
        state.builtinStoreStatus = 'degraded'
      })
    } catch {
      state.builtinStoreStatus = 'degraded'
    }
  }

  // Fire-and-forget write-through into the built-in store. The canonical
  // storageDomain write has already succeeded; a store failure only degrades
  // the derived index.
  const builtinWrite = (operation: (store: BuiltinMemoryStore) => Promise<void>): void => {
    if (!builtinStore) return
    void builtinStoreReady.then(async () => {
      if (!builtinStore || state.builtinStoreStatus === 'degraded') return
      try {
        await operation(builtinStore)
      } catch {
        state.builtinStoreStatus = 'degraded'
      }
    })
  }


  let enterpriseRuleRag: EnterpriseRuleRag | undefined
  if (config.enterpriseRuleRagEnabled) {
    try {
      const ruleSetId = config.enterpriseRuleSetId!.trim()
      if (!ruleSetId) throw new Error('enterpriseRuleSetId must be configured when enterprise rule RAG is enabled')
      const ruleCollection = config.enterpriseRuleCollection!.trim()
      if (!ruleCollection) throw new Error('enterpriseRuleCollection cannot be empty')
      const ruleDimension = config.enterpriseRuleEmbeddingDimension! > 0
        ? config.enterpriseRuleEmbeddingDimension!
        : embeddings.dimension
      enterpriseRuleRag = new EnterpriseRuleRag({
        qdrantUrl: config.qdrantUrl!,
        collection: ruleCollection,
        qdrantApiKey: process.env[config.enterpriseRuleQdrantApiKeyEnv!] || undefined,
        embeddingUrl: config.enterpriseRuleEmbeddingUrl!.trim() || embeddings.url,
        embeddingModel: config.enterpriseRuleEmbeddingModel!.trim() || embeddings.model,
        embeddingApiKey: process.env[config.enterpriseRuleEmbeddingApiKeyEnv!]
          || embeddings.key
          || undefined,
        embeddingDimension: ruleDimension,
        ruleSetId,
        candidatePoolSize: Math.floor(config.enterpriseRuleCandidatePoolSize!),
        resultLimit: Math.floor(config.enterpriseRuleResultLimit!),
        scoreThreshold: config.enterpriseRuleScoreThreshold!,
      }, apiFetch)
    } catch (error) {
      state.enterpriseRuleRagInitError = error instanceof Error ? error.message : 'enterprise rule RAG configuration is invalid'
    }
  }

  return {
    get apiConfig() {
      return apiConfig
    },
    get vectorIndex() {
      return vectorIndex
    },
    get documentVectorIndex() {
      return documentVectorIndex
    },
    get builtinStore() {
      return builtinStore
    },
    get enterpriseRuleRag() {
      return enterpriseRuleRag
    },
    get vectorIndexReady() {
      return vectorIndexReady
    },
    builtinWrite,
    documentVectorWrite,
    close: () => {
      apiConfig?.stop()
      // Awaitable shutdown: hosts that honor disposer promises get a clean
      // PGlite teardown; hosts that ignore them keep the fire-and-forget path.
      return builtinStore ? builtinStore.close().catch(() => {}) : undefined
    },
  }
}

export type IndexLayer = Awaited<ReturnType<typeof createIndexLayer>>
