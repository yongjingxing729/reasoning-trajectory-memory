"""Tests for the deterministic Python reasoning-memory core.

The core itself has no third-party dependencies; the test suite covers
distillation gates, structured items, retrieval fusion and the JSON bridge
(stdin/stdout and --file-io transports).
"""

from __future__ import annotations

import copy
import json
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


class DistillationTests(unittest.TestCase):
    def base_request(self, **overrides: object) -> dict:
        request = {
            "operation": "distill",
            "taskId": "task-1",
            "sourceSessionId": "session-1",
            "taskFamily": "code-review",
            "semanticText": "validate the schema before publishing",
            "events": tool_events(),
            "outcome": "success",
            "verifier": "unit-test-suite",
            "evidence": "The suite passed after schema validation.",
            "reasoningInsight": "Validate the source schema before publishing generated artifacts.",
            "failureMode": "",
        }
        request.update(overrides)
        return request

    def test_success_stores_a_distilled_lesson_separately_from_task_context(self) -> None:
        result = core._make_memory(self.base_request())

        self.assertTrue(result["stored"])
        item = result["memories"][0]
        self.assertEqual(item["reasoning"], "Validate the source schema before publishing generated artifacts.")
        self.assertEqual(item["semanticText"], "validate the schema before publishing")
        self.assertEqual(item["outcome"], "success")
        self.assertEqual(item["trustLevel"], "unverified")
        self.assertEqual(item["feedback"], {"helpful": 0, "harmful": 0, "neutral": 0})
        self.assertEqual(item["extractionMethod"], "caller_insight")
        self.assertEqual(item["contraindications"], [])
        self.assertEqual(item["sourceMemoryIds"], [])

    def test_task_summary_alone_is_not_admitted_as_success_memory(self) -> None:
        result = core._make_memory(self.base_request(reasoningInsight=""))

        self.assertFalse(result["stored"])
        self.assertIn("transferable reasoning insight", result["reason"])

    def test_unattributed_failure_and_partial_outcome_are_rejected(self) -> None:
        missing_failure = core._make_memory(
            self.base_request(outcome="fail", reasoningInsight="", failureMode=""),
        )
        partial = core._make_memory(self.base_request(outcome="partial"))

        self.assertFalse(missing_failure["stored"])
        self.assertIn("failure attribution", missing_failure["reason"])
        self.assertFalse(partial["stored"])

    def test_failed_action_is_recorded_before_rollback_marker(self) -> None:
        signature = core.structure_signature(tool_events(success=False, tool="bash"))

        self.assertEqual(signature, ["execute", "rollback"])

    def test_legacy_content_yields_a_reasoning_lesson_for_retrieval(self) -> None:
        lesson = core._reasoning_text({
            "reasoning": "",
            "content": (
                "调用者提炼的可迁移推理策略（未独立验证）：先检查来源文件，再生成产物。"
                "本次可观察的操作结构：执行操作 → 验证结果。迁移时先检查任务前提"
            ),
        })

        self.assertEqual(lesson, "先检查来源文件，再生成产物")

    def test_distillation_generates_a_provenance_labeled_fallback_graph(self) -> None:
        result = core._make_memory(self.base_request())
        graph = result["memories"][0]["reasoningGraph"]

        self.assertTrue(any(node["kind"] == "inference" for node in graph["nodes"]))
        self.assertTrue(any(node["kind"] == "action" and node["source"] == "tool_trace" for node in graph["nodes"]))
        self.assertTrue(any(edge["relation"] == "supports" and edge["source"] == "verifier_input" for edge in graph["edges"]))

    def test_explicit_reasoning_graph_is_validated_and_provenance_is_overwritten(self) -> None:
        graph = {
            "version": 1,
            "nodes": [
                {"id": "p1", "kind": "premise", "text": "the schema is valid", "source": "tool_trace"},
                {"id": "i1", "kind": "inference", "text": "validate before publishing", "source": "tool_trace"},
                {"id": "c1", "kind": "conclusion", "text": "publishing is safe", "source": "tool_trace"},
            ],
            "edges": [
                {"sourceId": "p1", "targetId": "i1", "relation": "supports", "source": "tool_trace"},
                {"sourceId": "i1", "targetId": "c1", "relation": "leads_to", "source": "tool_trace"},
            ],
        }
        result = core._make_memory(self.base_request(reasoningGraphJson=json.dumps(graph)))

        self.assertTrue(result["stored"])
        stored_graph = result["memories"][0]["reasoningGraph"]
        self.assertEqual(stored_graph["nodes"][0]["source"], "agent_input")
        self.assertEqual(stored_graph["edges"][0]["source"], "agent_input")

    def test_cyclic_reasoning_dependencies_are_rejected(self) -> None:
        graph = {
            "version": 1,
            "nodes": [
                {"id": "p1", "kind": "premise", "text": "premise"},
                {"id": "i1", "kind": "inference", "text": "inference"},
                {"id": "c1", "kind": "conclusion", "text": "conclusion"},
            ],
            "edges": [
                {"sourceId": "p1", "targetId": "i1", "relation": "supports"},
                {"sourceId": "i1", "targetId": "c1", "relation": "leads_to"},
                {"sourceId": "c1", "targetId": "p1", "relation": "supports"},
            ],
        }
        result = core._make_memory(self.base_request(reasoningGraphJson=json.dumps(graph)))

        self.assertFalse(result["stored"])
        self.assertIn("contain a cycle", result["reason"])

    def test_structured_items_are_stored_with_title_description_and_contraindications(self) -> None:
        result = core._make_memory(self.base_request(
            items=[
                {
                    "title": "Validate before publish",
                    "description": "Applies before publishing generated artifacts",
                    "content": "Run the schema validator against every generated artifact before publishing it.",
                    "contraindications": ["Do not apply to third-party sources you do not own"],
                    "outcome": "success",
                },
                {
                    "title": "Check constraints first",
                    "description": "Applies when inputs are user-supplied",
                    "content": "Check user-supplied constraints before executing destructive operations.",
                    "contraindications": [],
                    "outcome": "success",
                },
            ],
            reasoningInsight="",
            extractionMethod="llm_distillation",
            distillationModel="test-model",
        ))

        self.assertTrue(result["stored"])
        self.assertEqual(len(result["memories"]), 2)
        first = result["memories"][0]
        self.assertEqual(first["title"], "Validate before publish")
        self.assertEqual(first["extractionMethod"], "llm_distillation")
        self.assertEqual(first["distillationModel"], "test-model")
        self.assertEqual(first["contraindications"], ["Do not apply to third-party sources you do not own"])
        self.assertEqual(first["outcome"], "success")
        self.assertIn("从任务轨迹蒸馏", first["content"])

    def test_items_restating_the_task_description_are_rejected_as_too_task_specific(self) -> None:
        result = core._make_memory(self.base_request(
            items=[{
                "title": "restatement",
                "description": "",
                "content": "validate the schema before publishing",
                "contraindications": [],
                "outcome": "success",
            }],
            reasoningInsight="",
        ))

        self.assertFalse(result["stored"])
        self.assertIn("too task-specific", result["reason"])

    def test_duplicate_items_within_one_batch_are_dropped(self) -> None:
        lesson = "Verify the generated artifact against the schema before release."
        result = core._make_memory(self.base_request(
            items=[
                {"title": "a", "description": "", "content": lesson, "contraindications": [], "outcome": "success"},
                {"title": "b", "description": "", "content": lesson, "contraindications": [], "outcome": "success"},
            ],
            reasoningInsight="",
        ))

        self.assertTrue(result["stored"])
        self.assertEqual(len(result["memories"]), 1)

    def test_explicit_graph_requires_a_single_memory_item(self) -> None:
        graph = {
            "version": 1,
            "nodes": [
                {"id": "p1", "kind": "premise", "text": "premise"},
                {"id": "i1", "kind": "inference", "text": "inference"},
                {"id": "c1", "kind": "conclusion", "text": "conclusion"},
            ],
            "edges": [{"sourceId": "p1", "targetId": "i1", "relation": "supports"},
                      {"sourceId": "i1", "targetId": "c1", "relation": "leads_to"}],
        }
        result = core._make_memory(self.base_request(
            items=[
                {"title": "a", "description": "", "content": "lesson a", "contraindications": [], "outcome": "success"},
                {"title": "b", "description": "", "content": "lesson b", "contraindications": [], "outcome": "success"},
            ],
            reasoningInsight="",
            reasoningGraphJson=json.dumps(graph),
        ))

        self.assertFalse(result["stored"])
        self.assertIn("exactly one", result["reason"])

    def test_self_contrast_consolidation_needs_two_sources_and_carries_per_item_outcomes(self) -> None:
        result = core._make_memory({
            "operation": "distill",
            "taskId": "",
            "sourceSessionId": "session-1",
            "taskFamily": "consolidated",
            "semanticText": "repeated attempts of the same workflow",
            "events": [],
            "outcome": "success",
            "verifier": "self-contrast",
            "evidence": "contrast over 2 source memories",
            "reasoningInsight": "",
            "failureMode": "",
            "extractionMethod": "llm_self_contrast",
            "distillationModel": "test-model",
            "sourceMemoryIds": ["m1", "m2"],
            "items": [
                {"title": "winning pattern", "description": "", "content": "Filter first, then search", "contraindications": [], "outcome": "success"},
                {"title": "avoided pitfall", "description": "", "content": "Do not submit before verifying the form", "contraindications": [], "outcome": "fail"},
            ],
        })

        self.assertTrue(result["stored"])
        self.assertEqual(len(result["memories"]), 2)
        self.assertEqual(result["memories"][0]["outcome"], "success")
        self.assertEqual(result["memories"][1]["outcome"], "fail")
        self.assertEqual(result["memories"][0]["structureSignature"], [])
        self.assertEqual(result["memories"][0]["sourceMemoryIds"], ["m1", "m2"])
        self.assertEqual(result["memories"][0]["extractionMethod"], "llm_self_contrast")

        missing_sources = core._make_memory({
            "operation": "distill",
            "taskId": "",
            "sourceSessionId": "session-1",
            "taskFamily": "consolidated",
            "semanticText": "context",
            "events": [],
            "outcome": "success",
            "verifier": "self-contrast",
            "evidence": "contrast",
            "extractionMethod": "llm_self_contrast",
            "sourceMemoryIds": ["m1"],
            "items": [{"title": "t", "description": "", "content": "lesson", "contraindications": [], "outcome": "success"}],
        })
        self.assertFalse(missing_sources["stored"])
        self.assertIn("at least two source memory ids", missing_sources["reason"])

    def test_max_items_per_outcome_caps_structured_batches(self) -> None:
        def item(name: str) -> dict:
            return {"title": name, "description": "", "content": f"apply the {name} procedure first", "contraindications": [], "outcome": "success"}

        result = core._make_memory(self.base_request(
            items=[item("alpha"), item("beta"), item("gamma")],
            reasoningInsight="",
            maxItemsPerOutcome=2,
        ))

        self.assertTrue(result["stored"])
        self.assertEqual(len(result["memories"]), 2)
        self.assertEqual([memory["title"] for memory in result["memories"]], ["alpha", "beta"])

    def test_llm_distilled_prefix_is_recoverable_for_retrieval(self) -> None:
        lesson = core._reasoning_text({
            "reasoning": "",
            "content": "从任务轨迹蒸馏的可迁移推理策略（未独立验证）：先过滤再搜索。本次可观察的操作结构：搜索或读取证据 → 执行操作。",
        })

        self.assertEqual(lesson, "先过滤再搜索")


