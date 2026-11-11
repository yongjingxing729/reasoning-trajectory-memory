"""Shared imports, path setup and fixtures for the split reasoning-memory test modules.

Every test module imports this via ``from _support import *`` after putting its own
directory on ``sys.path``; the names below (ROOT, core, tool_events, make_memory and
the stdlib imports used by the tests) are re-exported by that star-import.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import reasoning_memory as core  # noqa: E402


def tool_events(*, success: bool = True, tool: str = "bash") -> list[dict]:
    return [
        {"seq": 1, "type": "tool_call", "callId": "c1", "toolName": tool},
        {"seq": 2, "type": "tool_result", "callId": "c1", "toolName": tool, "success": success},
    ]


def make_memory(
    memory_id: str,
    outcome: str,
    *,
    context: str = "validate schema before publishing",
    reasoning: str = "validate schema before publishing",
    task_id: str = "prior-task",
    feedback: dict | None = None,
    reasoning_graph: dict | None = None,
) -> dict:
    return {
        "memoryId": memory_id,
        "outcome": outcome,
        "semanticText": context,
        "reasoning": reasoning,
        "reasoningGraph": reasoning_graph or {"version": 1, "nodes": [], "edges": []},
        "content": "",
        "feedback": feedback or {"helpful": 0, "harmful": 0, "neutral": 0},
        "structureSignature": ["search", "execute", "verify"],
        "provenance": {"taskId": task_id},
    }
