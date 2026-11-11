// Verify that an extracted dsh-reasoning-memory package directory contains
// everything the DSH runtime needs after `npm pack`. Exit 1 with a readable
// report when any required artifact is missing.
//
// This is the shipping contract for the decoupled layout: every layer and tool
// module the composition root imports must be present in dist/, every Python
// core module (including the template sub-modules) must ship, and the offline
// evaluation harness must never ship.
//
// Usage: node scripts/verify-bundle.mjs <extracted-package-directory>
import { existsSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

const root = resolve(process.argv[2] ?? '.')
const problems = []
const requireFile = (relative) => {
  const path = join(root, relative)
  if (!existsSync(path)) problems.push(`missing file: ${relative}`)
}
const requireText = (relative, expected) => {
  requireFile(relative)
  if (!problems.includes(`missing file: ${relative}`)) {
    const text = readFileSync(join(root, relative), 'utf8')
    if (!text.includes(expected)) problems.push(`content mismatch: ${relative} does not contain ${JSON.stringify(expected)}`)
  }
}

// Plugin manifest and Cordis bundle patch.
requireFile('package.json')
requireText('package.json', '"name": "dsh-reasoning-memory"')
requireText('package.json', '"dsh"')
requireText('cordis.patch.yml', 'reasoning-memory')

// Built TypeScript: the composition root, its layers, and the tool surface.
// Keep this list in sync with src/ — it is the deployment contract.
for (const module of [
  'index', 'config', 'runtime',
  'capture', 'storage-layer', 'index-layer', 'skill-export',
  'types', 'tool-schemas', 'helpers',
  'domain', 'document-domain', 'causal-domain', 'quantum-domain', 'distill-causal-domain', 'skill-domain',
  'python', 'search-text', 'lexical-index', 'document-index', 'vector-index', 'document-vector',
  'builtin-store', 'api-config', 'enterprise-rules-rag', 'skill-format', 'distillation-llm', 'embedding-client',
]) {
  requireFile(`dist/${module}.js`)
}
for (const group of ['memory-lifecycle', 'memory-retrieval', 'memory-consolidation', 'templates', 'documents', 'skills', 'admin']) {
  requireFile(`dist/tools/${group}.js`)
}

// The entry must stay a thin composition root (see docs/ARCHITECTURE.md); a
// regression back to a monolith shows up here, not in production.
const entrySize = statSync(join(root, 'dist/index.js')).size
if (entrySize > 8 * 1024) {
  problems.push(`dist/index.js is ${entrySize} bytes; the entry must stay a composition root (limit 8192)`)
}

// Python worker and the full core package (feature modules + template facade).
requireFile('python/reasoning_memory.py')
for (const module of [
  '__init__', 'constants', 'trajectory', 'text_similarity', 'reasoning_graph',
  'distillation', 'retrieval', 'feedback', 'service',
  'templates', 'template_classify', 'template_match', 'template_build', 'template_plan', 'template_mining', 'template_publish',
  'doc_chunking', 'doc_rag', 'causal_utils', 'qprobability', 'distill_causal',
]) {
  requireFile(`python/reasoning_memory_core/${module}.py`)
}

// The npm files whitelist is the shipping contract: the Python core must be
// included and the offline evaluation harness must never ship.
const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
if (!(manifest.files || []).some(entry => entry.includes('reasoning_memory_core'))) {
  problems.push('package.json files[] does not include python/reasoning_memory_core/')
}
if ((manifest.files || []).some(entry => entry.includes('scripts'))) {
  problems.push('package.json files[] must not ship scripts/ (the eval harness and structure guards are local-only)')
}
if ((manifest.files || []).some(entry => entry.includes('test'))) {
  problems.push('package.json files[] must not ship test/')
}

// Documentation shipped with the bundle.
requireFile('README.md')

if (problems.length > 0) {
  console.error('bundle verification FAILED:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(`bundle verification OK: ${root}`)
