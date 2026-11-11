/** Agent-Skill filesystem export: publishes stored skills as a mountable
 *  skills/<name>/SKILL.md directory tree. Best-effort by design — a filesystem
 *  failure never blocks the stored skill, it only rides back in the tool note. */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderSkillIndex, renderSkillMarkdown } from './skill-format.js'
import type { Config } from './config.js'
import type { StorageLayer } from './storage-layer.js'

export function createSkillExport(config: Config, storage: StorageLayer) {
  const { skills } = storage
  const exportSkillDirectory = (): string => {
    const root = config.skillsExportDir!.trim()
    if (!root) return ''
    try {
      mkdirSync(join(root, 'skills'), { recursive: true })
      const records = [...skills.entries()].map(([, record]) => record)
      for (const record of records) {
        const directory = join(root, 'skills', record.name)
        mkdirSync(directory, { recursive: true })
        writeFileSync(join(directory, 'SKILL.md'), renderSkillMarkdown(record.name, record.description, record.body))
        for (const resource of record.resources) {
          writeFileSync(join(directory, resource.path), resource.content)
        }
      }
      writeFileSync(
        join(root, 'skills', 'README.md'),
        renderSkillIndex(records.map(record => ({ name: record.name, description: record.description }))),
      )
      return ''
    } catch (error) {
      return `filesystem export failed: ${error instanceof Error ? error.message : String(error)}`
    }
  }
  return exportSkillDirectory
}
