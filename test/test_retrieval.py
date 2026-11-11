"""Retrieval fusion, lexical/vector/structure scoring and diversity selection."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

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


if __name__ == "__main__":
    unittest.main()
