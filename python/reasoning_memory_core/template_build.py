"""Deterministic template construction and builtin seeds.

Covers module demands, step building, lossy signature derivation from caller
steps, the five idempotently seeded builtin templates (final spec 3.2), and
the small utility helpers consumed by planning.
"""

from __future__ import annotations

import time
from typing import Any

from .constants import (
    BUILTIN_TEMPLATES,
    MAX_TEMPLATE_STEPS,
    MODULE_DEMAND_BY_TAG,
    MODULE_DEMAND_INTENT,
    MODULE_DEMAND_TAG,
    TAG_TEXT,
)
from .template_match import _normalize_signature


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
