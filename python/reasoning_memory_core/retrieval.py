"""retrieval for the DSH reasoning-memory core."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from .constants import TAGS
from .reasoning_graph import _fallback_reasoning_graph, _parse_reasoning_graph, graph_similarity
from .text_similarity import _clean_text, _reasoning_text, semantic_similarity, sequence_similarity
from .trajectory import structure_signature


def _rank_score(item: dict[str, Any], field: str) -> float:
    value = item.get(field, 0.0)
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _vector_score(item: dict[str, Any]) -> float:
    return _rank_score(item, "vectorScore")


def _apply_reciprocal_rank_fusion(
    rows: list[dict[str, Any]],
    *,
    use_structure: bool,
    use_graph: bool,
    rank_constant: int = 60,
) -> None:
    channels = [
        ("lexicalScore", 0.30),
        ("reasoningScore", 0.10),
        ("vectorScore", 0.35),
    ]
    if use_structure:
        channels.append(("structureScore", 0.10))
    if use_graph:
        channels.append(("graphScore", 0.15))

    channel_ranks: dict[str, dict[str, int]] = {}
    for field, _ in channels:
        ordered = sorted(
            (row for row in rows if row[field] > 0.0),
            key=lambda row: (-row[field], row["memoryId"]),
        )
        channel_ranks[field] = {
            row["memoryId"]: rank for rank, row in enumerate(ordered, start=1)
        }
    active = [
        (field, weight)
        for field, weight in channels
        if channel_ranks[field]
    ]
    normalizer = sum(weight / (rank_constant + 1) for _, weight in active)
    if normalizer == 0.0:
        return
    for row in rows:
        fused = sum(
            weight / (rank_constant + channel_ranks[field][row["memoryId"]])
            for field, weight in active
            if row["memoryId"] in channel_ranks[field]
        ) / normalizer
        row["combinedScore"] = round(min(1.0, fused * row["utilityMultiplier"]), 6)


def _retrieve(request: dict[str, Any]) -> dict[str, Any]:
    query = _clean_text(request.get("query"))
    events = request.get("events") if isinstance(request.get("events"), list) else []
    query_signature = structure_signature(events)
    route = request.get("route", "auto")
    if route not in ("auto", "semantic", "structure", "graph", "hybrid"):
        route = "auto"
    k = max(1, min(20, int(request.get("k", 4))))
    ratio = max(0.1, min(10.0, float(request.get("positiveNegativeRatio", 1.0))))
    memories = request.get("memories") if isinstance(request.get("memories"), list) else []
    has_sparse_candidates = any(
        isinstance(item, dict) and _rank_score(item, "lexicalScore") > 0.0
        for item in memories
    )
    has_vector_candidates = any(
        isinstance(item, dict) and _vector_score(item) > 0.0
        for item in memories
    )
    has_ranked_candidates = has_sparse_candidates or has_vector_candidates
    fusion_mode = "rrf" if route in ("auto", "hybrid") and has_ranked_candidates else "weighted"
    excluded_task_id = _clean_text(request.get("excludeTaskId"), 100)
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    query_graph, graph_warning = _parse_reasoning_graph(request.get("queryGraphJson"))

    if route == "graph":
        fusion_weights = {"context": 0.0, "reasoning": 0.0, "structure": 0.0, "graph": 1.0, "vector": 0.0, "lexical": 0.0}
    elif route == "structure":
        fusion_weights = {"context": 0.0, "reasoning": 0.0, "structure": 1.0, "graph": 0.0, "vector": 0.0, "lexical": 0.0}
    elif route == "semantic":
        fusion_weights = {"context": 0.30, "reasoning": 0.70, "structure": 0.0, "graph": 0.0, "vector": 0.0, "lexical": 0.0}
    elif fusion_mode == "rrf":
        fusion_weights = {"context": 0.0, "reasoning": 0.10, "structure": 0.10, "graph": 0.15, "vector": 0.35, "lexical": 0.30}
    elif query_graph and query_signature:
        fusion_weights = {"context": 0.10, "reasoning": 0.35, "structure": 0.15, "graph": 0.40, "vector": 0.0, "lexical": 0.0}
    elif query_graph:
        fusion_weights = {"context": 0.15, "reasoning": 0.45, "structure": 0.0, "graph": 0.40, "vector": 0.0, "lexical": 0.0}
    elif query_signature:
        fusion_weights = {"context": 0.20, "reasoning": 0.50, "structure": 0.30, "graph": 0.0, "vector": 0.0, "lexical": 0.0}
    else:
        fusion_weights = {"context": 0.30, "reasoning": 0.70, "structure": 0.0, "graph": 0.0, "vector": 0.0, "lexical": 0.0}

    if route == "graph" and not query_graph:
        reason = f"invalid reasoning graph: {graph_warning}" if graph_warning else "graph route requires a valid reasoning_graph_json"
        return {
            "results": [], "candidateScores": [], "queryHash": query_hash,
            "balance": {"positiveCount": 0, "negativeCount": 0, "ratio": ratio, "fallbackUsed": False},
            "fusionWeights": fusion_weights,
            "fusionMode": fusion_mode,
            "reason": reason,
        }

    if route == "structure" and not query_signature:
        return {
            "results": [], "candidateScores": [], "queryHash": query_hash,
            "balance": {"positiveCount": 0, "negativeCount": 0, "ratio": ratio, "fallbackUsed": False},
            "fusionWeights": fusion_weights,
            "fusionMode": fusion_mode,
            "reason": "no observable tool-action sequence is available for structure retrieval",
        }

    ranked: list[dict[str, Any]] = []
    for item in memories:
        if not isinstance(item, dict) or item.get("outcome") not in ("success", "fail"):
            continue
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        if excluded_task_id and provenance.get("taskId") == excluded_task_id:
            continue
        context_score = semantic_similarity(query, str(item.get("semanticText", "")))
        reasoning_text = _reasoning_text(item)
        reasoning_score = semantic_similarity(query, reasoning_text)
        lexical_score = _rank_score(item, "lexicalScore")
        # The distilled strategy/failure lesson is the main semantic signal;
        # task wording provides context but cannot dominate retrieval.
        semantic_score = 0.30 * context_score + 0.70 * reasoning_score
        signature = item.get("structureSignature") if isinstance(item.get("structureSignature"), list) else []
        structure_score = sequence_similarity(query_signature, [tag for tag in signature if tag in TAGS])
        memory_graph = item.get("reasoningGraph")
        if not isinstance(memory_graph, dict) or not memory_graph.get("nodes"):
            memory_graph = _fallback_reasoning_graph(
                str(item.get("semanticText", "")), reasoning_text, item["outcome"],
                [tag for tag in signature if tag in TAGS],
            )
        graph_score = graph_similarity(query_graph, memory_graph)
        vector_score = _vector_score(item)
        feedback = item.get("feedback") if isinstance(item.get("feedback"), dict) else {}
        helpful = max(0, int(feedback.get("helpful", 0)))
        harmful = max(0, int(feedback.get("harmful", 0)))
        # Beta(1,1) smoothing and a bounded multiplier make self-reported
        # usefulness helpful but never an authority over evidence or relevance.
        utility_mean = (helpful + 1.0) / (helpful + harmful + 2.0)
        utility_multiplier = 0.8 + 0.4 * utility_mean
        combined = (
            fusion_weights["context"] * context_score
            + fusion_weights["reasoning"] * reasoning_score
            + fusion_weights["structure"] * structure_score
            + fusion_weights["graph"] * graph_score
            + fusion_weights["lexical"] * lexical_score
        )
        combined = min(1.0, combined * utility_multiplier)
        has_rrf_signal = any((reasoning_score, structure_score, graph_score, lexical_score, vector_score))
        if (fusion_mode == "rrf" and not has_rrf_signal) or (fusion_mode != "rrf" and combined <= 0.0):
            continue
        retrieval_item = dict(item)
        retrieval_item["reasoning"] = reasoning_text
        retrieval_item["reasoningGraph"] = memory_graph
        ranked.append({
            "item": retrieval_item,
            "memoryId": str(item.get("memoryId", "")),
            "outcome": item["outcome"],
            "contextScore": round(context_score, 6),
            "reasoningScore": round(reasoning_score, 6),
            "semanticScore": round(semantic_score, 6),
            "structureScore": round(structure_score, 6),
            "graphScore": round(graph_score, 6),
            "lexicalScore": round(lexical_score, 6),
            "vectorScore": round(vector_score, 6),
            "combinedScore": round(combined, 6),
            "utilityMultiplier": round(utility_multiplier, 6),
        })

    if fusion_mode == "rrf":
        _apply_reciprocal_rank_fusion(
            ranked,
            use_structure=bool(query_signature),
            use_graph=bool(query_graph),
        )
    hybrid_fallback = route == "hybrid" and not has_vector_candidates

    ranked.sort(key=lambda row: (-row["combinedScore"], row["memoryId"]))
    positive = [row for row in ranked if row["outcome"] == "success"]
    negative = [row for row in ranked if row["outcome"] == "fail"]
    positive_slots = int((k * ratio / (1.0 + ratio)) + 0.5)
    positive_slots = min(k, max(0, positive_slots))
    negative_slots = k - positive_slots
    selected = positive[:positive_slots] + negative[:negative_slots]
    selected_ids = {row["memoryId"] for row in selected}
    fallback_used = len(positive[:positive_slots]) < positive_slots or len(negative[:negative_slots]) < negative_slots
    if len(selected) < k:
        fallback_used = True
        selected.extend(row for row in ranked if row["memoryId"] not in selected_ids)
    selected = selected[:k]
    selected.sort(key=lambda row: (-row["combinedScore"], row["memoryId"]))
    if k == 1 and positive and negative:
        fallback_used = True

    context_top = sorted(ranked, key=lambda row: (-row["contextScore"], row["memoryId"]))[: min(k * 2, 20)]
    reasoning_top = sorted(ranked, key=lambda row: (-row["reasoningScore"], row["memoryId"]))[: min(k * 2, 20)]
    structure_top = sorted(ranked, key=lambda row: (-row["structureScore"], row["memoryId"]))[: min(k * 2, 20)]
    graph_top = sorted(ranked, key=lambda row: (-row["graphScore"], row["memoryId"]))[: min(k * 2, 20)]
    lexical_top = sorted(
        (row for row in ranked if row["lexicalScore"] > 0.0),
        key=lambda row: (-row["lexicalScore"], row["memoryId"]),
    )[: min(k * 2, 20)]
    vector_top = sorted(
        (row for row in ranked if row["vectorScore"] > 0.0),
        key=lambda row: (-row["vectorScore"], row["memoryId"]),
    )[: min(k * 2, 20)]
    context_ranks = {row["memoryId"]: rank for rank, row in enumerate(context_top, start=1)}
    reasoning_ranks = {row["memoryId"]: rank for rank, row in enumerate(reasoning_top, start=1)}
    structure_ranks = {row["memoryId"]: rank for rank, row in enumerate(structure_top, start=1)}
    graph_ranks = {row["memoryId"]: rank for rank, row in enumerate(graph_top, start=1)}
    lexical_ranks = {row["memoryId"]: rank for rank, row in enumerate(lexical_top, start=1)}
    vector_ranks = {row["memoryId"]: rank for rank, row in enumerate(vector_top, start=1)}
    combined_ranks = {row["memoryId"]: rank for rank, row in enumerate(ranked, start=1)}
    selected_ranks = {row["memoryId"]: rank for rank, row in enumerate(selected, start=1)}
    audit_rows: dict[str, dict[str, Any]] = {}
    for row in context_top + reasoning_top + structure_top + graph_top + lexical_top + vector_top + selected:
        audit_rows[row["memoryId"]] = {
            key: row[key] for key in (
                "memoryId", "outcome", "contextScore", "reasoningScore",
                "semanticScore", "structureScore", "graphScore", "lexicalScore", "vectorScore", "combinedScore", "utilityMultiplier",
            )
        }
    candidate_scores = []
    for memory_id, row in audit_rows.items():
        candidate_scores.append({
            **row,
            "contextRank": context_ranks.get(memory_id),
            "reasoningRank": reasoning_ranks.get(memory_id),
            "structureRank": structure_ranks.get(memory_id),
            "graphRank": graph_ranks.get(memory_id),
            "lexicalRank": lexical_ranks.get(memory_id),
            "vectorRank": vector_ranks.get(memory_id),
            "combinedRank": combined_ranks[memory_id],
            "selectedRank": selected_ranks.get(memory_id),
        })

    positive_count = sum(row["outcome"] == "success" for row in selected)
    negative_count = sum(row["outcome"] == "fail" for row in selected)
    return {
        "results": selected,
        "candidateScores": candidate_scores,
        "queryHash": query_hash,
        "balance": {
            "positiveCount": positive_count,
            "negativeCount": negative_count,
            "ratio": ratio,
            "fallbackUsed": fallback_used,
        },
        "fusionWeights": fusion_weights,
        "fusionMode": fusion_mode,
        "reason": (
            f"invalid task reasoning graph ignored: {graph_warning}" if graph_warning
            else "hybrid route had no dense-vector candidates; sparse and graph rankings were used" if hybrid_fallback
            else ""
        ),
    }
