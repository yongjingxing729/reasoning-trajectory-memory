"""Deterministic workflow mining over observed memory trajectories.

Online representative clustering with SequenceMatcher similarity follows
02-template-algorithms.md 2.2: join at >= CLUSTER_JOIN_SIMILARITY, open a
new cluster below CLUSTER_FORM_SIMILARITY, and park in-between members in
a pending queue that never moves representatives.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from .constants import (
    CLUSTER_FORM_SIMILARITY,
    CLUSTER_JOIN_SIMILARITY,
    MAX_TEMPLATE_DESCRIPTION_CHARS,
    MAX_TEMPLATE_PRECONDITION_CHARS,
    MAX_TEMPLATE_PRECONDITIONS,
    MAX_TEMPLATE_SOURCE_MEMORY_IDS,
    TAG_TEXT,
)
from .template_build import build_template_steps
from .template_classify import classify_task_pattern
from .template_match import _multi_sequence_lcs, _normalize_signature
from .text_similarity import _clean_text


def mine_templates(
    *,
    memories: list[dict[str, Any]],
    min_support: int = 3,
    max_templates: int = 20,
    existing_templates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministic workflow mining over (taskPattern, structureSignature).

    Online representative clustering with SequenceMatcher similarity follows
    02-template-algorithms.md 2.2: join at >= CLUSTER_JOIN_SIMILARITY, open a
    new cluster below CLUSTER_FORM_SIMILARITY, and park in-between members in
    a pending queue that never moves representatives.
    """
    support = 3
    try:
        support = max(1, int(float(min_support)))
    except (TypeError, ValueError):
        support = 3
    emit_cap = 20
    try:
        emit_cap = max(1, min(200, int(float(max_templates))))
    except (TypeError, ValueError):
        emit_cap = 20
    existing_keys: set[tuple[tuple[str, ...], str]] = set()
    for template in existing_templates or []:
        if not isinstance(template, dict):
            continue
        signature = tuple(_normalize_signature(template.get("structureSignature")))
        for pattern in template.get("taskPatterns") or []:
            existing_keys.add((signature, str(pattern)))

    rows: list[dict[str, Any]] = []
    for memory in memories or []:
        if not isinstance(memory, dict) or not memory.get("memoryId"):
            continue
        signature = _normalize_signature(memory.get("structureSignature"))
        if not signature:
            continue
        if memory.get("outcome") not in ("success", "fail"):
            continue
        rows.append({
            "memory": memory,
            "signature": signature,
            "sortKey": (int(memory.get("createdAt") or 0), str(memory.get("memoryId", ""))),
        })
    rows.sort(key=lambda row: row["sortKey"])

    clusters: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for row in rows:
        signature = row["signature"]
        best_cluster = None
        best_ratio = 0.0
        for cluster in clusters:
            ratio = SequenceMatcher(None, signature, cluster["signature"]).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_cluster = cluster
        if best_cluster is None:
            clusters.append({"signature": signature, "members": [row]})
        elif best_ratio >= CLUSTER_JOIN_SIMILARITY:
            best_cluster["members"].append(row)
        elif best_ratio < CLUSTER_FORM_SIMILARITY:
            clusters.append({"signature": signature, "members": [row]})
        else:
            pending.append(row)

    skipped: dict[str, int] = {
        "below_support": 0,
        "too_short": 0,
        "monotone": 0,
        "fail_majority": 0,
        "duplicate": 0,
        "pending": len(pending),
    }
    cluster_reports: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    now = time.time_ns() // 1_000_000
    name_counter = 0
    for cluster in clusters:
        members = cluster["members"]
        signatures = [member["signature"] for member in members]
        representative = cluster["signature"]
        core_signature = _multi_sequence_lcs(signatures) or representative
        pattern_counts: Counter[str] = Counter()
        for member in members:
            memory = member["memory"]
            label = classify_task_pattern(
                task_desc=_clean_text(memory.get("semanticText")),
                task_family=_clean_text(memory.get("taskScope"), 200),
            )["pattern"]
            pattern_counts[label] += 1
        pattern = max(
            pattern_counts,
            key=lambda label: (pattern_counts[label], -list(pattern_counts).index(label)),
        )
        successes = sum(member["memory"].get("outcome") == "success" for member in members)
        size = len(members)
        cluster_reports.append({
            "signature": representative,
            "core_signature": core_signature,
            "pattern": pattern,
            "memory_ids": [str(member["memory"].get("memoryId", "")) for member in members],
            "size": size,
            "success_ratio": round(successes / size, 6),
        })

        reason = ""
        if size < support:
            reason = "below_support"
        elif all(tag == "execute" for tag in representative):
            # Consecutive-dedupe normalization collapses pure-execute runs, so
            # a monotone cluster is reported before the generic length gate.
            reason = "monotone"
        elif len(representative) < 2:
            reason = "too_short"
        elif successes / size < 0.5:
            reason = "fail_majority"
        if reason:
            skipped[reason] += 1
            continue
        key = (tuple(representative), pattern)
        if key in existing_keys:
            skipped["duplicate"] += 1
            continue
        if len(candidates) >= emit_cap:
            break

        name_counter += 1
        pattern_slug = pattern.replace("_", "-")
        # Namespace-prefixed generated name: the memory_tpl_ prefix is fixed,
        # the body stays kebab-case per the publish validator's regex.
        name = f"memory_tpl_mined-{pattern_slug}-{name_counter}"
        tag_text = " → ".join(TAG_TEXT.get(tag, tag) for tag in representative)
        description = (
            f"Use when a task of type {pattern} follows this observed step pattern: {tag_text}."
        )[:MAX_TEMPLATE_DESCRIPTION_CHARS]
        preconditions = sorted({
            precondition[:MAX_TEMPLATE_PRECONDITION_CHARS]
            for member in members
            for precondition in (
                member["memory"].get("preconditions") or []
                if isinstance(member["memory"].get("preconditions"), list) else []
            )
            if isinstance(precondition, str) and precondition.strip()
        })[:MAX_TEMPLATE_PRECONDITIONS]
        source_memory_ids = [
            str(member["memory"].get("memoryId", "")) for member in members
        ][:MAX_TEMPLATE_SOURCE_MEMORY_IDS]
        source_task_ids = sorted({
            str(member["memory"].get("provenance", {}).get("taskId", ""))
            for member in members
            if member["memory"].get("provenance", {}).get("taskId")
        })[:MAX_TEMPLATE_SOURCE_MEMORY_IDS]
        candidates.append({
            "templateId": str(uuid.uuid4()),
            "name": name,
            "description": description,
            "taskPatterns": [pattern],
            "steps": build_template_steps(representative),
            "structureSignature": representative,
            "coreSignature": core_signature,
            "preconditions": preconditions,
            "provenance": {
                "author": "mined",
                "sourceMemoryIds": source_memory_ids,
                "sourceTaskIds": source_task_ids,
            },
            "utility": {"helpful": 0, "harmful": 0, "neutral": 0},
            "version": 1,
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
        })
        existing_keys.add(key)

    return {
        "templates": candidates,
        "clusters": cluster_reports,
        "skipped": skipped,
    }
