import type { LlmRuntime, PreparedLlmCall, StreamChunk } from '@deepseek-ai/dsh-llm'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

export type DistilledMemoryItem = {
  title: string
  description: string
  content: string
  contraindications: string[]
  outcome?: 'success' | 'fail'
}

export type DistillationKind = 'success' | 'fail' | 'consolidate'

export type DistillationTrajectory = {
  query: string
  actions: string
  evidence: string
  reasoningText: string
}

export type ConsolidateExperience = {
  outcome: 'success' | 'fail'
  context: string
  reasoning: string
  actions: string
  evidence: string
  title: string
}

// Distillation prompts adapt the ReasoningBank SUCCESSFUL_SI / FAILED_SI /
// PARALLEL_SI evidence: think about the cause first, at most N items, prefer
// concrete actionable procedures over abstract principles, and forbid embedding
// task-specific strings so that items stay transferable. Items are data, and
// every stored item stays unverified regardless of how plausible it reads.
export function distillSystemPrompt(kind: Exclude<DistillationKind, 'consolidate'>, maxItems: number): string {
  const header = 'You are an expert in agent tool workflows. You will be given a task query, the abstract tool-action sequence an agent executed, the task outcome with its evidence, and optionally the agent\'s own reasoning record.'
  const goal = kind === 'success'
    ? 'The task succeeded. First think about why the trajectory succeeded, then summarize the insights as transferable memory items.'
    : 'The task failed. First reflect on why the trajectory failed and attribute the cause using the provided evidence, then summarize lessons that prevent the failure from recurring. Do not invent an attribution the evidence does not support.'
  return `${header}

## Guidelines
${goal}
Extract at most ${maxItems} memory items from the trajectory.
- Prefer concrete, actionable procedures over abstract principles.
- Do not embed specific task strings, identifiers, product names, or literal query contents; write lessons that generalize to structurally similar but surface-different tasks.
- Do not repeat similar or overlapping items.
- Content must describe reasoning strategy or failure attribution, not restate the task description.

## Output Format
Your output must strictly follow this Markdown format:

# Memory Item i
## Title <brief label of the strategy or lesson>
## Description <one sentence: when this item applies or does NOT apply>
## Content <1-3 sentences: concrete, actionable, transferable insights>
## Contraindications <bullet list of conditions under which the item must NOT be applied; may be empty>

Output each item once, with no other text.`
}

export function consolidateSystemPrompt(maxItems: number): string {
  return `You are an expert in agent tool workflows. You will be given several reasoning memories distilled from repeated attempts of the same kind of task; some attempts succeeded and some failed.

## Guidelines
Use self-contrast reasoning: compare and contrast the experiences to identify the most useful and generalizable strategies as memory items.
- Identify patterns and strategies that consistently led to success.
- Identify mistakes from failed attempts and formulate preventative strategies.
- Each item must be marked with its outcome: success for strategies drawn from successful attempts, fail for lessons drawn primarily from failed attempts.
- Extract at most ${maxItems} memory items in total.
- Do not repeat similar or overlapping items.
- Do not embed specific task strings, identifiers, product names, or literal query contents; write lessons that generalize.

## Output Format
Your output must strictly follow this Markdown format:

# Memory Item i
## Title <brief label of the strategy or lesson>
## Description <one sentence: when this item applies or does NOT apply>
## Content <1-3 sentences: concrete, actionable, transferable insights>
## Contraindications <bullet list of conditions under which the item must NOT be applied; may be empty>
## Outcome <success or fail>

Output each item once, with no other text.`
}

export function distillUserText(trajectory: DistillationTrajectory, outcome: 'success' | 'fail'): string {
  const parts = [
    `**Query:** ${trajectory.query}`,
    `**Outcome:** ${outcome}`,
    `**Abstract tool-action sequence:** ${trajectory.actions || '(none observed)'}`,
    `**Evidence:** ${trajectory.evidence}`,
  ]
  if (trajectory.reasoningText.trim()) {
    parts.push(`**Model reasoning record (provided by the agent, may be incomplete):**\n${trajectory.reasoningText}`)
  }
  return parts.join('\n\n')
}

export function consolidateUserText(goal: string, experiences: ConsolidateExperience[]): string {
  const parts: string[] = []
  if (goal.trim()) parts.push(`**Goal:** ${goal}`)
  experiences.forEach((experience, index) => {
    parts.push([
      `## Experience ${index + 1} (outcome: ${experience.outcome})`,
      `Title: ${experience.title || '(untitled)'}`,
      `Task context: ${experience.context}`,
      `Reasoning lesson: ${experience.reasoning}`,
      `Action structure: ${experience.actions || '(none recorded)'}`,
      `Evidence: ${experience.evidence}`,
    ].join('\n'))
  })
  return parts.join('\n\n')
}

// Deterministic stream collection: join every `text-delta` chunk in stream
// order. Adapter failures surface as thrown errors from iteration.
export async function collectStreamText(stream: AsyncIterable<StreamChunk>): Promise<string> {
  let text = ''
  for await (const chunk of stream) {
    if (chunk.type === 'text-delta') text += chunk.text
  }
  return text.trim()
}

const FIELD_STOP = '(?=\\n##\\s*(?:Title|Description|Content|Contraindications|Outcome)\\b|\\s*$)'

// Deterministic Markdown parser for the distillation output format above.
// Blocks are split on "# Memory Item i" headers; fields are located by their
// "## Name" lines; Outcome is optional unless requireOutcome is set.
export function parseMemoryItems(text: string, requireOutcome: boolean): DistilledMemoryItem[] {
  const items: DistilledMemoryItem[] = []
  const blocks = text.split(/(?:^|\n)\s*#\s*Memory\s+Item\s*\d+\s*[:\-]?/i).slice(1)
  for (const block of blocks) {
    const grab = (name: string): string => {
      const match = block.match(new RegExp(`##\\s*${name}\\s*:?\\s*([\\s\\S]*?)${FIELD_STOP}`, 'i'))
      return match ? match[1].trim() : ''
    }
    const title = grab('Title')
    const description = grab('Description')
    const content = grab('Content')
    const outcomeMatch = block.match(/##\s*Outcome\s*:?\s*(success|fail)\b/i)
    const outcome = outcomeMatch?.[1] as 'success' | 'fail' | undefined
    if (!content.trim()) continue
    if (requireOutcome && !outcome) continue
    const contraindications = grab('Contraindications')
      .split(/(?:^|\n)\s*[-*]\s+/)
      .slice(1)
      .map(entry => entry.trim())
      .filter(Boolean)
    items.push({
      title: title.slice(0, 120) || content.trim().slice(0, 40),
      description: description.slice(0, 300),
      content: content.trim(),
      contraindications,
      ...(outcome ? { outcome } : {}),
    })
  }
  return items
}

// One LLM distillation call under the resolved model, returning parsed items.
export async function distillWithLlm(
  llm: LlmRuntime,
  provider: string,
  model: string,
  system: string,
  user: string,
  temperature: number,
  signal: AbortSignal,
): Promise<DistilledMemoryItem[]> {
  const prepared: PreparedLlmCall = await llm.prepareCall({ provider, model, temperature }, signal)
  const userMessage = createUserMessage({
    content: [{ type: 'text', text: user }],
    source: { kind: 'plugin', plugin: 'reasoning-memory' },
  })
  const stream = prepared.stream({
    provider: prepared.config.provider,
    model: prepared.config.model,
    temperature: prepared.config.temperature,
    messages: [userMessage],
    system,
    signal,
  })
  const text = await collectStreamText(stream)
  return parseMemoryItems(text, false)
}
