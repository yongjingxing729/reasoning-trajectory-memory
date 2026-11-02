"""Retrieval quality metrics for the offline evaluation harness.

Evaluates the deterministic Python core directly (no DSH host, no network):
for every query the bank holds the writes of all episodes up to and including
the current one, and the active episode's own writes are excluded through the
same excludeTaskId path the plugin uses. Metrics follow MVP_EVALUATION.md
section 6: Recall@k / nDCG@k per outcome, quota adherence (target 100%),
self-retrieval rate (target 0), and relevance-threshold filter statistics.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from reasoning_memory_core.retrieval import _retrieve  # noqa: E402

GAINS = {0: 0.0, 1: 1.0, 2: 3.0, 3: 7.0}


def memory_from_write(episode: dict[str, Any], write: dict[str, Any]) -> dict[str, Any]:
    """Project one memoryWrites entry onto the core-visible memory shape."""
    return {
        "memoryId": write["memoryId"],
        "outcome": write["outcome"],
        "semanticText": episode["task"]["taskDesc"],
        "reasoning": write["reasoning"],
        "content": write["content"],
        "reasoningGraph": write.get("reasoningGraph") or {"version": 1, "nodes": [], "edges": []},
        "feedback": {"helpful": 0, "harmful": 0, "neutral": 0},
        "structureSignature": write["structureSignature"],
        "preconditions": write["preconditions"],
        "strategyTemplateId": "",
        "status": "active",
        "provenance": {"taskId": episode["episodeId"]},
        "lexicalScore": 0.0,
        "vectorScore": 0.0,
    }


def outcome_by_memory(episodes: list[dict[str, Any]]) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for episode in episodes:
        for write in episode["episode"]["memoryWrites"]:
            outcomes[write["memoryId"]] = write["outcome"]
    return outcomes


def bank_through(episodes: list[dict[str, Any]], order: int) -> list[dict[str, Any]]:
    """All memory writes of episodes with order <= the given order."""
    bank: list[dict[str, Any]] = []
    for episode in episodes:
        if episode["episode"]["order"] > order:
            continue
        for write in episode["episode"]["memoryWrites"]:
            bank.append(memory_from_write(episode["episode"], write))
    return bank


def run_query(
    episode: dict[str, Any],
    query: dict[str, Any],
    bank: list[dict[str, Any]],
    *,
    threshold: float = 0.0,
    weights_override: dict[str, float] | None = None,
    active_template_id: str = "",
    active_template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one query through the core and return the raw retrieval response."""
    return _retrieve({
        "operation": "retrieve",
        "query": query["text"],
        "events": [],
        "memories": bank,
        "excludeTaskId": episode["episodeId"],
        "queryGraphJson": query.get("reasoningGraphJson") or "",
        "k": query["k"],
        "route": query.get("route", "auto"),
        "positiveNegativeRatio": 1.0,
        "relevanceThreshold": threshold,
        "activeTemplateId": active_template_id,
        "activeTemplate": active_template,
        "fusionWeightsOverride": weights_override,
    })


def _metrics_for_results(
    query: dict[str, Any],
    results: list[dict[str, Any]],
    own_write_ids: set[str],
    candidate_ids: set[str],
    outcomes: dict[str, str],
    threshold_filtered: int,
) -> dict[str, Any]:
    expected = query["expected"]
    relevance = {entry["memoryId"]: entry["relevance"] for entry in expected if isinstance(entry, dict)}
    retrieved_ids = [row["memoryId"] for row in results]
    relevant_total = {memory_id for memory_id, level in relevance.items() if level >= 1}

    def ideal_ids_for(outcome: str | None) -> list[str]:
        return [
            memory_id for memory_id, level in relevance.items()
            if level >= 1 and (outcome is None or outcomes.get(memory_id) == outcome)
        ]

    def ndcg_for(outcome: str | None) -> float | None:
        ideal_ids = ideal_ids_for(outcome)
        if not ideal_ids:
            return None
        ideal_dcg = sum(
            GAINS[relevance[memory_id]] / math.log2(index + 2)
            for index, memory_id in enumerate(sorted(ideal_ids, key=lambda mid: -GAINS[relevance[mid]]))
        )
        dcg = sum(
            GAINS[relevance[memory_id]] / math.log2(index + 2)
            for index, memory_id in enumerate(retrieved_ids)
            if memory_id in relevance and (outcome is None or outcomes.get(memory_id) == outcome)
        )
        return dcg / ideal_dcg if ideal_dcg > 0 else 0.0

    def recall_for(outcome: str | None) -> float | None:
        pool = {
            memory_id for memory_id in relevant_total
            if outcome is None or outcomes.get(memory_id) == outcome
        }
        if not pool:
            return None
        return sum(1 for memory_id in retrieved_ids if memory_id in pool) / len(pool)

    misleading_retrieved = sum(
        1 for entry in expected
        if isinstance(entry, dict) and entry.get("misleading") and entry["memoryId"] in retrieved_ids
    )
    self_retrieved = sum(1 for memory_id in retrieved_ids if memory_id in own_write_ids)
    return {
        "queryId": query["queryId"],
        "retrievedCount": len(retrieved_ids),
        "recall": recall_for(None),
        "recallSuccess": recall_for("success"),
        "recallFail": recall_for("fail"),
        "ndcg": ndcg_for(None),
        "ndcgSuccess": ndcg_for("success"),
        "ndcgFail": ndcg_for("fail"),
        "misleadingRetrieved": misleading_retrieved,
        "selfRetrieved": self_retrieved,
        "candidateCount": len(candidate_ids),
        "thresholdFiltered": threshold_filtered,
    }


