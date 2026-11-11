/** Runtime container: one composition point for the plugin's layers.
 *
 * `apply` builds a runtime, which owns the storage/retrieval/capture layers,
 * the mutable degraded-state flags the tool results surface, and the Cordis
 * lifecycle wiring (startup env gate + ctx.effect disposal). Tool modules take
 * this container instead of importing layers directly, so their dependency
 * surface stays explicit and testable. */
import type { Context } from '@deepseek-ai/cordis'
import { checkPythonVersion } from './python.js'
import { createCaptureLayer } from './capture.js'
import { createIndexLayer } from './index-layer.js'
import { createSkillExport } from './skill-export.js'
import { createStorageLayer } from './storage-layer.js'
import type { BuiltinStoreStatus } from './builtin-store.js'
import type { Config } from './config.js'

/** Mutable channel status shared between the retrieval layer (writer) and the
 *  tool results (reader). Object identity is stable for the plugin lifetime. */
export type RuntimeState = {
  vectorIndexDegraded: boolean
  vectorIndexSynchronized: boolean
  builtinStoreStatus: BuiltinStoreStatus
  enterpriseRuleRagInitError: string
  documentVectorDegraded: boolean
}

export async function createRuntime(ctx: Context, config: Config) {
  const state: RuntimeState = {
    vectorIndexDegraded: false,
    vectorIndexSynchronized: !config.vectorIndexEnabled,
    builtinStoreStatus: 'disabled',
    enterpriseRuleRagInitError: '',
    documentVectorDegraded: false,
  }

  const storage = await createStorageLayer(ctx, config)

  // Startup environment gate: the Python core needs 3.10+; an incompatible or
  // missing interpreter surfaces here as a readable error before any bridge
  // call fails later with an opaque spawn failure. It runs after the domains
  // are open and before the builtin-template seeding, matching the original
  // apply() order.
  const pythonVersionCheck = checkPythonVersion(config.pythonExecutable!)
  if (!pythonVersionCheck.ok) {
    ctx.logger.error(`reasoning-memory: ${pythonVersionCheck.message}`)
  }
  void storage.ensureTemplatesSeeded()

  const indexes = await createIndexLayer(ctx, config, storage, state)
  const capture = createCaptureLayer(config)
  const exportSkillDirectory = createSkillExport(config, storage)

  ctx.effect(() => () => {
    storage.close()
    return indexes.close()
  })

  capture.attach(ctx)

  return { config, state, storage, indexes, capture, exportSkillDirectory }
}

export type Runtime = Awaited<ReturnType<typeof createRuntime>>
