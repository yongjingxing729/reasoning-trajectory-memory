"""Build-time eval dataset schema validation (scripts/eval)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

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


if __name__ == "__main__":
    unittest.main()
