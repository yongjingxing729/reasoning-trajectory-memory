import z from 'zod'
import type { MemoryItem } from './domain.js'

// Agent-Skills formatting and validation for the plugin's skill layer.
//
// The conventions follow the engineering-prompt discipline of the
// skills-main project: a skill is a kebab-cased name, a model-facing
// description rich in trigger phrasing (context pointer), a concise body of
// steps with completion criteria, and reference material pushed behind
// pointers (progressive disclosure). Deterministic guards keep the prompt
// engineering honest: trigger-rich descriptions, no em-dashes (house rule),
// flat resource paths with no traversal, and hard size caps.

export const SKILL_NAME_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/
export const SKILL_NAME_MAX = 64
export const SKILL_DESCRIPTION_MAX = 400
export const SKILL_BODY_MAX = 8000
export const SKILL_RESOURCES_MAX = 5
export const SKILL_RESOURCE_SIZE_MAX = 64 * 1024
export const SKILL_RESOURCE_PATH_PATTERN = /^[a-z0-9][a-z0-9._-]{0,63}\.md$/
export const SKILL_RESERVED_PREFIX = /^memory[-_]/
const EM_DASH = /\u2014/
// Trigger tokens that make a description a context pointer: the model decides
// from these words when to reach for the skill. Bilingual on purpose.
const TRIGGER_TOKENS = /\b(when|if|for|mention|ask|say|当|如果|提到|需要|时)\b/i

export interface SkillResource {
  path: string
  content: string
}

export interface SkillInput {
  name: string
  description: string
  body: string
  resources: SkillResource[]
  sourceMemoryIds: string[]
}

export type SkillValidationIssue =
  | { field: 'name'; reason: string }
  | { field: 'description'; reason: string }
  | { field: 'body'; reason: string }
  | { field: 'resources'; reason: string }

// All deterministic, testable prompt-engineering guards in one pass.
export function validateSkillInput(input: SkillInput): SkillValidationIssue[] {
  const issues: SkillValidationIssue[] = []
  const name = input.name.trim()
  if (!name) {
    issues.push({ field: 'name', reason: 'the skill name must not be empty' })
  } else if (SKILL_RESERVED_PREFIX.test(name)) {
    issues.push({ field: 'name', reason: 'skill names cannot start with memory_' })
  } else if (!SKILL_NAME_PATTERN.test(name)) {
    issues.push({ field: 'name', reason: 'skill names must be kebab-case (lowercase letters, digits, hyphens)' })
  } else if (name.length > SKILL_NAME_MAX) {
    issues.push({ field: 'name', reason: `skill names are capped at ${SKILL_NAME_MAX} characters` })
  }
  const description = input.description.trim()
  if (!description) {
    issues.push({ field: 'description', reason: 'the description must not be empty' })
  } else {
    if (description.length > SKILL_DESCRIPTION_MAX) {
      issues.push({ field: 'description', reason: `descriptions are capped at ${SKILL_DESCRIPTION_MAX} characters` })
    }
    if (EM_DASH.test(description)) {
      issues.push({ field: 'description', reason: 'em-dashes are not allowed; rewrite with commas, colons, periods, or parentheses' })
    }
    if (!TRIGGER_TOKENS.test(description)) {
      issues.push({ field: 'description', reason: 'a model-invoked description must carry trigger phrasing (when/if/for/mention/ask/say, or 当/如果/提到/需要/时) so the model knows when to reach for the skill' })
    }
  }
  const body = input.body.trim()
  if (!body) {
    issues.push({ field: 'body', reason: 'the body must not be empty' })
  } else {
    if (body.length > SKILL_BODY_MAX) {
      issues.push({ field: 'body', reason: `the body is capped at ${SKILL_BODY_MAX} characters` })
    }
    if (EM_DASH.test(body)) {
      issues.push({ field: 'body', reason: 'em-dashes are not allowed in the body; rewrite with commas, colons, periods, or parentheses' })
    }
  }
  if (input.resources.length > SKILL_RESOURCES_MAX) {
    issues.push({ field: 'resources', reason: `a skill carries at most ${SKILL_RESOURCES_MAX} resource files` })
  }
  const seen = new Set<string>()
  for (const resource of input.resources) {
    if (!SKILL_RESOURCE_PATH_PATTERN.test(resource.path)) {
      issues.push({ field: 'resources', reason: `resource path '${resource.path}' is invalid: flat lowercase file name ending in .md, no slashes` })
    }
    if (seen.has(resource.path)) {
      issues.push({ field: 'resources', reason: `duplicate resource path '${resource.path}'` })
    }
    seen.add(resource.path)
    if (resource.content.length > SKILL_RESOURCE_SIZE_MAX) {
      issues.push({ field: 'resources', reason: `resource '${resource.path}' exceeds ${SKILL_RESOURCE_SIZE_MAX} bytes` })
    }
  }
  return issues
}

export function issuesToReason(issues: SkillValidationIssue[]): string {
  return issues
    .map(issue => `${issue.field}: ${issue.reason}`)
    .join('; ')
}

// Render the Agent-Skills SKILL.md for a validated skill.
export function renderSkillMarkdown(name: string, description: string, body: string): string {
  return `---\nname: ${name}\ndescription: ${description}\n---\n\n${body.trimEnd()}\n`
}

