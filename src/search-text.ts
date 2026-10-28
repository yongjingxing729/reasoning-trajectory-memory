import type { MemoryItem } from './domain.js'

function graphSearchText(item: MemoryItem): string {
  const graph = item.reasoningGraph
  if (!graph?.nodes.length) return ''
  const textById = new Map(graph.nodes.map(node => [node.id, `${node.kind}: ${node.text}`]))
  return [
    ...graph.nodes.map(node => `${node.kind}: ${node.text}`),
    ...graph.edges.map(edge =>
      `${edge.relation}: ${textById.get(edge.sourceId) ?? edge.sourceId} -> ${textById.get(edge.targetId) ?? edge.targetId}`,
    ),
  ].join('\n')
}

export function memorySearchText(item: MemoryItem): string {
  return [
    `task: ${item.semanticText}`,
    `reasoning lesson: ${item.reasoning || item.content}`,
    `preconditions: ${item.preconditions.join('; ')}`,
    `actions: ${item.structureSignature.join(' -> ')}`,
    `logic graph:\n${graphSearchText(item)}`,
  ].filter(part => part.trim()).join('\n')
}

export function querySearchText(query: string, reasoningGraphJson = ''): string {
  const parts = [query.trim()]
  if (!reasoningGraphJson.trim()) return parts[0]
  try {
    const graph: unknown = JSON.parse(reasoningGraphJson)
    if (typeof graph !== 'object' || graph === null || !('nodes' in graph) || !Array.isArray(graph.nodes)) {
      return parts[0]
    }
    for (const node of graph.nodes) {
      if (typeof node === 'object' && node !== null && 'kind' in node && 'text' in node) {
        parts.push(`${String(node.kind)}: ${String(node.text)}`)
      }
    }
    if ('edges' in graph && Array.isArray(graph.edges)) {
      for (const edge of graph.edges) {
        if (typeof edge === 'object' && edge !== null && 'relation' in edge) {
          parts.push(`relation: ${String(edge.relation)}`)
        }
      }
    }
  } catch {
    // The Python validator returns the useful input error to the caller.
  }
  return parts.filter(Boolean).join('\n')
}
