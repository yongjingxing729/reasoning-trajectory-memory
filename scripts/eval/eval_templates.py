"""Template-layer metrics for the offline evaluation harness.

Covers task-pattern classification accuracy (per pattern), strategy-template
matching top-1/top-3 accuracy, deterministic mining purity/coverage/cluster
statistics, and degenerate mining scenarios. Everything runs against the
deterministic Python core.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from reasoning_memory_core.templates import classify_task_pattern, mine_templates, plan_task  # noqa: E402

from eval_retrieval import memory_from_write  # noqa: E402


def evaluate_classification(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-pattern task classification accuracy against gold labels."""
    per_pattern: dict[str, list[bool]] = defaultdict(list)
    for episode in episodes:
        data = episode["episode"]
        task = data["task"]
        result = classify_task_pattern(
            task_desc=task["taskDesc"],
            goal=task.get("goal") or "",
            task_family=data["taskFamily"],
            structure_signature=task.get("abstractEvents") or [],
            has_graph=bool(task.get("reasoningGraph")),
        )
        gold = data["taskPattern"]
        per_pattern[gold].append(result["pattern"] == gold)
    accuracy = {
        pattern: round(sum(hits) / len(hits), 4) if hits else 1.0
        for pattern, hits in per_pattern.items()
    }
    total_hits = sum(sum(hits) for hits in per_pattern.values())
    total = sum(len(hits) for hits in per_pattern.values())
    return {
        "overall": round(total_hits / total, 4) if total else 1.0,
        "perPattern": dict(sorted(accuracy.items())),
    }


def evaluate_matching(
    episodes: list[dict[str, Any]],
    templates: list[dict[str, Any]],
    *,
    max_templates: int = 3,
) -> dict[str, Any]:
    """Template matching top-1/top-3 accuracy against query gold templates."""
    top1 = 0
    top3 = 0
    queries = 0
    for episode in episodes:
        data = episode["episode"]
        task = data["task"]
        plan = plan_task(
            task_desc=task["taskDesc"],
            goal=task.get("goal") or "",
            task_family=data["taskFamily"],
            events=[],
            has_graph=bool(task.get("reasoningGraph")),
            templates=templates,
            max_templates=max_templates,
        )
        matched_ids = [entry["templateId"] for entry in plan["matchedTemplates"]]
        for query in data["queries"]:
            gold = query.get("goldTemplate") or {}
            gold_id = gold.get("templateId")
            if not gold_id:
                continue
            queries += 1
            if matched_ids[:1] == [gold_id] or (matched_ids and matched_ids[0] == gold_id):
                top1 += 1
            if gold_id in matched_ids[:3]:
                top3 += 1
    return {
        "queries": queries,
        "top1": round(top1 / queries, 4) if queries else 0.0,
        "top3": round(top3 / queries, 4) if queries else 0.0,
    }


def evaluate_mining(episodes: list[dict[str, Any]], *, min_support: int = 3) -> dict[str, Any]:
    """Mining purity / coverage / cluster statistics over dev-split writes."""
    memories: list[dict[str, Any]] = []
    gold_by_memory: dict[str, str] = {}
    for episode in episodes:
        data = episode["episode"]
        for index, write in enumerate(data["memoryWrites"]):
            memory = memory_from_write(data, write)
            memory["createdAt"] = data["order"]
            memories.append(memory)
            gold_by_memory[write["memoryId"]] = write["ann"].get("goldTemplateId") or ""
    result = mine_templates(memories=memories, min_support=min_support)
    emitted = result["templates"]

    purities: list[float] = []
    covered_memory_ids: set[str] = set()
    for template in emitted:
        sources = template["provenance"]["sourceMemoryIds"]
        covered_memory_ids.update(sources)
        labels = Counter(gold_by_memory.get(memory_id, "") for memory_id in sources)
        if sources:
            purities.append(max(labels.values()) / len(sources))
    labeled = {memory_id for memory_id, label in gold_by_memory.items() if label}
    coverage = len(covered_memory_ids & labeled) / len(labeled) if labeled else 1.0
    return {
        "clusterCount": len(result["clusters"]),
        "emittedTemplates": len(emitted),
        "skipped": result["skipped"],
        "purity": round(sum(purities) / len(purities), 4) if purities else 0.0,
        "coverage": round(coverage, 4),
    }


def check_degenerate_scenarios(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Determinism and degenerate-input checks for the mining pipeline."""
    checks: dict[str, bool] = {}
    empty = mine_templates(memories=[], min_support=3)
    checks["empty_bank_emits_nothing"] = empty["templates"] == [] and empty["clusters"] == []

    single = [
        {
            "memoryId": f"s{i}", "outcome": "success", "createdAt": i,
            "semanticText": "重构代码库", "taskScope": "code_modification",
            "structureSignature": ["search", "execute", "verify"],
            "provenance": {"taskId": f"st{i}"},
        }
        for i in range(4)
    ]
    single_run = mine_templates(memories=single, min_support=3)
    checks["single_cluster_emits_one"] = len(single_run["templates"]) == 1

    extreme = [
        {
            "memoryId": f"e{i}", "outcome": "success", "createdAt": i,
            "semanticText": "执行操作", "taskScope": "general",
            "structureSignature": ["execute", "execute", "execute"],
            "provenance": {"taskId": f"et{i}"},
        }
        for i in range(4)
    ]
    extreme_run = mine_templates(memories=extreme, min_support=3)
    checks["monotone_cluster_skipped"] = extreme_run["templates"] == [] and extreme_run["skipped"]["monotone"] == 1

    memories = [
        memory_from_write(episode["episode"], write)
        for episode in episodes
        for write in episode["episode"]["memoryWrites"]
    ]
    first = mine_templates(memories=memories, min_support=3)
    second = mine_templates(memories=memories, min_support=3)
    checks["mining_deterministic"] = (
        first["clusters"] == second["clusters"]
        and first["skipped"] == second["skipped"]
    )
    return checks
