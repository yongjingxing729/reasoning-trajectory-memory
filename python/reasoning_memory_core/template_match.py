"""Strategy-template matching and structure-signature helpers.

match = (0.50 x struct + 0.30 x lexical + 0.20 x pattern_consistency)
        x quality, with quality bounded to [0.5, 1]. The signature helpers
here are shared by planning, mining and template construction.
"""

from __future__ import annotations

from typing import Any

from .constants import (
    MAX_TEMPLATE_DESCRIPTION_CHARS,
    MAX_TEMPLATE_NAME_CHARS,
    TAGS,
)
from .template_classify import _clip
from .text_similarity import _clean_text, semantic_similarity, sequence_similarity


def template_match_score(
    *,
    template: dict[str, Any],
    pattern: str,
    pattern_confidence: float,
    structure_signature: list[str],
    task_text: str,
) -> tuple[float, dict[str, float]]:
    """Score one template against the current task (final spec 3.1).

    match = (0.50 x struct + 0.30 x lexical + 0.20 x pattern_consistency)
            x quality, with quality bounded to [0.5, 1].
    pattern_confidence participates only in tie-breaks, never in the formula,
    so a weak classifier cannot inflate a match on its own.
    """
    if not isinstance(template, dict):
        return 0.0, {"struct": 0.0, "lexical": 0.0, "pattern": 0.0, "quality": 0.5}
    template_signature = template.get("coreSignature") or template.get("structureSignature") or []
    template_tags = [tag for tag in template_signature if tag in TAGS]
    query_tags = [tag for tag in (structure_signature or []) if tag in TAGS]
    struct = 0.0
    if template_tags and query_tags:
        struct = sequence_similarity(query_tags, template_tags)

    step_titles = []
    for step in template.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("title"), str):
            step_titles.append(step["title"])
    lexical_text = " ".join(filter(None, [
        _clean_text(template.get("name"), MAX_TEMPLATE_NAME_CHARS),
        _clean_text(template.get("description"), MAX_TEMPLATE_DESCRIPTION_CHARS),
        *step_titles,
    ]))
    lexical = semantic_similarity(task_text, lexical_text)

    task_patterns = template.get("taskPatterns") or []
    if pattern in task_patterns:
        consistency = 1.0
    elif pattern == "general":
        consistency = 0.5
    else:
        consistency = 0.0

    utility = template.get("utility") if isinstance(template.get("utility"), dict) else {}
    helpful = max(0, int(utility.get("helpful", 0)))
    harmful = max(0, int(utility.get("harmful", 0)))
    neutral = max(0, int(utility.get("neutral", 0)))
    use_count = helpful + harmful
    total_feedback = helpful + harmful + neutral
    harm_ratio = harmful / total_feedback if total_feedback > 0 else 0.0
    # EvolveR dynamic scoring (c_succ+1)/(c_use+2) replicated verbatim, with a
    # 0.5 floor so new templates stay visible (02-template-algorithms.md 2.4).
    quality = (0.5 + 0.5 * (helpful + 1.0) / (use_count + 2.0)) * (1.0 - 0.3 * harm_ratio)
    quality = _clip(quality, 0.5, 1.0)

    score = (0.50 * struct + 0.30 * lexical + 0.20 * consistency) * quality
    return round(_clip(score), 6), {
        "struct": round(struct, 6),
        "lexical": round(lexical, 6),
        "pattern": round(consistency, 6),
        "quality": round(quality, 6),
    }


def _normalize_signature(signature: list[str] | tuple[str, ...] | None) -> list[str]:
    output: list[str] = []
    for tag in signature or []:
        if tag in TAGS and (not output or output[-1] != tag):
            output.append(tag)
    return output


def _multi_sequence_lcs(signatures: list[list[str]]) -> list[str]:
    """Multi-sequence LCS by iterative pairwise contraction from the shortest."""
    if not signatures:
        return []
    common = list(min(signatures, key=len))
    for signature in signatures:
        height = len(common)
        width = len(signature)
        table = [[0] * (width + 1) for _ in range(height + 1)]
        for left in range(1, height + 1):
            for right in range(1, width + 1):
                if common[left - 1] == signature[right - 1]:
                    table[left][right] = table[left - 1][right - 1] + 1
                else:
                    table[left][right] = max(table[left - 1][right], table[left][right - 1])
        backtrack: list[str] = []
        left, right = height, width
        while left > 0 and right > 0:
            if common[left - 1] == signature[right - 1]:
                backtrack.append(common[left - 1])
                left -= 1
                right -= 1
            elif table[left - 1][right] >= table[left][right - 1]:
                left -= 1
            else:
                right -= 1
        common = list(reversed(backtrack))
        if not common:
            break
    return common
