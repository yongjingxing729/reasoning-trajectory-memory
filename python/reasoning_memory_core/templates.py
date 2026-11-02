"""strategy templates for the DSH reasoning-memory core.

Deterministic, stdlib-only implementation of the v0.4.0 four-layer mapping
skeleton (final spec 3.1-3.3): task-pattern classification, strategy-template
matching, deterministic workflow mining, template utility statistics, the
read-only plan_task entry point, and caller-template draft validation.

Every score is decomposed into named components so the evaluation harness can
audit and ablate each signal. No function here performs IO or touches the DSH
storage domain; persistence is decided by the TypeScript layer.
"""

from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from .constants import (
    BUILTIN_TEMPLATES,
    CLUSTER_FORM_SIMILARITY,
    CLUSTER_JOIN_SIMILARITY,
    MAX_MODULE_DEMANDS_PER_STEP,
    MAX_TEMPLATE_DESCRIPTION_CHARS,
    MAX_TEMPLATE_INTENT_CHARS,
    MAX_TEMPLATE_NAME_CHARS,
    MAX_TEMPLATE_PRECONDITION_CHARS,
    MAX_TEMPLATE_PRECONDITIONS,
    MAX_TEMPLATE_SOURCE_MEMORY_IDS,
    MAX_TEMPLATE_STEP_TITLE_CHARS,
    MAX_TEMPLATE_STEPS,
    MIN_PATTERN_MARGIN,
    MIN_PATTERN_SCORE,
    MODULE_DEMAND_BY_TAG,
    MODULE_DEMAND_INTENT,
    MODULE_DEMAND_KINDS,
    MODULE_DEMAND_TAG,
    PATTERN_KEYWORDS,
    PATTERN_SIGNAL_WEIGHTS,
    TAGS,
    TAG_TEXT,
    TASK_FAMILY_PATTERNS,
    TASK_PATTERNS,
    TEMPLATE_MATCH_MIN,
    TEMPLATE_MATCH_STRONG,
)
from .text_similarity import _clean_text, semantic_similarity, sequence_similarity
from .trajectory import structure_signature

_EM_DASH = re.compile("[\u2014\u2013]")
_TRIGGER_TOKENS = re.compile(r"\b(when|if|for|mention|ask|say)\b|当|如果|提到|需要|时", re.IGNORECASE)
# Template names keep the memory_tpl_ namespace prefix followed by a
# kebab-case body, mirroring the skill layer's name discipline.
_KABAB_NAME = re.compile(r"^memory_tpl_[a-z0-9]+(-[a-z0-9]+)*$")


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


def template_match_score(
    *,
    template: dict[str, Any],
    pattern: str,
    pattern_confidence: float,
    structure_signature: list[str],
    task_text: str,
) -> tuple[float, dict[str, float]]:
    """Score one template against the current task (final spec 3.1).

    match = (0.50 x struct + 0.30 x lexical + 0.20 x pattern_consistency)
            x quality, with quality bounded to [0.5, 1].
    pattern_confidence participates only in tie-breaks, never in the formula,
    so a weak classifier cannot inflate a match on its own.
    """
    if not isinstance(template, dict):
        return 0.0, {"struct": 0.0, "lexical": 0.0, "pattern": 0.0, "quality": 0.5}
    template_signature = template.get("coreSignature") or template.get("structureSignature") or []
    template_tags = [tag for tag in template_signature if tag in TAGS]
    query_tags = [tag for tag in (structure_signature or []) if tag in TAGS]
    struct = 0.0
    if template_tags and query_tags:
        struct = sequence_similarity(query_tags, template_tags)

    step_titles = []
    for step in template.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("title"), str):
            step_titles.append(step["title"])
    lexical_text = " ".join(filter(None, [
        _clean_text(template.get("name"), MAX_TEMPLATE_NAME_CHARS),
        _clean_text(template.get("description"), MAX_TEMPLATE_DESCRIPTION_CHARS),
        *step_titles,
    ]))
    lexical = semantic_similarity(task_text, lexical_text)

    task_patterns = template.get("taskPatterns") or []
    if pattern in task_patterns:
        consistency = 1.0
    elif pattern == "general":
        consistency = 0.5
    else:
        consistency = 0.0

    utility = template.get("utility") if isinstance(template.get("utility"), dict) else {}
    helpful = max(0, int(utility.get("helpful", 0)))
    harmful = max(0, int(utility.get("harmful", 0)))
    neutral = max(0, int(utility.get("neutral", 0)))
    use_count = helpful + harmful
    total_feedback = helpful + harmful + neutral
    harm_ratio = harmful / total_feedback if total_feedback > 0 else 0.0
    # EvolveR dynamic scoring (c_succ+1)/(c_use+2) replicated verbatim, with a
    # 0.5 floor so new templates stay visible (02-template-algorithms.md 2.4).
    quality = (0.5 + 0.5 * (helpful + 1.0) / (use_count + 2.0)) * (1.0 - 0.3 * harm_ratio)
    quality = _clip(quality, 0.5, 1.0)

    score = (0.50 * struct + 0.30 * lexical + 0.20 * consistency) * quality
    return round(_clip(score), 6), {
        "struct": round(struct, 6),
        "lexical": round(lexical, 6),
        "pattern": round(consistency, 6),
        "quality": round(quality, 6),
    }


