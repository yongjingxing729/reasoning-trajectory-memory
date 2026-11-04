"""Operation dispatch for the Python reasoning-memory core."""

from __future__ import annotations

from typing import Any

from .constants import TEMPLATE_MATCH_MIN
from .distillation import _make_memory
from .doc_chunking import chunk_document
from .doc_rag import delete_document_plan, retrieve_document
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
    if operation == "import_document":
        result = chunk_document(
            doc_id=str(request.get("docId", "")),
            content=str(request.get("content", "")),
            chunk_target=request.get("chunkTargetChars", 1200),
            chunk_min=request.get("chunkMinChars", 300),
        )
        return {
            "docId": request.get("docId", ""),
            "name": str(request.get("name", "")),
            "charCount": result["charCount"],
            "chunkCount": result["chunkCount"],
            "chunks": result["chunks"],
            "audit": result["audit"],
        }
    if operation == "retrieve_document":
        return retrieve_document(
            query=str(request.get("query", "")),
            reasoning_graph_json=str(request.get("reasoningGraphJson", "")),
            chunks=_as_list(request, "chunks"),
            documents=_as_list(request, "documents"),
            task_pattern=str(request.get("taskPattern", "")),
            task_family=str(request.get("taskFamily", "")),
            active_template_id=str(request.get("activeTemplateId", "")),
            active_template=request.get("activeTemplate") if isinstance(request.get("activeTemplate"), dict) else None,
            step_index=request.get("stepIndex", 0),
            retrieval_intent=str(request.get("retrievalIntent", "")),
            max_hops=request.get("maxHops", 2),
            k=request.get("k", 4),
            pseudo_feedback=bool(request.get("pseudoFeedback", False)),
            lexical_scores=request.get("lexicalScores") if isinstance(request.get("lexicalScores"), dict) else None,
            vector_scores=request.get("vectorScores") if isinstance(request.get("vectorScores"), dict) else None,
        )
    if operation == "delete_document_plan":
        return delete_document_plan(
            doc_id=str(request.get("docId", "")),
            chunks=_as_list(request, "chunks"),
        )
    raise ValueError(f"unsupported operation: {operation}")
