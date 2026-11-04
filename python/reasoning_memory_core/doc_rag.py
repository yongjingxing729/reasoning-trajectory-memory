"""reasoning-aware document retrieval for the v0.5.0 document surface.

Deterministic, stdlib-only retrieval over user-imported document chunks
(final spec 0.5.0 section 3.2). No LLM at index or query time: the L1 task
pattern filter, the query expansion, and the bounded multi-hop walk are all
rule-based. Documents never enter the memory RRF channels or the
positive/negative quota; the audit stays on dedicated fields.
"""

from __future__ import annotations

import re
from typing import Any

from .constants import (
    DOC_EXPAND_CAP,
    DOC_HOP_DECAY,
    DOC_INTENT_WEIGHT_DELTAS,
    DOC_MAX_HOPS,
    DOC_QUERY_DEFAULT_K,
    DOC_QUERY_MAX_K,
    DOC_RRF_WEIGHTS,
    PATTERN_KEYWORDS,
    TASK_FAMILY_PATTERNS,
    TASK_PATTERNS,
)
from .reasoning_graph import _parse_reasoning_graph
from .templates import classify_task_pattern
from .text_similarity import _clean_text, semantic_similarity

_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if not isinstance(value, (int, float)):
        return low
    return max(low, min(high, float(value)))


def _documents_index(documents: list[dict[str, Any]] | dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if isinstance(documents, dict):
        return documents
    index: dict[str, dict[str, Any]] = {}
    for entry in documents or []:
        if isinstance(entry, dict) and entry.get("docId"):
            index[str(entry["docId"])] = entry
    return index


def _doc_patterns(doc: dict[str, Any]) -> list[str]:
    patterns = doc.get("taskPatterns") or []
    if isinstance(patterns, list):
        return [pattern for pattern in patterns if pattern in TASK_PATTERNS]
    return []


def _pattern_matches(doc: dict[str, Any], pattern: str) -> bool:
    patterns = _doc_patterns(doc)
    if not patterns or "general" in patterns or pattern == "general":
        return True
    return pattern in patterns


def _pattern_score(doc: dict[str, Any], pattern: str) -> float:
    patterns = _doc_patterns(doc)
    if pattern in patterns:
        return 1.0
    if "general" in patterns:
        return 0.5
    return 0.0


def _expansion_terms(query: str, pattern: str) -> list[str]:
    """Deterministic step-back generalization (no LLM).

    Appends the pattern vocabulary entries that hit the query plus the
    taskFamily mapping keys for the pattern, so related surface terms can
    reach the lexical index.
    """
    lowered = query.lower()
    terms: list[str] = []
    for keyword in PATTERN_KEYWORDS.get(pattern, ()):
        if keyword and keyword.lower() in lowered:
            terms.append(keyword)
    terms.extend(key for key, cls in TASK_FAMILY_PATTERNS if cls == pattern)
    return terms


def _graph_node_texts(reasoning_graph_json: str) -> list[str]:
    graph, _ = _parse_reasoning_graph(reasoning_graph_json)
    if not graph:
        return []
    return [
        node.get("text", "")
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("text"), str) and node["text"].strip()
    ]


def _intent_texts(active_template: dict[str, Any] | None, step_index: int) -> list[str]:
    if not isinstance(active_template, dict):
        return []
    steps = active_template.get("steps") or []
    selected: list[dict[str, Any]] = []
    if isinstance(step_index, int) and step_index > 0:
        selected = [
            step for step in steps
            if isinstance(step, dict) and step.get("index") == step_index
        ]
    else:
        # Without a concrete step, use the steps that declare document
        # retrieval as their module demand (the L2/L4 alignment point).
        selected = [
            step for step in steps
            if isinstance(step, dict)
            and any(
                isinstance(demand, dict) and demand.get("kind") == "retrieve_document"
                for demand in (step.get("moduleDemands") or [])
            )
        ]
    intents: list[str] = []
    for step in selected:
        for demand in step.get("moduleDemands") or []:
            if isinstance(demand, dict) and isinstance(demand.get("intent"), str) and demand["intent"].strip():
                intents.append(demand["intent"].strip())
    return intents


