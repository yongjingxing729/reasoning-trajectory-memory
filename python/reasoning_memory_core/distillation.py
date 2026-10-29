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
) -> tuple[list[dict[str, Any]], str]:
    """Normalize, gate, de-specify, and deduplicate structured memory items."""
    raw_items = request.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return [], "no memory items were provided"
    accepted: list[dict[str, Any]] = []
    seen_contents: list[str] = []
    first_rejection = ""
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
        })
    if not accepted:
        return [], (
            f"{first_rejection}; items must carry a success/fail outcome, non-empty content, "
            "must not restate the task description, and must not duplicate each other"
        )
    return accepted[:max_items], ""


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
        items, item_error = _normalize_items(request, semantic_text, threshold, max_items)
        if item_error:
            empty["reason"] = item_error
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
        lesson = reasoning_insight if outcome == "success" else failure_mode
        items = [{
            "title": lesson[:MAX_TITLE_CHARS] or "untitled",
            "description": "",
            "content": lesson,
            "contraindications": [],
            "outcome": outcome,
        }]

    now = time.time_ns() // 1_000_000
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
            "confidence": 0.55 if item_outcome == "success" else 0.35,
            "trustLevel": "unverified",
            "extractorVersion": EXTRACTOR_VERSION,
            "createdAt": now,
            "updatedAt": now,
            "semanticText": semantic_text,
            "structureSignature": signature if method != "llm_self_contrast" else [],
        }
        memories.append(item)

    label = "memories" if len(memories) > 1 else "memory"
    return {
        "stored": True,
        "reason": f"stored {len(memories)} unverified reasoning {label}",
        "memories": memories,
    }