def _normalize_signature(signature: list[str] | tuple[str, ...] | None) -> list[str]:
    output: list[str] = []
    for tag in signature or []:
        if tag in TAGS and (not output or output[-1] != tag):
            output.append(tag)
    return output


def _multi_sequence_lcs(signatures: list[list[str]]) -> list[str]:
    """Multi-sequence LCS by iterative pairwise contraction from the shortest."""
    if not signatures:
        return []
    common = list(min(signatures, key=len))
    for signature in signatures:
        height = len(common)
        width = len(signature)
        table = [[0] * (width + 1) for _ in range(height + 1)]
        for left in range(1, height + 1):
            for right in range(1, width + 1):
                if common[left - 1] == signature[right - 1]:
                    table[left][right] = table[left - 1][right - 1] + 1
                else:
                    table[left][right] = max(table[left - 1][right], table[left][right - 1])
        backtrack: list[str] = []
        left, right = height, width
        while left > 0 and right > 0:
            if common[left - 1] == signature[right - 1]:
                backtrack.append(common[left - 1])
                left -= 1
                right -= 1
            elif table[left - 1][right] >= table[left][right - 1]:
                left -= 1
            else:
                right -= 1
        common = list(reversed(backtrack))
        if not common:
            break
    return common


def _module_demands_for_tag(tag: str) -> list[dict[str, str]]:
    required, optional = MODULE_DEMAND_BY_TAG.get(tag, ((), ()))
    demands: list[dict[str, str]] = []
    for kind in (*required, *optional):
        demands.append({"kind": kind, "intent": MODULE_DEMAND_INTENT.get(kind, "")})
    return demands


