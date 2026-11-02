"""Operation dispatch for the Python reasoning-memory core."""

from __future__ import annotations

from typing import Any

from .constants import TEMPLATE_MATCH_MIN
from .distillation import _make_memory
from .feedback import _record_feedback
from .retrieval import _retrieve
from .templates import (
    apply_template_feedback,
    builtin_template_records,
    mine_templates,
    plan_task,
    publish_template_validate,
)


def _as_list(request: dict[str, Any], key: str) -> list[Any]:
    value = request.get(key)
    return value if isinstance(value, list) else []


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    if operation == "distill":
        return _make_memory(request)
    if operation == "retrieve":
        return _retrieve(request)
    if operation == "feedback":
        return _record_feedback(request)
    if operation == "plan_task":
        return plan_task(
            task_desc=str(request.get("taskDesc", "")),
            goal=str(request.get("goal", "")),
            task_family=str(request.get("taskFamily", "")),
            events=_as_list(request, "events"),
            has_graph=bool(request.get("hasGraph")),
            graph_node_kinds=_as_list(request, "graphNodeKinds") or None,
            templates=_as_list(request, "templates"),
            max_templates=request.get("maxTemplates", 2),
            template_match_min=request.get("templateMatchMin", TEMPLATE_MATCH_MIN),
        )
    if operation == "mine_templates":
        return mine_templates(
            memories=_as_list(request, "memories"),
            min_support=request.get("minSupport", 3),
            max_templates=request.get("maxTemplates", 20),
            existing_templates=_as_list(request, "existingTemplates") or None,
        )
    if operation == "template_feedback":
        template = request.get("template")
        if not isinstance(template, dict):
            raise ValueError("template_feedback requires a template record")
        return apply_template_feedback(template, str(request.get("verdict", "")))
    if operation == "publish_template_validate":
        draft = request.get("draft")
        if not isinstance(draft, dict):
            raise ValueError("publish_template_validate requires a template draft object")
        return publish_template_validate(draft)
    if operation == "builtin_templates":
        return {"templates": builtin_template_records()}
    raise ValueError(f"unsupported operation: {operation}")
