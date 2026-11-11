/** Small shared helpers used by the capture layer and tool modules. */
import type { Session } from '@deepseek-ai/dsh-session'

export function renderJson(_args: unknown, value: unknown) {
  return [{ type: 'text' as const, text: JSON.stringify(value, null, 2) }]
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function getSessionIdFromAgent(agent: unknown): string | undefined {
  if (!isRecord(agent) || !isRecord(agent.session) || !isRecord(agent.session.header)) return undefined
  const id = agent.session.header.id
  return typeof id === 'string' && id.length > 0 ? id : undefined
}

export function getSessionId(session: Session): string {
  return String(session.header.id)
}

export function clipText(value: string, maxLength: number): string {
  return value.trim().slice(0, maxLength)
}
