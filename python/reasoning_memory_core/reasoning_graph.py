"""reasoning graph for the DSH reasoning-memory core."""

from __future__ import annotations

import json
import re
from typing import Any

from .constants import GRAPH_NODE_KINDS, GRAPH_RELATIONS, MAX_GRAPH_EDGES, MAX_GRAPH_NODES, MAX_GRAPH_NODE_TEXT, TAG_TEXT
from .text_similarity import semantic_similarity


def _parse_reasoning_graph(value: Any) -> tuple[dict[str, Any] | None, str]:
    if value is None or value == "":
        return None, ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            return None, f"invalid JSON: {error.msg}"
    if not isinstance(value, dict) or value.get("version") != 1:
        return None, "expected a version-1 graph object"
    nodes = value.get("nodes")
    edges = value.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return None, "nodes and edges must be arrays"
    if not 2 <= len(nodes) <= MAX_GRAPH_NODES:
        return None, f"graph must have 2-{MAX_GRAPH_NODES} nodes"
    if not 1 <= len(edges) <= MAX_GRAPH_EDGES:
        return None, f"graph must have 1-{MAX_GRAPH_EDGES} edges"

    normalized_nodes: list[dict[str, str]] = []
    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            return None, "every node must be an object"
        node_id = node.get("id")
        kind = node.get("kind")
        text = node.get("text")
        if not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", node_id):
            return None, "node ids must be 1-40 ASCII letters, digits, '_' or '-' and start with a letter"
        if node_id in node_ids:
            return None, f"duplicate node id: {node_id}"
        if not isinstance(kind, str) or kind not in GRAPH_NODE_KINDS:
            return None, f"unsupported node kind on {node_id}"
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_GRAPH_NODE_TEXT:
            return None, f"node {node_id} text must contain 1-{MAX_GRAPH_NODE_TEXT} characters"
        node_ids.add(node_id)
        normalized_nodes.append({"id": node_id, "kind": kind, "text": text.strip(), "source": "agent_input"})

    if not any(node["kind"] in ("context", "premise") for node in normalized_nodes):
        return None, "graph needs a context or premise node"
    if not any(node["kind"] in ("inference", "failure") for node in normalized_nodes):
        return None, "graph needs an inference or failure node"
    if not any(node["kind"] == "conclusion" for node in normalized_nodes):
        return None, "graph needs a conclusion node"

    normalized_edges: list[dict[str, str]] = []
    edge_keys: set[tuple[str, str, str]] = set()
    causal_adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    causal_indegree = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            return None, "every edge must be an object"
        source_id = edge.get("sourceId")
        target_id = edge.get("targetId")
        relation = edge.get("relation")
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            return None, "edge endpoints must be node-id strings"
        if source_id not in node_ids or target_id not in node_ids:
            return None, "edge endpoint does not refer to a graph node"
        if source_id == target_id:
            return None, "self-edges are not allowed"
        if not isinstance(relation, str) or relation not in GRAPH_RELATIONS:
            return None, f"unsupported edge relation: {relation}"
        key = (source_id, target_id, relation)
        if key in edge_keys:
            return None, "duplicate edges are not allowed"
        edge_keys.add(key)
        normalized_edges.append({
            "sourceId": source_id,
            "targetId": target_id,
            "relation": relation,
            "source": "agent_input",
        })
        if relation != "contradicts":
            causal_adjacency[source_id].append(target_id)
            causal_indegree[target_id] += 1

    # Reasoning dependencies are a DAG. Explicit contradiction edges may point
    # both ways because they describe incompatibility, not causal dependence.
    queue = [node_id for node_id, degree in causal_indegree.items() if degree == 0]
    visited = 0
    while queue:
        node_id = queue.pop()
        visited += 1
        for target_id in causal_adjacency[node_id]:
            causal_indegree[target_id] -= 1
            if causal_indegree[target_id] == 0:
                queue.append(target_id)
    if visited != len(node_ids):
        return None, "reasoning dependency edges contain a cycle"
    return {"version": 1, "nodes": normalized_nodes, "edges": normalized_edges}, ""