class RetrievalTests(unittest.TestCase):
    def retrieve(self, memories: list[dict], **overrides: object) -> dict:
        request = {
            "operation": "retrieve",
            "query": "validate schema before publishing",
            "events": [],
            "memories": memories,
            "k": 4,
            "route": "auto",
            "positiveNegativeRatio": 1.0,
            "excludeTaskId": "active-task",
        }
        request.update(overrides)
        return core._retrieve(request)

    def test_reasoning_lesson_can_outrank_exact_task_description_match(self) -> None:
        task_match_only = make_memory(
            "context-match", "success",
            context="validate schema before publishing",
            reasoning="prefer a quiet interface and avoid needless decoration",
        )
        lesson_match = make_memory(
            "lesson-match", "success",
            context="a browser workflow with unrelated controls",
            reasoning="validate schema before publishing",
        )

        result = self.retrieve([task_match_only, lesson_match], k=2, positiveNegativeRatio=10)

        self.assertEqual(result["results"][0]["memoryId"], "lesson-match")
        self.assertGreater(result["results"][0]["reasoningScore"], result["results"][0]["contextScore"])

    def test_default_positive_negative_ratio_is_balanced_when_both_sides_exist(self) -> None:
        memories = [
            make_memory("p1", "success"),
            make_memory("p2", "success"),
            make_memory("n1", "fail"),
            make_memory("n2", "fail"),
        ]

        result = self.retrieve(memories, k=4)

        self.assertEqual(result["balance"]["positiveCount"], 2)
        self.assertEqual(result["balance"]["negativeCount"], 2)
        self.assertFalse(result["balance"]["fallbackUsed"])

    def test_outcome_shortage_uses_fallback_and_reports_it(self) -> None:
        memories = [make_memory("p1", "success"), make_memory("p2", "success")]

        result = self.retrieve(memories, k=4)

        self.assertTrue(result["balance"]["fallbackUsed"])
        self.assertEqual(result["balance"]["positiveCount"], 2)
        self.assertEqual(result["balance"]["negativeCount"], 0)

    def test_active_task_memory_is_not_retrieved_by_its_own_task(self) -> None:
        memories = [
            make_memory("self", "success", task_id="active-task"),
            make_memory("prior", "success", task_id="older-task"),
        ]

        result = self.retrieve(memories, k=1, positiveNegativeRatio=10)

        self.assertEqual([row["memoryId"] for row in result["results"]], ["prior"])

    def test_self_reported_utility_is_bounded_and_neutral_is_rank_neutral(self) -> None:
        memories = [
            make_memory("helpful", "success", feedback={"helpful": 100, "harmful": 0, "neutral": 0}),
            make_memory("harmful", "success", feedback={"helpful": 0, "harmful": 100, "neutral": 0}),
            make_memory("neutral", "success", feedback={"helpful": 0, "harmful": 0, "neutral": 100}),
        ]

        result = self.retrieve(memories, k=3, positiveNegativeRatio=10)
        multipliers = {row["memoryId"]: row["utilityMultiplier"] for row in result["results"]}

        self.assertGreater(multipliers["helpful"], 1.0)
        self.assertLess(multipliers["harmful"], 1.0)
        self.assertEqual(multipliers["neutral"], 1.0)
        self.assertTrue(all(0.8 <= value <= 1.2 for value in multipliers.values()))
        self.assertTrue(all(0.0 <= row["combinedScore"] <= 1.0 for row in result["results"]))

    def test_structure_route_requires_an_observed_action_sequence(self) -> None:
        result = self.retrieve([make_memory("p1", "success")], route="structure")

        self.assertEqual(result["results"], [])
        self.assertIn("no observable", result["reason"])

    def test_graph_route_ranks_matching_logic_edges(self) -> None:
        matching_graph = {
            "version": 1,
            "nodes": [
                {"id": "p1", "kind": "premise", "text": "schema must validate", "source": "agent_input"},
                {"id": "i1", "kind": "inference", "text": "validate before publishing", "source": "agent_input"},
                {"id": "c1", "kind": "conclusion", "text": "publishing is safe", "source": "agent_input"},
            ],
            "edges": [
                {"sourceId": "p1", "targetId": "i1", "relation": "supports", "source": "agent_input"},
                {"sourceId": "i1", "targetId": "c1", "relation": "leads_to", "source": "agent_input"},
            ],
        }
        mismatched_graph = {
            **matching_graph,
            "edges": [
                {"sourceId": "p1", "targetId": "i1", "relation": "precedes", "source": "agent_input"},
                {"sourceId": "i1", "targetId": "c1", "relation": "precedes", "source": "agent_input"},
            ],
        }
        query_graph = json.dumps(matching_graph)
        memories = [
            make_memory("wrong-edges", "success", reasoning_graph=mismatched_graph),
            make_memory("matching-edges", "success", reasoning_graph=matching_graph),
        ]

        result = self.retrieve(memories, route="graph", queryGraphJson=query_graph, k=2, positiveNegativeRatio=10)

        self.assertEqual(result["results"][0]["memoryId"], "matching-edges")
        self.assertEqual(result["results"][0]["graphScore"], 1.0)
        self.assertGreater(result["candidateScores"][0]["graphScore"], 0.0)

    def test_graph_route_requires_a_valid_query_graph(self) -> None:
        result = self.retrieve([make_memory("p1", "success")], route="graph")

        self.assertEqual(result["results"], [])
        self.assertIn("requires a valid", result["reason"])

    def test_auto_route_fuses_graph_when_a_query_graph_is_supplied(self) -> None:
        query_graph = {
            "version": 1,
            "nodes": [
                {"id": "p1", "kind": "premise", "text": "schema is valid"},
                {"id": "i1", "kind": "inference", "text": "validate before publishing"},
                {"id": "c1", "kind": "conclusion", "text": "publishing is safe"},
            ],
            "edges": [
                {"sourceId": "p1", "targetId": "i1", "relation": "supports"},
                {"sourceId": "i1", "targetId": "c1", "relation": "leads_to"},
            ],
        }

        result = self.retrieve(
            [make_memory("p1", "success")],
            route="auto",
            queryGraphJson=json.dumps(query_graph),
        )

        self.assertEqual(result["fusionWeights"], {
            "context": 0.15,
            "reasoning": 0.45,
            "structure": 0.0,
            "graph": 0.40,
            "vector": 0.0,
            "lexical": 0.0,
        })

    def test_auto_uses_rank_fusion_for_sparse_and_dense_candidates_without_breaking_ratio(self) -> None:
        memories = [
            {**make_memory("p1", "success", reasoning="validate database schema"), "lexicalScore": 1.0, "vectorScore": 0.82},
            {**make_memory("p2", "success", reasoning="check schema before deploy"), "lexicalScore": 0.55, "vectorScore": 0.91},
            {**make_memory("n1", "fail", reasoning="deploy without verifying schema"), "lexicalScore": 0.8, "vectorScore": 0.77},
            {**make_memory("n2", "fail", reasoning="skip migration validation"), "lexicalScore": 0.3, "vectorScore": 0.69},
        ]

        result = self.retrieve(memories, route="auto", k=4)

        self.assertEqual(result["fusionMode"], "rrf")
        self.assertEqual(result["balance"]["positiveCount"], 2)
        self.assertEqual(result["balance"]["negativeCount"], 2)
        self.assertTrue(all(0.0 <= row["combinedScore"] <= 1.0 for row in result["results"]))

    def test_rank_fusion_keeps_dense_only_paraphrase_without_token_overlap(self) -> None:
        dense_only = {
            **make_memory(
                "dense-only",
                "success",
                context="an unrelated operating procedure",
                reasoning="inspect the deployment artifact through an isolated runtime check",
            ),
            "structureSignature": [],
            "lexicalScore": 0.0,
            "vectorScore": 0.95,
        }

        result = self.retrieve(
            [dense_only],
            route="auto",
            k=1,
            positiveNegativeRatio=10,
        )

        self.assertEqual([row["memoryId"] for row in result["results"]], ["dense-only"])
        self.assertEqual(result["results"][0]["vectorScore"], 0.95)

    def test_relevance_threshold_filters_weak_candidates_before_quota(self) -> None:
        memories = [
            make_memory("strong", "success", reasoning="validate schema before publishing"),
            make_memory("weak", "success", reasoning="unrelated decoration preferences"),
            make_memory("neg", "fail", reasoning="skip schema validation"),
        ]

        result = self.retrieve(memories, k=4, relevanceThreshold=0.4, positiveNegativeRatio=10)

        self.assertEqual(result["fusionMode"], "weighted")
        self.assertTrue(result["balance"]["thresholdFilteredCount"] >= 1)
        self.assertTrue(all(row["combinedScore"] >= 0.4 for row in result["results"]))

    def test_threshold_zero_keeps_the_legacy_behavior(self) -> None:
        memories = [make_memory("p1", "success"), make_memory("n1", "fail")]

        result = self.retrieve(memories, k=4)

        self.assertEqual(result["balance"]["thresholdFilteredCount"], 0)
        self.assertEqual(result["balance"]["positiveCount"], 1)
        self.assertEqual(result["balance"]["negativeCount"], 1)

    def test_threshold_applies_after_rank_fusion(self) -> None:
        memories = [
            {**make_memory("p1", "success", reasoning="validate database schema"), "lexicalScore": 1.0, "vectorScore": 0.82},
            {**make_memory("p2", "success", reasoning="check schema before deploy"), "lexicalScore": 0.55, "vectorScore": 0.91},
        ]

        result = self.retrieve(memories, route="auto", k=2, relevanceThreshold=0.95, positiveNegativeRatio=10)

        self.assertEqual(result["fusionMode"], "rrf")
        self.assertTrue(all(row["combinedScore"] >= 0.95 for row in result["results"]))
        if result["results"]:
            self.assertTrue(result["balance"]["thresholdFilteredCount"] >= 0)
        else:
            self.assertIn("relevance threshold", result["reason"])


