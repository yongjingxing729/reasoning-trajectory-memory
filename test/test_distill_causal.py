"""Distill-causal integration and regression behavior."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

class DistillCausalTests(unittest.TestCase):
    """v0.8.0 causal distillation uplift: synthetic known-structure checks."""

    def _event(self, treatment: str, outcome: str, pattern: str = "code_synthesis", **overrides: object) -> dict:
        event: dict = {
            "memoryId": "m1", "taskId": "t", "taskPattern": pattern,
            "treatment": treatment, "outcome": outcome, "stepIndex": 0,
            "modelVersion": "", "pairId": "", "createdAt": 0,
        }
        event.update(overrides)
        return event

    def _synthetic(self, n1: int, n0: int, p1: float, p0: float, rng: random.Random) -> list[dict]:
        events: list[dict] = []
        events += [self._event("distilled", "success" if rng.random() < p1 else "fail") for _ in range(n1)]
        events += [self._event("not_distilled", "success" if rng.random() < p0 else "fail") for _ in range(n0)]
        return events

    def test_estimate_recovers_known_uplift(self) -> None:
        events = []
        events += [self._event("distilled", "success") for _ in range(70)]
        events += [self._event("distilled", "fail") for _ in range(30)]
        events += [self._event("not_distilled", "success") for _ in range(50)]
        events += [self._event("not_distilled", "fail") for _ in range(50)]
        result = core.estimate_distill_uplift(events=events)
        self.assertTrue(result["upliftApplied"])
        self.assertGreater(result["uplift"], 0.1)
        self.assertLessEqual(result["upliftClipped"], 0.20)
        self.assertEqual(result["inherited"], 0.5)

    def test_inherited_pollution_is_separated(self) -> None:
        # Near-duplicate: apparent is high but the control arm is equally high,
        # so the incremental uplift is ~0 and the candidate should be rejected.
        events = []
        events += [self._event("distilled", "success") for _ in range(90)]
        events += [self._event("distilled", "fail") for _ in range(10)]
        events += [self._event("not_distilled", "success") for _ in range(90)]
        events += [self._event("not_distilled", "fail") for _ in range(10)]
        result = core.estimate_distill_uplift(events=events)
        self.assertTrue(result["upliftApplied"])
        self.assertGreater(result["apparent"], 0.8)
        self.assertLess(abs(result["uplift"]), 0.05)

    def test_gate_boundaries(self) -> None:
        self.assertEqual(core.gate_distill(uplift_shrunk=0.1, threshold=0.0, enabled=True, applied=True)["gate"], "passed")
        rejected = core.gate_distill(uplift_shrunk=-0.1, threshold=0.0, enabled=True, applied=True)
        self.assertEqual(rejected["gate"], "rejected")
        self.assertEqual(rejected["reason"], "below_distill_uplift_gate")
        self.assertEqual(core.gate_distill(uplift_shrunk=-0.1, threshold=0.0, enabled=False, applied=True)["gate"], "na")
        self.assertEqual(core.gate_distill(uplift_shrunk=-0.1, threshold=0.0, enabled=True, applied=False)["gate"], "na")

    def test_shrinkage_is_monotone_in_effective_samples(self) -> None:
        # Deterministic counts: the raw uplift is identical across scales, so
        # the unclipped shrunk uplift grows monotonically with n_eff.
        def uplift(scale: int) -> float:
            events = []
            events += [self._event("distilled", "success") for _ in range(70 * scale)]
            events += [self._event("distilled", "fail") for _ in range(30 * scale)]
            events += [self._event("not_distilled", "success") for _ in range(50 * scale)]
            events += [self._event("not_distilled", "fail") for _ in range(50 * scale)]
            return core.estimate_distill_uplift(events=events)["upliftShrunk"]
        self.assertGreater(uplift(4), uplift(1))

    def test_sample_gate_fallback(self) -> None:
        # Fewer than 20 effective samples -> not applied.
        events = []
        events += [self._event("distilled", "success") for _ in range(5)]
        events += [self._event("distilled", "fail") for _ in range(5)]
        events += [self._event("not_distilled", "success") for _ in range(5)]
        events += [self._event("not_distilled", "fail") for _ in range(4)]
        result = core.estimate_distill_uplift(events=events)
        self.assertFalse(result["upliftApplied"])
        self.assertIn("sample gate", result["reason"])

        # Per-arm gate: one arm short of the minimum.
        arm_short = []
        arm_short += [self._event("distilled", "success") for _ in range(2)]
        arm_short += [self._event("distilled", "fail") for _ in range(2)]
        arm_short += [self._event("not_distilled", "success") for _ in range(30)]
        arm_short += [self._event("not_distilled", "fail") for _ in range(30)]
        result_arm = core.estimate_distill_uplift(events=arm_short)
        self.assertFalse(result_arm["upliftApplied"])
        self.assertEqual(result_arm["droppedStrata"], ["code_synthesis"])

    def test_phase_mapping(self) -> None:
        self.assertEqual(core.phase_from_uplift(uplift_shrunk=0.1), 0.0)
        self.assertEqual(core.phase_from_uplift(uplift_shrunk=-0.1), math.pi)
        self.assertEqual(core.phase_from_uplift(uplift_shrunk=0.0), 0.0)

    def test_inherited_utility(self) -> None:
        self.assertAlmostEqual(core.inherited_utility(apparent=0.7, uplift_shrunk=0.2), 0.5, places=6)

    def test_paired_rollout_estimate(self) -> None:
        events = [
            self._event("counterfactual_with", "success", pairId="p1"),
            self._event("counterfactual_without", "fail", pairId="p1"),
            self._event("counterfactual_with", "fail", pairId="p2"),
            self._event("counterfactual_without", "fail", pairId="p2"),
        ]
        result = core.estimate_distill_uplift(events=events)
        self.assertEqual(result["pairedPairs"], 2)
        self.assertEqual(result["pairedUplift"], 0.5)

    def test_simpson_construction_is_detected(self) -> None:
        events = []
        events += [self._event("distilled", "success", "code_synthesis") for _ in range(144)]
        events += [self._event("distilled", "fail", "code_synthesis") for _ in range(216)]
        events += [self._event("not_distilled", "success", "code_synthesis") for _ in range(8)]
        events += [self._event("not_distilled", "fail", "code_synthesis") for _ in range(32)]
        events += [self._event("distilled", "success", "multi_hop_qa") for _ in range(36)]
        events += [self._event("distilled", "fail", "multi_hop_qa") for _ in range(4)]
        events += [self._event("not_distilled", "success", "multi_hop_qa") for _ in range(216)]
        events += [self._event("not_distilled", "fail", "multi_hop_qa") for _ in range(144)]
        result = core.estimate_distill_uplift(events=events)
        self.assertLess(result["crude"], 0.0)
        self.assertGreater(result["uplift"], 0.0)
        self.assertTrue(result["simpsonFlip"])

    def test_determinism_across_runs(self) -> None:
        events = self._synthetic(70, 50, 0.7, 0.5, random.Random(0))
        first = json.dumps(core.estimate_distill_uplift(events=events), sort_keys=True)
        second = json.dumps(core.estimate_distill_uplift(events=events), sort_keys=True)
        self.assertEqual(first, second)


class DistillCausalRegressionTests(unittest.TestCase):
    """v0.8.0 causal distillation: gate/fields integration and zero regression."""

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

    def _negative_events(self) -> list[dict]:
        events: list[dict] = []
        events += [{"memoryId": "m1", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "distilled", "outcome": "fail", "stepIndex": 0, "modelVersion": "", "pairId": "", "createdAt": 0} for _ in range(90)]
        events += [{"memoryId": "m1", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "distilled", "outcome": "success", "stepIndex": 0, "modelVersion": "", "pairId": "", "createdAt": 0} for _ in range(10)]
        events += [{"memoryId": "m1", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "not_distilled", "outcome": "success", "stepIndex": 0, "modelVersion": "", "pairId": "", "createdAt": 0} for _ in range(90)]
        events += [{"memoryId": "m1", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "not_distilled", "outcome": "fail", "stepIndex": 0, "modelVersion": "", "pairId": "", "createdAt": 0} for _ in range(10)]
        return events

    def test_zero_regression_without_distill_events(self) -> None:
        result = core._make_memory(self.base_request())
        self.assertTrue(result["stored"])
        item = result["memories"][0]
        for field in ("distillUplift", "distillInherited", "distillSamples", "distillPhase", "distillGate", "distillEstimand", "distillEvidenceProvenance", "distillFalsificationTest"):
            self.assertNotIn(field, item)
        self.assertEqual(item["confidence"], 0.55)

    def test_negative_uplift_gate_rejects_new_write(self) -> None:
        result = core._make_memory(self.base_request(
            distillGateEnabled=True,
            distillUpliftThreshold=0.0,
            distillCausalEvents=self._negative_events(),
        ))
        self.assertFalse(result["stored"])
        self.assertIn("below_distill_uplift_gate", result["reason"])

    def test_positive_uplift_writes_fields_and_maps_confidence(self) -> None:
        events: list[dict] = []
        events += [{"memoryId": "m1", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "distilled", "outcome": "success", "stepIndex": 0, "modelVersion": "", "pairId": "", "createdAt": 0} for _ in range(70)]
        events += [{"memoryId": "m1", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "distilled", "outcome": "fail", "stepIndex": 0, "modelVersion": "", "pairId": "", "createdAt": 0} for _ in range(30)]
        events += [{"memoryId": "m1", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "not_distilled", "outcome": "success", "stepIndex": 0, "modelVersion": "", "pairId": "", "createdAt": 0} for _ in range(50)]
        events += [{"memoryId": "m1", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "not_distilled", "outcome": "fail", "stepIndex": 0, "modelVersion": "", "pairId": "", "createdAt": 0} for _ in range(50)]
        result = core._make_memory(self.base_request(
            distillGateEnabled=True,
            distillUpliftThreshold=0.0,
            distillCausalEvents=events,
        ))
        self.assertTrue(result["stored"])
        item = result["memories"][0]
        self.assertEqual(item["distillGate"], "passed")
        self.assertGreater(item["distillUplift"], 0.0)
        self.assertGreater(item["distillSamples"], 0)
        self.assertEqual(item["distillPhase"], 0.0)
        self.assertGreater(item["confidence"], 0.55)
        self.assertIn("do(distill m)", item["distillEstimand"])

    def test_insufficient_samples_keep_the_association_path(self) -> None:
        events = [
            {"memoryId": "m1", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "distilled", "outcome": "success", "stepIndex": 0, "modelVersion": "", "pairId": "", "createdAt": 0}
            for _ in range(5)
        ]
        result = core._make_memory(self.base_request(
            distillGateEnabled=True,
            distillCausalEvents=events,
        ))
        self.assertTrue(result["stored"])
        item = result["memories"][0]
        self.assertEqual(item["distillGate"], "na")
        self.assertEqual(item["distillUplift"], 0.0)
        self.assertEqual(item["confidence"], 0.55)


if __name__ == "__main__":
    unittest.main()
