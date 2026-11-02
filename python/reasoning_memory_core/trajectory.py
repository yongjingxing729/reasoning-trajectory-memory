"""trajectory for the DSH reasoning-memory core."""

from __future__ import annotations

import re
from typing import Any

from .constants import MAX_CAPTURED_EVENTS, TAGS


def classify_tool(name: str) -> str | None:
    # Token-based matching instead of raw substring containment: in-word hits
    # like "read" inside "thread" or "bread" used to misclassify unrelated
    # tools as evidence search. A part matches when a whole token equals it,
    # a token starts with it (reads/reader/reviewer/runner), or the name
    # itself starts with it (websearch/bash).
    lowered = name.lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]

    def matches(part: str) -> bool:
        return any(token == part or token.startswith(part) for token in tokens) or lowered.startswith(part)

    if any(matches(part) for part in ("plan", "todo", "goal", "subtask", "decompose")):
        return "decompose"
    if "check" in tokens and "scope" in tokens:
        return "constrain"
    if any(matches(part) for part in ("constraint", "inspect", "review")):
        return "constrain"
    if any(matches(part) for part in ("search", "grep", "glob", "read", "web", "query", "find")):
        return "search"
    if any(matches(part) for part in ("test", "verify", "lint", "typecheck", "build", "check")):
        return "verify"
    if any(matches(part) for part in ("edit", "write", "patch", "bash", "terminal", "execute", "run")):
        return "execute"
    return None

def structure_signature(events: list[dict[str, Any]]) -> list[str]:
    pending: dict[str, str] = {}
    labels: list[str] = []
    ordered = sorted(events[:MAX_CAPTURED_EVENTS], key=lambda row: int(row.get("seq", 0)))
    for event in ordered:
        kind = event.get("type")
        call_id = str(event.get("callId", ""))
        if kind == "tool_call":
            name = str(event.get("toolName", ""))
            if name and not name.startswith("memory_"):
                pending[call_id] = name
        elif kind == "tool_result":
            pending_name = pending.pop(call_id, "")
            name = str(event.get("toolName", "")) or pending_name
            if not name or name.startswith("memory_"):
                continue
            label = classify_tool(name)
            if label:
                labels.append(label)
            if event.get("success") is False:
                # Preserve which abstract action preceded the error, then mark
                # the observed recovery signal. This remains metadata-only.
                labels.append("rollback")
    return labels
