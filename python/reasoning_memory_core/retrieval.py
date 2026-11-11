"""retrieval for the DSH reasoning-memory core."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from .causal_utils import causal_estimate
from .constants import (
    MAX_SUPERPOSITION_SIZE,
    MODULE_DEMAND_KINDS,
    RETRIEVAL_INTENT_WEIGHT_DELTAS,
    TAGS,
    TEMPLATE_RRF_WEIGHTS,
    TEMPLATE_WEIGHTED_WEIGHTS,
)
from .qprobability import build_superposition
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


# Fusion weight names to the score fields they rank (overrides arrive with
# the same vocabulary as the fusionWeights audit dict).
_FIELD_BY_WEIGHT = {
    "lexical": "lexicalScore",
    "reasoning": "reasoningScore",
    "vector": "vectorScore",
    "structure": "structureScore",
    "graph": "graphScore",
    "template": "templateScore",
}


def _apply_reciprocal_rank_fusion(
    rows: list[dict[str, Any]],
    *,
    use_structure: bool,
    use_graph: bool,
    template_weight: float = 0.0,
    weight_overrides: dict[str, float] | None = None,
    rank_constant: int = 60,
) -> None:
    if weight_overrides:
        channels = [
            (_FIELD_BY_WEIGHT[name], max(0.0, min(1.0, weight_overrides.get(name, 0.0))))
            for name in ("lexical", "reasoning", "vector", "structure", "graph", "template")
        ]
    elif template_weight > 0.0:
        channels = [
            ("lexicalScore", 0.25),
            ("reasoningScore", 0.10),
            ("vectorScore", 0.30),
        ]
        if use_structure:
            channels.append(("structureScore", 0.10))
        if use_graph:
            channels.append(("graphScore", 0.10))
        channels.append(("templateScore", template_weight))
    else:
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


def _superposition_audit(
    rows: list[dict[str, Any]],
    *,
    max_superposition_size: int,
    phase_from_causal: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the v0.7.0 superposition audit over already-selected rows.

    Returns (superposition[], quantum summary). Pure additive: it never touches
    ranking, RRF weights, the outcome quota, or the returned multiplier.
    """
    candidates: list[dict[str, Any]] = []
    for row in rows:
        phase = 0.0
        if phase_from_causal and row.get("causalApplied"):
            phase = 0.0 if row.get("causalUplift", 0.0) >= 0.0 else math.pi
        candidates.append({
            "id": str(row.get("memoryId", "")),
            "amplitude": float(row.get("combinedScore", 0.0) or 0.0),
            "phase": phase,
        })

    full = build_superposition(candidates=candidates)
    collapse_reason = ""
    coherence_before = full["coherence"]
    if len(candidates) > max_superposition_size:
        collapse_reason = "rank_cutoff"
        collapsed = build_superposition(candidates=candidates[:max_superposition_size])
        superposition = collapsed["superposition"]
        coherence_after = collapsed["coherence"]
    else:
        superposition = full["superposition"]
        coherence_after = full["coherence"]

    for entry in superposition:
        entry["collapseReason"] = collapse_reason

    quantum = {
        "coherence": coherence_after,
        "coherenceBefore": coherence_before,
        "coherenceAfter": coherence_after,
        "collapseReason": collapse_reason,
        "estimatorVersion": full["estimatorVersion"],
    }
    return superposition, quantum