// Render the bucket index: one line per skill, name linked to its SKILL.md.
export function renderSkillIndex(entries: Array<{ name: string; description: string }>): string {
  if (entries.length === 0) return '# Memory Skills\n\nNo skills published yet.\n'
  const lines = ['# Memory Skills', '',
    ...entries.map(entry => `- [${entry.name}](./${entry.name}/SKILL.md): ${entry.description}`),
    '',
  ]
  return lines.join('\n')
}

function slugify(text: string): string {
  const slug = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, SKILL_NAME_MAX)
  return slug
}

// Derive a default kebab-case name from the first source memory's title.
// Falls back to a timestamped name when the title carries no ASCII letters.
export function defaultSkillName(memory: MemoryItem, now: number): string {
  const fromTitle = slugify(memory.title || memory.reasoning || memory.content)
  return fromTitle || `memory-skill-${now}`
}

function clip(text: string, limit: number): string {
  return text.trim().slice(0, limit)
}

// Derive a default model-facing description from the source memory.
export function defaultSkillDescription(memory: MemoryItem): string {
  const scope = clip(memory.semanticText, 120) || 'an unlabeled task family'
  const contraindications = (memory.contraindications ?? []).filter(entry => entry.trim())
  const negative = contraindications.length > 0
    ? `; do not apply when ${contraindications.join('; ')}`
    : ''
  return `Use when a task involves ${scope}${negative}. Mentions from the distilled reasoning lesson: ${clip(memory.reasoning || memory.content, 120)}.`
}

function renderGraphMarkdown(memory: MemoryItem): string {
  const graph = memory.reasoningGraph ?? { version: 1, nodes: [], edges: [] }
  const lines = ['# Reasoning Graph', '', 'Typed rationale graph with per-element provenance (v1).', '']
  const label = (id: string) => {
    const node = graph.nodes.find(candidate => candidate.id === id)
    return node ? `${node.kind} ${id}` : id
  }
  if (graph.nodes.length === 0) {
    lines.push('(no graph nodes recorded)', '')
  } else {
    lines.push('## Nodes', '')
    for (const node of graph.nodes) {
      lines.push(`- ${node.id} (${node.kind}, source: ${node.source}): ${node.text}`)
    }
    lines.push('')
    if (graph.edges.length > 0) {
      lines.push('## Edges', '')
      for (const edge of graph.edges) {
        lines.push(`- ${label(edge.sourceId)} --${edge.relation}--> ${label(edge.targetId)} (source: ${edge.source})`)
      }
      lines.push('')
    }
  }
  return lines.join('\n')
}

function renderSignatureMarkdown(memory: MemoryItem): string {
  const signature = memory.structureSignature ?? []
  const lines = ['# Structure Signature', '', 'Abstract tool-action sequence observed for this lesson.', '']
  if (signature.length === 0) {
    lines.push('(no observable tool-action sequence)', '')
  } else {
    lines.push(signature.join(' -> '), '')
  }
  return lines.join('\n')
}

function renderEvidenceMarkdown(memory: MemoryItem): string {
  const evidence = memory.evidence
  return [
    '# Evidence', '',
    `- Verifier: ${evidence.verifier}`,
    `- Summary: ${evidence.summary}`,
    `- Independently verified: ${evidence.independentlyVerified ? 'yes' : 'no (trustLevel stays unverified)'}`,
    '',
  ].join('\n')
}

// Render the skill body from the source memory using the engineering-prompt
// conventions: steps, explicit when-not-to-use, and completion criteria, with
// the heavy reference material disclosed behind pointers.
export function renderSkillBodyFromMemory(memories: MemoryItem[]): string {
  const first = memories[0]
  const lines: string[] = []
  lines.push(`# ${first.title || first.reasoning.slice(0, 80) || 'Memory Skill'}`)
  lines.push('')
  lines.push('A reusable reasoning discipline distilled from verified task experience. Treat every step as a pointer into the resource files: load only what the current task needs.')
  lines.push('')
  lines.push('## Steps')
  lines.push('')
  memories.forEach((memory, index) => {
    lines.push(`${index + 1}. ${memory.reasoning || memory.content}`)
    lines.push(`   - Completion criterion: the result must be re-verified in the current environment against ${memory.evidence.verifier} before this step counts as done.`)
  })
  lines.push('')
  const contraindications = (first.contraindications ?? []).filter(entry => entry.trim())
  if (contraindications.length > 0) {
    lines.push('## When NOT to use')
    lines.push('')
    for (const entry of contraindications) lines.push(`- ${entry}`)
    lines.push('')
  }
  const preconditions = (first.preconditions ?? []).filter(entry => entry.trim())
  if (preconditions.length > 0) {
    lines.push('## Preconditions')
    lines.push('')
    for (const entry of preconditions) lines.push(`- ${entry}`)
    lines.push('')
  }
  lines.push('## Resources')
  lines.push('')
  lines.push('- [reasoning-graph.md](reasoning-graph.md): the typed rationale graph with provenance')
  lines.push('- [structure-signature.md](structure-signature.md): the abstract tool-action sequence')
  lines.push('- [evidence.md](evidence.md): the verifier and evidence summary')
  lines.push('')
  return lines.join('\n')
}

// Render the default resource set for a skill published from one memory.
export function renderSkillResources(memory: MemoryItem): SkillResource[] {
  return [
    { path: 'reasoning-graph.md', content: renderGraphMarkdown(memory) },
    { path: 'structure-signature.md', content: renderSignatureMarkdown(memory) },
    { path: 'evidence.md', content: renderEvidenceMarkdown(memory) },
  ]
}