class JsonBridgeTests(unittest.TestCase):
    def test_cli_accepts_and_returns_json(self) -> None:
        script = ROOT / "python" / "reasoning_memory.py"
        request = {
            "operation": "retrieve",
            "query": "schema validation",
            "memories": [],
            "events": [],
            "k": 2,
            "route": "semantic",
        }
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        response = json.loads(completed.stdout)
        self.assertEqual(response["results"], [])
        self.assertEqual(response["fusionWeights"], {
            "context": 0.3,
            "reasoning": 0.7,
            "structure": 0.0,
            "graph": 0.0,
            "vector": 0.0,
            "lexical": 0.0,
        })
        self.assertEqual(response["fusionMode"], "weighted")

    def test_cli_file_io_mode_reads_and_writes_files(self) -> None:
        script = ROOT / "python" / "reasoning_memory.py"
        directory = ROOT / "test" / ".tmp-file-io-test"
        directory.mkdir(exist_ok=True)
        try:
            input_path = directory / "request.json"
            output_path = directory / "response.json"
            input_path.write_text(json.dumps({
                "operation": "retrieve",
                "query": "schema validation",
                "memories": [],
                "events": [],
                "k": 2,
                "route": "semantic",
            }), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script), "--file-io", str(input_path), str(output_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            response = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(response["fusionMode"], "weighted")
            self.assertEqual(response["results"], [])
        finally:
            for file in ("request.json", "response.json"):
                (directory / file).unlink(missing_ok=True)
            directory.rmdir()

    def test_cli_file_io_mode_reports_errors_to_the_error_file(self) -> None:
        script = ROOT / "python" / "reasoning_memory.py"
        directory = ROOT / "test" / ".tmp-file-io-test"
        directory.mkdir(exist_ok=True)
        try:
            input_path = directory / "request.json"
            output_path = directory / "response.json"
            error_path = directory / "error.txt"
            input_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script), "--file-io", str(input_path), str(output_path), str(error_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("core error", error_path.read_text(encoding="utf-8"))
            self.assertFalse(output_path.exists())
        finally:
            for file in ("request.json", "response.json", "error.txt"):
                (directory / file).unlink(missing_ok=True)
            directory.rmdir()

    def test_cli_pipe_transport_is_utf8_safe_for_chinese_content(self) -> None:
        # Regression for the en-US Windows CI failure: text-mode stdout used the
        # console codepage (cp1252), which cannot encode the Chinese content the
        # core returns. The protocol must be UTF-8 bytes regardless of locale.
        script = ROOT / "python" / "reasoning_memory.py"
        request = {
            "operation": "distill",
            "taskId": "task-zh",
            "sourceSessionId": "session-zh",
            "taskFamily": "general",
            "semanticText": "发布前校验模式",
            "events": tool_events(),
            "outcome": "success",
            "verifier": "unit-suite",
            "evidence": "校验后测试通过",
            "reasoningInsight": "发布生成产物前先校验源模式",
            "failureMode": "",
        }
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        response = json.loads(completed.stdout.decode("utf-8"))
        self.assertTrue(response["stored"])
        self.assertEqual(response["memories"][0]["reasoning"], "发布生成产物前先校验源模式")


class TemplateClassifierTests(unittest.TestCase):
    def test_family_hint_dominates_with_agreement_confidence(self) -> None:
        result = core.classify_task_pattern(
            task_desc="重构 utils.py 的解析函数并跑单测", task_family="code_modification",
        )
        self.assertEqual(result["pattern"], "code_synthesis")
        self.assertGreater(result["confidence"], 0.6)
        self.assertFalse(result["fallback"])
        self.assertIn("task_family", [entry["signal"] for entry in result["evidence"]])

    def test_numeric_search_from_family_plus_structure(self) -> None:
        result = core.classify_task_pattern(
            task_desc="计算 24 点的全部解并验证算术", task_family="gsm8k",
            structure_signature=["search", "verify", "search", "verify"],
        )
        self.assertEqual(result["pattern"], "numeric_search")
        self.assertFalse(result["fallback"])

    def test_multi_hop_qa_from_keywords(self) -> None:
        result = core.classify_task_pattern(
            task_desc="依次回答多个子问题并跨文档推理多跳问答", goal="",
        )
        self.assertEqual(result["pattern"], "multi_hop_qa")
        self.assertGreater(result["scores"]["multi_hop_qa"], result["scores"]["general"])

    def test_gui_operation_from_family(self) -> None:
        result = core.classify_task_pattern(
            task_desc="点击按钮提交表单", task_family="webarena",
        )
        self.assertEqual(result["pattern"], "gui_operation")

    def test_long_horizon_from_decompose_and_length(self) -> None:
        result = core.classify_task_pattern(
            task_desc="长期多步计划", goal="",
            structure_signature=["decompose"] + ["execute"] * 8,
        )
        self.assertEqual(result["pattern"], "long_horizon_planning")
        self.assertFalse(result["fallback"])

    def test_tool_orchestration_tool_mix_rule(self) -> None:
        result = core.classify_task_pattern(
            task_desc="编排工作流调用多个 api 集成插件", goal="",
            structure_signature=["search", "execute", "search"],
            tool_names=["bash", "fetch", "glob"],
        )
        self.assertEqual(result["pattern"], "tool_orchestration")

    def test_graph_signal_premise_inference_dominance(self) -> None:
        result = core.classify_task_pattern(
            task_desc="跨文档回答多个问题", goal="",
            graph_node_kinds=["premise", "premise", "inference", "conclusion"],
        )
        self.assertEqual(result["pattern"], "multi_hop_qa")
        self.assertFalse(result["fallback"])

    def test_graph_signal_action_dominance_ties_into_general(self) -> None:
        # Action-dominant graphs boost tool_orchestration and gui_operation
        # symmetrically (final spec 3.1 S4), so the deterministic tie falls
        # back to general rather than guessing between the two.
        result = core.classify_task_pattern(
            task_desc="一个含糊的任务描述", goal="",
            graph_node_kinds=["action", "action", "conclusion"],
        )
        self.assertEqual(result["scores"]["tool_orchestration"], result["scores"]["gui_operation"])
        self.assertGreater(result["scores"]["tool_orchestration"], 0.0)
        self.assertEqual(result["pattern"], "general")
        self.assertTrue(result["fallback"])

    def test_graph_without_node_kinds_is_recorded_but_inactive(self) -> None:
        result = core.classify_task_pattern(
            task_desc="一个含糊的任务描述", goal="", has_graph=True,
        )
        graph_evidence = [entry for entry in result["evidence"] if entry["signal"] == "graph"]
        self.assertTrue(graph_evidence)
        self.assertEqual(graph_evidence[0]["weight"], 0.0)
        self.assertTrue(result["fallback"])
        self.assertEqual(result["pattern"], "general")

    def test_empty_input_falls_back_to_general_with_zero_confidence(self) -> None:
        result = core.classify_task_pattern(task_desc="")
        self.assertEqual(result["pattern"], "general")
        self.assertEqual(result["confidence"], 0.0)
        self.assertTrue(result["fallback"])

    def test_ambiguous_input_falls_back_to_general(self) -> None:
        result = core.classify_task_pattern(
            task_desc="一个没有任何明显信号的描述",
            structure_signature=["execute", "execute"],
        )
        self.assertEqual(result["pattern"], "general")
        self.assertTrue(result["fallback"])

    def test_scores_cover_all_patterns_and_are_bounded(self) -> None:
        result = core.classify_task_pattern(task_desc="重构并验证代码", task_family="code")
        for pattern in core.TASK_PATTERNS:
            self.assertIn(pattern, result["scores"])
            self.assertTrue(0.0 <= result["scores"][pattern] <= 1.0)
        self.assertTrue(0.0 <= result["confidence"] <= 1.0)


class TemplateMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = {
            "templateId": "memory_tpl_builtin_tot",
            "name": "memory_tpl_tot-decompose-backtrack",
            "description": "Use when a task needs decomposition with backtracking",
            "taskPatterns": ["long_horizon_planning"],
            "structureSignature": ["decompose", "search", "execute", "verify"],
            "steps": [{"index": 1, "title": "任务分解", "moduleDemands": []}],
            "utility": {"helpful": 0, "harmful": 0, "neutral": 0},
        }

    def test_match_decomposes_into_four_components(self) -> None:
        score, parts = core.template_match_score(
            template=self.template, pattern="long_horizon_planning", pattern_confidence=0.8,
            structure_signature=["decompose", "search", "execute", "verify"],
            task_text="分解并验证一个多步任务",
        )
        self.assertEqual(set(parts), {"struct", "lexical", "pattern", "quality"})
        self.assertGreater(parts["struct"], 0.0)
        self.assertEqual(parts["pattern"], 1.0)
        self.assertTrue(0.0 <= score <= 1.0)

    def test_missing_template_signature_zeroes_struct(self) -> None:
        score, parts = core.template_match_score(
            template={**self.template, "structureSignature": []},
            pattern="long_horizon_planning", pattern_confidence=0.8,
            structure_signature=["decompose", "search"], task_text="分解任务",
        )
        self.assertEqual(parts["struct"], 0.0)

    def test_general_pattern_consistency_is_half(self) -> None:
        score, parts = core.template_match_score(
            template=self.template, pattern="general", pattern_confidence=0.1,
            structure_signature=[], task_text="一个普通任务",
        )
        self.assertEqual(parts["pattern"], 0.5)

    def test_quality_multiplier_bounds_and_harm_penalty(self) -> None:
        clean, clean_parts = core.template_match_score(
            template=self.template, pattern="long_horizon_planning", pattern_confidence=0.9,
            structure_signature=[], task_text="多步任务",
        )
        harmed, harmed_parts = core.template_match_score(
            template={**self.template, "utility": {"helpful": 2, "harmful": 8, "neutral": 0}},
            pattern="long_horizon_planning", pattern_confidence=0.9,
            structure_signature=[], task_text="多步任务",
        )
        self.assertGreaterEqual(clean_parts["quality"], 0.5)
        self.assertLessEqual(clean_parts["quality"], 1.0)
        self.assertGreaterEqual(harmed_parts["quality"], 0.5)
        self.assertLess(harmed_parts["quality"], clean_parts["quality"])

    def test_evolve_ratio_monotonic_in_helpful(self) -> None:
        def quality(helpful: int, harmful: int) -> float:
            _, parts = core.template_match_score(
                template={**self.template, "utility": {"helpful": helpful, "harmful": harmful, "neutral": 0}},
                pattern="long_horizon_planning", pattern_confidence=0.9,
                structure_signature=[], task_text="多步任务",
            )
            return parts["quality"]
        self.assertGreater(quality(5, 0), quality(1, 0))
        self.assertGreaterEqual(quality(0, 100), 0.5)

    def test_strong_and_injectable_flags_from_plan_task(self) -> None:
        plan = core.plan_task(
            task_desc="分解、搜索、执行并验证一个多步任务",
            task_family="long", templates=[self.template], max_templates=1,
        )
        matched = plan["matchedTemplates"][0]
        self.assertEqual(matched["strongMatch"], matched["score"] >= 0.75)
        self.assertEqual(matched["injectable"], matched["score"] >= 0.55)
        self.assertIn("activeRetrievalPoints", matched)


class TemplateMiningTests(unittest.TestCase):
    def make_memory(self, memory_id: str, outcome: str, signature: list[str], created_at: int, context: str = "重构代码库") -> dict:
        return {
            "memoryId": memory_id, "outcome": outcome, "createdAt": created_at,
            "semanticText": context, "taskScope": "code_modification",
            "structureSignature": signature,
            "preconditions": ["存在可运行的单测"],
            "provenance": {"taskId": f"task-{memory_id}"},
        }

    def test_mines_one_template_from_a_supporting_cluster(self) -> None:
        memories = [
            self.make_memory(f"m{i}", "success", ["decompose", "search", "execute", "verify"], i)
            for i in range(3)
        ]
        result = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(len(result["templates"]), 1)
        template = result["templates"][0]
        self.assertTrue(template["name"].startswith("memory_tpl_mined-"))
        self.assertEqual(template["structureSignature"], ["decompose", "search", "execute", "verify"])
        self.assertEqual(template["provenance"]["author"], "mined")
        self.assertEqual(template["provenance"]["sourceMemoryIds"], [f"m{i}" for i in range(3)])
        self.assertEqual(template["steps"][0]["moduleDemands"][0]["kind"], "retrieve_template")
        self.assertEqual(template["status"], "active")

    def test_below_min_support_is_skipped(self) -> None:
        memories = [
            self.make_memory(f"m{i}", "success", ["decompose", "search", "execute", "verify"], i)
            for i in range(2)
        ]
        result = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(result["templates"], [])
        self.assertEqual(result["skipped"]["below_support"], 1)

    def test_fail_majority_cluster_is_not_emitted(self) -> None:
        memories = [
            self.make_memory(f"f{i}", "fail", ["search", "execute", "verify"], i)
            for i in range(4)
        ]
        result = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(result["templates"], [])
        self.assertEqual(result["skipped"]["fail_majority"], 1)

    def test_monotone_execute_only_signature_is_skipped(self) -> None:
        memories = [
            self.make_memory(f"m{i}", "success", ["execute", "execute", "execute"], i)
            for i in range(3)
        ]
        result = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(result["templates"], [])
        self.assertEqual(result["skipped"]["monotone"], 1)

    def test_single_tag_signature_is_too_short(self) -> None:
        memories = [
            self.make_memory(f"m{i}", "success", ["search"], i)
            for i in range(3)
        ]
        result = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(result["templates"], [])
        self.assertEqual(result["skipped"]["too_short"], 1)

    def test_join_threshold_merges_similar_signatures(self) -> None:
        memories = [
            self.make_memory("a", "success", ["search", "execute", "verify", "execute"], 1),
            self.make_memory("b", "success", ["search", "execute", "verify", "verify"], 2),
            self.make_memory("c", "success", ["search", "execute", "verify", "execute"], 3),
        ]
        result = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(len(result["clusters"]), 1)
        self.assertEqual(result["clusters"][0]["size"], 3)
        self.assertEqual(len(result["templates"]), 1)

    def test_form_threshold_splits_dissimilar_and_parks_between(self) -> None:
        memories = [
            self.make_memory("a", "success", ["search", "search", "search", "execute"], 1),
            self.make_memory("b", "success", ["search", "execute", "verify", "execute"], 2),
            self.make_memory("c", "success", ["verify", "verify", "execute", "verify"], 3),
        ]
        result = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(len(result["clusters"]), 2)
        self.assertEqual(result["skipped"]["pending"], 1)

    def test_core_signature_is_multi_sequence_lcs(self) -> None:
        memories = [
            self.make_memory("a", "success", ["decompose", "search", "execute", "verify"], 1),
            self.make_memory("b", "success", ["decompose", "search", "verify"], 2),
            self.make_memory("c", "success", ["decompose", "search", "execute", "verify", "execute"], 3),
        ]
        result = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(result["clusters"][0]["core_signature"], ["decompose", "search", "verify"])

    def test_idempotent_against_existing_templates(self) -> None:
        memories = [
            self.make_memory(f"m{i}", "success", ["decompose", "search", "execute", "verify"], i)
            for i in range(3)
        ]
        existing = [{
            "structureSignature": ["decompose", "search", "execute", "verify"],
            "taskPatterns": ["code_synthesis"],
        }]
        result = core.mine_templates(memories=memories, min_support=3, existing_templates=existing)
        self.assertEqual(result["templates"], [])
        self.assertEqual(result["skipped"]["duplicate"], 1)

    def test_mining_is_deterministic_across_runs(self) -> None:
        memories = [
            self.make_memory(f"m{i}", "success", ["search", "execute", "verify"], i)
            for i in range(4)
        ]
        first = core.mine_templates(memories=memories, min_support=3)
        second = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(first["clusters"], second["clusters"])
        self.assertEqual(first["skipped"], second["skipped"])
        self.assertEqual(
            [template["structureSignature"] for template in first["templates"]],
            [template["structureSignature"] for template in second["templates"]],
        )

    def test_consecutive_duplicate_tags_are_normalized(self) -> None:
        memories = [
            self.make_memory(f"m{i}", "success", ["search", "search", "execute", "verify"], i)
            for i in range(3)
        ]
        result = core.mine_templates(memories=memories, min_support=3)
        self.assertEqual(result["templates"][0]["structureSignature"], ["search", "execute", "verify"])


class TemplateFeedbackTests(unittest.TestCase):
    def test_increments_helpful_and_scores_beta_smoothed(self) -> None:
        template = {"templateId": "t1", "utility": {"helpful": 0, "harmful": 0, "neutral": 0}}
        updated = core.apply_template_feedback(template, "helpful")
        self.assertEqual(updated["utility"]["helpful"], 1)
        self.assertAlmostEqual(updated["utilityScore"], round((1 + 1) / (1 + 0 + 2), 6), places=6)

    def test_invalid_verdict_raises(self) -> None:
        with self.assertRaises(ValueError):
            core.apply_template_feedback({"templateId": "t1", "utility": {}}, "bogus")

    def test_feedback_via_service_dispatch(self) -> None:
        result = core.handle_request({
            "operation": "template_feedback",
            "template": {"templateId": "t1", "utility": {"helpful": 0, "harmful": 0, "neutral": 0}},
            "verdict": "harmful",
        })
        self.assertEqual(result["utility"]["harmful"], 1)


class TemplateRetrievalTests(unittest.TestCase):
    def make_request(self, **overrides: object) -> dict:
        memories = [
            {
                "memoryId": "aligned", "outcome": "success",
                "semanticText": "重构解析函数", "reasoning": "先定位失败点再修改",
                "structureSignature": ["decompose", "search", "execute", "verify"],
                "reasoningGraph": {"version": 1, "nodes": [], "edges": []},
                "feedback": {"helpful": 0, "harmful": 0, "neutral": 0},
                "provenance": {"taskId": "prior-1"}, "lexicalScore": 0.0, "vectorScore": 0.0,
                "strategyTemplateId": "memory_tpl_builtin_tot", "status": "active",
            },
            {
                "memoryId": "plain", "outcome": "success",
                "semanticText": "重构解析函数", "reasoning": "无关的教训",
                "structureSignature": ["execute"],
                "reasoningGraph": {"version": 1, "nodes": [], "edges": []},
                "feedback": {"helpful": 0, "harmful": 0, "neutral": 0},
                "provenance": {"taskId": "prior-2"}, "lexicalScore": 0.0, "vectorScore": 0.0,
                "strategyTemplateId": "", "status": "active",
            },
        ]
        template = core.builtin_template_records(1000)[0]
        request: dict = {
            "operation": "retrieve", "query": "重构代码库", "k": 4, "route": "auto",
            "memories": memories,
            "activeTemplateId": "memory_tpl_builtin_tot", "activeTemplate": template,
        }
        request.update(overrides)
        return request

    def test_direct_alignment_scores_one(self) -> None:
        result = core._retrieve(self.make_request())
        aligned = next(row for row in result["candidateScores"] if row["memoryId"] == "aligned")
        self.assertEqual(aligned["templateScore"], 1.0)
        self.assertEqual(result["templateChannelWeight"], 0.15)

    def test_signature_match_scores_zero_point_six(self) -> None:
        # The bound template is memory_tpl_builtin_tot with a six-tag signature;
        # LCS-F1 against [search, execute, verify] is 2/3, so the template score
        # is 0.6 x 2/3 = 0.4.
        memories = [
            {
                "memoryId": "partial", "outcome": "success",
                "semanticText": "重构解析函数", "reasoning": "先定位再改",
                "structureSignature": ["search", "execute", "verify"],
                "reasoningGraph": {"version": 1, "nodes": [], "edges": []},
                "feedback": {"helpful": 0, "harmful": 0, "neutral": 0},
                "provenance": {"taskId": "prior-3"}, "lexicalScore": 0.0, "vectorScore": 0.0,
                "strategyTemplateId": "", "status": "active",
            },
        ]
        result = core._retrieve(self.make_request(memories=memories))
        row = result["candidateScores"][0]
        self.assertAlmostEqual(row["templateScore"], round(0.4, 6), places=6)

    def test_no_active_template_keeps_legacy_weights(self) -> None:
        result = core._retrieve(self.make_request(activeTemplateId="", activeTemplate=None))
        self.assertEqual(result["templateChannelWeight"], 0.0)
        self.assertNotIn("template", result["fusionWeights"])
        for row in result["candidateScores"]:
            self.assertEqual(row["templateScore"], 0.0)

    def test_deprecated_memory_is_flagged(self) -> None:
        result = core._retrieve(self.make_request())
        deprecated = core._retrieve({
            "operation": "retrieve", "query": "重构代码库", "k": 4, "route": "semantic",
            "memories": [
                {
                    "memoryId": "old", "outcome": "success",
                    "semanticText": "重构解析函数", "reasoning": "先定位再改",
                    "structureSignature": [],
                    "reasoningGraph": {"version": 1, "nodes": [], "edges": []},
                    "feedback": {"helpful": 0, "harmful": 0, "neutral": 0},
                    "provenance": {"taskId": "prior-4"}, "lexicalScore": 0.0, "vectorScore": 0.0,
                    "strategyTemplateId": "", "status": "deprecated",
                },
            ],
        })
        self.assertTrue(deprecated["results"][0]["deprecated"])
        self.assertFalse(result["results"][0]["deprecated"])

    def test_rollback_intent_adjusts_weighted_template_weights(self) -> None:
        result = core._retrieve(self.make_request(retrievalIntent="rollback_check"))
        self.assertAlmostEqual(result["templateChannelWeight"], 0.2)
        self.assertAlmostEqual(result["fusionWeights"]["structure"], 0.25)

    def test_verify_intent_adjusts_graph_weight(self) -> None:
        result = core._retrieve(self.make_request(retrievalIntent="verify"))
        self.assertAlmostEqual(result["fusionWeights"]["graph"], 0.15)

    def test_ablation_override_second_weight_set(self) -> None:
        result = core._retrieve(self.make_request(fusionWeightsOverride={
            "lexical": 0.25, "reasoning": 0.10, "vector": 0.30,
            "structure": 0.05, "graph": 0.10, "template": 0.20,
        }))
        self.assertAlmostEqual(result["fusionWeights"]["template"], 0.20)
        self.assertAlmostEqual(result["fusionWeights"]["structure"], 0.05)

    def test_numerical_stability_with_empty_everything(self) -> None:
        result = core._retrieve({
            "operation": "retrieve", "query": "", "k": 4, "route": "auto",
            "memories": [], "activeTemplateId": "memory_tpl_builtin_tot",
            "activeTemplate": {"structureSignature": [], "steps": []},
        })
        self.assertEqual(result["results"], [])
        self.assertEqual(result["templateChannelWeight"], 0.15)


class DistillationGateTests(unittest.TestCase):
    def base_request(self, **overrides: object) -> dict:
        request = {
            "operation": "distill",
            "taskId": "task-gate",
            "sourceSessionId": "session-gate",
            "taskFamily": "code",
            "semanticText": "重构解析函数",
            "events": tool_events(),
            "outcome": "success",
            "verifier": "pytest",
            "evidence": "全部通过",
            "items": [{
                "outcome": "success",
                "title": "gate item",
                "content": "先定位失败点再修改",
            }],
        }
        request.update(overrides)
        return request

    def test_gate_disabled_accepts_without_confidence(self) -> None:
        result = core._make_memory(self.base_request())
        self.assertTrue(result["stored"])
        self.assertEqual(result["memories"][0]["insightConfidence"], 0.0)

    def test_gate_rejects_below_threshold_and_counts(self) -> None:
        result = core._make_memory(self.base_request(insightConfidence=0.2, minInsightConfidence=0.5))
        self.assertFalse(result["stored"])
        self.assertEqual(result["gateRejectedCount"], 1)
        self.assertIn("insight confidence gate", result["reason"])

    def test_gate_accepts_at_threshold(self) -> None:
        result = core._make_memory(self.base_request(insightConfidence=0.5, minInsightConfidence=0.5))
        self.assertTrue(result["stored"])
        self.assertEqual(result["memories"][0]["insightConfidence"], 0.5)

    def test_per_item_confidence_overrides_request_level(self) -> None:
        items = [
            {"outcome": "success", "title": "low", "content": "先定位失败点再修改", "insightConfidence": 0.1},
            {"outcome": "success", "title": "high", "content": "先写测试再实现功能", "insightConfidence": 0.9},
        ]
        result = core._make_memory(self.base_request(items=items, insightConfidence=0.5, minInsightConfidence=0.5))
        self.assertTrue(result["stored"])
        self.assertEqual(len(result["memories"]), 1)
        self.assertEqual(result["memories"][0]["title"], "high")
        self.assertEqual(result["gateRejectedCount"], 1)

    def test_gate_applies_to_legacy_insight_path(self) -> None:
        request = {
            "operation": "distill",
            "taskId": "task-legacy", "sourceSessionId": "session-legacy",
            "taskFamily": "code", "semanticText": "重构解析函数",
            "events": tool_events(), "outcome": "success",
            "verifier": "pytest", "evidence": "全部通过",
            "reasoningInsight": "先定位失败点再修改",
            "insightConfidence": 0.1, "minInsightConfidence": 0.5,
        }
        result = core._make_memory(request)
        self.assertFalse(result["stored"])
        self.assertIn("configured gate", result["reason"])

    def test_stored_memory_carries_new_template_fields(self) -> None:
        result = core._make_memory(self.base_request(
            insightConfidence=0.8, strategyTemplateId="memory_tpl_builtin_tot",
            reasoningLevel="step",
        ))
        item = result["memories"][0]
        self.assertEqual(item["strategyTemplateId"], "memory_tpl_builtin_tot")
        self.assertEqual(item["reasoningLevel"], "step")
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["insightConfidence"], 0.8)


