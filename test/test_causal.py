"""Causal utility estimation and causal boundary conditions."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

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