def build_template_steps(signature: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    normalized = _normalize_signature(signature)[:MAX_TEMPLATE_STEPS]
    return [
        {
            "index": index,
            "title": TAG_TEXT.get(tag, tag),
            "moduleDemands": _module_demands_for_tag(tag),
        }
        for index, tag in enumerate(normalized, start=1)
    ]


def derive_signature_from_demands(steps: list[dict[str, Any]]) -> list[str]:
    """Lossy structureSignature derivation from caller-authored steps.

    Each step contributes the canonical tag of its first module demand; the
    reverse mapping is ambiguous (several demands share tags), so the result
    is an approximation used only for caller-published templates.
    """
    signature: list[str] = []
    for step in sorted(steps, key=lambda entry: int(entry.get("index", 0))):
        demands = step.get("moduleDemands") if isinstance(step.get("moduleDemands"), list) else []
        first = demands[0] if demands else {}
        kind = first.get("kind") if isinstance(first, dict) else ""
        tag = MODULE_DEMAND_TAG.get(kind)
        if tag and (not signature or signature[-1] != tag):
            signature.append(tag)
    return signature[:MAX_TEMPLATE_STEPS]


def builtin_template_records(now_ms: int | None = None) -> list[dict[str, Any]]:
    """Build the five idempotently seeded builtin templates (final spec 3.2)."""
    now = now_ms if isinstance(now_ms, int) and now_ms > 0 else time.time_ns() // 1_000_000
    records: list[dict[str, Any]] = []
    for seed in BUILTIN_TEMPLATES:
        records.append({
            "templateId": seed["templateId"],
            "name": seed["name"],
            "description": seed["description"],
            "taskPatterns": list(seed["taskPatterns"]),
            "steps": build_template_steps(seed["structureSignature"]),
            "structureSignature": list(seed["structureSignature"]),
            "preconditions": [],
            "provenance": {"author": "builtin", "sourceMemoryIds": [], "sourceTaskIds": []},
            "utility": {"helpful": 0, "harmful": 0, "neutral": 0},
            "version": 1,
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
        })
    return records


def _active_retrieval_points(template: dict[str, Any]) -> list[int]:
    points: list[int] = []
    for step in template.get("steps") or []:
        if not isinstance(step, dict):
            continue
        demands = step.get("moduleDemands") if isinstance(step.get("moduleDemands"), list) else []
        kinds = {
            demand.get("kind")
            for demand in demands
            if isinstance(demand, dict) and isinstance(demand.get("kind"), str)
        }
        if kinds & {"retrieve_memory", "retrieve_template"}:
            index = step.get("index")
            if isinstance(index, int):
                points.append(index)
    return points


def _prior_utility(template: dict[str, Any]) -> float:
    utility = template.get("utility") if isinstance(template.get("utility"), dict) else {}
    helpful = max(0, int(utility.get("helpful", 0)))
    harmful = max(0, int(utility.get("harmful", 0)))
    return (helpful + 1.0) / (helpful + harmful + 2.0)


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

    return {
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


def mine_templates(
    *,
    memories: list[dict[str, Any]],
    min_support: int = 3,
    max_templates: int = 20,
    existing_templates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministic workflow mining over (taskPattern, structureSignature).

    Online representative clustering with SequenceMatcher similarity follows
    02-template-algorithms.md 2.2: join at >= CLUSTER_JOIN_SIMILARITY, open a
    new cluster below CLUSTER_FORM_SIMILARITY, and park in-between members in
    a pending queue that never moves representatives.
    """
    support = 3
    try:
        support = max(1, int(float(min_support)))
    except (TypeError, ValueError):
        support = 3
    emit_cap = 20
    try:
        emit_cap = max(1, min(200, int(float(max_templates))))
    except (TypeError, ValueError):
        emit_cap = 20
    existing_keys: set[tuple[tuple[str, ...], str]] = set()
    for template in existing_templates or []:
        if not isinstance(template, dict):
            continue
        signature = tuple(_normalize_signature(template.get("structureSignature")))
        for pattern in template.get("taskPatterns") or []:
            existing_keys.add((signature, str(pattern)))

    rows: list[dict[str, Any]] = []
    for memory in memories or []:
        if not isinstance(memory, dict) or not memory.get("memoryId"):
            continue
        signature = _normalize_signature(memory.get("structureSignature"))
        if not signature:
            continue
        if memory.get("outcome") not in ("success", "fail"):
            continue
        rows.append({
            "memory": memory,
            "signature": signature,
            "sortKey": (int(memory.get("createdAt") or 0), str(memory.get("memoryId", ""))),
        })
    rows.sort(key=lambda row: row["sortKey"])

    clusters: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for row in rows:
        signature = row["signature"]
        best_cluster = None
        best_ratio = 0.0
        for cluster in clusters:
            ratio = SequenceMatcher(None, signature, cluster["signature"]).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_cluster = cluster
        if best_cluster is None:
            clusters.append({"signature": signature, "members": [row]})
        elif best_ratio >= CLUSTER_JOIN_SIMILARITY:
            best_cluster["members"].append(row)
        elif best_ratio < CLUSTER_FORM_SIMILARITY:
            clusters.append({"signature": signature, "members": [row]})
        else:
            pending.append(row)

    skipped: dict[str, int] = {
        "below_support": 0,
        "too_short": 0,
        "monotone": 0,
        "fail_majority": 0,
        "duplicate": 0,
        "pending": len(pending),
    }
    cluster_reports: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    now = time.time_ns() // 1_000_000
    name_counter = 0
    for cluster in clusters:
        members = cluster["members"]
        signatures = [member["signature"] for member in members]
        representative = cluster["signature"]
        core_signature = _multi_sequence_lcs(signatures) or representative
        pattern_counts: Counter[str] = Counter()
        for member in members:
            memory = member["memory"]
            label = classify_task_pattern(
                task_desc=_clean_text(memory.get("semanticText")),
                task_family=_clean_text(memory.get("taskScope"), 200),
            )["pattern"]
            pattern_counts[label] += 1
        pattern = max(
            pattern_counts,
            key=lambda label: (pattern_counts[label], -list(pattern_counts).index(label)),
        )
        successes = sum(member["memory"].get("outcome") == "success" for member in members)
        size = len(members)
        cluster_reports.append({
            "signature": representative,
            "core_signature": core_signature,
            "pattern": pattern,
            "memory_ids": [str(member["memory"].get("memoryId", "")) for member in members],
            "size": size,
            "success_ratio": round(successes / size, 6),
        })

        reason = ""
        if size < support:
            reason = "below_support"
        elif all(tag == "execute" for tag in representative):
            # Consecutive-dedupe normalization collapses pure-execute runs, so
            # a monotone cluster is reported before the generic length gate.
            reason = "monotone"
        elif len(representative) < 2:
            reason = "too_short"
        elif successes / size < 0.5:
            reason = "fail_majority"
        if reason:
            skipped[reason] += 1
            continue
        key = (tuple(representative), pattern)
        if key in existing_keys:
            skipped["duplicate"] += 1
            continue
        if len(candidates) >= emit_cap:
            break

        name_counter += 1
        pattern_slug = pattern.replace("_", "-")
        # Namespace-prefixed generated name: the memory_tpl_ prefix is fixed,
        # the body stays kebab-case per the publish validator's regex.
        name = f"memory_tpl_mined-{pattern_slug}-{name_counter}"
        tag_text = " → ".join(TAG_TEXT.get(tag, tag) for tag in representative)
        description = (
            f"Use when a task of type {pattern} follows this observed step pattern: {tag_text}."
        )[:MAX_TEMPLATE_DESCRIPTION_CHARS]
        preconditions = sorted({
            precondition[:MAX_TEMPLATE_PRECONDITION_CHARS]
            for member in members
            for precondition in (
                member["memory"].get("preconditions") or []
                if isinstance(member["memory"].get("preconditions"), list) else []
            )
            if isinstance(precondition, str) and precondition.strip()
        })[:MAX_TEMPLATE_PRECONDITIONS]
        source_memory_ids = [
            str(member["memory"].get("memoryId", "")) for member in members
        ][:MAX_TEMPLATE_SOURCE_MEMORY_IDS]
        source_task_ids = sorted({
            str(member["memory"].get("provenance", {}).get("taskId", ""))
            for member in members
            if member["memory"].get("provenance", {}).get("taskId")
        })[:MAX_TEMPLATE_SOURCE_MEMORY_IDS]
        candidates.append({
            "templateId": str(uuid.uuid4()),
            "name": name,
            "description": description,
            "taskPatterns": [pattern],
            "steps": build_template_steps(representative),
            "structureSignature": representative,
            "coreSignature": core_signature,
            "preconditions": preconditions,
            "provenance": {
                "author": "mined",
                "sourceMemoryIds": source_memory_ids,
                "sourceTaskIds": source_task_ids,
            },
            "utility": {"helpful": 0, "harmful": 0, "neutral": 0},
            "version": 1,
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
        })
        existing_keys.add(key)

    return {
        "templates": candidates,
        "clusters": cluster_reports,
        "skipped": skipped,
    }


def apply_template_feedback(template: dict[str, Any], verdict: str) -> dict[str, Any]:
    """Increment one template utility counter (idempotency enforced in TS)."""
    if verdict not in ("helpful", "harmful", "neutral"):
        raise ValueError("template feedback requires a helpful/harmful/neutral verdict")
    utility = template.get("utility") if isinstance(template.get("utility"), dict) else {}
    counts = {
        key: max(0, int(utility.get(key, 0)))
        for key in ("helpful", "harmful", "neutral")
    }
    counts[verdict] += 1
    score = (counts["helpful"] + 1.0) / (counts["helpful"] + counts["harmful"] + 2.0)
    return {
        "templateId": str(template.get("templateId", "")),
        "utility": counts,
        "utilityScore": round(score, 6),
    }


def publish_template_validate(draft: dict[str, Any]) -> dict[str, Any]:
    """Deterministic guards for caller-published templates (final spec 3.3).

    Mirrors the skill-layer prompt-engineering discipline: memory_tpl_-prefixed
    kebab-case name, trigger-phrased description, no em-dashes, and hard caps.
    """
    issues: list[str] = []

    raw_name = draft.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name:
        issues.append("name: the template name must not be empty")
    elif not _KABAB_NAME.fullmatch(name):
        issues.append("name: template names must start with memory_tpl_ and be kebab-case afterwards (lowercase letters, digits, hyphens)")
    if len(name) > MAX_TEMPLATE_NAME_CHARS:
        issues.append(f"name: template names are capped at {MAX_TEMPLATE_NAME_CHARS} characters")
    name = name[:MAX_TEMPLATE_NAME_CHARS]

    raw_description = draft.get("description")
    description = raw_description.strip() if isinstance(raw_description, str) else ""
    if not description:
        issues.append("description: the description must not be empty")
    else:
        if len(description) > MAX_TEMPLATE_DESCRIPTION_CHARS:
            issues.append(f"description: descriptions are capped at {MAX_TEMPLATE_DESCRIPTION_CHARS} characters")
        if _EM_DASH.search(description):
            issues.append("description: em-dashes are not allowed; rewrite with commas, colons, or periods")
        if not _TRIGGER_TOKENS.search(description):
            issues.append("description: a model-invoked description must carry trigger phrasing (when/if/for/mention/ask/say, or 当/如果/提到/需要/时)")
    description = description[:MAX_TEMPLATE_DESCRIPTION_CHARS]

    raw_patterns = draft.get("task_patterns")
    task_patterns: list[str] = []
    if not isinstance(raw_patterns, list) or not raw_patterns:
        issues.append("task_patterns: provide at least one task pattern")
    else:
        seen: set[str] = set()
        for entry in raw_patterns[: len(TASK_PATTERNS)]:
            if not isinstance(entry, str) or entry not in TASK_PATTERNS:
                issues.append(f"task_patterns: unsupported task pattern {entry!r}")
                continue
            if entry in seen:
                issues.append(f"task_patterns: duplicate task pattern {entry}")
                continue
            seen.add(entry)
            task_patterns.append(entry)

    raw_steps = draft.get("steps")
    steps: list[dict[str, Any]] = []
    if not isinstance(raw_steps, list) or not raw_steps:
        issues.append("steps: provide at least one step")
    elif len(raw_steps) > MAX_TEMPLATE_STEPS:
        issues.append(f"steps: a template carries at most {MAX_TEMPLATE_STEPS} steps")
    else:
        seen_indexes: set[int] = set()
        for step in raw_steps:
            if not isinstance(step, dict):
                issues.append("steps: every step must be an object")
                continue
            index = step.get("index")
            if not isinstance(index, int) or index < 1 or index > MAX_TEMPLATE_STEPS:
                issues.append(f"steps: step index must be an integer in 1..{MAX_TEMPLATE_STEPS}")
                continue
            if index in seen_indexes:
                issues.append(f"steps: duplicate step index {index}")
                continue
            seen_indexes.add(index)
            title = _clean_text(step.get("title"), MAX_TEMPLATE_STEP_TITLE_CHARS)
            if not title:
                issues.append(f"steps: step {index} title must not be empty")
            demands: list[dict[str, str]] = []
            raw_demands = step.get("module_demands")
            if not isinstance(raw_demands, list) or not raw_demands:
                issues.append(f"steps: step {index} must declare at least one module demand")
            else:
                if len(raw_demands) > MAX_MODULE_DEMANDS_PER_STEP:
                    issues.append(f"steps: step {index} carries at most {MAX_MODULE_DEMANDS_PER_STEP} module demands")
                seen_kinds: set[str] = set()
                for demand in raw_demands[:MAX_MODULE_DEMANDS_PER_STEP]:
                    if not isinstance(demand, dict):
                        issues.append(f"steps: step {index} has a module demand that is not an object")
                        continue
                    kind = demand.get("kind")
                    if not isinstance(kind, str) or kind not in MODULE_DEMAND_KINDS:
                        issues.append(f"steps: step {index} has an unsupported module demand kind {kind!r}")
                        continue
                    if kind in seen_kinds:
                        issues.append(f"steps: step {index} repeats module demand kind {kind}")
                        continue
                    seen_kinds.add(kind)
                    intent = _clean_text(demand.get("intent"), MAX_TEMPLATE_INTENT_CHARS)
                    demands.append({"kind": kind, "intent": intent})
            steps.append({"index": index, "title": title, "moduleDemands": demands})
    steps.sort(key=lambda entry: entry.get("index", 0))

    preconditions: list[str] = []
    raw_preconditions = draft.get("preconditions")
    if raw_preconditions is not None:
        if not isinstance(raw_preconditions, list):
            issues.append("preconditions: preconditions must be an array of strings")
        else:
            for entry in raw_preconditions[:MAX_TEMPLATE_PRECONDITIONS]:
                cleaned = _clean_text(entry, MAX_TEMPLATE_PRECONDITION_CHARS)
                if cleaned:
                    preconditions.append(cleaned)

    return {
        "valid": not issues,
        "issues": issues,
        "normalized": {
            "name": name,
            "description": description,
            "taskPatterns": task_patterns,
            "steps": steps,
            "preconditions": preconditions,
            "structureSignature": derive_signature_from_demands(steps),
        },
    }
