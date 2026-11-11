// SkillTools — model-facing tool surface.

import type { Context } from '@deepseek-ai/cordis'
import type { Runtime } from '../runtime.js'
import type { MemoryItem } from '../domain.js'
import { clipText, getSessionIdFromAgent, renderJson } from '../helpers.js'
import { skillSchema } from '../skill-domain.js'
import { defaultSkillDescription, defaultSkillName, issuesToReason, renderSkillBodyFromMemory, renderSkillResources, validateSkillInput } from '../skill-format.js'
import type { SkillResource } from '../skill-format.js'
import { SKILL_LIST_OUTPUT_SCHEMA, SKILL_LOAD_OUTPUT_SCHEMA, SKILL_PUBLISH_OUTPUT_SCHEMA, SKILL_RESOURCE_OUTPUT_SCHEMA } from '../tool-schemas.js'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { randomUUID } from 'node:crypto'

export function registerSkillTools(ctx: Context, rt: Runtime): void {
  const { memories, skills } = rt.storage
  const { exportSkillDirectory } = rt

  ctx.tools.register(defineTool({
    name: 'memory_publish_skill',
    description: 'Promote one or more distilled reasoning memories into a reusable Agent Skill in SKILL.md format (frontmatter name/description plus a concise body with completion criteria and resource pointers). Skills carry provenance back to their source memories and inherit unverified trust. Republishing the same name bumps its version.',
    parameters: {
      memory_ids: { type: 'array', required: true, items: { type: 'string' }, description: '1 to 10 memory IDs to fold into the skill (the first memory supplies the defaults).' },
      name: { type: 'string', description: 'kebab-case skill name; defaults to a slug of the first memory title.' },
      description: { type: 'string', description: 'Model-facing trigger description (when to reach for the skill); defaults to one derived from the first memory.' },
      body: { type: 'string', description: 'SKILL.md body; defaults to an auto-rendered body with steps, completion criteria, when-not-to-use, and resource pointers.' },
      include_resources: { type: 'boolean', description: 'Attach reasoning-graph.md, structure-signature.md, and evidence.md resources (default true).' },
    },
    output: { schema: SKILL_PUBLISH_OUTPUT_SCHEMA, render: renderJson },
    async execute(args, exec) {
      const ids = [...new Set(args.memory_ids)].slice(0, 10)
      const sources = ids
        .map(id => memories.get(id))
        .filter((item): item is MemoryItem => Boolean(item))
      if (sources.length === 0) {
        return { published: false, skillId: '', name: '', version: 0, resourceCount: 0, note: 'provide at least one existing memory id.' }
      }
      const sessionId = getSessionIdFromAgent(exec.agent) ?? ''
      const now = Date.now()
      const first = sources[0]
      const name = typeof args.name === 'string' && args.name.trim()
        ? clipText(args.name, 64)
        : defaultSkillName(first, now)
      const description = typeof args.description === 'string' && args.description.trim()
        ? clipText(args.description, 400)
        : defaultSkillDescription(first)
      const body = typeof args.body === 'string' && args.body.trim()
        ? clipText(args.body, 8000)
        : renderSkillBodyFromMemory(sources)
      const resources: SkillResource[] = args.include_resources === false
        ? []
        : renderSkillResources(first)
      const issues = validateSkillInput({ name, description, body, resources, sourceMemoryIds: ids })
      if (issues.length > 0) {
        return { published: false, skillId: '', name, version: 0, resourceCount: 0, note: issuesToReason(issues) }
      }
      const existing = [...skills.entries()]
        .map(([, record]) => record)
        .find(record => record.name === name)
      const version = (existing?.version ?? 0) + 1
      const record = skillSchema.parse({
        skillId: existing?.skillId ?? randomUUID(),
        name,
        description,
        body,
        resources,
        provenance: { sourceMemoryIds: ids, sourceSessionId: sessionId, publishedAt: now },
        version,
        trustLevel: 'unverified' as const,
        extractorVersion: 'dsh-reasoning-memory/0.6.0',
        createdAt: existing?.createdAt ?? now,
        updatedAt: now,
      })
      await skills.put(record.skillId, record)
      const exportNote = exportSkillDirectory()
      return {
        published: true,
        skillId: record.skillId,
        name,
        version,
        resourceCount: resources.length,
        note: [exportNote, 'the skill inherits unverified trust from its source memories; re-verify it in the current environment before applying.']
          .filter(Boolean).join(' '),
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_list_skills',
    description: 'List published reasoning skills. Layer 1 of progressive disclosure: metadata only (name, trigger description, version, resource and source counts). Load a skill with memory_load_skill to see its full SKILL.md.',
    parameters: {},
    output: { schema: SKILL_LIST_OUTPUT_SCHEMA, render: renderJson },
    async execute() {
      const records = [...skills.entries()]
        .map(([, record]) => record)
        .sort((left, right) => left.name.localeCompare(right.name))
      return {
        count: records.length,
        skills: records.map(record => ({
          name: record.name,
          description: record.description,
          version: record.version,
          resourceCount: record.resources.length,
          sourceMemoryCount: record.provenance.sourceMemoryIds.length,
          updatedAt: record.updatedAt,
          trustLevel: record.trustLevel,
        })),
        note: 'Layer 1 of progressive disclosure: metadata only. Load a skill with memory_load_skill for its full SKILL.md, then load individual resources with memory_load_skill_resource.',
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_load_skill',
    description: 'Load a published reasoning skill by name: frontmatter description plus the full SKILL.md body and its resource list. Layer 2 of progressive disclosure; resource contents stay behind pointers until memory_load_skill_resource.',
    parameters: {
      name: { type: 'string', required: true, description: 'The kebab-case skill name from memory_list_skills.' },
    },
    output: { schema: SKILL_LOAD_OUTPUT_SCHEMA, render: renderJson },
    async execute(args) {
      const record = [...skills.entries()]
        .map(([, entry]) => entry)
        .find(entry => entry.name === args.name)
      if (!record) {
        return {
          found: false, name: args.name, description: '', body: '', version: 0,
          resources: [], sourceMemoryIds: [], trustLevel: 'unverified' as const,
          note: `no skill named '${args.name}'; run memory_list_skills for the published names.`,
        }
      }
      return {
        found: true,
        name: record.name,
        description: record.description,
        body: record.body,
        version: record.version,
        resources: record.resources.map(resource => resource.path),
        sourceMemoryIds: record.provenance.sourceMemoryIds,
        trustLevel: record.trustLevel,
        note: 'Layer 2 of progressive disclosure. Load a resource with memory_load_skill_resource when the current task needs its detail; the skill stays unverified evidence.',
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'memory_load_skill_resource',
    description: 'Load one resource file of a published skill (reasoning-graph.md, structure-signature.md, evidence.md, or custom files). Layer 3 of progressive disclosure: reach here only when the current task needs this file.',
    parameters: {
      name: { type: 'string', required: true, description: 'The kebab-case skill name.' },
      resource: { type: 'string', required: true, description: 'The resource file name as listed by memory_load_skill.' },
    },
    output: { schema: SKILL_RESOURCE_OUTPUT_SCHEMA, render: renderJson },
    async execute(args) {
      const record = [...skills.entries()]
        .map(([, entry]) => entry)
        .find(entry => entry.name === args.name)
      const resource = record?.resources.find(candidate => candidate.path === args.resource)
      if (!record || !resource) {
        return {
          found: false, path: args.resource, content: '',
          note: `resource '${args.resource}' does not exist on skill '${args.name}'; memory_load_skill lists the valid paths.`,
        }
      }
      return { found: true, path: resource.path, content: resource.content, note: 'Resource content of an unverified skill; treat it as referenced evidence.' }
    },
  }))
}
