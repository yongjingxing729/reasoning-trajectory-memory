import assert from 'node:assert/strict'
import test from 'node:test'
import {
  defaultSkillDescription,
  defaultSkillName,
  renderSkillBodyFromMemory,
  renderSkillIndex,
  renderSkillMarkdown,
  renderSkillResources,
  validateSkillInput,
} from '../dist/skill-format.js'

function memory(overrides = {}) {
  return {
    memoryId: 'm1',
    type: 'strategy',
    outcome: 'success',
    taskScope: 'code-review',
    preconditions: ['the deployment target is current'],
    content: '调用者提炼的可迁移推理策略（未独立验证）：validate the schema before publishing。',
    reasoning: 'validate the schema before publishing generated artifacts',
    title: 'Validate Schema Before Publishing',
    description: 'Applies before publishing generated artifacts',
    contraindications: ['skip for read-only checks'],
    reasoningGraph: {
      version: 1,
      nodes: [
        { id: 'p1', kind: 'premise', text: 'schema must validate', source: 'agent_input' },
        { id: 'c1', kind: 'conclusion', text: 'publishing is safe', source: 'agent_input' },
      ],
      edges: [{ sourceId: 'p1', targetId: 'c1', relation: 'supports', source: 'agent_input' }],
    },
    feedback: { helpful: 0, harmful: 0, neutral: 0 },
    provenance: { taskId: 'task-a', sourceSessionId: 's', eventSeqs: [1] },
    evidence: { verifier: 'unit-suite', summary: 'passed', independentlyVerified: false },
    confidence: 0.55,
    trustLevel: 'unverified',
    extractorVersion: 'x',
    createdAt: 1,
    updatedAt: 1,
    semanticText: 'database schema migration',
    structureSignature: ['search', 'execute', 'verify'],
    extractionMethod: 'caller_insight',
    distillationModel: '',
    sourceMemoryIds: [],
    ...overrides,
  }
}

function validInput(overrides = {}) {
  return {
    name: 'schema-before-publish',
    description: 'Use when a task publishes generated artifacts and the schema must be validated first.',
    body: '# Schema Before Publish\n\n1. Validate the source schema.\n   - Completion criterion: the validator reports clean before publishing.',
    resources: [
      { path: 'reasoning-graph.md', content: 'graph' },
      { path: 'evidence.md', content: 'evidence' },
    ],
    sourceMemoryIds: ['m1'],
    ...overrides,
  }
}

test('validation accepts a well-formed skill input', () => {
  assert.deepEqual(validateSkillInput(validInput()), [])
})

test('validation rejects malformed names', () => {
  assert.deepEqual(validateSkillInput(validInput({ name: '' }))[0].field, 'name')
  assert.ok(validateSkillInput(validInput({ name: 'Schema-Before' })).some(issue => issue.field === 'name'))
  assert.ok(validateSkillInput(validInput({ name: 'schema before' })).some(issue => issue.field === 'name'))
  assert.ok(validateSkillInput(validInput({ name: '-leading' })).some(issue => issue.field === 'name'))
  assert.ok(validateSkillInput(validInput({ name: 'memory_schema' })).some(issue => issue.field === 'name' && /memory_/.test(issue.reason)))
  assert.ok(validateSkillInput(validInput({ name: 'a'.repeat(65) })).some(issue => issue.field === 'name'))
})

test('validation enforces trigger-rich, em-dash-free descriptions', () => {
  assert.ok(validateSkillInput(validInput({ description: '' })).some(issue => issue.field === 'description'))
  assert.ok(validateSkillInput(validInput({ description: 'A schema validator thing.' }))
    .some(issue => issue.field === 'description' && /trigger/.test(issue.reason)))
  assert.ok(validateSkillInput(validInput({ description: 'Use when a task publishes artifacts — with care.' }))
    .some(issue => issue.field === 'description' && /em-dash/.test(issue.reason)))
  assert.ok(validateSkillInput(validInput({ description: 'x'.repeat(401) }))
    .some(issue => issue.field === 'description'))
})

