"""distillation for the DSH reasoning-memory core.

Structured distillation follows the ReasoningBank evidence: success and failure
trajectories each produce a small number of transferable, de-specified memory
items with a title, a one-line applicability description, the lesson content,
and explicit when-NOT-to-use contraindications. Deterministic gates stay in
place: unattributed failures and task-summary-only successes are rejected, and
every stored item remains unverified.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .constants import (
    DEDUP_SIMILARITY,
    EXTRACTOR_VERSION,
    EXTRACTION_METHODS,
    MAX_CONTRAINDICATION_CHARS,
    MAX_CONTRAINDICATIONS,
    MAX_DESCRIPTION_CHARS,
    MAX_ITEMS_PER_OUTCOME,
    MAX_MEMORY_TEXT,
    MAX_SOURCE_MEMORY_IDS,
    MAX_TITLE_CHARS,
    TAG_TEXT,
)
from .distill_causal import (
    estimate_distill_uplift,
    gate_distill,
    inherited_utility,
    phase_from_uplift,
)
from .reasoning_graph import _fallback_reasoning_graph, _parse_reasoning_graph
from .text_similarity import _clean_text, semantic_similarity
from .trajectory import structure_signature

# Content prefixes record the extraction provenance inside the durable text and
# keep the legacy `_reasoning_text` recovery path working for older records.
_CONTENT_PREFIXES = {
    ("caller_insight", "success"): "调用者提炼的可迁移推理策略（未独立验证）：",
    ("caller_insight", "fail"): "失败模式（由任务调用者归因，尚未独立验证）：",
    ("llm_distillation", "success"): "从任务轨迹蒸馏的可迁移推理策略（未独立验证）：",
    ("llm_distillation", "fail"): "失败模式（从任务轨迹蒸馏归因，尚未独立验证）：",
    ("llm_self_contrast", "success"): "由成败轨迹对比蒸馏的可迁移推理策略（未独立验证）：",
    ("llm_self_contrast", "fail"): "失败模式（由成败轨迹对比蒸馏归因，尚未独立验证）：",
}
_STRUCTURE_MARKER = "。本次可观察的操作结构："
_SUCCESS_SUFFIX = "。迁移时先检查任务前提是否相符，再独立验证结果。"
_FAIL_SUFFIX = "。再次遇到时先核实这一归因和适用前提，再选择是否改变操作路径。"


def _format_content(lesson: str, outcome: str, signature: list[str], method: str) -> str:
    prefix = _CONTENT_PREFIXES[(method, outcome)]
    if outcome == "success" and signature:
        structure = _STRUCTURE_MARKER + " → ".join(TAG_TEXT[tag] for tag in signature)
    else:
        structure = ""
    suffix = _SUCCESS_SUFFIX if outcome == "success" else _FAIL_SUFFIX
    return (prefix + lesson + structure + suffix)[:MAX_MEMORY_TEXT]


def _normalize_items(
    request: dict[str, Any],
    semantic_text: str,
    threshold: float,
    max_items: int,
    min_confidence: float,
    request_confidence: float,
) -> tuple[list[dict[str, Any]], str, int]:
    """Normalize, gate, de-specify, and deduplicate structured memory items.

    The insight-confidence gate (final spec 3.5) rejects items whose declared
    insightConfidence falls below the configured minimum; items without a
    declared value inherit the request-level value (0 = not provided).
    """
    raw_items = request.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return [], "no memory items were provided", 0
    accepted: list[dict[str, Any]] = []
    seen_contents: list[str] = []
    first_rejection = ""
    gate_rejected = 0
    for raw in raw_items[: max_items * 2]:
        if not isinstance(raw, dict):
            if not first_rejection:
                first_rejection = "an item was not an object"
            continue
        outcome = raw.get("outcome")
        if outcome not in ("success", "fail"):
            if not first_rejection:
                first_rejection = "an item carried an invalid outcome"
            continue
        content = _clean_text(raw.get("content"), MAX_MEMORY_TEXT)
        if not content:
            if not first_rejection:
                first_rejection = "an item had empty content"
            continue
        title = _clean_text(raw.get("title"), MAX_TITLE_CHARS) or content[:40]
        description = _clean_text(raw.get("description"), MAX_DESCRIPTION_CHARS)
        contraindications: list[str] = []
        raw_contra = raw.get("contraindications")
        if isinstance(raw_contra, list):
            for entry in raw_contra[:MAX_CONTRAINDICATIONS]:
                cleaned = _clean_text(entry, MAX_CONTRAINDICATION_CHARS)
                if cleaned:
                    contraindications.append(cleaned)
        raw_confidence = raw.get("insightConfidence")
        if isinstance(raw_confidence, (int, float)):
            item_confidence = max(0.0, min(1.0, float(raw_confidence)))
        else:
            item_confidence = request_confidence
        # The gate is a write policy, not a calibrated quality verdict (the
        # MoT confidence-filtering idea in engineering approximation).
        if min_confidence > 0.0 and item_confidence < min_confidence:
            gate_rejected += 1
            if not first_rejection:
                first_rejection = "an item fell below the insight confidence gate"
            continue
        # De-specification guard: a lesson that nearly restates the task
        # description cannot transfer to structurally similar, surface-different
        # tasks and is rejected as too task-specific.
        if semantic_similarity(content, semantic_text) >= threshold:
            if not first_rejection:
                first_rejection = "an item restated the task description and was rejected as too task-specific"
            continue
        # Intra-batch deduplication keeps one trajectory from flooding the bank
        # with near-identical items.
        if any(semantic_similarity(content, seen) >= DEDUP_SIMILARITY for seen in seen_contents):
            if not first_rejection:
                first_rejection = "an item duplicated an already accepted item and was dropped"
            continue
        seen_contents.append(content)
        accepted.append({
            "title": title,
            "description": description,
            "content": content,
            "contraindications": contraindications,
            "outcome": outcome,
            "insightConfidence": item_confidence,
        })
    if not accepted:
        gate_note = f" ({gate_rejected} fell below the insight confidence gate)" if gate_rejected else ""
        return [], (
            f"{first_rejection}{gate_note}; items must carry a success/fail outcome, non-empty content, "
            "must not restate the task description, and must not duplicate each other"
        ), gate_rejected
    return accepted[:max_items], "", gate_rejected


def _make_memory(request: dict[str, Any]) -> dict[str, Any]:
    outcome = request.get("outcome")
    verifier = _clean_text(request.get("verifier"), 200)
    evidence = _clean_text(request.get("evidence"), 2000)
    task_id = _clean_text(request.get("taskId"), 100)
    session_id = _clean_text(request.get("sourceSessionId"), 200)
    task_scope = _clean_text(request.get("taskFamily"), 200) or "general"
    semantic_text = _clean_text(request.get("semanticText"))
    events = request.get("events") if isinstance(request.get("events"), list) else []
    signature = structure_signature(events)

    method = request.get("extractionMethod", "caller_insight")
    if method not in EXTRACTION_METHODS:
        method = "caller_insight"
    distillation_model = _clean_text(request.get("distillationModel"), 100)
    source_memory_ids: list[str] = []
    raw_sources = request.get("sourceMemoryIds")
    if isinstance(raw_sources, list):
        source_memory_ids = [
            entry for entry in (_clean_text(value, 100) for value in raw_sources[:MAX_SOURCE_MEMORY_IDS])
            if entry
        ]

    threshold = 0.9
    raw_threshold = request.get("deSpecificationThreshold")
    if isinstance(raw_threshold, (int, float)):
        threshold = max(0.0, min(1.0, float(raw_threshold)))
    max_items = MAX_ITEMS_PER_OUTCOME
    raw_max_items = request.get("maxItemsPerOutcome")
    if isinstance(raw_max_items, (int, float)):
        max_items = max(1, min(MAX_ITEMS_PER_OUTCOME, int(raw_max_items)))

    # v0.4.0 additive fields (final spec 2.2, 3.5): the insight-confidence
    # gate is a write policy, not a calibrated quality verdict.
    min_confidence = 0.0
    raw_min_confidence = request.get("minInsightConfidence")
    if isinstance(raw_min_confidence, (int, float)):
        min_confidence = max(0.0, min(1.0, float(raw_min_confidence)))
    request_confidence = 0.0
    raw_request_confidence = request.get("insightConfidence")
    if isinstance(raw_request_confidence, (int, float)):
        request_confidence = max(0.0, min(1.0, float(raw_request_confidence)))
    strategy_template_id = _clean_text(request.get("strategyTemplateId"), 100)
    reasoning_level = request.get("reasoningLevel")
    if reasoning_level not in ("analysis", "path", "step"):
        reasoning_level = "path"
    gate_rejected = 0

    # v0.8.0 causal distillation inputs (optional and additive). When no
    # distill causal events are supplied, the whole causal path is skipped so
    # the distilled MemoryItem stays byte-identical to v0.7.0.
    distill_gate_enabled = bool(request.get("distillGateEnabled", False))
    distill_uplift_threshold = 0.0
    raw_uplift_threshold = request.get("distillUpliftThreshold")
    if isinstance(raw_uplift_threshold, (int, float)):
        distill_uplift_threshold = max(-1.0, min(1.0, float(raw_uplift_threshold)))
    phase_from_distill_uplift = bool(request.get("phaseFromDistillUplift", True))
    distill_shrink_prior = 20
    raw_shrink = request.get("distillShrinkPrior")
    if isinstance(raw_shrink, (int, float)) and float(raw_shrink) > 0:
        distill_shrink_prior = int(float(raw_shrink))
    distill_min_arm_samples = 5
    raw_arm = request.get("distillMinArmSamples")
    if isinstance(raw_arm, (int, float)) and float(raw_arm) > 0:
        distill_min_arm_samples = int(float(raw_arm))
    distill_events = request.get("distillCausalEvents")
    if not isinstance(distill_events, list):
        distill_events = []

    empty = {"stored": False, "reason": "", "memories": []}
    if outcome not in ("success", "fail"):
        empty["reason"] = "partial/unknown outcomes are not admitted as positive or negative reasoning memories"
        return empty
    if not verifier or not evidence:
        empty["reason"] = "provide both a verifier name and evidence summary"
        return empty

    supplied_graph = request.get("reasoningGraphJson")
    reasoning_graph, graph_error = _parse_reasoning_graph(supplied_graph)
    if graph_error:
        empty["reason"] = f"reasoning graph rejected: {graph_error}"
        return empty

    raw_items = request.get("items")
    if isinstance(raw_items, list):
        # Structured path: caller-curated or LLM-distilled items.
        if reasoning_graph is not None and len(raw_items) > 1:
            empty["reason"] = "an explicit reasoning graph requires exactly one memory item"
            return empty
        if method == "llm_self_contrast":
            if len(source_memory_ids) < 2:
                empty["reason"] = "self-contrast consolidation requires at least two source memory ids"
                return empty
        elif not signature:
            empty["reason"] = "no classifiable tool-action sequence was observed"
            return empty
        items, item_error, gate_rejected = _normalize_items(
            request, semantic_text, threshold, max_items, min_confidence, request_confidence,
        )
        if item_error:
            empty["reason"] = item_error
            empty["gateRejectedCount"] = gate_rejected
            return empty
    else:
        # Legacy single-insight path keeps its original gates verbatim.
        if not signature:
            empty["reason"] = "no classifiable tool-action sequence was observed"
            return empty
        reasoning_insight = _clean_text(request.get("reasoningInsight"), 1000)
        failure_mode = _clean_text(request.get("failureMode"), 500)
        if outcome == "success" and not reasoning_insight:
            empty["reason"] = "a transferable reasoning insight is required; task details alone are not stored as memory"
            return empty
        if outcome == "fail" and not failure_mode:
            empty["reason"] = "failure attribution is missing; no negative lesson was created"
            return empty
        if min_confidence > 0.0 and request_confidence < min_confidence:
            empty["reason"] = "the insight confidence is below the configured gate; no memory was created"
            return empty
        lesson = reasoning_insight if outcome == "success" else failure_mode
        items = [{
            "title": lesson[:MAX_TITLE_CHARS] or "untitled",
            "description": "",
            "content": lesson,
            "contraindications": [],
            "outcome": outcome,
            "insightConfidence": request_confidence,
        }]

    # v0.8.0 causal uplift gate / weight / phase source. The association-path
    # insightConfidence gate has already run (the self-reported lower bound);
    # this causal lower bound runs after it. Rejection only blocks NEW writes.
    distill_estimate = None
    distill_gate = {"gate": "na", "reason": ""}
    if distill_events:
        distill_estimate = estimate_distill_uplift(
            events=distill_events,
            shrink_prior=distill_shrink_prior,
            min_arm_samples=distill_min_arm_samples,
        )
        distill_gate = gate_distill(
            uplift_shrunk=distill_estimate["upliftClipped"],
            threshold=distill_uplift_threshold,
            enabled=distill_gate_enabled,
            applied=distill_estimate["upliftApplied"],
        )
        if distill_gate["gate"] == "rejected":
            empty["reason"] = distill_gate["reason"]
            empty["gateRejectedCount"] = gate_rejected
            return empty

    now = time.time_ns() // 1_000_000
    # Precompute the v0.8.0 causal distillation write-back for this batch.
    distill_applied = bool(distill_estimate and distill_estimate["upliftApplied"])
    distill_uplift_clipped = distill_estimate["upliftClipped"] if distill_estimate else 0.0
    distill_apparent = distill_estimate["apparent"] if distill_estimate else 0.0
    memories: list[dict[str, Any]] = []
    for entry in items:
        item_outcome = entry["outcome"]
        lesson = entry["content"]
        if reasoning_graph is not None:
            # An explicit graph can only describe a single item; consume it there.
            item_graph = reasoning_graph
            reasoning_graph = None
        else:
            item_graph = _fallback_reasoning_graph(
                semantic_text, lesson, item_outcome, signature, verifier, evidence,
            )
        # v0.8.0: when the causal uplift is applied it drives the confidence
        # (bounded map of uplift_shrunk); otherwise the legacy fixed confidence
        # stays verbatim (zero regression).
        confidence = (
            max(0.0, min(1.0, 0.5 + distill_uplift_clipped))
            if distill_applied
            else 0.55 if item_outcome == "success" else 0.35
        )
        item = {
            "memoryId": str(uuid.uuid4()),
            "type": "strategy" if item_outcome == "success" else "failure",
            "outcome": item_outcome,
            "taskScope": task_scope,
            "preconditions": [
                f"task family: {task_scope}",
                "only reuse when the current task's preconditions match and its result can be re-verified",
            ],
            "content": _format_content(lesson, item_outcome, signature, method),
            "reasoning": lesson,
            "reasoningGraph": item_graph,
            "title": entry["title"],
            "description": entry["description"],
            "contraindications": entry["contraindications"],
            "extractionMethod": method,
            "distillationModel": distillation_model,
            "sourceMemoryIds": source_memory_ids,
            "feedback": {"helpful": 0, "harmful": 0, "neutral": 0},
            "provenance": {
                "taskId": task_id,
                "sourceSessionId": session_id,
                "eventSeqs": sorted({
                    int(row["seq"])
                    for row in events
                    if isinstance(row, dict) and isinstance(row.get("seq"), int)
                }),
            },
            "evidence": {
                "verifier": verifier,
                "summary": evidence,
                "independentlyVerified": False,
            },
            "confidence": confidence,
            "trustLevel": "unverified",
            "extractorVersion": EXTRACTOR_VERSION,
            "createdAt": now,
            "updatedAt": now,
            "semanticText": semantic_text,
            "structureSignature": signature if method != "llm_self_contrast" else [],
            "strategyTemplateId": strategy_template_id,
            "reasoningLevel": reasoning_level,
            "insightConfidence": entry.get("insightConfidence", request_confidence),
            "status": "active",
        }
        # v0.8.0 additive causal distillation fields. Absent unless distill
        # causal events were supplied (so the v0.7.0 output stays byte-identical
        # when no events are present).
        if distill_estimate is not None:
            item["distillUplift"] = distill_uplift_clipped if distill_applied else 0.0
            item["distillInherited"] = (
                inherited_utility(apparent=distill_apparent, uplift_shrunk=distill_uplift_clipped)
                if distill_applied else 0.0
            )
            item["distillSamples"] = int(distill_estimate["nEff"])
            item["distillPhase"] = (
                phase_from_uplift(uplift_shrunk=distill_uplift_clipped)
                if distill_applied and phase_from_distill_uplift else 0.0
            )
            item["distillGate"] = distill_gate["gate"]
            item["distillEstimand"] = (
                "E[Y | do(distill m)] - E[Y | do(not distill m)], stratified over taskPattern"
                if distill_applied else ""
            )
            item["distillEvidenceProvenance"] = (
                "paired restored-state rollout" if distill_estimate["pairedPairs"] > 0 else "observational stratified distill events"
                if distill_applied else ""
            )
            item["distillFalsificationTest"] = (
                "sign disagreement between crude and stratified uplift (Simpson) or n_eff < 20 falsifies the sign"
                if distill_applied else ""
            )
        memories.append(item)

    label = "memories" if len(memories) > 1 else "memory"
    return {
        "stored": True,
        "reason": f"stored {len(memories)} unverified reasoning {label}",
        "memories": memories,
        "gateRejectedCount": gate_rejected,
    }
