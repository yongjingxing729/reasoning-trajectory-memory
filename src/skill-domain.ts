import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { z } from 'zod'

// Skill records live in their own storage domain so the existing
// reasoning-memory domain (v1) and its data stay untouched: adding the skill
// layer never migrates or invalidates existing installs. Skills are derived
// artifacts with provenance back to the source memories.
export const skillResourceSchema = z.object({
  path: z.string(),
  content: z.string(),
})

export const skillSchema = z.object({
  skillId: z.string(),
  name: z.string(),
  description: z.string(),
  body: z.string(),
  resources: z.array(skillResourceSchema),
  provenance: z.object({
    sourceMemoryIds: z.array(z.string()),
    sourceSessionId: z.string(),
    publishedAt: z.number().int().nonnegative(),
  }),
  version: z.number().int().positive(),
  trustLevel: z.enum(['unverified']),
  extractorVersion: z.string(),
  createdAt: z.number().int().nonnegative(),
  updatedAt: z.number().int().nonnegative(),
})

export type SkillRecord = z.infer<typeof skillSchema>
export type SkillResource = z.infer<typeof skillResourceSchema>

export const reasoningMemorySkillsDomain = defineDomain({
  name: 'reasoning_memory_skills',
  version: 1,
  layout: 'per-record',
  tables: {
    skills: domainTable<string, SkillRecord>(skillSchema),
  },
})