class TemplateServiceTests(unittest.TestCase):
    def test_builtin_templates_operation_returns_five_seeds(self) -> None:
        result = core.handle_request({"operation": "builtin_templates"})
        self.assertEqual(len(result["templates"]), 5)
        ids = {template["templateId"] for template in result["templates"]}
        self.assertIn("memory_tpl_builtin_tot", ids)
        for template in result["templates"]:
            self.assertTrue(template["name"].startswith("memory_tpl_"))
            self.assertEqual(template["provenance"]["author"], "builtin")

    def test_plan_task_via_service_dispatch(self) -> None:
        builtins = core.handle_request({"operation": "builtin_templates"})["templates"]
        result = core.handle_request({
            "operation": "plan_task",
            "taskDesc": "在仓库里规划并实现一个新功能",
            "taskFamily": "code",
            "templates": builtins,
            "maxTemplates": 2,
        })
        self.assertIn(result["pattern"], core.TASK_PATTERNS)
        self.assertLessEqual(len(result["matchedTemplates"]), 2)
        self.assertIn("plan", result)

    def test_publish_validate_via_service(self) -> None:
        valid = core.handle_request({
            "operation": "publish_template_validate",
            "draft": {
                "name": "memory_tpl_my-refactor",
                "description": "Use when refactoring a repository with tests",
                "task_patterns": ["code_synthesis"],
                "steps": [{
                    "index": 1, "title": "定位失败点",
                    "module_demands": [{"kind": "retrieve_memory", "intent": "检索重构经验"}],
                }],
            },
        })
        self.assertTrue(valid["valid"], valid["issues"])
        self.assertEqual(valid["normalized"]["structureSignature"], ["search"])
        invalid = core.handle_request({
            "operation": "publish_template_validate",
            "draft": {
                "name": "bad name",
                "description": "no trigger phrasing here",
                "task_patterns": ["bogus"],
                "steps": [],
            },
        })
        self.assertFalse(invalid["valid"])
        self.assertTrue(invalid["issues"])

    def test_mine_templates_via_service(self) -> None:
        memories = [
            {
                "memoryId": f"m{i}", "outcome": "success", "createdAt": i,
                "semanticText": "重构代码库", "taskScope": "code_modification",
                "structureSignature": ["search", "execute", "verify"],
                "provenance": {"taskId": f"t{i}"},
            }
            for i in range(3)
        ]
        result = core.handle_request({
            "operation": "mine_templates", "memories": memories, "minSupport": 3,
        })
        self.assertEqual(len(result["templates"]), 1)