def _retrieve(request: dict[str, Any]) -> dict[str, Any]:
    query = _clean_text(request.get("query"))
    events = request.get("events") if isinstance(request.get("events"), list) else []
    query_signature = structure_signature(events)
    route = request.get("route", "auto")
    if route not in ("auto", "semantic", "structure", "graph", "hybrid"):
        route = "auto"
    k = max(1, min(20, int(request.get("k", 4))))
    ratio = max(0.1, min(10.0, float(request.get("positiveNegativeRatio", 1.0))))
    threshold = 0.0
    raw_threshold = request.get("relevanceThreshold")
    if isinstance(raw_threshold, (int, float)):
        threshold = max(0.0, min(1.0, float(raw_threshold)))
    memories = request.get("memories") if isinstance(request.get("memories"), list) else []

    # v0.4.0 include_deprecated opt-out: deprecated memories are removed from
    # the candidate pool BEFORE the positive/negative quota is applied, so the
    # quota contract always holds against the returned rows (final spec 3.4).
    include_deprecated = True
    raw_include_deprecated = request.get("includeDeprecated")
    if isinstance(raw_include_deprecated, bool):
        include_deprecated = raw_include_deprecated
    deprecated_filtered_count = 0

    # v0.4.0 template channel inputs (all optional and additive).
    active_template_id = _clean_text(request.get("activeTemplateId"), 100)
    raw_active_template = request.get("activeTemplate")
    active_template = raw_active_template if isinstance(raw_active_template, dict) else None
    template_active = bool(active_template_id)
    step_index = 0
    raw_step_index = request.get("stepIndex")
    if isinstance(raw_step_index, int):
        step_index = max(0, min(1000, raw_step_index))
    retrieval_intent = str(request.get("retrievalIntent", ""))
    if retrieval_intent not in MODULE_DEMAND_KINDS:
        retrieval_intent = ""
    overrides: dict[str, float] = {}
    raw_overrides = request.get("fusionWeightsOverride")
    if isinstance(raw_overrides, dict):
        for key in ("context", "reasoning", "structure", "graph", "vector", "lexical", "template"):
            value = raw_overrides.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                overrides[key] = max(0.0, min(1.0, float(value)))

    # v0.6.0 causal utility inputs (optional and additive): per-memory causal
    # event histories computed into per-candidate estimates inside the loop.
    causal_events_by_memory: dict[str, list[dict[str, Any]]] = {}
    raw_causal = request.get("causalEventsByMemory")
    if isinstance(raw_causal, dict):
        for memory_id, events in raw_causal.items():
            if isinstance(events, list):
                causal_events_by_memory[str(memory_id)] = events

    # v0.7.0 quantum multi-probability inputs (optional and additive). When
    # disabled (the default) the quantum path is skipped entirely so the output
    # stays byte-identical to v0.6.0.
    quantum_enabled = bool(request.get("quantumDecisionEnabled", False))
    max_superposition_size = MAX_SUPERPOSITION_SIZE
    raw_max_superposition = request.get("maxSuperpositionSize")
    if isinstance(raw_max_superposition, (int, float)):
        max_superposition_size = max(1, min(MAX_SUPERPOSITION_SIZE, int(raw_max_superposition)))
    phase_from_causal = bool(request.get("phaseFromCausal", True))

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
    elif fusion_mode == "rrf" and template_active:
        fusion_weights = {**TEMPLATE_RRF_WEIGHTS, "context": 0.0}
    elif fusion_mode == "rrf":
        fusion_weights = {"context": 0.0, "reasoning": 0.10, "structure": 0.10, "graph": 0.15, "vector": 0.35, "lexical": 0.30}
    elif template_active:
        fusion_weights = {**TEMPLATE_WEIGHTED_WEIGHTS, "vector": 0.0, "lexical": 0.0}
    elif query_graph and query_signature:
        fusion_weights = {"context": 0.10, "reasoning": 0.35, "structure": 0.15, "graph": 0.40, "vector": 0.0, "lexical": 0.0}
    elif query_graph:
        fusion_weights = {"context": 0.15, "reasoning": 0.45, "structure": 0.0, "graph": 0.40, "vector": 0.0, "lexical": 0.0}
    elif query_signature:
        fusion_weights = {"context": 0.20, "reasoning": 0.50, "structure": 0.30, "graph": 0.0, "vector": 0.0, "lexical": 0.0}
    else:
        fusion_weights = {"context": 0.30, "reasoning": 0.70, "structure": 0.0, "graph": 0.0, "vector": 0.0, "lexical": 0.0}

    # Step-level intent micro adjustments (weighted fallback with a bound
    # template only; ablation overrides replace weights and skip adjustments).
    if fusion_mode != "rrf" and template_active and retrieval_intent and not overrides:
        for key, delta in RETRIEVAL_INTENT_WEIGHT_DELTAS.get(retrieval_intent, {}).items():
            if key in fusion_weights:
                fusion_weights[key] = max(0.0, min(1.0, fusion_weights.get(key, 0.0) + delta))
    if overrides:
        fusion_weights.update(overrides)

    if route == "graph" and not query_graph:
        reason = f"invalid reasoning graph: {graph_warning}" if graph_warning else "graph route requires a valid reasoning_graph_json"
        return {
            "results": [], "candidateScores": [], "queryHash": query_hash,
            "balance": {"positiveCount": 0, "negativeCount": 0, "ratio": ratio, "fallbackUsed": False, "thresholdFilteredCount": 0},
            "fusionWeights": fusion_weights,
            "fusionMode": fusion_mode,
            "templateChannelWeight": 0.0,
            "deprecatedFilteredCount": 0,
            "reason": reason,
        }

    if route == "structure" and not query_signature:
        return {
            "results": [], "candidateScores": [], "queryHash": query_hash,
            "balance": {"positiveCount": 0, "negativeCount": 0, "ratio": ratio, "fallbackUsed": False, "thresholdFilteredCount": 0},
            "fusionWeights": fusion_weights,
            "fusionMode": fusion_mode,
            "templateChannelWeight": 0.0,
            "deprecatedFilteredCount": 0,
            "reason": "no observable tool-action sequence is available for structure retrieval",
        }

    ranked: list[dict[str, Any]] = []
    for item in memories:
        if not isinstance(item, dict) or item.get("outcome") not in ("success", "fail"):
            continue
        if not include_deprecated and item.get("status") == "deprecated":
            deprecated_filtered_count += 1
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
        # Template channel: direct alignment wins outright; otherwise the
        # memory's structure signature is compared against the bound template.
        template_score = 0.0
        if template_active:
            memory_template_id = str(item.get("strategyTemplateId", ""))
            if memory_template_id and memory_template_id == active_template_id:
                template_score = 1.0
            elif active_template is not None:
                template_tags = [
                    tag for tag in (active_template.get("structureSignature") or []) if tag in TAGS
                ]
                if template_tags and signature:
                    template_score = 0.6 * sequence_similarity(
                        [tag for tag in signature if tag in TAGS], template_tags,
                    )
        template_score = round(min(1.0, max(0.0, template_score)), 6)
        deprecated = item.get("status") == "deprecated"
        feedback = item.get("feedback") if isinstance(item.get("feedback"), dict) else {}
        helpful = max(0, int(feedback.get("helpful", 0)))
        harmful = max(0, int(feedback.get("harmful", 0)))
        # Beta(1,1) smoothing and a bounded multiplier make self-reported
        # usefulness helpful but never an authority over evidence or relevance.
        utility_mean = (helpful + 1.0) / (helpful + harmful + 2.0)
        utility_multiplier = 0.8 + 0.4 * utility_mean
        # v0.6.0 causal utility: a stratified causal estimate may REPLACE the
        # Beta multiplier (bounded to the same [0.8, 1.2] band). It never
        # touches the RRF channel weights or the outcome quota. When the
        # estimate does not meet the sample gates, the Beta fallback above
        # stays in place verbatim (zero regression).
        causal_estimate_result = None
        memory_id = str(item.get("memoryId", ""))
        causal_events = causal_events_by_memory.get(memory_id)
        if isinstance(causal_events, list) and causal_events:
            causal_estimate_result = causal_estimate(events=causal_events)
            if causal_estimate_result["causalApplied"]:
                utility_multiplier = causal_estimate_result["causalMultiplier"]
        combined = (
            fusion_weights["context"] * context_score
            + fusion_weights["reasoning"] * reasoning_score
            + fusion_weights["structure"] * structure_score
            + fusion_weights["graph"] * graph_score
            + fusion_weights["lexical"] * lexical_score
            + fusion_weights.get("template", 0.0) * template_score
        )
        combined = min(1.0, combined * utility_multiplier)
        has_rrf_signal = any((reasoning_score, structure_score, graph_score, lexical_score, vector_score, template_score))
        if (fusion_mode == "rrf" and not has_rrf_signal) or (fusion_mode != "rrf" and combined <= 0.0):
            continue
        retrieval_item = dict(item)
        retrieval_item["reasoning"] = reasoning_text
        retrieval_item["reasoningGraph"] = memory_graph
        ranked.append({
            "item": retrieval_item,
            "memoryId": memory_id,
            "outcome": item["outcome"],
            "contextScore": round(context_score, 6),
            "reasoningScore": round(reasoning_score, 6),
            "semanticScore": round(semantic_score, 6),
            "structureScore": round(structure_score, 6),
            "graphScore": round(graph_score, 6),
            "lexicalScore": round(lexical_score, 6),
            "vectorScore": round(vector_score, 6),
            "templateScore": template_score,
            "deprecated": deprecated,
            "combinedScore": round(combined, 6),
            "utilityMultiplier": round(utility_multiplier, 6),
            "causalApplied": causal_estimate_result["causalApplied"] if causal_estimate_result else False,
            "causalUplift": causal_estimate_result["ateShrunk"] if causal_estimate_result else 0.0,
            "causalSamples": causal_estimate_result["nEff"] if causal_estimate_result else 0,
            "simpsonParadox": causal_estimate_result["simpsonFlip"] if causal_estimate_result else False,
            "causalStrata": ",".join(
                stratum["stratum"] for stratum in (causal_estimate_result["cate"] if causal_estimate_result else [])
            ),
            "estimatorVersion": causal_estimate_result["estimatorVersion"] if causal_estimate_result else "",
        })

    if fusion_mode == "rrf":
        _apply_reciprocal_rank_fusion(
            ranked,
            use_structure=bool(query_signature),
            use_graph=bool(query_graph),
            template_weight=fusion_weights.get("template", 0.0),
            weight_overrides=overrides or None,
        )
    hybrid_fallback = route == "hybrid" and not has_vector_candidates

    # Quality gate: ReasoningBank reports that relevance beats volume (k=1
    # outperformed k=2..4 for injection), so a configurable minimum fused score
    # drops weak candidates before the positive/negative quota is applied.
    threshold_filtered_count = 0
    if threshold > 0.0:
        kept = []
        for row in ranked:
            if row["combinedScore"] >= threshold:
                kept.append(row)
            else:
                threshold_filtered_count += 1
        ranked = kept

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

    # v0.7.0 superposition audit over the selected rows (additive, deferred
    # collapse): emitted only when the quantum path is enabled.
    superposition: list[dict[str, Any]] | None = None
    quantum_audit: dict[str, Any] | None = None
    if quantum_enabled:
        superposition, quantum_audit = _superposition_audit(
            selected,
            max_superposition_size=max_superposition_size,
            phase_from_causal=phase_from_causal,
        )

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
    template_top = sorted(
        (row for row in ranked if row["templateScore"] > 0.0),
        key=lambda row: (-row["templateScore"], row["memoryId"]),
    )[: min(k * 2, 20)]
    template_ranks = {row["memoryId"]: rank for rank, row in enumerate(template_top, start=1)}
    combined_ranks = {row["memoryId"]: rank for rank, row in enumerate(ranked, start=1)}
    selected_ranks = {row["memoryId"]: rank for rank, row in enumerate(selected, start=1)}
    audit_rows: dict[str, dict[str, Any]] = {}
    for row in context_top + reasoning_top + structure_top + graph_top + lexical_top + vector_top + template_top + selected:
        audit_rows[row["memoryId"]] = {
            key: row[key] for key in (
                "memoryId", "outcome", "contextScore", "reasoningScore",
                "semanticScore", "structureScore", "graphScore", "lexicalScore", "vectorScore",
                "templateScore", "combinedScore", "utilityMultiplier",
                "causalApplied", "causalUplift", "causalSamples", "simpsonParadox",
                "causalStrata", "estimatorVersion",
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
    simpson_note = ""
    if any(row.get("simpsonParadox") for row in selected):
        simpson_note = (
            "at least one selected memory shows a Simpson paradox: its overall "
            "utility sign differs from its per-task-pattern utility sign; read "
            "its utility by the current task pattern"
        )
    threshold_note = ""
    if threshold > 0.0 and threshold_filtered_count > 0:
        if not selected:
            threshold_note = f"{threshold_filtered_count} candidates fell below the relevance threshold and none qualified"
        else:
            threshold_note = f"{threshold_filtered_count} candidates fell below the relevance threshold and were excluded"
    result: dict[str, Any] = {
        "results": selected,
        "candidateScores": candidate_scores,
        "queryHash": query_hash,
        "balance": {
            "positiveCount": positive_count,
            "negativeCount": negative_count,
            "ratio": ratio,
            "fallbackUsed": fallback_used,
            "thresholdFilteredCount": threshold_filtered_count,
        },
        "fusionWeights": fusion_weights,
        "fusionMode": fusion_mode,
        "templateChannelWeight": fusion_weights.get("template", 0.0),
        "simpsonNote": simpson_note,
        "deprecatedFilteredCount": deprecated_filtered_count,
        "reason": (
            threshold_note if threshold_note
            else f"invalid task reasoning graph ignored: {graph_warning}" if graph_warning
            else "hybrid route had no dense-vector candidates; sparse and graph rankings were used" if hybrid_fallback
            else ""
        ),
    }
    # v0.7.0 additive output: the keys are absent when the quantum path is off,
    # keeping the dictionary shape byte-identical to v0.6.0.
    if quantum_enabled and superposition is not None and quantum_audit is not None:
        result["superposition"] = superposition
        result["quantum"] = quantum_audit
    return result
