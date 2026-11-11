// Architecture invariants for the decoupled plugin source.
//
// This is the CI guard for the structure described in docs/ARCHITECTURE.md and
// design doc §2.3/§15.2: a thin composition root, layered modules that point
// inwards, and one model-facing tool surface per concern. It fails loud when a
// refactor re-creates a monolith or introduces a layer cycle, instead of
// relying on review comments.
//
// Usage: node scripts/verify-structure.mjs [repo-root]
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const root = process.argv[2] ?? '.'
const problems = []
const fail = (message) => problems.push(message)
const read = (path) => readFileSync(join(root, path), 'utf8')
const lines = (path) => read(path).split(/\r?\n/).length
const listFiles = (dir) => readdirSync(join(root, dir), { withFileTypes: true })
  .filter(entry => entry.isFile())
  .map(entry => `${dir}/${entry.name}`)
const parseImports = (text) => [...text.matchAll(
  /import\s+(type\s+)?(?:\{([^}]*)\}|(\*\s+as\s+\w+)|(\w+))\s+from\s+'([^']+)'/g,
)].map(([, typeOnly, named, , , module]) => ({ typeOnly: Boolean(typeOnly), module }))

const MAX_FILE_LINES = 900
const MAX_ENTRY_LINES = 60
const TOOL_FILES = listFiles('src/tools').filter(path => path.endsWith('.ts'))
const LAYER_FILES = ['src/capture.ts', 'src/storage-layer.ts', 'src/index-layer.ts', 'src/skill-export.ts']
const SOURCE_FILES = (() => {
  const out = []
  const walk = (dir) => {
    for (const entry of readdirSync(join(root, dir), { withFileTypes: true })) {
      const path = `${dir}/${entry.name}`
      if (entry.isDirectory()) walk(path)
      else if (path.endsWith('.ts')) out.push(path)
    }
  }
  walk('src')
  return out
})()

// ---------------------------------------------------------------- composition root
const entry = read('src/index.ts')
if (lines('src/index.ts') > MAX_ENTRY_LINES) {
  fail(`src/index.ts is ${lines('src/index.ts')} lines (limit ${MAX_ENTRY_LINES}); the entry must stay a composition root`)
}
for (const token of ['defineTool', 'ctx.tools.register', 'storageDomain.open']) {
  if (entry.includes(token)) fail(`src/index.ts must not contain "${token}"; keep tool/domain wiring in the layers`)
}
for (const token of ["export const name", "export const inject", 'export { Config }', 'export async function apply']) {
  if (!entry.includes(token)) fail(`src/index.ts is missing the Cordis contract export: ${token}`)
}

// ---------------------------------------------------------------- tool surfaces
for (const path of TOOL_FILES) {
  const text = read(path)
  const signature = text.match(/export function register(\w+)Tools\(ctx: Context, rt: Runtime\): void/)
  if (!signature) {
    fail(`${path}: tool modules must export register<Group>Tools(ctx: Context, rt: Runtime)`)
    continue
  }
  if (/from '\.\.\/tools\//.test(text)) fail(`${path}: tool modules must not import each other`)
  for (const statement of parseImports(text)) {
    if (statement.typeOnly) continue
    if (statement.module.startsWith('./')) {
      fail(`${path}: tool modules must not import each other (sibling import '${statement.module}')`)
    }
    if (/^\.\.\/(runtime|capture|storage-layer|index-layer)\.js$/.test(statement.module)) {
      fail(`${path}: imports ${statement.module} as a value; tool modules get their dependencies through the runtime container (import type is fine)`)
    }
  }
  const wired = entry.split(`register${signature[1]}Tools(`).length - 1
  if (wired !== 1) fail(`${path}: register${signature[1]}Tools must be wired exactly once in src/index.ts (found ${wired})`)
}

// ---------------------------------------------------------------- layers point inwards
for (const path of LAYER_FILES) {
  const text = read(path)
  if (/from '\.\/tools\//.test(text)) fail(`${path}: layers must not depend on the tool surface`)
  if (/from '\.\/index\.js'/.test(text)) fail(`${path}: layers must not depend on the plugin entry`)
}
const runtime = read('src/runtime.ts')
if (/from '\.\/tools\//.test(runtime)) fail('src/runtime.ts: the container must not depend on the tool surface')

// ---------------------------------------------------------------- size guard
for (const path of SOURCE_FILES) {
  const count = lines(path)
  if (count > MAX_FILE_LINES) fail(`${path} is ${count} lines (limit ${MAX_FILE_LINES}); split it by responsibility`)
}

// ---------------------------------------------------------------- Python core stays feature-sized
if (statSync(join(root, 'python/reasoning_memory_core')).isDirectory()) {
  for (const path of listFiles('python/reasoning_memory_core').filter(f => f.endsWith('.py'))) {
    const count = lines(path)
    if (count > MAX_FILE_LINES) fail(`${path} is ${count} lines (limit ${MAX_FILE_LINES}); split it by responsibility`)
  }
  const service = read('python/reasoning_memory_core/service.py')
  if (!/def dispatch|def handle|def run_operation/.test(service)) {
    fail('python/reasoning_memory_core/service.py: expected the operation dispatcher entry point')
  }
}

// ---------------------------------------------------------------- shipping contract
const manifest = JSON.parse(read('package.json'))
if (manifest.main !== './dist/index.js') fail('package.json: main must stay ./dist/index.js')
if (manifest.scripts['verify:structure'] === undefined) fail('package.json: add the verify:structure script')
if (!(manifest.files || []).some(entry => entry.includes('dist/'))) fail('package.json files[] must ship dist/')
if ((manifest.files || []).some(entry => entry.includes('scripts'))) fail('package.json files[] must not ship scripts/')

// Python tests must never be collected by default discovery in a package
for (const path of listFiles('test').filter(f => f.endsWith('.py'))) {
  if (!/^(test_.*|_support)\.py$/.test(path.split('/').pop())) {
    fail(`${path}: Python test files must be test_*.py (or the _support helper) so discovery stays predictable`)
  }
}

if (problems.length > 0) {
  console.error('architecture verification FAILED:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(`architecture verification OK: ${SOURCE_FILES.length} TS files, ${TOOL_FILES.length} tool groups, entry ${lines('src/index.ts')} lines`)
