"""Build-time validator for the episode JSONL schema (04-eval-schema.md).

Implements the six assertion classes from 04 section 8: parseability and
schemaVersion, required fields / enums / length caps, every anti-leakage rule
from section 4, reference integrity for expected memory ids, structural
conformance of structureSignature / reasoningGraph, and a family-split report.
Any violation fails the build: the harness refuses to evaluate invalid data.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from reasoning_memory_core.constants import GRAPH_NODE_KINDS, GRAPH_RELATIONS, MAX_GRAPH_EDGES, MAX_GRAPH_NODES, MAX_GRAPH_NODE_TEXT, TAGS  # noqa: E402

from eval_dataset import (  # noqa: E402
    CATEGORIES,
    EVIDENCE_QUALITY,
    POTENTIAL_HARM,
    SCHEMA_VERSION,
    SPLITS,
    TRANSFERABILITY,
    load_jsonl,
)

EPISODE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
TOP_LEVEL_KEYS = {"schemaVersion", "episode"}
EPISODE_KEYS = {
    "episodeId", "order", "split", "taskFamily", "taskFamilyGroup", "taskPattern",
    "task", "outcome", "verifier", "evidence", "memoryWrites", "queries", "ann",
}
TASK_KEYS = {"taskDesc", "goal", "abstractEvents", "reasoningGraph"}
WRITE_KEYS = {
    "memoryId", "type", "outcome", "taskScope", "preconditions", "title",
    "description", "content", "contraindications", "reasoning", "reasoningGraph",
    "structureSignature", "ann",
}
WRITE_ANN_KEYS = {
    "transferability", "evidenceQuality", "potentialHarm", "deSpecified",
    "goldTemplateId", "annotators", "agreement",
}
QUERY_KEYS = {
    "queryId", "turn", "text", "k", "route", "reasoningGraphJson", "expected",
    "goldTemplate", "ann",
}
EXPECTED_KEYS = {"memoryId", "relevance", "misleading", "basis", "category"}
GOLD_TEMPLATE_KEYS = {"templateId", "structureSignature", "moduleRequirements", "justification"}
QUERY_ANN_KEYS = {"allowSelfRetrieval", "intent"}
EPISODE_ANN_KEYS = {"containsSecretAnswer", "notes"}
ROUTES = {"auto", "semantic", "structure", "graph", "hybrid"}
PATTERNS = {"multi_hop_qa", "code_synthesis", "tool_orchestration", "gui_operation", "long_horizon_planning", "numeric_search", "general"}
INTENTS = {"transfer", "recall", "trap"}
MEMORY_TYPES = {"strategy", "structure", "failure"}

# Length caps mirror the plugin-side clips (04 section 8.2).
CAPS = {
    ("episode", "task.taskDesc"): 4000,
    ("episode", "verifier"): 200,
    ("episode", "evidence"): 2000,
    ("write", "content"): 4000,
    ("write", "reasoning"): 4000,
    ("write", "taskScope"): 64,
    ("write", "title"): 120,
    ("write", "description"): 300,
    ("write", "contraindications[]"): 200,
    ("write", "preconditions[]"): 200,
    ("query", "text"): 4000,
}


def _check_keys(location: str, value: dict[str, Any], allowed: set[str], violations: list[str]) -> None:
    extras = sorted(set(value) - allowed)
    for extra in extras:
        violations.append(f"{location}: unknown key '{extra}'")


def _check_len(location: str, value: Any, cap: int, violations: list[str]) -> None:
    if isinstance(value, str) and len(value) > cap:
        violations.append(f"{location}: length {len(value)} exceeds cap {cap}")


def _graph_error(location: str, graph: Any, violations: list[str]) -> None:
    if graph is None:
        return
    if not isinstance(graph, dict) or graph.get("version") != 1:
        violations.append(f"{location}: reasoningGraph must be a version-1 object")
        return
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        violations.append(f"{location}: reasoningGraph nodes and edges must be arrays")
        return
    if not 2 <= len(nodes) <= MAX_GRAPH_NODES:
        violations.append(f"{location}: graph must have 2-{MAX_GRAPH_NODES} nodes")
    if not 1 <= len(edges) <= MAX_GRAPH_EDGES:
        violations.append(f"{location}: graph must have 1-{MAX_GRAPH_EDGES} edges")
    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not isinstance(node.get("kind"), str):
            violations.append(f"{location}: every node needs string id and kind")
            continue
        node_id = node["id"]
        if node_id in node_ids:
            violations.append(f"{location}: duplicate node id {node_id}")
        node_ids.add(node_id)
        if node["kind"] not in GRAPH_NODE_KINDS:
            violations.append(f"{location}: unsupported node kind {node['kind']}")
        if not isinstance(node.get("text"), str) or not node["text"].strip() or len(node["text"]) > MAX_GRAPH_NODE_TEXT:
            violations.append(f"{location}: node {node_id} text must be 1-{MAX_GRAPH_NODE_TEXT} chars")
    for edge in edges:
        if not isinstance(edge, dict):
            violations.append(f"{location}: every edge must be an object")
            continue
        if edge.get("sourceId") not in node_ids or edge.get("targetId") not in node_ids:
            violations.append(f"{location}: edge endpoints must reference nodes")
        if edge.get("relation") not in GRAPH_RELATIONS:
            violations.append(f"{location}: unsupported edge relation {edge.get('relation')!r}")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def validate(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate parsed JSONL lines; returns {ok, violations, stats}."""
    violations: list[str] = []
    episodes: list[dict[str, Any]] = []
    orders: list[int] = []
    episode_ids: set[str] = set()
    memory_locations: dict[str, tuple[int, str]] = {}

    for line in lines:
        location = f"line {line['__line__']}"
        try:
            value = line["value"]
        except Exception:
            violations.append(f"line {line['__line__']}: cannot parse line as JSON")
            continue
        if not isinstance(value, dict):
            violations.append(f"{location}: line must be a JSON object")
            continue
        _check_keys(location, value, TOP_LEVEL_KEYS, violations)
        if value.get("schemaVersion") != SCHEMA_VERSION:
            violations.append(f"{location}: schemaVersion must be {SCHEMA_VERSION}")
        episode = value.get("episode")
        if not isinstance(episode, dict):
            violations.append(f"{location}: episode must be an object")
            continue
        _check_keys(f"{location} episode", episode, EPISODE_KEYS, violations)

        episode_id = episode.get("episodeId")
        if not isinstance(episode_id, str) or not EPISODE_ID_RE.fullmatch(episode_id):
            violations.append(f"{location}: episodeId must match {EPISODE_ID_RE.pattern}")
        elif episode_id in episode_ids:
            violations.append(f"{location}: duplicate episodeId {episode_id}")
        else:
            episode_ids.add(episode_id)

        order = episode.get("order")
        if not isinstance(order, int):
            violations.append(f"{location}: order must be an integer")
        elif order in orders:
            violations.append(f"{location}: duplicate order {order}")
        else:
            orders.append(order)

        split = episode.get("split")
        if split not in SPLITS:
            violations.append(f"{location}: split must be dev or test")
        if not isinstance(episode.get("taskFamily"), str) or not episode["taskFamily"].strip() or len(episode["taskFamily"]) > 64:
            violations.append(f"{location}: taskFamily must be a non-empty string of at most 64 chars")
        if not isinstance(episode.get("taskFamilyGroup"), str) or not episode["taskFamilyGroup"].strip():
            violations.append(f"{location}: taskFamilyGroup must be a non-empty string")
        if episode.get("taskPattern") not in PATTERNS:
            violations.append(f"{location}: unsupported taskPattern {episode.get('taskPattern')!r}")
        _check_len(f"{location}", episode.get("verifier"), CAPS[("episode", "verifier")], violations)
        _check_len(f"{location}", episode.get("evidence"), CAPS[("episode", "evidence")], violations)
        if episode.get("outcome") not in ("success", "fail"):
            violations.append(f"{location}: outcome must be success or fail")

        task = episode.get("task")
        if not isinstance(task, dict):
            violations.append(f"{location}: task must be an object")
        else:
            _check_keys(f"{location} task", task, TASK_KEYS, violations)
            if not isinstance(task.get("taskDesc"), str) or not task["taskDesc"].strip():
                violations.append(f"{location}: task.taskDesc must be a non-empty string")
            _check_len(f"{location} task", task.get("taskDesc"), CAPS[("episode", "task.taskDesc")], violations)
            if not isinstance(task.get("goal"), (str, type(None))):
                violations.append(f"{location}: task.goal must be a string or null")
            abstract = task.get("abstractEvents")
            if not isinstance(abstract, list) or not all(tag in TAGS for tag in abstract):
                violations.append(f"{location}: abstractEvents must be a list of the six structure tags")
            _graph_error(f"{location} task", task.get("reasoningGraph"), violations)

        writes = episode.get("memoryWrites")
        if not isinstance(writes, list) or not writes:
            violations.append(f"{location}: memoryWrites must contain at least one write")
        else:
            for index, write in enumerate(writes):
                where = f"{location} memoryWrites[{index}]"
                if not isinstance(write, dict):
                    violations.append(f"{where}: write must be an object")
                    continue
                _check_keys(where, write, WRITE_KEYS, violations)
                memory_id = write.get("memoryId")
                if not isinstance(memory_id, str) or not memory_id.strip():
                    violations.append(f"{where}: memoryId must be a non-empty string")
                elif isinstance(order, int):
                    memory_locations[memory_id] = (order, where)
                if write.get("type") not in MEMORY_TYPES:
                    violations.append(f"{where}: unsupported memory type {write.get('type')!r}")
                if write.get("outcome") != episode.get("outcome"):
                    violations.append(f"{where}: write outcome must match the episode outcome")
                if not isinstance(write.get("taskScope"), str) or not write["taskScope"].strip():
                    violations.append(f"{where}: taskScope must be a non-empty string")
                _check_len(where, write.get("taskScope"), CAPS[("write", "taskScope")], violations)
                _check_len(where, write.get("title"), CAPS[("write", "title")], violations)
                _check_len(where, write.get("description"), CAPS[("write", "description")], violations)
                _check_len(where, write.get("content"), CAPS[("write", "content")], violations)
                _check_len(where, write.get("reasoning"), CAPS[("write", "reasoning")], violations)
                for field in ("preconditions", "contraindications"):
                    entries = write.get(field)
                    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
                        violations.append(f"{where}: {field} must be an array of strings")
                    else:
                        for entry in entries:
                            _check_len(where, entry, CAPS[("write", f"{field}[]")], violations)
                signature = write.get("structureSignature")
                if not isinstance(signature, list) or not all(tag in TAGS for tag in signature):
                    violations.append(f"{where}: structureSignature must be a list of the six structure tags")
                _graph_error(where, write.get("reasoningGraph"), violations)
                ann = write.get("ann")
                if not isinstance(ann, dict):
                    violations.append(f"{where}: ann annotation is required")
                else:
                    _check_keys(f"{where} ann", ann, WRITE_ANN_KEYS, violations)
                    if ann.get("transferability") not in TRANSFERABILITY:
                        violations.append(f"{where}: ann.transferability must be high/medium/low")
                    if ann.get("evidenceQuality") not in EVIDENCE_QUALITY:
                        violations.append(f"{where}: ann.evidenceQuality must be strong/weak/unattributable")
                    if ann.get("potentialHarm") not in POTENTIAL_HARM:
                        violations.append(f"{where}: ann.potentialHarm must be none/contextual/general")
                    if not isinstance(ann.get("deSpecified"), bool):
                        violations.append(f"{where}: ann.deSpecified must be a boolean")
                    if ann.get("goldTemplateId") is not None and not isinstance(ann["goldTemplateId"], str):
                        violations.append(f"{where}: ann.goldTemplateId must be a string or null")
                    if not isinstance(ann.get("annotators"), list) or len(ann.get("annotators") or []) < 2:
                        violations.append(f"{where}: ann.annotators needs at least two annotator ids")
                    if ann.get("agreement") not in ("agree", "resolved"):
                        violations.append(f"{where}: ann.agreement must be agree or resolved")

        queries = episode.get("queries")
        if not isinstance(queries, list) or not queries:
            violations.append(f"{location}: queries must contain at least one query")
        else:
            for index, query in enumerate(queries):
                where = f"{location} queries[{index}]"
                if not isinstance(query, dict):
                    violations.append(f"{where}: query must be an object")
                    continue
                _check_keys(where, query, QUERY_KEYS, violations)
                if not isinstance(query.get("queryId"), str) or not query["queryId"].strip():
                    violations.append(f"{where}: queryId must be a non-empty string")
                if not isinstance(query.get("turn"), int) or query.get("turn") < 1:
                    violations.append(f"{where}: turn must be a positive integer")
                _check_len(where, query.get("text"), CAPS[("query", "text")], violations)
                if not isinstance(query.get("k"), int) or not 1 <= query.get("k", 0) <= 20:
                    violations.append(f"{where}: k must be an integer in 1..20")
                if query.get("route") not in ROUTES:
                    violations.append(f"{where}: unsupported route {query.get('route')!r}")
                if query.get("reasoningGraphJson") is not None and not isinstance(query["reasoningGraphJson"], str):
                    violations.append(f"{where}: reasoningGraphJson must be a JSON string or null")
                expected = query.get("expected")
                if not isinstance(expected, list):
                    violations.append(f"{where}: expected must be an array")
                else:
                    for label_index, entry in enumerate(expected):
                        label_where = f"{where} expected[{label_index}]"
                        if not isinstance(entry, dict):
                            violations.append(f"{label_where}: expected entry must be an object")
                            continue
                        _check_keys(label_where, entry, EXPECTED_KEYS, violations)
                        if not isinstance(entry.get("memoryId"), str) or not entry["memoryId"].strip():
                            violations.append(f"{label_where}: memoryId must be a non-empty string")
                        relevance = entry.get("relevance")
                        if not isinstance(relevance, int) or not 0 <= relevance <= 3:
                            violations.append(f"{label_where}: relevance must be an integer 0..3")
                        misleading = entry.get("misleading")
                        if not isinstance(misleading, bool):
                            violations.append(f"{label_where}: misleading must be a boolean")
                        elif misleading and relevance != 0:
                            violations.append(f"{label_where}: misleading=true requires relevance 0")
                        if entry.get("category") not in CATEGORIES:
                            violations.append(f"{label_where}: unsupported category {entry.get('category')!r}")
                        # Reference integrity: the memory must exist in this or
                        # an earlier episode (checked after the full pass).
                gold = query.get("goldTemplate")
                if gold is not None:
                    if not isinstance(gold, dict):
                        violations.append(f"{where}: goldTemplate must be an object or null")
                    else:
                        _check_keys(f"{where} goldTemplate", gold, GOLD_TEMPLATE_KEYS, violations)
                        if not isinstance(gold.get("templateId"), str) or not gold["templateId"].strip():
                            violations.append(f"{where}: goldTemplate.templateId must be a non-empty string")
                        signature = gold.get("structureSignature")
                        if not isinstance(signature, list) or not all(tag in TAGS for tag in signature):
                            violations.append(f"{where}: goldTemplate.structureSignature must be a list of the six tags")
                query_ann = query.get("ann")
                if not isinstance(query_ann, dict):
                    violations.append(f"{where}: query ann is required")
                else:
                    _check_keys(f"{where} ann", query_ann, QUERY_ANN_KEYS, violations)
                    if not isinstance(query_ann.get("allowSelfRetrieval"), bool):
                        violations.append(f"{where}: ann.allowSelfRetrieval must be a boolean")
                    if query_ann.get("intent") not in INTENTS:
                        violations.append(f"{where}: ann.intent must be transfer/recall/trap")
        episode_ann = episode.get("ann")
        if not isinstance(episode_ann, dict):
            violations.append(f"{location}: episode ann is required")
        else:
            _check_keys(f"{location} ann", episode_ann, EPISODE_ANN_KEYS, violations)
            if not isinstance(episode_ann.get("containsSecretAnswer"), bool):
                violations.append(f"{location}: ann.containsSecretAnswer must be a boolean")
        episodes.append((order, episode, location))

    # Leak rule 1: a taskFamily appears in exactly one split.
    family_splits: dict[str, set[str]] = defaultdict(set)
    for _, episode, location in episodes:
        family = episode.get("taskFamily")
        split = episode.get("split")
        if isinstance(family, str) and isinstance(split, str):
            family_splits[family].add(split)
    for family, splits in family_splits.items():
        if len(splits) > 1:
            violations.append(f"leak rule 1: taskFamily '{family}' spans splits {sorted(splits)}")

    # Leak rule 2: a taskFamilyGroup stays inside one split.
    group_splits: dict[str, set[str]] = defaultdict(set)
    for _, episode, location in episodes:
        group = episode.get("taskFamilyGroup")
        split = episode.get("split")
        if isinstance(group, str) and isinstance(split, str):
            group_splits[group].add(split)
    for group, splits in group_splits.items():
        if len(splits) > 1:
            violations.append(f"leak rule 2: taskFamilyGroup '{group}' spans splits {sorted(splits)}")

    # Leak rule 3: cross-split task descriptions must not be near-duplicates.
    by_split: dict[str, list[tuple[int, dict[str, Any], str]]] = {"dev": [], "test": []}
    for order, episode, location in episodes:
        split = episode.get("split")
        if split in by_split:
            by_split[split].append((order, episode, location))
    for dev_order, dev_episode, _ in by_split["dev"]:
        dev_task = dev_episode.get("task") or {}
        left = _normalize(str(dev_task.get("taskDesc", "")))
        for test_order, test_episode, _ in by_split["test"]:
            test_task = test_episode.get("task") or {}
            right = _normalize(str(test_task.get("taskDesc", "")))
            if left and right and SequenceMatcher(None, left, right).ratio() >= 0.85:
                violations.append(
                    f"leak rule 3: '{dev_episode['episodeId']}' and '{test_episode['episodeId']}' "
                    "taskDesc similarity is at least 0.85 across splits",
                )

    # Leak rule 6 + reference integrity: expected references must point at
    # this or an earlier episode's writes, and self-references need the flag.
    for order, episode, location in episodes:
        writes = episode.get("memoryWrites") or []
        own_writes = {write["memoryId"] for write in writes if isinstance(write, dict) and isinstance(write.get("memoryId"), str)}
        for index, query in enumerate(episode.get("queries") or []):
            if not isinstance(query, dict) or not isinstance(query.get("expected"), list):
                continue
            where = f"{location} queries[{index}]"
            for entry in query["expected"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("memoryId"), str):
                    continue
                memory_id = entry["memoryId"]
                if memory_id not in memory_locations:
                    violations.append(f"{where}: expected memory '{memory_id}' does not exist in any memoryWrites")
                    continue
                write_order, write_where = memory_locations[memory_id]
                if isinstance(order, int) and isinstance(write_order, int) and write_order > order:
                    violations.append(f"{where}: expected memory '{memory_id}' is written by a future episode ({write_where})")
                if memory_id in own_writes and not (query.get("ann") or {}).get("allowSelfRetrieval"):
                    violations.append(f"{where}: expected memory '{memory_id}' is written by this episode but allowSelfRetrieval is false")

    # Leak rule 7: double annotation entries must be complete (checked above)
    # and resolved disagreements must keep notes.
    for order, episode, location in episodes:
        for write in episode.get("memoryWrites") or []:
            ann = write.get("ann") if isinstance(write, dict) else None
            if isinstance(ann, dict) and ann.get("agreement") == "resolved" and not (episode.get("ann") or {}).get("notes"):
                violations.append(f"{location}: resolved disagreement without episode notes")

    # Family-split report (assertion class 6).
    matrix: Counter[tuple[str, str]] = Counter()
    family_counts: Counter[str] = Counter()
    annotators: Counter[str] = Counter()
    agreements: Counter[str] = Counter()
    for _, episode, _ in episodes:
        family = episode.get("taskFamily")
        split = episode.get("split")
        if isinstance(family, str) and isinstance(split, str):
            matrix[(family, split)] += 1
            family_counts[family] += 1
        for write in episode.get("memoryWrites") or []:
            ann = write.get("ann") if isinstance(write, dict) else None
            if not isinstance(ann, dict):
                continue
            for annotator in ann.get("annotators") or []:
                annotators[str(annotator)] += 1
            agreements[str(ann.get("agreement"))] += 1

    return {
        "ok": not violations,
        "violations": violations,
        "stats": {
            "episodes": len(episodes),
            "families": sorted(family_counts),
            "familyCounts": {family: family_counts[family] for family in sorted(family_counts)},
            "familySplitMatrix": {
                f"{family} [{split}]": matrix[(family, split)]
                for family, split in sorted(matrix)
            },
            "annotators": dict(annotators),
            "agreements": dict(agreements),
        },
    }


def validate_file(path: str | Path) -> dict[str, Any]:
    return validate(load_jsonl(path))


if __name__ == "__main__":
    import json as jsonlib
    report = validate_file(ROOT / "scripts" / "eval" / "data" / "synthetic-dev.jsonl")
    print(jsonlib.dumps(report, ensure_ascii=False, indent=2))
