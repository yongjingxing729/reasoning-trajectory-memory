// Verify that an extracted dsh-reasoning-memory package directory contains
// everything the DSH runtime needs after `npm pack`. Exit 1 with a readable
// report when any required artifact is missing.
//
// Usage: node scripts/verify-bundle.mjs <extracted-package-directory>
import { existsSync, readFileSync } from 'node:fs'
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

// Built TypeScript entry and companion modules.
requireFile('dist/index.js')
requireFile('dist/domain.js')
requireFile('dist/python.js')
requireFile('dist/search-text.js')
requireFile('dist/lexical-index.js')
requireFile('dist/vector-index.js')
requireFile('dist/enterprise-rules-rag.js')
requireFile('dist/distillation-llm.js')
requireFile('dist/embedding-client.js')
requireFile('dist/builtin-store.js')
requireFile('dist/api-config.js')

// Python worker and the full core package.
requireFile('python/reasoning_memory.py')
for (const module of ['constants', 'distillation', 'feedback', 'reasoning_graph', 'retrieval', 'service', 'text_similarity', 'trajectory', '__init__']) {
  requireFile(`python/reasoning_memory_core/${module}.py`)
}

// Documentation shipped with the bundle.
requireFile('README.md')

if (problems.length > 0) {
  console.error('bundle verification FAILED:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(`bundle verification OK: ${root}`)
