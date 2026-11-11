"""Reasoning templates: classification, matching, mining, feedback, retrieval and service."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

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


if __name__ == "__main__":
    unittest.main()