def _fallback_reasoning_graph(
    context: str,
    reasoning: str,
    outcome: str,
    signature: list[str],
    verifier: str = "",
    evidence: str = "",
) -> dict[str, Any]:
    lesson_kind = "inference" if outcome == "success" else "failure"
    nodes: list[dict[str, str]] = [
        {"id": "context", "kind": "context", "text": context[:MAX_GRAPH_NODE_TEXT] or "未提供任务情境", "source": "task_input"},
        {"id": "lesson", "kind": lesson_kind, "text": reasoning[:MAX_GRAPH_NODE_TEXT] or "历史版本未保存独立推理教训", "source": "agent_input"},
    ]
    edges: list[dict[str, str]] = [{
        "sourceId": "context", "targetId": "lesson", "relation": "frames", "source": "derived",
    }]
    action_ids: list[str] = []
    for index, tag in enumerate(signature[:12], start=1):
        action_id = f"action_{index}"
        action_ids.append(action_id)
        nodes.append({
            "id": action_id,
            "kind": "action",
            "text": TAG_TEXT.get(tag, tag)[:MAX_GRAPH_NODE_TEXT],
            "source": "tool_trace",
        })
    if action_ids:
        edges.append({
            "sourceId": "lesson", "targetId": action_ids[0], "relation": "guides", "source": "derived",
        })
        for previous_id, next_id in zip(action_ids, action_ids[1:]):
            edges.append({
                "sourceId": previous_id, "targetId": next_id, "relation": "precedes", "source": "tool_trace",
            })
    evidence_text = "；".join(part for part in (verifier, evidence) if part)
    if evidence_text:
        nodes.append({"id": "evidence", "kind": "evidence", "text": evidence_text[:MAX_GRAPH_NODE_TEXT], "source": "verifier_input"})
    nodes.append({"id": "outcome", "kind": "conclusion", "text": f"任务结果：{outcome}", "source": "outcome_input"})
    edges.append({
        "sourceId": "lesson", "targetId": "outcome",
        "relation": "supports" if outcome == "success" else "explains",
        "source": "agent_input",
    })
    if evidence_text:
        edges.append({
            "sourceId": "evidence", "targetId": "outcome", "relation": "supports", "source": "verifier_input",
        })
    if action_ids and evidence_text:
        edges.append({
            "sourceId": action_ids[-1], "targetId": "evidence", "relation": "precedes", "source": "derived",
        })
    return {"version": 1, "nodes": nodes, "edges": edges}

def graph_similarity(query_graph: dict[str, Any] | None, memory_graph: dict[str, Any] | None) -> float:
    if not query_graph or not memory_graph:
        return 0.0
    query_nodes = query_graph.get("nodes", [])
    memory_nodes = memory_graph.get("nodes", [])
    if not query_nodes or not memory_nodes:
        return 0.0

    possible_pairs: list[tuple[float, str, str]] = []
    for query_node in query_nodes:
        for memory_node in memory_nodes:
            if query_node.get("kind") != memory_node.get("kind"):
                continue
            score = semantic_similarity(str(query_node.get("text", "")), str(memory_node.get("text", "")))
            if score > 0.0:
                possible_pairs.append((score, str(query_node.get("id")), str(memory_node.get("id"))))
    possible_pairs.sort(key=lambda row: (-row[0], row[1], row[2]))
    mapping: dict[str, str] = {}
    matched_memory_ids: set[str] = set()
    for score, query_id, memory_id in possible_pairs:
        if query_id in mapping or memory_id in matched_memory_ids:
            continue
        mapping[query_id] = memory_id
        matched_memory_ids.add(memory_id)
    if not mapping:
        return 0.0

    node_precision = len(mapping) / len(memory_nodes)
    node_recall = len(mapping) / len(query_nodes)
    node_f1 = (2.0 * node_precision * node_recall / (node_precision + node_recall)) if node_precision + node_recall else 0.0

    query_edges = query_graph.get("edges", [])
    memory_edges = memory_graph.get("edges", [])
    if not query_edges or not memory_edges:
        return node_f1
    memory_edge_set = {
        (edge.get("sourceId"), edge.get("targetId"), edge.get("relation"))
        for edge in memory_edges
    }
    matched_edges = 0
    comparable_query_edges = 0
    for edge in query_edges:
        source_id = edge.get("sourceId")
        target_id = edge.get("targetId")
        if source_id not in mapping or target_id not in mapping:
            continue
        comparable_query_edges += 1
        if (mapping[source_id], mapping[target_id], edge.get("relation")) in memory_edge_set:
            matched_edges += 1
    edge_precision = matched_edges / len(memory_edges)
    edge_recall = matched_edges / max(1, comparable_query_edges)
    edge_f1 = (2.0 * edge_precision * edge_recall / (edge_precision + edge_recall)) if edge_precision + edge_recall else 0.0
    return round(0.60 * node_f1 + 0.40 * edge_f1, 6)
