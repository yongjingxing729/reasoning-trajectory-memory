"""Operation dispatch for the Python reasoning-memory core."""

from __future__ import annotations

from typing import Any

from .distillation import _make_memory
from .feedback import _record_feedback
from .retrieval import _retrieve


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    if operation == "distill":
        return _make_memory(request)
    if operation == "retrieve":
        return _retrieve(request)
    if operation == "feedback":
        return _record_feedback(request)
    raise ValueError(f"unsupported operation: {operation}")
