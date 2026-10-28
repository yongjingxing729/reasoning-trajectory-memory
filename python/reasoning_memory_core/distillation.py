"""distillation for the DSH reasoning-memory core."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .constants import EXTRACTOR_VERSION, MAX_MEMORY_TEXT, TAG_TEXT
from .reasoning_graph import _fallback_reasoning_graph, _parse_reasoning_graph
from .text_similarity import _clean_text
from .trajectory import structure_signature


def _make_memory(request: dict[str, Any]) -> dict[str, Any]:
    outcome = request.get("outcome")
    verifier = _clean_text(request.get("verifier"), 200)
    evidence = _clean_text(request.get("evidence"), 2000)
    reasoning_insight = _clean_text(request.get("reasoningInsight"), 1000)
    failure_mode = _clean_text(request.get("failureMode"), 500)
    task_id = _clean_text(request.get("taskId"), 100)
    session_id = _clean_text(request.get("sourceSessionId"), 200)
    task_scope = _clean_text(request.get("taskFamily"), 200) or "general"
    semantic_text = _clean_text(request.get("semanticText"))
    events = request.get("events") if isinstance(request.get("events"), list) else []
    signature = structure_signature(events)

    if outcome not in ("success", "fail"):
        return {"stored": False, "reason": "partial/unknown outcomes are not admitted as positive or negative reasoning memories"}
    if not verifier or not evidence:
        return {"stored": False, "reason": "provide both a verifier name and evidence summary"}
    if not signature:
        return {"stored": False, "reason": "no classifiable tool-action sequence was observed"}
    if outcome == "success" and not reasoning_insight:
        return {"stored": False, "reason": "a transferable reasoning insight is required; task details alone are not stored as memory"}
    if outcome == "fail" and not failure_mode:
        return {"stored": False, "reason": "failure attribution is missing; no negative lesson was created"}

    supplied_graph = request.get("reasoningGraphJson")
    reasoning_graph, graph_error = _parse_reasoning_graph(supplied_graph)
    if graph_error:
        return {"stored": False, "reason": f"reasoning graph rejected: {graph_error}"}

    if outcome == "success":
        reasoning = reasoning_insight
        content = (
            "调用者提炼的可迁移推理策略（未独立验证）：" + reasoning_insight
            + "。本次可观察的操作结构：" + " → ".join(TAG_TEXT[tag] for tag in signature)
            + "。迁移时先检查任务前提是否相符，再独立验证结果。"
        )
        memory_type = "strategy"
        confidence = 0.55
    else:
        reasoning = failure_mode
        content = (
            "失败模式（由任务调用者归因，尚未独立验证）：" + failure_mode
            + "。再次遇到时先核实这一归因和适用前提，再选择是否改变操作路径。"
        )
        memory_type = "failure"
        confidence = 0.35

    if reasoning_graph is None:
        reasoning_graph = _fallback_reasoning_graph(
            semantic_text, reasoning, outcome, signature, verifier, evidence,
        )

    now = time.time_ns() // 1_000_000
    item = {
        "memoryId": str(uuid.uuid4()),
        "type": memory_type,
        "outcome": outcome,
        "taskScope": task_scope,
        "preconditions": [
            f"task family: {task_scope}",
            "only reuse when the current task's preconditions match and its result can be re-verified",
        ],
        "content": content[:MAX_MEMORY_TEXT],
        "reasoning": reasoning,
        "reasoningGraph": reasoning_graph,
        "feedback": {"helpful": 0, "harmful": 0, "neutral": 0},
        "provenance": {
            "taskId": task_id,
            "sourceSessionId": session_id,
            "eventSeqs": sorted({int(row["seq"]) for row in events if isinstance(row, dict) and isinstance(row.get("seq"), int)}),
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
        "structureSignature": signature,
    }
    return {"stored": True, "reason": "stored as an unverified reasoning memory", "memory": item}
