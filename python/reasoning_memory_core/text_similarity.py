"""text similarity for the DSH reasoning-memory core."""

from __future__ import annotations

import re
from typing import Any

from .constants import MAX_MEMORY_TEXT


def _clean_text(value: Any, limit: int = MAX_MEMORY_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]

def _token_set(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[^\W_]+", text.lower(), flags=re.UNICODE):
        if any("\u4e00" <= char <= "\u9fff" for char in token):
            tokens.update(token)
            tokens.update(token[index:index + 2] for index in range(len(token) - 1))
        else:
            tokens.add(token)
    return tokens

def semantic_similarity(left: str, right: str) -> float:
    a = _token_set(left)
    b = _token_set(right)
    if not a or not b:
        return 0.0
    overlap = len(a & b) / len(a | b)
    compact_left = re.sub(r"\s+", "", left).lower()
    compact_right = re.sub(r"\s+", "", right).lower()
    substring = 1.0 if compact_left and compact_left in compact_right else 0.0
    return max(overlap, substring)

def _reasoning_text(item: dict[str, Any]) -> str:
    current = _clean_text(item.get("reasoning"))
    if current:
        return current
    # v1 memories kept the distilled lesson inside content. Recover it for
    # retrieval without rewriting the durable record during a read.
    content = _clean_text(item.get("content"))
    prefixes = (
        "调用者提炼的可迁移推理策略（未独立验证）：",
        "失败模式（由任务调用者归因，尚未独立验证）：",
        "从任务轨迹蒸馏的可迁移推理策略（未独立验证）：",
        "失败模式（从任务轨迹蒸馏归因，尚未独立验证）：",
        "由成败轨迹对比蒸馏的可迁移推理策略（未独立验证）：",
        "失败模式（由成败轨迹对比蒸馏归因，尚未独立验证）：",
    )
    for prefix in prefixes:
        if content.startswith(prefix):
            lesson = content[len(prefix):]
            for marker in ("。本次可观察的操作结构：", "。再次遇到时"):
                lesson = lesson.split(marker, 1)[0]
            return lesson.strip()
    return ""

def sequence_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    previous = [0] * (len(right) + 1)
    for a_tag in left:
        current = [0]
        for index, b_tag in enumerate(right, start=1):
            current.append(previous[index - 1] + 1 if a_tag == b_tag else max(previous[index], current[-1]))
        previous = current
    lcs = previous[-1]
    return (2.0 * lcs) / (len(left) + len(right))