def evaluate_retrieval(
    episodes: list[dict[str, Any]],
    *,
    threshold: float = 0.0,
    weights_override: dict[str, float] | None = None,
    bind_gold_template: bool = False,
    gold_templates: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run every query and aggregate the retrieval metrics."""
    rows: list[dict[str, Any]] = []
    quota_eligible = 0
    quota_compliant = 0
    outcomes = outcome_by_memory(episodes)
    for episode in episodes:
        data = episode["episode"]
        order = data["order"]
        bank = bank_through(episodes, order)
        own_ids = {write["memoryId"] for write in data["memoryWrites"]}
        eligible_ids = {
            memory["memoryId"] for memory in bank
            if memory["provenance"]["taskId"] != data["episodeId"]
        }
        for query in data["queries"]:
            active_template_id = ""
            active_template = None
            if bind_gold_template and gold_templates:
                gold = query.get("goldTemplate") or {}
                active_template_id = gold.get("templateId", "")
                active_template = gold_templates.get(active_template_id)
            response = run_query(
                data, query, bank,
                threshold=threshold,
                weights_override=weights_override,
                active_template_id=active_template_id,
                active_template=active_template,
            )
            balance = response["balance"]
            results = response["results"]
            # Quota adherence: measurable when both outcome pools hold enough
            # candidates to fill their slots; shortage cases follow the
            # documented fallback rule and are reported separately.
            k = query["k"]
            positive_slots = int((k * 1.0 / 2.0) + 0.5)
            negative_slots = k - positive_slots
            candidates = [memory for memory in bank if memory["memoryId"] in eligible_ids]
            positive_available = sum(1 for memory in candidates if memory["outcome"] == "success")
            negative_available = sum(1 for memory in candidates if memory["outcome"] == "fail")
            if positive_available >= positive_slots and negative_available >= negative_slots and len(results) > 0:
                quota_eligible += 1
                if balance["positiveCount"] == positive_slots and balance["negativeCount"] == negative_slots:
                    quota_compliant += 1
            rows.append(_metrics_for_results(
                query, results, own_ids, eligible_ids, outcomes,
                balance["thresholdFilteredCount"],
            ))

    def mean(key: str) -> float:
        values = [row[key] for row in rows if isinstance(row[key], (int, float))]
        return sum(values) / len(values) if values else 0.0

    def mean_or_none(key: str) -> float | None:
        values = [row[key] for row in rows if isinstance(row[key], (int, float))]
        return sum(values) / len(values) if values else None

    def rounded(value: float | None) -> float:
        return round(value, 4) if value is not None else 0.0

    return {
        "queries": len(rows),
        "recall": rounded(mean_or_none("recall")),
        "recallSuccess": rounded(mean_or_none("recallSuccess")),
        "recallFail": rounded(mean_or_none("recallFail")),
        "recallFailCoverage": sum(1 for row in rows if row["recallFail"] is not None),
        "ndcg": rounded(mean_or_none("ndcg")),
        "ndcgSuccess": rounded(mean_or_none("ndcgSuccess")),
        "ndcgFail": rounded(mean_or_none("ndcgFail")),
        "ndcgFailCoverage": sum(1 for row in rows if row["ndcgFail"] is not None),
        "misleadingRetrieved": sum(row["misleadingRetrieved"] for row in rows),
        "selfRetrievalRate": round(sum(row["selfRetrieved"] for row in rows) / len(rows), 4) if rows else 0.0,
        "quotaAdherence": round(quota_compliant / quota_eligible, 4) if quota_eligible else 1.0,
        "quotaEligibleQueries": quota_eligible,
        "thresholdFilteredMean": round(mean("thresholdFiltered"), 2),
        "rows": rows,
    }
