"""Tests for the deterministic Python reasoning-memory core.

The core itself has no third-party dependencies; the test suite covers
distillation gates, structured items, retrieval fusion and the JSON bridge
(stdin/stdout and --file-io transports).
"""

from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