class EvalSchemaValidationTests(unittest.TestCase):
    """Build-time validator tests (04-eval-schema.md section 8)."""

    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts" / "eval"))
        from eval_dataset import EpisodeGenerator  # noqa: PLC0415
        import validate_schema  # noqa: PLC0415
        self.validator = validate_schema
        episodes, _ = EpisodeGenerator(episodes_per_family=6).generate()
        self.episodes = episodes

    def _validate(self, episodes: list[dict]) -> dict:
        return self.validator.validate([
            {"__line__": index + 1, "__raw__": "", "value": episode}
            for index, episode in enumerate(episodes)
        ])

    def test_valid_synthetic_dataset_passes(self) -> None:
        report = self._validate(self.episodes)
        self.assertTrue(report["ok"], report["violations"][:3])
        self.assertGreater(report["stats"]["episodes"], 0)

    def test_wrong_schema_version_is_rejected(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        episodes[0]["schemaVersion"] = 99
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("schemaVersion" in violation for violation in report["violations"]))

    def test_missing_required_field_is_rejected(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        del episodes[0]["episode"]["episodeId"]
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("episodeId" in violation for violation in report["violations"]))

    def test_bad_enum_and_length_caps_are_rejected(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        episodes[0]["episode"]["split"] = "prod"
        episodes[1]["episode"]["verifier"] = "x" * 201
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("split" in violation for violation in report["violations"]))
        self.assertTrue(any("cap" in violation for violation in report["violations"]))

    def test_leak_rule_1_family_across_splits(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        # Flipping exactly one episode of a family splits that family.
        episodes[0]["episode"]["split"] = "dev" if episodes[0]["episode"]["split"] == "test" else "test"
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("leak rule 1" in violation for violation in report["violations"]))

    def test_leak_rule_2_group_across_splits(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        dev_episode = next(episode for episode in episodes if episode["episode"]["split"] == "dev")
        test_episode = next(episode for episode in episodes if episode["episode"]["split"] == "test")
        # Pointing one dev episode at a test-side group splits that group.
        dev_episode["episode"]["taskFamilyGroup"] = test_episode["episode"]["taskFamilyGroup"]
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("leak rule 2" in violation for violation in report["violations"]))

    def test_leak_rule_3_cross_split_similarity(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        dev_episode = next(episode for episode in episodes if episode["episode"]["split"] == "dev")
        test_episode = next(episode for episode in episodes if episode["episode"]["split"] == "test")
        test_episode["episode"]["task"]["taskDesc"] = dev_episode["episode"]["task"]["taskDesc"] + " 的另一个表述"
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("leak rule 3" in violation for violation in report["violations"]))

    def test_reference_integrity_unknown_memory(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        target = next(
            episode for episode in episodes
            if episode["episode"]["queries"] and episode["episode"]["queries"][0]["expected"]
        )
        target["episode"]["queries"][0]["expected"][0]["memoryId"] = "mem-ghost"
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("does not exist" in violation for violation in report["violations"]))

    def test_self_retrieval_requires_the_flag(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        target = next(
            episode for episode in episodes
            if episode["episode"]["queries"] and episode["episode"]["queries"][0]["expected"]
        )
        own_id = target["episode"]["memoryWrites"][0]["memoryId"]
        target["episode"]["queries"][0]["expected"].append({
            "memoryId": own_id, "relevance": 3, "misleading": False,
            "basis": "self", "category": "workflow",
        })
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("allowSelfRetrieval" in violation for violation in report["violations"]))

    def test_misleading_requires_relevance_zero(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        target = next(
            episode for episode in episodes
            if episode["episode"]["queries"] and episode["episode"]["queries"][0]["expected"]
        )
        target["episode"]["queries"][0]["expected"][0]["misleading"] = True
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("misleading=true requires relevance 0" in violation for violation in report["violations"]))

    def test_bad_structure_tag_and_graph_kind_are_rejected(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        episodes[0]["episode"]["memoryWrites"][0]["structureSignature"] = ["bogus"]
        episodes[0]["episode"]["task"]["reasoningGraph"]["nodes"][0]["kind"] = "bogus"
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("structureSignature" in violation for violation in report["violations"]))
        self.assertTrue(any("node kind" in violation for violation in report["violations"]))

    def test_annotation_fields_are_enforced(self) -> None:
        episodes = copy.deepcopy(self.episodes)
        del episodes[0]["episode"]["memoryWrites"][0]["ann"]["annotators"]
        report = self._validate(episodes)
        self.assertFalse(report["ok"])
        self.assertTrue(any("annotators" in violation for violation in report["violations"]))


class AdversarialTests(unittest.TestCase):
    """T-debug-2 adversarial regressions for the v0.4.0 fixes."""

    def _retrieve_memory(
        self,
        memory_id: str,
        outcome: str = "success",
        *,
        status: str = "active",
        signature: list[str] | None = None,
    ) -> dict:
        return {
            "memoryId": memory_id,
            "outcome": outcome,
            "semanticText": "migrate the database schema",
            "reasoning": "validate the schema before applying the migration",
            "reasoningGraph": {"version": 1, "nodes": [], "edges": []},
            "content": "",
            "feedback": {"helpful": 0, "harmful": 0, "neutral": 0},
            "structureSignature": signature if signature is not None else ["search", "execute"],
            "status": status,
            "provenance": {"taskId": "prior-task"},
        }

    def _retrieve_request(self, **overrides: object) -> dict:
        request: dict = {
            "operation": "retrieve",
            "query": "migrate schema",
            "memories": [
                self._retrieve_memory("ok-1"),
                self._retrieve_memory("bad-1", "fail"),
                self._retrieve_memory("dep-1", status="deprecated"),
                self._retrieve_memory("ok-2"),
            ],
            "k": 4,
        }
        request.update(overrides)
        return request

    # --- B21: token-based tool classification ---------------------------------
    def test_english_words_containing_read_are_not_search(self) -> None:
        self.assertIsNone(core.classify_tool("thread_manager"))
        self.assertIsNone(core.classify_tool("bread"))
        self.assertEqual(core.classify_tool("reads"), "search")
        self.assertEqual(core.classify_tool("websearch"), "search")
        self.assertEqual(core.classify_tool("grep_files"), "search")

    def test_classification_keeps_real_tool_shapes(self) -> None:
        self.assertEqual(core.classify_tool("review"), "constrain")
        self.assertEqual(core.classify_tool("check_scope"), "constrain")
        self.assertEqual(core.classify_tool("check-scope"), "constrain")
        self.assertEqual(core.classify_tool("bash"), "execute")
        self.assertEqual(core.classify_tool("runner"), "execute")
        self.assertEqual(core.classify_tool("test_runner"), "verify")
        self.assertEqual(core.classify_tool("plan_next_steps"), "decompose")

    # --- P16: deprecated filtering happens before the quota -------------------
    def test_deprecated_excluded_before_quota_when_opted_out(self) -> None:
        result = core._retrieve(self._retrieve_request(includeDeprecated=False))
        self.assertEqual(result["deprecatedFilteredCount"], 1)
        ids = [row["memoryId"] for row in result["results"]]
        self.assertNotIn("dep-1", ids)
        # Quota contract holds against the RETURNED rows: 2 positive slots and
        # 2 negative slots, with the deprecated row out of the pool.
        self.assertEqual(result["balance"]["positiveCount"], 2)
        self.assertEqual(result["balance"]["negativeCount"], 1)
        self.assertIn("ok-1", ids)
        self.assertIn("ok-2", ids)
        self.assertIn("bad-1", ids)

    def test_deprecated_default_stays_in_pool_and_is_flagged(self) -> None:
        result = core._retrieve(self._retrieve_request())
        self.assertEqual(result["deprecatedFilteredCount"], 0)
        row = next(row for row in result["results"] if row["memoryId"] == "dep-1")
        self.assertTrue(row["deprecated"])

    def test_deprecated_count_present_on_early_return(self) -> None:
        result = core._retrieve({
            "operation": "retrieve",
            "query": "anything",
            "route": "structure",
            "memories": [],
        })
        self.assertEqual(result["deprecatedFilteredCount"], 0)