def _neighbors(chunk: dict[str, Any], reverse_refs: dict[str, list[str]], children: dict[str, list[str]]) -> list[str]:
    neighbors: list[str] = []
    for key in ("prevChunkId", "nextChunkId", "parentChunkId"):
        value = chunk.get(key)
        if isinstance(value, str) and value:
            neighbors.append(value)
    neighbors.extend(ref for ref in (chunk.get("refs") or []) if isinstance(ref, str) and ref)
    neighbors.extend(reverse_refs.get(str(chunk.get("chunkId", "")), []))
    neighbors.extend(children.get(str(chunk.get("chunkId", "")), []))
    return neighbors


def _fuse(
    rows: list[dict[str, Any]],
    weights: dict[str, float],
) -> tuple[str, list[dict[str, Any]]]:
    """Document-domain mini-RRF.

    RRF when both lexical and vector channels have ranked candidates;
    otherwise a weighted sum with the weights normalized over the active
    channels (channels without any non-zero signal are excluded).
    """
    rank_constant = 60
    channel_ranks: dict[str, dict[str, int]] = {}
    for field in ("lexical", "vector", "pattern", "link"):
        ordered = sorted(
            (row for row in rows if row[field] > 0.0),
            key=lambda row: (-row[field], row["chunkId"]),
        )
        channel_ranks[field] = {
            row["chunkId"]: rank for rank, row in enumerate(ordered, start=1)
        }
    use_rrf = bool(channel_ranks["lexical"]) and bool(channel_ranks["vector"])
    active = [field for field in weights if channel_ranks[field]]
    if use_rrf:
        normalizer = sum(weights[field] / (rank_constant + 1) for field in active)
        if normalizer > 0.0:
            for row in rows:
                fused = sum(
                    weights[field] / (rank_constant + channel_ranks[field][row["chunkId"]])
                    for field in active
                    if row["chunkId"] in channel_ranks[field]
                ) / normalizer
                row["score"] = round(min(1.0, fused), 6)
        return "rrf", rows
    total_weight = sum(weights[field] for field in active)
    for row in rows:
        if total_weight > 0.0:
            row["score"] = round(min(1.0, sum(
                weights[field] * row[field] for field in active
            ) / total_weight), 6)
        else:
            row["score"] = 0.0
    return "weighted", rows


