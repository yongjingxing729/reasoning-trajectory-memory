"""Template feedback accounting and caller-template draft validation (spec 3.3)."""

from __future__ import annotations

import re
from typing import Any

from .constants import (
    MAX_MODULE_DEMANDS_PER_STEP,
    MAX_TEMPLATE_DESCRIPTION_CHARS,
    MAX_TEMPLATE_INTENT_CHARS,
    MAX_TEMPLATE_NAME_CHARS,
    MAX_TEMPLATE_PRECONDITION_CHARS,
    MAX_TEMPLATE_PRECONDITIONS,
    MAX_TEMPLATE_STEPS,
    MAX_TEMPLATE_STEP_TITLE_CHARS,
    MODULE_DEMAND_KINDS,
    TASK_PATTERNS,
)
from .template_build import derive_signature_from_demands
from .text_similarity import _clean_text


_EM_DASH = re.compile("[\u2014\u2013]")


_TRIGGER_TOKENS = re.compile(r"\b(when|if|for|mention|ask|say)\b|当|如果|提到|需要|时", re.IGNORECASE)


# Template names keep the memory_tpl_ namespace prefix followed by a
# kebab-case body, mirroring the skill layer's name discipline.
_KABAB_NAME = re.compile(r"^memory_tpl_[a-z0-9]+(-[a-z0-9]+)*$")


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