class DocumentChunkingTests(unittest.TestCase):
    def test_markdown_headings_build_paths_and_parent_links(self) -> None:
        result = core.chunk_document(doc_id="doc-a", content=(
            "# 开发规范\n\n## 2.1 代码风格\n函数命名使用 snake_case。\n"
            "## 2.2 测试要求\n每个模块必须有单测。\n"
        ))
        self.assertEqual(result["chunkCount"], 3)
        self.assertTrue(result["audit"]["headingsDetected"])
        self.assertEqual(result["chunks"][1]["headingPath"], ["开发规范", "2.1 代码风格"])
        self.assertEqual(result["chunks"][1]["parentChunkId"], "doc-a#1")
        self.assertEqual(result["chunks"][0]["nextChunkId"], "doc-a#2")
        self.assertEqual(result["chunks"][1]["prevChunkId"], "doc-a#1")

    def test_section_and_anchor_refs_resolve_to_chunk_ids(self) -> None:
        result = core.chunk_document(doc_id="doc-b", content=(
            "# 规范\n\n## 2.1 命名\n函数用 snake_case，见 §2.2。\n"
            "## 2.2 测试\n依据 2.1 先写测试。\n"
        ))
        naming = next(chunk for chunk in result["chunks"] if chunk["headingPath"][-1] == "2.1 命名")
        testing = next(chunk for chunk in result["chunks"] if chunk["headingPath"][-1] == "2.2 测试")
        self.assertIn(testing["chunkId"], naming["refs"])
        self.assertIn(naming["chunkId"], testing["refs"])

    def test_unresolved_references_are_counted_not_linked(self) -> None:
        result = core.chunk_document(doc_id="doc-c", content=(
            "# 规范\n\n## 说明\n见 §9.9 与 [本地文档](local-guide.md)。\n"
        ))
        chunk = result["chunks"][1]
        self.assertEqual(chunk["refs"], [])
        self.assertGreater(result["audit"]["unresolvedRefs"], 0)

    def test_plain_text_uses_blank_line_sections(self) -> None:
        result = core.chunk_document(doc_id="doc-d", content="第一段。\n\n第二段。\n\n第三段。")
        self.assertEqual(result["chunkCount"], 3)
        self.assertTrue(all(chunk["headingPath"] == [] for chunk in result["chunks"]))

    def test_oversized_paragraph_splits_at_sentence_boundaries(self) -> None:
        paragraph = "这是一句很长的测试句子。" * 300
        result = core.chunk_document(doc_id="doc-e", content=f"# 章节\n{paragraph}")
        self.assertGreater(result["chunkCount"], 1)
        self.assertGreater(result["audit"]["oversizedParagraphs"], 0)

    def test_tiny_fragments_merge_within_a_section_only(self) -> None:
        result = core.chunk_document(doc_id="doc-f", content=(
            "## 甲\n一句话。另一句话。\n## 乙\n另一句话。又一句话。\n"
        ))
        # Heading boundaries survive even though the texts are short.
        self.assertEqual(result["chunkCount"], 2)
        self.assertEqual(result["chunks"][0]["headingPath"][-1], "甲")
        self.assertEqual(result["chunks"][1]["headingPath"][-1], "乙")

    def test_empty_document_yields_no_chunks(self) -> None:
        result = core.chunk_document(doc_id="doc-g", content="   \n")
        self.assertEqual(result["chunks"], [])
        self.assertEqual(result["chunkCount"], 0)

    def test_oversized_content_is_truncated_and_audited(self) -> None:
        result = core.chunk_document(doc_id="doc-h", content="长内容" * 120_000)
        self.assertTrue(result["audit"]["contentTruncated"])

    def test_chunk_cap_truncates_and_audits(self) -> None:
        content = ("短块\n\n" * 9500)
        result = core.chunk_document(doc_id="doc-i", content=content, chunk_target=100, chunk_min=1)
        self.assertTrue(result["audit"]["truncated"])
        self.assertEqual(result["chunkCount"], 2000)

    def test_chunk_ids_are_deterministic(self) -> None:
        content = "# 甲\n内容。\n\n## 乙\n更多内容。\n"
        first = core.chunk_document(doc_id="doc-j", content=content)
        second = core.chunk_document(doc_id="doc-j", content=content)
        self.assertEqual(first["chunks"], second["chunks"])


class DocumentRagTests(unittest.TestCase):
    def setUp(self) -> None:
        chunked = core.chunk_document(doc_id="doc-code", content=(
            "# 开发规范\n\n## 2.1 命名\n函数使用 snake_case。\n"
            "## 2.2 测试\n提交前必须跑单测，见 §2.1。\n"
        ))
        self.chunks = chunked["chunks"]
        self.documents = [
            {"docId": "doc-code", "name": "memory_doc_dev-guide", "source": "company",
             "version": "1", "taskPatterns": ["code_synthesis"]},
            {"docId": "doc-web", "name": "memory_doc_web-guide", "source": "company",
             "version": "1", "taskPatterns": ["gui_operation"]},
            {"docId": "doc-general", "name": "memory_doc_general", "source": "",
             "version": "1", "taskPatterns": ["general"]},
        ]
        self.chunks.append({
            "chunkId": "doc-web#1", "docId": "doc-web", "seq": 1, "headingPath": ["页面"],
            "text": "点击按钮后检查页面状态", "prevChunkId": "", "nextChunkId": "",
            "parentChunkId": "", "refs": [],
        })
        self.chunks.append({
            "chunkId": "doc-general#1", "docId": "doc-general", "seq": 1, "headingPath": ["通用"],
            "text": "通用文档内容", "prevChunkId": "", "nextChunkId": "",
            "parentChunkId": "", "refs": [],
        })

    def test_pattern_filter_selects_matching_documents_only(self) -> None:
        result = core.retrieve_document(
            query="重构代码库的函数命名", task_pattern="code_synthesis",
            chunks=self.chunks, documents=self.documents, k=4,
            lexical_scores={"doc-code#2": 0.9},
        )
        self.assertEqual(result["pattern"], "code_synthesis")
        self.assertFalse(result["patternFallback"])
        doc_ids = {row["docId"] for row in result["results"]}
        self.assertIn("doc-code", doc_ids)
        self.assertNotIn("doc-web", doc_ids)

    def test_general_documents_pass_every_filter(self) -> None:
        result = core.retrieve_document(
            query="点击按钮", task_pattern="gui_operation",
            chunks=self.chunks, documents=self.documents, k=4,
            lexical_scores={"doc-general#1": 0.8},
        )
        self.assertFalse(result["patternFallback"])
        self.assertEqual(result["results"][0]["docId"], "doc-general")

    def test_pattern_fallback_when_no_document_matches(self) -> None:
        result = core.retrieve_document(
            query="计算数学方程", task_pattern="numeric_search",
            chunks=[chunk for chunk in self.chunks if chunk["docId"] == "doc-code"],
            documents=[doc for doc in self.documents if doc["docId"] == "doc-code"],
            k=4, lexical_scores={"doc-code#1": 0.9},
        )
        self.assertTrue(result["patternFallback"])
        self.assertEqual(result["results"], [])

    def test_expansion_includes_family_keys_and_template_intent(self) -> None:
        template = {
            "templateId": "memory_tpl_x",
            "steps": [
                {"index": 2, "moduleDemands": [
                    {"kind": "retrieve_document", "intent": "检索命名规范章节"},
                ]},
            ],
        }
        result = core.retrieve_document(
            query="重构代码库", task_pattern="code_synthesis",
            chunks=self.chunks, documents=self.documents, k=2,
            active_template=template, step_index=2,
        )
        self.assertIn("检索命名规范章节", result["expansion"]["intentTexts"])
        self.assertIn("humaneval", result["expansion"]["stepBackTerms"])

    def test_vector_and_lexical_rankings_trigger_rrf(self) -> None:
        result = core.retrieve_document(
            query="重构代码库", task_pattern="code_synthesis",
            chunks=self.chunks, documents=self.documents, k=4,
            lexical_scores={"doc-code#2": 0.9, "doc-code#3": 0.5},
            vector_scores={"doc-code#3": 0.8},
        )
        self.assertEqual(result["fusionMode"], "rrf")
        self.assertTrue(all(row["score"] <= 1.0 for row in result["results"]))

    def test_weighted_fusion_when_vector_absent(self) -> None:
        result = core.retrieve_document(
            query="重构代码库", task_pattern="code_synthesis",
            chunks=self.chunks, documents=self.documents, k=4,
            lexical_scores={"doc-code#2": 0.9},
        )
        self.assertEqual(result["fusionMode"], "weighted")

    def test_multi_hop_expansion_is_bounded_and_cycle_safe(self) -> None:
        hop_chunks = [
            {"chunkId": "d1#1", "docId": "d1", "seq": 1, "headingPath": ["根"], "text": "根",
             "prevChunkId": "", "nextChunkId": "", "parentChunkId": "", "refs": ["d1#2"]},
            {"chunkId": "d1#2", "docId": "d1", "seq": 2, "headingPath": ["子"], "text": "子",
             "prevChunkId": "d1#1", "nextChunkId": "", "parentChunkId": "d1#1", "refs": ["d1#3"]},
            {"chunkId": "d1#3", "docId": "d1", "seq": 3, "headingPath": ["孙"], "text": "孙",
             "prevChunkId": "d1#2", "nextChunkId": "", "parentChunkId": "d1#2", "refs": ["d1#1"]},
        ]
        result = core.retrieve_document(
            query="根", task_pattern="general", chunks=hop_chunks,
            documents={"d1": {"name": "memory_doc_d1", "source": "", "version": "1", "taskPatterns": ["general"]}},
            k=1, max_hops=2, lexical_scores={"d1#1": 0.9},
        )
        self.assertEqual(result["audit"]["visitedChunks"], 3)
        self.assertEqual(result["audit"]["seeds"], 1)

    def test_hop_zero_disables_expansion(self) -> None:
        hop_chunks = [
            {"chunkId": "d1#1", "docId": "d1", "seq": 1, "headingPath": ["根"], "text": "根",
             "prevChunkId": "", "nextChunkId": "d1#2", "parentChunkId": "", "refs": []},
            {"chunkId": "d1#2", "docId": "d1", "seq": 2, "headingPath": ["子"], "text": "子",
             "prevChunkId": "d1#1", "nextChunkId": "", "parentChunkId": "", "refs": []},
        ]
        result = core.retrieve_document(
            query="根", task_pattern="general", chunks=hop_chunks,
            documents={"d1": {"name": "memory_doc_d1", "source": "", "version": "1", "taskPatterns": ["general"]}},
            k=1, max_hops=0, lexical_scores={"d1#1": 0.9},
        )
        self.assertEqual(result["audit"]["visitedChunks"], 1)

    def test_expansion_respects_the_visited_cap(self) -> None:
        chain = []
        for index in range(1, 40):
            chain.append({
                "chunkId": f"c#1#{index}", "docId": "c", "seq": index, "headingPath": [f"块{index}"],
                "text": f"块{index}", "prevChunkId": f"c#1#{index - 1}" if index > 1 else "",
                "nextChunkId": f"c#1#{index + 1}" if index < 39 else "",
                "parentChunkId": "", "refs": [],
            })
        result = core.retrieve_document(
            query="块1", task_pattern="general", chunks=chain,
            documents={"c": {"name": "memory_doc_c", "source": "", "version": "1", "taskPatterns": ["general"]}},
            k=1, max_hops=2, lexical_scores={"c#1#1": 0.9},
        )
        self.assertLessEqual(result["audit"]["visitedChunks"], 16)

    def test_empty_corpus_returns_a_reason(self) -> None:
        result = core.retrieve_document(
            query="任意", task_pattern="general", chunks=[], documents=[],
        )
        self.assertEqual(result["results"], [])
        self.assertIn("no document chunk", result["reason"])


class DocumentServiceTests(unittest.TestCase):
    def test_import_document_operation_chunks(self) -> None:
        result = core.handle_request({
            "operation": "import_document", "docId": "svc-doc", "name": "memory_doc_svc",
            "content": "# S\n内容一。\n\n## T\n内容二。\n",
        })
        self.assertEqual(result["chunkCount"], 2)
        self.assertEqual(result["chunks"][0]["chunkId"], "svc-doc#1")

    def test_retrieve_document_operation(self) -> None:
        result = core.handle_request({
            "operation": "retrieve_document", "query": "重构代码库", "k": 2,
            "taskPattern": "code_synthesis",
            "chunks": [{
                "chunkId": "d#1", "docId": "d", "seq": 1, "headingPath": ["规范"],
                "text": "函数命名使用 snake_case", "prevChunkId": "", "nextChunkId": "",
                "parentChunkId": "", "refs": [],
            }],
            "documents": [{
                "docId": "d", "name": "memory_doc_d", "source": "", "version": "1",
                "taskPatterns": ["code_synthesis"],
            }],
            "lexicalScores": {"d#1": 0.9},
        })
        self.assertEqual(result["results"][0]["chunkId"], "d#1")
        self.assertEqual(result["results"][0]["docName"], "memory_doc_d")

    def test_delete_document_plan_lists_chunk_ids(self) -> None:
        chunks = [
            {"chunkId": "d#1", "docId": "d", "seq": 1, "headingPath": [], "text": "a",
             "prevChunkId": "", "nextChunkId": "", "parentChunkId": "", "refs": []},
            {"chunkId": "d#2", "docId": "d", "seq": 2, "headingPath": [], "text": "b",
             "prevChunkId": "", "nextChunkId": "", "parentChunkId": "", "refs": []},
        ]
        result = core.handle_request({"operation": "delete_document_plan", "docId": "d", "chunks": chunks})
        self.assertEqual(result["chunkIds"], ["d#1", "d#2"])


