"""Distillation output shape, lessons, structured reasoning and distillation gates."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

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


if __name__ == "__main__":
    unittest.main()
