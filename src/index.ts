/** Cordis bundle entry for the reasoning-memory plugin.
 *
 * Composition root only: exports the Cordis contract (name/inject/Config/apply)
 * and wires the runtime plus the model-facing tool surfaces. Every other
 * responsibility lives in its own module — see docs/ARCHITECTURE.md. */
import type { Context } from '@deepseek-ai/cordis'
import { createRuntime } from './runtime.js'
import { registerAdminTools } from './tools/admin.js'
import { registerDocumentTools } from './tools/documents.js'
import { registerMemoryConsolidationTools } from './tools/memory-consolidation.js'
import { registerMemoryLifecycleTools } from './tools/memory-lifecycle.js'
import { registerMemoryRetrievalTools } from './tools/memory-retrieval.js'
import { registerSkillTools } from './tools/skills.js'
import { registerTemplateTools } from './tools/templates.js'
import type { Config } from './config.js'

export { Config } from './config.js'

export const name = 'reasoning-memory'
export const inject = ['tools', 'storageDomain']

export async function apply(ctx: Context, config: Config): Promise<void> {
  const runtime = await createRuntime(ctx, config)
  // Registration order follows the memory lifecycle: capture -> retrieval ->
  // consolidation -> planning/templates -> documents -> admin -> skills.
  registerMemoryLifecycleTools(ctx, runtime)
  registerMemoryRetrievalTools(ctx, runtime)
  registerMemoryConsolidationTools(ctx, runtime)
  registerTemplateTools(ctx, runtime)
  registerDocumentTools(ctx, runtime)
  registerAdminTools(ctx, runtime)
  registerSkillTools(ctx, runtime)
}