test('validation enforces body bounds and em-dash ban', () => {
  assert.ok(validateSkillInput(validInput({ body: '' })).some(issue => issue.field === 'body'))
  assert.ok(validateSkillInput(validInput({ body: 'Step 1 — do it.' }))
    .some(issue => issue.field === 'body' && /em-dash/.test(issue.reason)))
  assert.ok(validateSkillInput(validInput({ body: 'x'.repeat(8001) })).some(issue => issue.field === 'body'))
})

test('validation rejects unsafe or oversized resources', () => {
  assert.ok(validateSkillInput(validInput({ resources: [{ path: '../evil.md', content: 'x' }] }))
    .some(issue => issue.field === 'resources' && /flat/.test(issue.reason)))
  assert.ok(validateSkillInput(validInput({ resources: [{ path: 'dir/nested.md', content: 'x' }] }))
    .some(issue => issue.field === 'resources'))
  assert.ok(validateSkillInput(validInput({ resources: [{ path: 'notes.txt', content: 'x' }] }))
    .some(issue => issue.field === 'resources'))
  assert.ok(validateSkillInput(validInput({
    resources: [
      { path: 'a.md', content: 'x' },
      { path: 'a.md', content: 'y' },
    ],
  })).some(issue => issue.field === 'resources' && /duplicate/.test(issue.reason)))
  assert.ok(validateSkillInput(validInput({
    resources: ['a.md', 'b.md', 'c.md', 'd.md', 'e.md', 'f.md'].map(path => ({ path, content: 'x' })),
  })).some(issue => issue.field === 'resources' && /at most/.test(issue.reason)))
  assert.ok(validateSkillInput(validInput({ resources: [{ path: 'big.md', content: 'x'.repeat(64 * 1024 + 1) }] }))
    .some(issue => issue.field === 'resources' && /exceeds/.test(issue.reason)))
})

test('SKILL.md rendering carries the frontmatter and the body', () => {
  const markdown = renderSkillMarkdown('schema-before-publish', 'Use when a task publishes artifacts.', 'Step 1.')
  assert.ok(markdown.startsWith('---\nname: schema-before-publish\ndescription: Use when a task publishes artifacts.\n---\n'))
  assert.ok(markdown.includes('Step 1.'))
})

test('body rendering folds memories into steps, criteria, and pointers', () => {
  const body = renderSkillBodyFromMemory([memory(), memory({ memoryId: 'm2', reasoning: 'check constraints first' })])
  assert.ok(body.includes('## Steps'))
  assert.ok(body.includes('Completion criterion'))
  assert.ok(body.includes('## When NOT to use'))
  assert.ok(body.includes('skip for read-only checks'))
  assert.ok(body.includes('[reasoning-graph.md](reasoning-graph.md)'))
  assert.ok(body.includes('[structure-signature.md](structure-signature.md)'))
  assert.ok(body.includes('[evidence.md](evidence.md)'))
})

test('default resources render graph, signature, and evidence with provenance', () => {
  const resources = renderSkillResources(memory())
  assert.deepEqual(resources.map(resource => resource.path), ['reasoning-graph.md', 'structure-signature.md', 'evidence.md'])
  assert.ok(resources[0].content.includes('supports'))
  assert.ok(resources[0].content.includes('source: agent_input'))
  assert.ok(resources[1].content.includes('search -> execute -> verify'))
  assert.ok(resources[2].content.includes('unit-suite'))
})

test('default name slugs the title and falls back for non-ASCII titles', () => {
  assert.equal(defaultSkillName(memory(), 42), 'validate-schema-before-publishing')
  assert.equal(defaultSkillName(memory({ title: '发布前校验', reasoning: '发布前校验' }), 42), 'memory-skill-42')
})

test('default description carries trigger phrasing and contraindications', () => {
  const description = defaultSkillDescription(memory())
  assert.ok(/Use when/.test(description))
  assert.ok(description.includes('database schema migration'))
  assert.ok(description.includes('skip for read-only checks'))
  assert.deepEqual(validateSkillInput({ ...validInput(), description }), [])
})

test('index rendering lists skills and covers the empty case', () => {
  assert.ok(renderSkillIndex([]).includes('No skills published yet'))
  const index = renderSkillIndex([{ name: 'a-skill', description: 'Use when a task fits.' }])
  assert.ok(index.includes('[a-skill](./a-skill/SKILL.md)'))
  assert.ok(index.includes('Use when a task fits.'))
})