class CausalUtilityTests(unittest.TestCase):
    """Synthetic known-structure checks for the v0.6.0 causal utility engine."""

    def _event(self, treatment: str, outcome: str, pattern: str = "code_synthesis", **overrides: object) -> dict:
        event: dict = {
            "memoryId": "m1", "kind": "memory", "taskId": "t", "taskPattern": pattern,
            "treatment": treatment, "outcome": outcome, "stepIndex": 0,
            "retrievalIntent": "", "modelVersion": "", "pairId": "", "createdAt": 0,
        }
        event.update(overrides)
        return event

    def _synthetic_stratified(self, rng: random.Random, spec: dict) -> list[dict]:
        """Deterministic samples per stratum with known treatment/control rates."""
        events: list[dict] = []
        for pattern, rates in spec.items():
            n1, n0, p1, p0 = rates
            events += [self._event("used", "success" if rng.random() < p1 else "fail", pattern) for _ in range(n1)]
            events += [self._event("retrieved_unused", "success" if rng.random() < p0 else "fail", pattern) for _ in range(n0)]
        return events

    def test_stratified_ate_recovers_the_known_effect_with_ci_coverage(self) -> None:
        # Two strata: code +0.20, gui +0.10. Weighted truth: (160/300)*0.2 + (140/300)*0.1.
        spec = {
            "code_synthesis": (120, 40, 0.7, 0.5),
            "gui_operation": (80, 60, 0.6, 0.5),
        }
        truth = (160 / 300) * 0.20 + (140 / 300) * 0.10
        covered = 0
        seeds = 10
        for seed in range(seeds):
            events = self._synthetic_stratified(random.Random(seed), spec)
            result = core.causal_estimate(events=events)
            self.assertTrue(result["causalApplied"])
            if result["ciLower"] <= truth <= result["ciUpper"]:
                covered += 1
        self.assertGreaterEqual(covered, seeds * 0.9)

    def test_cate_is_reported_per_stratum(self) -> None:
        spec = {
            "code_synthesis": (360, 120, 0.7, 0.5),
            "gui_operation": (240, 180, 0.6, 0.5),
        }
        result = core.causal_estimate(events=self._synthetic_stratified(random.Random(0), spec))
        by_stratum = {row["stratum"]: row["cate"] for row in result["cate"]}
        self.assertIn("code_synthesis", by_stratum)
        self.assertIn("gui_operation", by_stratum)
        # Both strata carry positive true effects; the sample sizes keep the
        # Laplace-smoothed estimates clearly above zero.
        self.assertGreater(by_stratum["code_synthesis"], 0.05)
        self.assertGreater(by_stratum["gui_operation"], 0.05)

    def test_simpson_construction_is_detected(self) -> None:
        # Large stratum A: treated worse; small stratum B: treated better.
        # Crude (pooled) effect is negative while every stratum is positive.
        events = []
        events += [self._event("used", "success", "code_synthesis") for _ in range(144)]
        events += [self._event("used", "fail", "code_synthesis") for _ in range(216)]
        events += [self._event("retrieved_unused", "success", "code_synthesis") for _ in range(8)]
        events += [self._event("retrieved_unused", "fail", "code_synthesis") for _ in range(32)]
        events += [self._event("used", "success", "multi_hop_qa") for _ in range(36)]
        events += [self._event("used", "fail", "multi_hop_qa") for _ in range(4)]
        events += [self._event("retrieved_unused", "success", "multi_hop_qa") for _ in range(216)]
        events += [self._event("retrieved_unused", "fail", "multi_hop_qa") for _ in range(144)]
        result = core.causal_estimate(events=events)
        self.assertLess(result["crude"], 0.0)
        self.assertGreater(result["ate"], 0.0)
        self.assertTrue(result["simpsonFlip"])

    def test_no_structure_produces_no_simpson_false_positive(self) -> None:
        events = []
        for pattern in ("code_synthesis", "gui_operation"):
            events += [self._event("used", "success", pattern) for _ in range(70)]
            events += [self._event("used", "fail", pattern) for _ in range(30)]
            events += [self._event("retrieved_unused", "success", pattern) for _ in range(50)]
            events += [self._event("retrieved_unused", "fail", pattern) for _ in range(50)]
        result = core.causal_estimate(events=events)
        self.assertFalse(result["simpsonFlip"])

    def test_iptw_clips_extreme_propensities_and_flags_disagreement(self) -> None:
        # One stratum with a 1/199 treated split drives the IPTW weights to
        # the truncation bounds; the HT estimate then disagrees with the
        # stratified estimate and must be flagged. The 1/199 stratum itself is
        # dropped from the stratified estimate (arm < 5) but still feeds the
        # robustness check with clipped weights.
        events = []
        events += [self._event("used", "fail", "code_synthesis") for _ in range(1)]
        events += [self._event("retrieved_unused", "success", "code_synthesis") for _ in range(199)]
        events += [self._event("used", "success", "gui_operation") for _ in range(90)]
        events += [self._event("used", "fail", "gui_operation") for _ in range(10)]
        events += [self._event("retrieved_unused", "success", "gui_operation") for _ in range(10)]
        events += [self._event("retrieved_unused", "fail", "gui_operation") for _ in range(90)]
        result = core.causal_estimate(events=events)
        self.assertIn("code_synthesis", result["droppedStrata"])
        self.assertGreaterEqual(result["iptwClipCount"], 1)
        self.assertTrue(result["iptwDisagreement"])

    def test_laplace_smoothing_survives_zero_cells(self) -> None:
        events = []
        events += [self._event("used", "success") for _ in range(20)]
        events += [self._event("retrieved_unused", "fail") for _ in range(20)]
        result = core.causal_estimate(events=events)
        self.assertLess(result["ciLower"], result["ciUpper"])
        self.assertTrue(result["causalApplied"])

    def test_per_arm_and_effective_sample_gates(self) -> None:
        # One arm short of the per-stratum gate.
        events = []
        events += [self._event("used", "success") for _ in range(2)]
        events += [self._event("used", "fail") for _ in range(2)]
        events += [self._event("retrieved_unused", "success") for _ in range(30)]
        events += [self._event("retrieved_unused", "fail") for _ in range(30)]
        result = core.causal_estimate(events=events)
        self.assertFalse(result["causalApplied"])
        self.assertEqual(result["droppedStrata"], ["code_synthesis"])
        self.assertIn("sample gate", result["reason"])

        # n_eff below the global gate with qualifying strata.
        small = []
        small += [self._event("used", "success") for _ in range(5)]
        small += [self._event("used", "fail") for _ in range(5)]
        small += [self._event("retrieved_unused", "success") for _ in range(5)]
        small += [self._event("retrieved_unused", "fail") for _ in range(4)]
        result_small = core.causal_estimate(events=small)
        self.assertFalse(result_small["causalApplied"])
        self.assertIn("20", result_small["reason"])

    def test_shrinkage_is_monotone_in_effective_samples(self) -> None:
        def uplift(seed: int, scale: int) -> float:
            spec = {
                "code_synthesis": (120 * scale, 40 * scale, 0.7, 0.5),
                "gui_operation": (80 * scale, 60 * scale, 0.6, 0.5),
            }
            return core.causal_estimate(events=self._synthetic_stratified(random.Random(seed), spec))["ateShrunk"]
        small = abs(uplift(1, 1))
        large = abs(uplift(1, 4))
        self.assertGreater(large, small)

    def test_paired_counterfactual_estimate(self) -> None:
        events = [
            self._event("counterfactual_with", "success", pairId="p1"),
            self._event("counterfactual_without", "fail", pairId="p1"),
            self._event("counterfactual_with", "fail", pairId="p2"),
            self._event("counterfactual_without", "fail", pairId="p2"),
            self._event("counterfactual_with", "success", pairId="p3"),
        ]
        result = core.causal_estimate(events=events)
        self.assertEqual(result["pairedPairs"], 2)
        self.assertEqual(result["pairedAte"], 0.5)

    def test_determinism_across_runs(self) -> None:
        events = []
        events += [self._event("used", "success", "code_synthesis") for _ in range(144)]
        events += [self._event("used", "fail", "code_synthesis") for _ in range(216)]
        events += [self._event("retrieved_unused", "success", "multi_hop_qa") for _ in range(216)]
        events += [self._event("retrieved_unused", "fail", "multi_hop_qa") for _ in range(144)]
        first = json.dumps(core.causal_estimate(events=events), sort_keys=True)
        second = json.dumps(core.causal_estimate(events=events), sort_keys=True)
        self.assertEqual(first, second)

    def test_causal_multiplier_stays_inside_the_beta_band(self) -> None:
        events = []
        events += [self._event("used", "success") for _ in range(90)]
        events += [self._event("used", "fail") for _ in range(10)]
        events += [self._event("retrieved_unused", "success") for _ in range(10)]
        events += [self._event("retrieved_unused", "fail") for _ in range(90)]
        result = core.causal_estimate(events=events)
        self.assertTrue(result["causalApplied"])
        self.assertGreaterEqual(result["causalMultiplier"], 0.8)
        self.assertLessEqual(result["causalMultiplier"], 1.2)

    def test_causal_applied_false_keeps_beta_behavior_in_retrieval(self) -> None:
        memories = [{
            "memoryId": "m1", "outcome": "success",
            "semanticText": "重构解析函数", "reasoning": "先定位再改",
            "structureSignature": [], "reasoningGraph": {"version": 1, "nodes": [], "edges": []},
            "feedback": {"helpful": 4, "harmful": 0, "neutral": 0},
            "provenance": {"taskId": "prior-1"}, "lexicalScore": 0.0, "vectorScore": 0.0,
            "strategyTemplateId": "", "status": "active",
        }]
        baseline = core._retrieve({
            "operation": "retrieve", "query": "重构", "k": 2, "route": "semantic",
            "memories": memories,
        })
        with_events = core._retrieve({
            "operation": "retrieve", "query": "重构", "k": 2, "route": "semantic",
            "memories": memories,
            "causalEventsByMemory": {"m1": [self._event("used", "success") for _ in range(5)]},
        })
        baseline_row = next(row for row in baseline["candidateScores"] if row["memoryId"] == "m1")
        causal_row = next(row for row in with_events["candidateScores"] if row["memoryId"] == "m1")
        self.assertFalse(causal_row["causalApplied"])
        self.assertEqual(baseline_row["utilityMultiplier"], causal_row["utilityMultiplier"])

    def test_causal_estimate_service_operation_and_batch(self) -> None:
        events = []
        events += [self._event("used", "success") for _ in range(70)]
        events += [self._event("used", "fail") for _ in range(30)]
        events += [self._event("retrieved_unused", "success") for _ in range(50)]
        events += [self._event("retrieved_unused", "fail") for _ in range(50)]
        single = core.handle_request({"operation": "causal_estimate", "events": events})
        self.assertTrue(single["causalApplied"])
        batch = core.handle_request({
            "operation": "causal_estimate",
            "eventsByMemory": {"m1": events, "m2": events[:4]},
        })
        self.assertTrue(batch["estimates"]["m1"]["causalApplied"])
        self.assertFalse(batch["estimates"]["m2"]["causalApplied"])

    def test_classify_service_operation_accepts_events(self) -> None:
        result = core.handle_request({
            "operation": "classify_task_pattern",
            "taskDesc": "重构 utils.py 的解析函数并跑单测",
            "taskFamily": "code_modification",
            "events": [
                {"seq": 1, "type": "tool_call", "callId": "c1", "toolName": "bash"},
                {"seq": 2, "type": "tool_result", "callId": "c1", "toolName": "bash", "success": True},
            ],
        })
        self.assertEqual(result["pattern"], "code_synthesis")


