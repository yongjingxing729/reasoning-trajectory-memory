"""Read-only joint planning: task-pattern classification plus template match."""

from __future__ import annotations

from typing import Any

from .constants import (
    MAX_SUPERPOSITION_SIZE,
    MAX_TEMPLATE_DESCRIPTION_CHARS,
    MAX_TEMPLATE_NAME_CHARS,
    TEMPLATE_MATCH_MIN,
    TEMPLATE_MATCH_STRONG,
)
from .qprobability import build_superposition
from .template_build import _active_retrieval_points, _prior_utility
from .template_classify import _clip, classify_task_pattern
from .template_match import template_match_score
from .text_similarity import _clean_text
from .trajectory import structure_signature


def plan_task(
    *,
    task_desc: str,
    goal: str = "",
    task_family: str = "",
    events: list[dict[str, Any]] | None = None,
    has_graph: bool = False,
    graph_node_kinds: list[str] | None = None,
    templates: list[dict[str, Any]] | None = None,
    max_templates: int = 2,
    template_match_min: float = TEMPLATE_MATCH_MIN,
    quantum_decision_enabled: bool = False,
    max_superposition_size: int = MAX_SUPERPOSITION_SIZE,
    phase_from_causal: bool = True,
    decoherence_rate: float = 0.0,
) -> dict[str, Any]:
    """Read-only joint decision: classify the task and match templates."""
    raw_events = events if isinstance(events, list) else []
    signature = structure_signature(raw_events)
    tool_names = sorted({
        str(event.get("toolName", ""))
        for event in raw_events
        if isinstance(event, dict) and str(event.get("toolName", ""))
        and not str(event.get("toolName", "")).startswith("memory_")
    })

    classification = classify_task_pattern(
        task_desc=task_desc,
        goal=goal,
        task_family=task_family,
        structure_signature=signature,
        has_graph=has_graph,
        tool_names=tool_names,
        graph_node_kinds=graph_node_kinds,
    )
    pattern = classification["pattern"]
    pattern_confidence = classification["confidence"]

    try:
        limit = max(1, min(10, int(float(max_templates))))
    except (TypeError, ValueError):
        limit = 2
    threshold = _clip(template_match_min) if isinstance(template_match_min, (int, float)) else TEMPLATE_MATCH_MIN
    matched: list[dict[str, Any]] = []
    for template in templates or []:
        if not isinstance(template, dict) or not template.get("templateId"):
            continue
        score, breakdown = template_match_score(
            template=template,
            pattern=pattern,
            pattern_confidence=pattern_confidence,
            structure_signature=signature,
            task_text=_clean_text(task_desc),
        )
        matched.append({
            "templateId": str(template.get("templateId", "")),
            "name": _clean_text(template.get("name"), MAX_TEMPLATE_NAME_CHARS),
            "description": _clean_text(template.get("description"), MAX_TEMPLATE_DESCRIPTION_CHARS),
            "taskPatterns": list(template.get("taskPatterns") or []),
            "steps": template.get("steps") or [],
            "structureSignature": list(template.get("structureSignature") or []),
            "preconditions": list(template.get("preconditions") or []),
            "activeRetrievalPoints": _active_retrieval_points(template),
            "score": score,
            "breakdown": breakdown,
            "strongMatch": score >= TEMPLATE_MATCH_STRONG,
            "injectable": score >= threshold,
            "deprecated": template.get("status") == "deprecated",
            "utility": template.get("utility") if isinstance(template.get("utility"), dict) else {},
        })
    matched.sort(key=lambda row: (
        -row["score"],
        -_prior_utility({"utility": row["utility"]}),
        row["templateId"],
    ))
    top = matched[:limit]

    if not top or top[0]["score"] < threshold:
        plan_text = (
            f"Task pattern: {pattern} (confidence {classification['confidence']}). "
            f"No strategy template cleared the match threshold {threshold}; "
            "run the task in general mode instead of forcing a template."
        )
    else:
        best = top[0]
        points = best["activeRetrievalPoints"]
        point_text = ", ".join(str(point) for point in points) if points else "none"
        plan_text = (
            f"Task pattern: {pattern} (confidence {classification['confidence']}). "
            f"Best template: {best['name']} (score {best['score']}). "
            f"Proactive retrieval points (steps): {point_text}. "
            "Treat template steps as guidance; verify each result in the current environment."
        )

    result: dict[str, Any] = {
        "pattern": pattern,
        "confidence": classification["confidence"],
        "evidence": classification["evidence"],
        "scores": classification["scores"],
        "fallback": classification["fallback"],
        "matchedTemplates": top,
        "plan": plan_text,
        "audit": {
            "matchThreshold": threshold,
            "evaluatedTemplates": len(matched),
            "querySignature": signature,
        },
    }

    # v0.7.0 additive template superposition (deferred collapse). Templates
    # carry no retrieval-side causal phase in the MVP, so their phase is 0.
    if quantum_decision_enabled:
        size = max(1, min(MAX_SUPERPOSITION_SIZE, int(max_superposition_size)))
        template_candidates = [
            {"id": str(template.get("templateId", "")), "amplitude": float(template.get("score", 0.0) or 0.0), "phase": 0.0}
            for template in top
        ]
        full = build_superposition(candidates=template_candidates)
        collapse_reason = ""
        coherence_before = full["coherence"]
        if len(template_candidates) > size:
            collapse_reason = "rank_cutoff"
            collapsed = build_superposition(candidates=template_candidates[:size])
            superposition = collapsed["superposition"]
            coherence_after = collapsed["coherence"]
        else:
            superposition = full["superposition"]
            coherence_after = full["coherence"]
        for entry in superposition:
            entry["collapseReason"] = collapse_reason
        result["superposition"] = superposition
        result["quantum"] = {
            "coherence": coherence_after,
            "coherenceBefore": coherence_before,
            "coherenceAfter": coherence_after,
            "collapseReason": collapse_reason,
            "estimatorVersion": full["estimatorVersion"],
        }
    return result
