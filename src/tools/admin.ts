// AdminTools — model-facing tool surface.

import type { Context } from '@deepseek-ai/cordis'
import type { Runtime } from '../runtime.js'
import { renderJson } from '../helpers.js'
import { API_STATUS_OUTPUT_SCHEMA } from '../tool-schemas.js'
import { defineTool } from '@deepseek-ai/dsh-tools'

export function registerAdminTools(ctx: Context, rt: Runtime): void {
  const { apiConfig } = rt.indexes

  ctx.tools.register(defineTool({
    name: 'memory_api_status',
    description: 'Report the loaded API endpoint configuration (from apiConfigPath). Names, kinds, models, key SOURCES, and proxy settings are shown; API key values and proxy credentials are always masked. Pass reload to re-read the config file before reporting.',
    parameters: {
      reload: { type: 'boolean', description: 'Re-read the API config file before reporting (default false).' },
    },
    output: { schema: API_STATUS_OUTPUT_SCHEMA, render: renderJson },
    async execute(args) {
      if (!apiConfig) {
        return {
          enabled: false,
          path: '',
          loaded: false,
          lastError: '',
          proxy: { url: '', noProxy: [] },
          endpoints: [],
          note: 'No apiConfigPath is set in the plugin profile; add one to declare user-configured API endpoints (embeddings, distillation, generic HTTP).',
        }
      }
      if (args.reload) apiConfig.reload()
      return {
        enabled: true,
        ...apiConfig.status(),
        note: apiConfig.loaded
          ? 'The configuration is active. Proxy and key edits hot-apply; model/dimension changes require a plugin restart (the vector store schema binds them at startup).'
          : 'The configuration could not be loaded; see lastError. The previous good configuration stays active if one was loaded.',
      }
    },
  }))
}