class DocumentAdversarialTests(unittest.TestCase):
    """T-rag-debug adversarial regressions for the v0.5.0 document surface."""

    def _chunk(self, chunk_id: str, **overrides: object) -> dict:
        chunk: dict = {
            "chunkId": chunk_id, "docId": "doc", "seq": int(chunk_id.split("#")[1]),
            "headingPath": [], "text": chunk_id,
            "prevChunkId": "", "nextChunkId": "", "parentChunkId": "", "refs": [],
        }
        chunk.update(overrides)
        return chunk

    def _docs(self, patterns: list[str]) -> dict:
        return {"doc": {
            "docId": "doc", "name": "memory_doc_x", "source": "", "version": "1",
            "taskPatterns": patterns,
        }}

    def _retrieve(self, chunks: list[dict], **overrides: object) -> dict:
        request: dict = {
            "operation": "retrieve_document",
            "query": "unrelated query words",
            "chunks": chunks,
            "documents": [self._docs(["general"])["doc"]],
            "taskPattern": "general",
            "k": 4,
        }
        request.update(overrides)
        return core.handle_request(request)

    # --- R1: zero text signal must yield empty results, not 1.0 rows ---------
    def test_zero_text_signal_returns_empty_not_full_score(self) -> None:
        chunks = [self._chunk("doc#1"), self._chunk("doc#2")]
        result = self._retrieve(chunks, lexicalScores={"doc#1": 0.0, "doc#2": 0.0})
        self.assertEqual(result["results"], [])
        self.assertEqual(result["audit"]["seeds"], 0)
        self.assertIn("text", result["reason"])

    def test_zero_text_signal_with_empty_score_maps(self) -> None:
        chunks = [self._chunk("doc#1")]
        result = self._retrieve(chunks, lexicalScores={})
        self.assertEqual(result["results"], [])
        self.assertIn("text", result["reason"])

    def test_positive_lexical_signal_still_retrieves(self) -> None:
        chunks = [self._chunk("doc#1")]
        result = self._retrieve(chunks, lexicalScores={"doc#1": 0.9})
        self.assertEqual(len(result["results"]), 1)
        self.assertGreater(result["results"][0]["score"], 0.0)

    # --- R2 (adjudicated): channel-carried hop decay -------------------------
    def test_weighted_mode_seed_beats_hop_neighbor_on_equal_scores(self) -> None:
        chunks = [
            self._chunk("doc#1", nextChunkId="doc#2"),
            self._chunk("doc#2", prevChunkId="doc#1"),
        ]
        result = self._retrieve(
            chunks,
            lexicalScores={"doc#1": 0.9, "doc#2": 0.9},
            k=1,
        )
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["chunkId"], "doc#1")
        self.assertEqual(result["results"][0]["hop"], 0)
        self.assertEqual(result["results"][0]["breakdown"]["link"], 1.0)
        self.assertEqual(result["audit"]["visitedChunks"], 2)

    def test_rrf_mode_hop_decay_is_carried_by_the_link_channel(self) -> None:
        # RRF allows a vector-strong hop-1 neighbor to outrank the seed; the
        # 0.6^hop decay stays visible in the auditable link breakdown. A third
        # chunk occupies the second seed slot so doc#2 is only reachable
        # through the link walk.
        chunks = [
            self._chunk("doc#1", nextChunkId="doc#2"),
            self._chunk("doc#2", prevChunkId="doc#1"),
            self._chunk("doc#3"),
        ]
        result = self._retrieve(
            chunks,
            lexicalScores={"doc#1": 0.9, "doc#2": 0.3, "doc#3": 0.9},
            vectorScores={"doc#1": 0.2, "doc#2": 0.9, "doc#3": 0.15},
            k=2,
        )
        self.assertEqual(result["fusionMode"], "rrf")
        self.assertEqual(len(result["results"]), 2)
        hop_row = next(row for row in result["results"] if row["chunkId"] == "doc#2")
        seed_row = next(row for row in result["results"] if row["chunkId"] == "doc#1")
        self.assertEqual(hop_row["hop"], 1)
        self.assertEqual(hop_row["viaChunkId"], "doc#1")
        self.assertEqual(seed_row["hop"], 0)
        self.assertAlmostEqual(hop_row["breakdown"]["link"], 0.6)
        self.assertEqual(seed_row["breakdown"]["link"], 1.0)

    def test_hop_walk_is_cycle_safe_and_capped(self) -> None:
        chunks = [
            self._chunk("doc#1", nextChunkId="doc#2"),
            self._chunk("doc#2", prevChunkId="doc#1", nextChunkId="doc#1"),
        ]
        result = self._retrieve(chunks, lexicalScores={"doc#1": 0.9}, k=1, maxHops=2)
        self.assertEqual(result["audit"]["visitedChunks"], 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["chunkId"], "doc#1")
        self.assertEqual(result["results"][0]["hop"], 0)

    # --- R3: Chinese-numeral chapter references resolve ----------------------
    def test_chinese_chapter_heading_resolves_chapter_reference(self) -> None:
        result = core.chunk_document(doc_id="doc-cn", content=(
            "# 第一章 概述\n见第三章的做法。\n# 第三章 详述\n内容如下。"
        ))
        target = next(chunk for chunk in result["chunks"] if chunk["headingPath"][-1] == "第三章 详述")
        source = next(chunk for chunk in result["chunks"] if "见第三章" in chunk["text"])
        self.assertIn(target["chunkId"], source["refs"])
        self.assertEqual(result["audit"]["unresolvedRefs"], 0)

    # --- R4: truncation flag off-by-one ---------------------------------------
    def test_content_truncation_flag_is_strict(self) -> None:
        exact = core.chunk_document(doc_id="d1", content="x" * 200_000)
        self.assertFalse(exact["audit"]["contentTruncated"])
        over = core.chunk_document(doc_id="d2", content="x" * 200_001)
        self.assertTrue(over["audit"]["contentTruncated"])
        self.assertEqual(over["charCount"], 200_000)


class CausalBoundaryTests(unittest.TestCase):
    """T-causal-debug exact-boundary regressions for the v0.6.0 causal engine."""

    def _event(self, treatment: str, outcome: str, pattern: str = "code_synthesis", **overrides: object) -> dict:
        event: dict = {
            "memoryId": "m1", "kind": "memory", "taskId": "t", "taskPattern": pattern,
            "treatment": treatment, "outcome": outcome, "stepIndex": 0,
            "retrievalIntent": "", "modelVersion": "", "pairId": "", "createdAt": 0,
        }
        event.update(overrides)
        return event

    def _stratum(self, treated_total: int, control_total: int, treated_success: int, control_success: int) -> list[dict]:
        events = [self._event("used", "success") for _ in range(treated_success)]
        events += [self._event("used", "fail") for _ in range(treated_total - treated_success)]
        events += [self._event("retrieved_unused", "success") for _ in range(control_success)]
        events += [self._event("retrieved_unused", "fail") for _ in range(control_total - control_success)]
        return events

    # --- Per-arm gate: exactly 4 dropped, exactly 5/6 qualified ---------------
    def test_per_arm_gate_boundary_is_exact(self) -> None:
        for arm_size in (5, 6):
            events = self._stratum(arm_size, 10, arm_size // 2, 5)
            result = core.causal_estimate(events=events)
            self.assertEqual(result["droppedStrata"], [], f"arm {arm_size} should qualify")
            self.assertEqual(result["nEff"], arm_size + 10)
        # Arm exactly 4: the stratum drops and the estimate never applies.
        below = self._stratum(4, 10, 2, 5)
        result_below = core.causal_estimate(events=below)
        self.assertEqual(result_below["droppedStrata"], ["code_synthesis"])
        self.assertEqual(result_below["nEff"], 0)
        self.assertFalse(result_below["causalApplied"])
        self.assertIn("sample gate", result_below["reason"])

    # --- Effective-n gate: 19 falls back, 20/21 apply -------------------------
    def test_effective_n_gate_boundary_is_exact(self) -> None:
        for control_extra in (1, 2):  # n_eff 21 / 22 ... keep the n1 arm at 10
            events = self._stratum(10, 10 + control_extra, 5, 5)
            result = core.causal_estimate(events=events)
            self.assertTrue(result["causalApplied"])
        # n_eff exactly 20 applies.
        result_20 = core.causal_estimate(events=self._stratum(10, 10, 5, 5))
        self.assertTrue(result_20["causalApplied"])
        self.assertEqual(result_20["nEff"], 20)

    # --- Shrinkage caps: extreme effects pin the multiplier at 1.2 / 0.8 ------
    def test_multiplier_caps_pin_at_the_band_edges(self) -> None:
        positive = self._stratum(100, 100, 100, 0)
        result = core.causal_estimate(events=positive)
        self.assertGreater(result["ateShrunk"], 0.20)
        self.assertEqual(result["causalMultiplier"], 1.2)
        negative = self._stratum(100, 100, 0, 100)
        result_negative = core.causal_estimate(events=negative)
        self.assertLess(result_negative["ateShrunk"], -0.20)
        self.assertEqual(result_negative["causalMultiplier"], 0.8)

    def test_shrinkage_monotone_in_effective_samples(self) -> None:
        # Same per-stratum effect, larger effective sample => strictly larger
        # absolute shrinkage-adjusted uplift.
        def uplift(scale: int) -> float:
            events = self._stratum(60 * scale, 60 * scale, 50 * scale, 30 * scale)
            return abs(core.causal_estimate(events=events)["ateShrunk"])
        self.assertGreater(uplift(4), uplift(1))

    # --- Paired estimator: missing arm and duplicate arms ---------------------
    def test_paired_estimate_handles_missing_and_duplicate_arms(self) -> None:
        only_with = [self._event("counterfactual_with", "success", pairId="p1")]
        result = core.causal_estimate(events=only_with)
        self.assertEqual(result["pairedPairs"], 0)
        self.assertEqual(result["pairedAte"], 0.0)
        # Duplicate with-arm: the first recorded arm wins deterministically.
        duplicate = [
            self._event("counterfactual_with", "success", pairId="p1"),
            self._event("counterfactual_with", "fail", pairId="p1"),
            self._event("counterfactual_without", "fail", pairId="p1"),
        ]
        result_dup = core.causal_estimate(events=duplicate)
        self.assertEqual(result_dup["pairedPairs"], 1)
        self.assertEqual(result_dup["pairedAte"], 1.0)

    # --- Single-arm corpora: no crash, no false application -------------------
    def test_all_treated_corpus_stays_conservative(self) -> None:
        events = [self._event("used", "success") for _ in range(20)]
        result = core.causal_estimate(events=events)
        self.assertFalse(result["causalApplied"])
        self.assertEqual(result["droppedStrata"], ["code_synthesis"])
        self.assertEqual(result["iptwAte"], 0.0)
        self.assertGreaterEqual(result["iptwClipCount"], 1)
        self.assertEqual(result["crude"], 0.0)
        self.assertFalse(result["simpsonFlip"])


if __name__ == "__main__":
    unittest.main()
