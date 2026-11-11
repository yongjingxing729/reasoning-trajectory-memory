/** 捕获层: projects the DSH session event stream into bounded trajectory
 *  events and keeps the active-task registries.
 *
 * Capture is deliberately observable-only: tool names, event order, token
 * usage and (when enabled) reasoning deltas. Raw arguments, tool outputs,
 * prompts and full model reasoning are never stored here. */
import type { Context } from '@deepseek-ai/cordis'
import type { Session, SessionEvent } from '@deepseek-ai/dsh-session'
import type { Config } from './config.js'
import { getSessionId, isRecord } from './helpers.js'

export type CapturedEvent = {
  seq: number
  type: 'tool_call' | 'tool_result'
  callId: string
  toolName?: string
  success?: boolean
}
export type ActiveTask = {
  taskId: string
  sessionId: string
  taskFamily: string
  semanticText: string
  startedAt: number
  events: CapturedEvent[]
  captureTruncated: boolean
  pendingToolNames: Map<string, string>
  retrievedMemoryIds: Set<string>
  feedbackMemoryIds: Set<string>
  planTemplateIds: Set<string>
  templateFeedbackRecorded: Set<string>
  causalEventsRecorded: boolean
  counterfactualRecorded: Set<string>
  reasoningText: string
  reasoningCaptureStatus: 'disabled' | 'provided' | 'not-provided'
  capturedModel: { provider: string; model: string } | null
  usage: { inputTokens: number; outputTokens: number; reasoningTokens: number }
}

export function createCaptureLayer(config: Config) {
  const activeBySession = new Map<string, ActiveTask>()
  const activeByTaskId = new Map<string, ActiveTask>()
  const ignoredMemoryCalls = new Map<string, Set<string>>()

  const attach = (ctx: Context): void => {
    ctx.on('session/event', (session: Session, event: SessionEvent) => {
      const sessionId = getSessionId(session)

      if (event.type === 'tool/call') {
        const name = event.data.name
        const callId = String(event.data.callId)
        if (name.startsWith('memory_')) {
          let ignored = ignoredMemoryCalls.get(sessionId)
          if (!ignored) {
            ignored = new Set<string>()
            ignoredMemoryCalls.set(sessionId, ignored)
          }
          ignored.add(callId)
          return
        }
        const active = activeBySession.get(sessionId)
        if (!active) return
        if (active.events.length >= config.maxTrajectoryEvents!) {
          active.captureTruncated = true
          return
        }
        active.pendingToolNames.set(callId, name)
        active.events.push({ seq: Number(event.seq), type: 'tool_call', callId, toolName: name })
        return
      }

      if (event.type === 'tool/result') {
        const message = event.data.message as unknown as Record<string, unknown>
        const source = isRecord(message.source) ? message.source : undefined
        const possibleId = source?.callId ?? message.toolCallId ?? message.callId
        const callId = typeof possibleId === 'string' ? possibleId : ''
        const ignored = ignoredMemoryCalls.get(sessionId)
        if (ignored?.delete(callId)) {
          if (ignored.size === 0) ignoredMemoryCalls.delete(sessionId)
          return
        }
        const active = activeBySession.get(sessionId)
        if (!active) return
        if (active.events.length >= config.maxTrajectoryEvents!) {
          active.captureTruncated = true
          return
        }
        const toolName = active.pendingToolNames.get(callId)
        const failed = message.isError === true || event.data.error !== undefined
        active.events.push({
          seq: Number(event.seq),
          type: 'tool_result',
          callId,
          ...(toolName ? { toolName } : {}),
          success: !failed,
        })
        active.pendingToolNames.delete(callId)
      }

      if (event.type === 'assistant/message') {
        const active = activeBySession.get(sessionId)
        if (!active) return
        const data = event.data as unknown as {
          message?: { source?: { provider?: unknown; model?: unknown } }
        }
        const source = data.message?.source
        if (typeof source?.provider === 'string' && typeof source?.model === 'string') {
          active.capturedModel = { provider: source.provider, model: source.model }
        }
      }

      if (event.type === 'assistant/chunk') {
        const active = activeBySession.get(sessionId)
        if (!active) return
        const chunk = (event.data as unknown as {
          chunk?: { type?: unknown; text?: unknown; usage?: { inputTokens?: unknown; outputTokens?: unknown; reasoningTokens?: unknown } }
        }).chunk
        if (!chunk || typeof chunk !== 'object') return
        if (chunk.type === 'usage' && chunk.usage && typeof chunk.usage === 'object') {
          if (typeof chunk.usage.inputTokens === 'number') active.usage.inputTokens += chunk.usage.inputTokens
          if (typeof chunk.usage.outputTokens === 'number') active.usage.outputTokens += chunk.usage.outputTokens
          if (typeof chunk.usage.reasoningTokens === 'number') active.usage.reasoningTokens += chunk.usage.reasoningTokens
          return
        }
        if (chunk.type !== 'reasoning-delta' || typeof chunk.text !== 'string' || !chunk.text) return
        if (!config.captureReasoningEnabled) {
          if (active.reasoningCaptureStatus !== 'provided') active.reasoningCaptureStatus = 'disabled'
          return
        }
        const remaining = config.maxReasoningTextChars! - active.reasoningText.length
        if (remaining <= 0) return
        active.reasoningText += chunk.text.slice(0, remaining)
        active.reasoningCaptureStatus = 'provided'
      }
    })

    ctx.on('session/disposed', (session: Session) => {
      const sessionId = getSessionId(session)
      const active = activeBySession.get(sessionId)
      if (active) activeByTaskId.delete(active.taskId)
      activeBySession.delete(sessionId)
      ignoredMemoryCalls.delete(sessionId)
    })
  }

  return { activeBySession, activeByTaskId, ignoredMemoryCalls, attach }
}

export type CaptureLayer = ReturnType<typeof createCaptureLayer>
