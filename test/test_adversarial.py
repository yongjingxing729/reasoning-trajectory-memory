"""Adversarial inputs for distillation and recall."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

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


if __name__ == "__main__":
    unittest.main()