def retrieve_document(
    *,
    query: str,
    reasoning_graph_json: str = "",
    chunks: list[dict[str, Any]],
    documents: list[dict[str, Any]] | dict[str, dict[str, Any]] | None = None,
    task_pattern: str = "",
    task_family: str = "",
    active_template_id: str = "",
    active_template: dict[str, Any] | None = None,
    step_index: int = 0,
    retrieval_intent: str = "",
    max_hops: int = DOC_MAX_HOPS,
    k: int = DOC_QUERY_DEFAULT_K,
    pseudo_feedback: bool = False,
    lexical_scores: dict[str, float] | None = None,
    vector_scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Reasoning-aware chunk retrieval (pattern filter + expansion + hops)."""
    clean_query = _clean_text(query)
    k = max(1, min(DOC_QUERY_MAX_K, int(k)))
    hops = max(0, min(DOC_MAX_HOPS, int(max_hops)))
    docs = _documents_index(documents)

    pattern = task_pattern if task_pattern in TASK_PATTERNS else ""
    if not pattern:
        pattern = classify_task_pattern(task_desc=clean_query, task_family=task_family)["pattern"]

    # L1 pattern filter with soft fallback to general documents.
    eligible = [
        chunk for chunk in chunks
        if isinstance(chunk, dict) and chunk.get("chunkId")
        and _pattern_matches(docs.get(str(chunk.get("docId", "")), {}), pattern)
    ]
    pattern_fallback = not eligible
    if not eligible:
        general_eligible = [
            chunk for chunk in chunks
            if isinstance(chunk, dict) and chunk.get("chunkId")
            and "general" in _doc_patterns(docs.get(str(chunk.get("docId", "")), {}))
        ]
        eligible = general_eligible
    if not eligible:
        return {
            "results": [], "pattern": pattern, "patternFallback": pattern_fallback,
            "expansion": {}, "fusionMode": "weighted",
            "audit": {"eligibleChunks": 0, "visitedChunks": 0, "seeds": 0, "maxHops": hops},
            "reason": "no document chunk matches the task pattern and no general document exists",
        }

    # Deterministic query expansion.
    graph_texts = _graph_node_texts(reasoning_graph_json)
    step_back_terms = _expansion_terms(clean_query, pattern)
    intent_texts = _intent_texts(active_template, step_index)
    expansion_parts = [clean_query, *graph_texts, *step_back_terms, *intent_texts]
    expanded_query = "\n".join(dict.fromkeys(part for part in expansion_parts if part))[:4000]

    # Channel scores. TS supplies BM25/vector scores; when absent, a
    # deterministic token-overlap fallback keeps the core standalone-testable.
    if isinstance(lexical_scores, dict):
        lexical = {chunk["chunkId"]: _clip(lexical_scores.get(chunk["chunkId"], 0.0)) for chunk in eligible}
    else:
        lexical = {
            chunk["chunkId"]: round(_clip(semantic_similarity(
                expanded_query, " ".join([*chunk.get("headingPath", []), chunk.get("text", "")]),
            )), 6)
            for chunk in eligible
        }
    if isinstance(vector_scores, dict):
        vector = {chunk["chunkId"]: _clip(vector_scores.get(chunk["chunkId"], 0.0)) for chunk in eligible}
    else:
        vector = {chunk["chunkId"]: 0.0 for chunk in eligible}
    pattern_scores = {
        chunk["chunkId"]: _pattern_score(docs.get(str(chunk.get("docId", "")), {}), pattern)
        for chunk in eligible
    }

    # Text-signal gate: pattern and link channels are auxiliary and must not
    # manufacture a perfect score for chunks that carry no query-text signal
    # at all. With zero lexical AND vector signal nothing matched the query,
    # so the answer is empty rather than a ranked list of 1.0 rows.
    has_text_signal = any(value > 0.0 for value in lexical.values()) or any(
        value > 0.0 for value in vector.values()
    )
    if not has_text_signal:
        return {
            "results": [], "pattern": pattern, "patternFallback": pattern_fallback,
            "expansion": {
                "graphNodeCount": len(graph_texts),
                "stepBackTerms": step_back_terms,
                "intentTexts": intent_texts,
                "pseudoFeedbackUsed": False,
            },
            "fusionMode": "weighted",
            "audit": {
                "eligibleChunks": len(eligible),
                "visitedChunks": 0,
                "seeds": 0,
                "maxHops": hops,
                "expandCap": DOC_EXPAND_CAP,
            },
            "reason": "no chunk matched the query text (lexical and vector channels are both empty)",
        }

    def base_of(chunk_id: str) -> float:
        return (
            DOC_RRF_WEIGHTS["lexical"] * lexical.get(chunk_id, 0.0)
            + DOC_RRF_WEIGHTS["vector"] * vector.get(chunk_id, 0.0)
            + DOC_RRF_WEIGHTS["pattern"] * pattern_scores.get(chunk_id, 0.0)
        )

    # Seeds: top-k eligible chunks by the base (lexical/vector/pattern) sum.
    seeds = sorted(eligible, key=lambda chunk: (-base_of(chunk["chunkId"]), chunk["chunkId"]))[:k]

    chunk_by_id = {chunk["chunkId"]: chunk for chunk in eligible}
    reverse_refs: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for chunk in eligible:
        for ref in (chunk.get("refs") or []):
            if isinstance(ref, str) and ref:
                reverse_refs.setdefault(ref, []).append(chunk["chunkId"])
        parent = chunk.get("parentChunkId")
        if isinstance(parent, str) and parent:
            children.setdefault(parent, []).append(chunk["chunkId"])

    # Bounded BFS expansion: depth <= hops, 0.6^hop decay, cap 16 chunks.
    visited: dict[str, dict[str, Any]] = {}
    queue: list[tuple[str, int, str]] = [(chunk["chunkId"], 0, "") for chunk in seeds]
    while queue:
        chunk_id, hop, via = queue.pop(0)
        if chunk_id in visited:
            continue
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        visited[chunk_id] = {"hop": hop, "via": via}
        if len(visited) >= DOC_EXPAND_CAP:
            break
        if hop >= hops:
            continue
        for neighbor_id in _neighbors(chunk, reverse_refs, children):
            if neighbor_id not in visited and neighbor_id in chunk_by_id:
                queue.append((neighbor_id, hop + 1, chunk_id))

    # Channel scores per visited chunk, with the link channel carrying the
    # 0.6^hop decay so the final score is the base fused with the decay.
    rows: list[dict[str, Any]] = []
    for chunk_id, state in visited.items():
        chunk = chunk_by_id[chunk_id]
        hop = state["hop"]
        link = round(DOC_HOP_DECAY ** hop, 6)
        rows.append({
            "chunkId": chunk_id,
            "docId": str(chunk.get("docId", "")),
            "seq": int(chunk.get("seq", 0)),
            "headingPath": list(chunk.get("headingPath") or []),
            "text": _clean_text(chunk.get("text"), 2000),
            "hop": hop,
            "viaChunkId": state["via"],
            "lexical": round(_clip(lexical.get(chunk_id, 0.0)), 6),
            "vector": round(_clip(vector.get(chunk_id, 0.0)), 6),
            "pattern": round(_clip(pattern_scores.get(chunk_id, 0.0)), 6),
            "link": link,
            "score": 0.0,
        })

    weights = dict(DOC_RRF_WEIGHTS)
    if retrieval_intent == "constrain":
        for key, delta in DOC_INTENT_WEIGHT_DELTAS.get("constrain", {}).items():
            weights[key] = max(0.0, min(1.0, weights.get(key, 0.0) + delta))
    fusion_mode, rows = _fuse(rows, weights)
    rows.sort(key=lambda row: (-row["score"], row["chunkId"]))

    pseudo_used = False
    if pseudo_feedback and rows:
        heading_terms = [
            term for row in rows[:3]
            for part in row["headingPath"]
            for term in _WORD_RE.findall(part)
        ]
        if heading_terms:
            pseudo_used = True
            # One deterministic pseudo-relevance round: re-run with the top
            # headings appended to the query. With TS-supplied BM25 scores the
            # rerun keeps those scores, so this round is most effective on the
            # core-side lexical fallback (tests and the offline harness).
            return retrieve_document(
                query=f"{clean_query}\n{' '.join(heading_terms)}",
                reasoning_graph_json=reasoning_graph_json,
                chunks=chunks,
                documents=documents,
                task_pattern=pattern,
                task_family=task_family,
                active_template_id=active_template_id,
                active_template=active_template,
                step_index=step_index,
                retrieval_intent=retrieval_intent,
                max_hops=hops,
                k=k,
                pseudo_feedback=False,
                lexical_scores=lexical_scores,
                vector_scores=vector_scores,
            )

    results = []
    for row in rows[:k]:
        doc = docs.get(row["docId"], {})
        results.append({
            **row,
            "docName": _clean_text(doc.get("name"), 80),
            "source": _clean_text(doc.get("source"), 200),
            "version": _clean_text(doc.get("version"), 40) or "1",
            "links": {
                "prevChunkId": chunk_by_id[row["chunkId"]].get("prevChunkId", ""),
                "nextChunkId": chunk_by_id[row["chunkId"]].get("nextChunkId", ""),
                "parentChunkId": chunk_by_id[row["chunkId"]].get("parentChunkId", ""),
            },
            "breakdown": {
                "lexical": row["lexical"],
                "vector": row["vector"],
                "pattern": row["pattern"],
                "link": row["link"],
            },
        })

    return {
        "results": results,
        "pattern": pattern,
        "patternFallback": pattern_fallback,
        "expansion": {
            "graphNodeCount": len(graph_texts),
            "stepBackTerms": step_back_terms,
            "intentTexts": intent_texts,
            "pseudoFeedbackUsed": pseudo_used,
        },
        "fusionMode": fusion_mode,
        "audit": {
            "eligibleChunks": len(eligible),
            "visitedChunks": len(visited),
            "seeds": len(seeds),
            "maxHops": hops,
            "expandCap": DOC_EXPAND_CAP,
        },
        "reason": "",
    }


def delete_document_plan(*, doc_id: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic chunk deletion list for one document (TS owns the write)."""
    return {
        "docId": doc_id,
        "chunkIds": [
            chunk["chunkId"] for chunk in chunks
            if isinstance(chunk, dict) and chunk.get("docId") == doc_id and chunk.get("chunkId")
        ],
    }
