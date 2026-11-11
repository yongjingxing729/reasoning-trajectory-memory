"""Task-pattern classification for the DSH reasoning-memory core.

Signals follow docs/research-notes/02-template-algorithms.md section 1:
S1 task_family substring table, S2 bilingual keyword vocabulary, S3
structure-signature statistics, S4 graph node-kind distribution. Missing
signals are dropped and the remaining weights renormalized to 1. S5
(historical retrieval hits) is deliberately deferred past v0.4.0.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .constants import (
    MIN_PATTERN_MARGIN,
    MIN_PATTERN_SCORE,
    PATTERN_KEYWORDS,
    PATTERN_SIGNAL_WEIGHTS,
    TAGS,
    TASK_FAMILY_PATTERNS,
    TASK_PATTERNS,
)


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if not isinstance(value, (int, float)):
        return low
    return max(low, min(high, float(value)))


def _family_hit(task_family: str) -> str | None:
    lowered = task_family.strip().lower()
    if not lowered:
        return None
    for needle, pattern in TASK_FAMILY_PATTERNS:
        if needle in lowered:
            return pattern
    return None


def _keyword_hits(text: str, pattern: str) -> int:
    vocabulary = PATTERN_KEYWORDS.get(pattern, ())
    if not vocabulary:
        return 0
    lowered = text.lower()
    hits = 0
    for keyword in vocabulary:
        if keyword.isascii():
            if re.search(rf"\b{re.escape(keyword)}\b", lowered):
                hits += 1
        elif keyword in lowered:
            hits += 1
    return hits


def _argmax(scores: dict[str, float]) -> str:
    # max() sorts ascending, so the key uses the raw score with a negative
    # index tie-break (earlier vocabulary order wins ties).
    return max(scores, key=lambda label: (scores[label], -list(scores).index(label)))


def classify_task_pattern(
    *,
    task_desc: str,
    goal: str = "",
    task_family: str = "",
    structure_signature: list[str] | None = None,
    has_graph: bool = False,
    tool_names: list[str] | None = None,
    graph_node_kinds: list[str] | None = None,
) -> dict[str, Any]:
    """Classify a task into one of the seven task patterns.

    Signals follow docs/research-notes/02-template-algorithms.md section 1:
    S1 task_family substring table, S2 bilingual keyword vocabulary, S3
    structure-signature statistics, S4 graph node-kind distribution. Missing
    signals are dropped and the remaining weights renormalized to 1. S5
    (historical retrieval hits) is deliberately deferred past v0.4.0.
    """
    family_hit = _family_hit(task_family)
    family_present = family_hit is not None and task_family.strip() != ""

    text = f"{task_desc}\n{goal}".strip()
    lexical_counts = {
        pattern: _keyword_hits(text, pattern) for pattern in TASK_PATTERNS
    }
    lexical_scores: dict[str, float] = {}
    for pattern, count in lexical_counts.items():
        vocabulary = PATTERN_KEYWORDS.get(pattern, ())
        lexical_scores[pattern] = min(1.0, count / len(vocabulary)) if vocabulary else 0.0

    signature = [tag for tag in (structure_signature or []) if tag in TAGS]
    structure_scores = _structure_scores(signature, tool_names)

    kinds = [kind for kind in (graph_node_kinds or []) if isinstance(kind, str)]
    graph_scores = _graph_scores(kinds)

    signals: list[tuple[str, float, dict[str, float]]] = []
    if family_present:
        signals.append(("task_family", PATTERN_SIGNAL_WEIGHTS["task_family"], {
            pattern: 1.0 if pattern == family_hit else 0.0 for pattern in TASK_PATTERNS
        }))
    if text:
        signals.append(("lexical", PATTERN_SIGNAL_WEIGHTS["lexical"], lexical_scores))
    if signature:
        signals.append(("structure", PATTERN_SIGNAL_WEIGHTS["structure"], structure_scores))
    if kinds:
        signals.append(("graph", PATTERN_SIGNAL_WEIGHTS["graph"], graph_scores))

    total_weight = sum(weight for _, weight, _ in signals)
    scores = {pattern: 0.0 for pattern in TASK_PATTERNS}
    for _, weight, signal_scores in signals:
        for pattern in TASK_PATTERNS:
            scores[pattern] += (weight / total_weight) * signal_scores[pattern] if total_weight > 0.0 else 0.0

    ranked = sorted(TASK_PATTERNS, key=lambda pattern: (-scores[pattern], pattern))
    best, second = ranked[0], ranked[1]
    margin = scores[best] - scores[second]

    fallback = False
    if family_present:
        pattern = family_hit or "general"
        agreeing = 0.0
        other_weight = 0.0
        for name, weight, signal_scores in signals:
            if name == "task_family":
                continue
            other_weight += weight
            if _argmax(signal_scores) == pattern:
                agreeing += weight
        agreement = agreeing / other_weight if other_weight > 0.0 else 0.0
        confidence = min(0.9, 0.6 + 0.3 * agreement)
    elif scores[best] >= MIN_PATTERN_SCORE and margin >= MIN_PATTERN_MARGIN:
        pattern = best
        confidence = _clip(2.0 * margin)
    else:
        pattern = "general"
        confidence = _clip(scores["general"])
        fallback = True

    evidence: list[dict[str, Any]] = []
    if task_family.strip():
        evidence.append({
            "signal": "task_family",
            "hit": family_hit if family_hit else f"{task_family.strip()} (no mapping)",
            "weight": PATTERN_SIGNAL_WEIGHTS["task_family"] if family_present else 0.0,
        })
    if text:
        evidence.append({
            "signal": "lexical",
            "hit": {pattern: count for pattern, count in lexical_counts.items() if count},
            "weight": PATTERN_SIGNAL_WEIGHTS["lexical"],
        })
    if signature:
        evidence.append({
            "signal": "structure",
            "hit": signature,
            "weight": PATTERN_SIGNAL_WEIGHTS["structure"],
        })
    if kinds:
        evidence.append({
            "signal": "graph",
            "hit": kinds,
            "weight": PATTERN_SIGNAL_WEIGHTS["graph"],
        })
    elif has_graph:
        evidence.append({
            "signal": "graph",
            "hit": "graph present without node kinds; signal skipped",
            "weight": 0.0,
        })

    return {
        "pattern": pattern,
        "confidence": round(confidence, 6),
        "evidence": [
            {"signal": entry["signal"], "hit": entry["hit"], "weight": round(entry["weight"], 6)}
            for entry in evidence
        ],
        "scores": {key: round(value, 6) for key, value in scores.items()},
        "fallback": fallback,
    }


def _structure_scores(signature: list[str], tool_names: list[str] | None) -> dict[str, float]:
    length = len(signature)
    if length == 0:
        return {pattern: 0.0 for pattern in TASK_PATTERNS}
    counts = Counter(signature)
    search_dominant = counts.get("search", 0) / length > 0.4
    distinct_tools = {name for name in (tool_names or []) if name}
    tool_mix = (
        len(distinct_tools) >= 3
        and "verify" not in signature
        and "rollback" not in signature
        and length <= 8
    )
    feature_hits = {
        "verify_present": "verify" in signature,
        "rollback_present": "rollback" in signature,
        "search_dominant": search_dominant,
        "decompose_present": "decompose" in signature,
        "long_sequence": length > 8,
        "tool_mix": tool_mix,
    }
    feature_classes = {
        "verify_present": ("code_synthesis",),
        "rollback_present": ("gui_operation", "long_horizon_planning"),
        "search_dominant": ("numeric_search", "multi_hop_qa"),
        "decompose_present": ("long_horizon_planning",),
        "long_sequence": ("long_horizon_planning",),
        "tool_mix": ("tool_orchestration",),
    }
    possible: dict[str, int] = {}
    hits_per_class: dict[str, int] = {pattern: 0 for pattern in TASK_PATTERNS}
    for name, classes in feature_classes.items():
        for pattern in classes:
            possible[pattern] = possible.get(pattern, 0) + 1
        if feature_hits[name]:
            for pattern in classes:
                hits_per_class[pattern] += 1
    return {
        pattern: hits_per_class[pattern] / possible[pattern] if possible.get(pattern) else 0.0
        for pattern in TASK_PATTERNS
    }


def _graph_scores(kinds: list[str]) -> dict[str, float]:
    total = len(kinds)
    if total == 0:
        return {pattern: 0.0 for pattern in TASK_PATTERNS}
    counts = Counter(kinds)
    premise_inference = counts.get("premise", 0) + counts.get("inference", 0)
    action_ratio = counts.get("action", 0) / total
    evidence_present = counts.get("evidence", 0) > 0
    feature_hits = {
        "premise_inference_dominant": premise_inference / total > 0.5,
        "action_dominant": action_ratio > 0.4,
        "evidence_present": evidence_present,
    }
    feature_classes = {
        "premise_inference_dominant": ("multi_hop_qa",),
        "action_dominant": ("tool_orchestration", "gui_operation"),
        "evidence_present": ("numeric_search",),
    }
    possible: dict[str, int] = {}
    hits_per_class: dict[str, int] = {pattern: 0 for pattern in TASK_PATTERNS}
    for name, classes in feature_classes.items():
        for pattern in classes:
            possible[pattern] = possible.get(pattern, 0) + 1
        if feature_hits[name]:
            for pattern in classes:
                hits_per_class[pattern] += 1
    return {
        pattern: hits_per_class[pattern] / possible[pattern] if possible.get(pattern) else 0.0
        for pattern in TASK_PATTERNS
    }
