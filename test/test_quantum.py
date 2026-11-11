"""Quantum-probability scoring and its regression/consistency checks."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

class QuantumProbabilityTests(unittest.TestCase):
    """v0.7.0 multi-probability engine: pure-primitive and injection checks."""

    def _candidate(self, cid: str, amplitude: float, phase: float = 0.0) -> dict:
        return {"id": cid, "amplitude": amplitude, "phase": phase}

    def test_interference_pair_signs(self) -> None:
        # G4.1: same phase cos(0)=+1, opposite cos(pi)=-1, double-negative cos(0)=+1.
        self.assertAlmostEqual(core.interference_pair(r_i=0.5, r_j=0.5, phase_i=0.0, phase_j=0.0), 0.5, places=6)
        self.assertAlmostEqual(core.interference_pair(r_i=0.5, r_j=0.5, phase_i=0.0, phase_j=math.pi), -0.5, places=6)
        self.assertAlmostEqual(core.interference_pair(r_i=0.5, r_j=0.5, phase_i=math.pi, phase_j=math.pi), 0.5, places=6)
        # G5.2 symmetry.
        self.assertEqual(
            core.interference_pair(r_i=0.3, r_j=0.4, phase_i=0.0, phase_j=math.pi),
            core.interference_pair(r_i=0.4, r_j=0.3, phase_i=math.pi, phase_j=0.0),
        )

    def test_build_superposition_preserves_amplitude_and_born_normalization(self) -> None:
        # G2.2: k=1 collapses to a classical point.
        single = core.build_superposition(candidates=[self._candidate("a", 3.0, 0.0)])
        self.assertEqual(single["superposition"][0]["p"], 1.0)
        self.assertEqual(single["superposition"][0]["interferenceDeltaRaw"], 0.0)
        self.assertEqual(single["coherence"], 0.0)

        # G2.2: k=2 keeps the second amplitude and normalizes to sum p = 1.
        pair = core.build_superposition(candidates=[
            self._candidate("a", 3.0, 0.0), self._candidate("b", 1.0, 0.0),
        ])
        self.assertGreater(pair["superposition"][1]["amplitude"], 0.0)
        self.assertAlmostEqual(sum(row["p"] for row in pair["superposition"]), 1.0, places=5)

    def test_collapse_vs_superposition_preserves_top1_marginal(self) -> None:
        # G2.3: collapsing to a single candidate reaches the classical point;
        # keeping the superposition preserves the runner-up probability mass
        # instead of collapsing it to zero, without demoting the top-1.
        collapsed = core.build_superposition(candidates=[self._candidate("a", 3.0, 0.0)])
        kept = core.build_superposition(candidates=[
            self._candidate("a", 3.0, 0.0), self._candidate("b", 1.0, 0.0),
        ])
        self.assertEqual(len(collapsed["superposition"]), 1)
        self.assertEqual(collapsed["coherence"], 0.0)
        self.assertEqual(len(kept["superposition"]), 2)
        self.assertGreater(kept["coherence"], 0.0)
        # The runner-up keeps non-zero probability mass (not collapsed to 0).
        self.assertGreater(kept["superposition"][1]["p"], 0.0)
        # The top-1 keeps its rank.
        self.assertEqual(kept["superposition"][0]["id"], "a")
        self.assertAlmostEqual(
            kept["superposition"][0]["p"] + kept["superposition"][1]["p"], 1.0, places=5,
        )

    def test_build_superposition_preserves_input_order(self) -> None:
        # G3.2: output order == input order; the density matrix is symmetric.
        candidates = [
            self._candidate("a", 3.0, 0.0), self._candidate("b", 2.0, math.pi), self._candidate("c", 1.0, 0.0),
        ]
        result = core.build_superposition(candidates=candidates)
        self.assertEqual([row["id"] for row in result["superposition"]], ["a", "b", "c"])
        rho = result["densityMatrix"]
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(rho[i][j], rho[j][i], places=6)

    def test_aggregation_order_independence(self) -> None:
        # G5.2: different permutations give an equal canonical density matrix
        # (sorted by id), equal coherence, and trace 1.
        base = [("a", 3.0, 0.0), ("b", 2.0, math.pi), ("c", 1.0, 0.0)]
        permuted = [base[1], base[2], base[0]]
        first = core.build_superposition(candidates=[self._candidate(i, a, p) for i, a, p in base])
        second = core.build_superposition(candidates=[self._candidate(i, a, p) for i, a, p in permuted])
        self.assertAlmostEqual(first["coherence"], second["coherence"], places=6)
        self.assertAlmostEqual(first["trace"], 1.0, places=6)
        self.assertAlmostEqual(second["trace"], 1.0, places=6)
        # Canonical form: the id-sorted diagonal probabilities are identical.
        first_p = {row["id"]: row["p"] for row in first["superposition"]}
        second_p = {row["id"]: row["p"] for row in second["superposition"]}
        self.assertEqual(first_p, second_p)

    def test_real_domain_only(self) -> None:
        # G5.3: phases in {0, pi}, no complex values, p == amplitude^2.
        result = core.build_superposition(candidates=[
            self._candidate("a", 3.0, 0.0), self._candidate("b", 2.0, math.pi),
        ])
        for row in result["superposition"]:
            self.assertIn(row["phase"], (0.0, math.pi))
            self.assertAlmostEqual(row["p"], row["amplitude"] ** 2, places=5)
            self.assertIsInstance(row["amplitude"], float)
            self.assertNotIsInstance(row["amplitude"], complex)

    def test_no_random_or_numpy(self) -> None:
        # G5.1: the module is stdlib-only.
        source = Path(core.__file__).with_name("reasoning_memory_core").joinpath("qprobability.py")
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("import random", text)
        self.assertNotIn("import numpy", text)

    def test_determinism_double_run(self) -> None:
        # G5.1: every primitive is bit-identical across runs.
        rho = [[1.0, 0.5], [0.5, 1.0]]
        ids = ["a", "b"]
        first = {
            "pair": core.interference_pair(r_i=0.5, r_j=0.5, phase_i=0.0, phase_j=math.pi),
            "coherence": core.coherence(rho=rho),
            "decoherence": core.decoherence(rho=rho, rate=0.5),
            "luders": core.luders_update(rho=rho, ids=ids, observed_ids=["a"]),
            "build": core.build_superposition(candidates=[self._candidate("a", 3.0, 0.0), self._candidate("b", 2.0, math.pi)]),
        }
        second = {
            "pair": core.interference_pair(r_i=0.5, r_j=0.5, phase_i=0.0, phase_j=math.pi),
            "coherence": core.coherence(rho=rho),
            "decoherence": core.decoherence(rho=rho, rate=0.5),
            "luders": core.luders_update(rho=rho, ids=ids, observed_ids=["a"]),
            "build": core.build_superposition(candidates=[self._candidate("a", 3.0, 0.0), self._candidate("b", 2.0, math.pi)]),
        }
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_decoherence_rate_one_reaches_the_classical_limit(self) -> None:
        # G6.2: rate=1 zeroes off-diagonals and coherence.
        rho = [[1.0, 0.5, 0.25], [0.5, 1.0, 0.5], [0.25, 0.5, 1.0]]
        result = core.decoherence(rho=rho, rate=1.0)
        updated = result["densityMatrix"]
        for i in range(3):
            for j in range(3):
                if i != j:
                    self.assertEqual(updated[i][j], 0.0)
        self.assertEqual(result["coherenceAfter"], 0.0)
        self.assertGreater(result["coherenceBefore"], 0.0)

    def test_decoherence_rate_zero_is_identity(self) -> None:
        rho = [[1.0, 0.5], [0.5, 1.0]]
        result = core.decoherence(rho=rho, rate=0.0)
        self.assertEqual(result["densityMatrix"], rho)
        self.assertEqual(result["coherenceBefore"], result["coherenceAfter"])

    def test_luders_update_preserves_unobserved_coherence(self) -> None:
        # Partial measurement collapses coherence between subspaces only.
        rho = [[1.0, 0.5, 0.5], [0.5, 1.0, 0.5], [0.5, 0.5, 1.0]]
        result = core.luders_update(rho=rho, ids=["a", "b", "c"], observed_ids=["a"])
        updated = result["densityMatrix"]
        # Cross-subspace entries vanish.
        self.assertEqual(updated[0][1], 0.0)
        self.assertEqual(updated[0][2], 0.0)
        self.assertEqual(updated[1][0], 0.0)
        self.assertEqual(updated[2][0], 0.0)
        # Unobserved subspace keeps its coherence.
        self.assertEqual(updated[1][2], 0.5)
        self.assertLess(result["coherenceAfter"], result["coherenceBefore"])

    def test_invalid_phase_raises(self) -> None:
        # G6.5: phases outside {0, pi} raise (fail loudly, deterministic).
        with self.assertRaises(ValueError):
            core.build_superposition(candidates=[self._candidate("a", 1.0, 0.5)])
        with self.assertRaises(ValueError):
            core.interference_pair(r_i=1.0, r_j=1.0, phase_i=0.5, phase_j=0.0)

    def test_all_zero_amplitudes_return_degenerate_state(self) -> None:
        # G6.5: no division by zero, no NaN/Inf.
        result = core.build_superposition(candidates=[self._candidate("a", 0.0), self._candidate("b", 0.0)])
        for row in result["superposition"]:
            self.assertEqual(row["amplitude"], 0.0)
            self.assertEqual(row["p"], 0.0)
            self.assertEqual(row["interferenceDeltaRaw"], 0.0)
            self.assertTrue(math.isfinite(row["interferenceDelta"]))
        self.assertEqual(result["coherence"], 0.0)

    def test_empty_candidates(self) -> None:
        # G6.5: empty list -> empty superposition, no exception.
        result = core.build_superposition(candidates=[])
        self.assertEqual(result["superposition"], [])
        self.assertEqual(result["densityMatrix"], [])
        self.assertEqual(result["coherence"], 0.0)

    def test_numerical_stability(self) -> None:
        # G6.6: near-degenerate amplitudes keep rho_ii >= 0, |rho_ij| bounded,
        # trace ~= 1, p >= 0, all finite.
        candidates = [
            self._candidate("a", 1.0, 0.0),
            self._candidate("b", 1.0 + 1e-12, math.pi),
            self._candidate("c", 1.0, 0.0),
        ]
        result = core.build_superposition(candidates=candidates)
        rho = result["densityMatrix"]
        for i in range(3):
            self.assertGreaterEqual(rho[i][i], 0.0)
            for j in range(3):
                self.assertLessEqual(abs(rho[i][j]), math.sqrt(rho[i][i] * rho[j][j]) + 1e-6)
                self.assertTrue(math.isfinite(rho[i][j]))
        self.assertAlmostEqual(result["trace"], 1.0, places=6)
        self.assertTrue(all(row["p"] >= 0.0 for row in result["superposition"]))

    def test_interference_delta_is_capped(self) -> None:
        # G6.7: raw interference may exceed the band; the emitted value is
        # clipped to +-0.20.
        result = core.build_superposition(candidates=[
            self._candidate("a", 1.0, 0.0),
            self._candidate("b", 1.0, 0.0),
        ])
        raw = result["superposition"][0]["interferenceDeltaRaw"]
        capped = result["superposition"][0]["interferenceDelta"]
        self.assertAlmostEqual(raw, 0.5, places=6)  # 2 * 0.707 * 0.707 = 1 -> per-candidate 0.5
        self.assertEqual(capped, 0.20)


class QuantumRegressionTests(unittest.TestCase):
    """v0.7.0 zero-regression: the quantum path must be byte-absent when off."""

    def _sha(self, result: dict) -> str:
        return hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def test_retrieve_zero_regression_when_disabled(self) -> None:
        # G1.1: default and explicit false both match the frozen v0.6.0 baseline
        # and carry no `superposition` key.
        memories = [
            make_memory("p1", "success"),
            make_memory("p2", "success", context="an unrelated browser workflow", reasoning="prefer a quiet interface and avoid needless decoration"),
            make_memory("n1", "fail", context="deploy without verifying", reasoning="deploy without verifying the schema"),
            make_memory("n2", "fail", context="skip migration validation", reasoning="skip migration validation"),
        ]
        base_request = {
            "operation": "retrieve", "query": "validate schema before publishing", "events": [],
            "memories": memories, "k": 4, "route": "auto", "positiveNegativeRatio": 1.0,
            "excludeTaskId": "active-task",
        }
        default = core._retrieve(base_request)
        explicit = core._retrieve({**base_request, "quantumDecisionEnabled": False})
        self.assertNotIn("superposition", default)
        self.assertNotIn("superposition", explicit)
        self.assertEqual(self._sha(default), "23fd07c3123fa20af20ea40ccf66ea1ca40e46fa22f67f0d944443f23c646fc4")
        self.assertEqual(self._sha(explicit), self._sha(default))

    def test_plan_task_zero_regression_when_disabled(self) -> None:
        # G1.2: plan_task unchanged when the quantum path is off.
        templates = core.builtin_template_records(now_ms=1700000000000)
        kwargs = dict(
            task_desc="重构解析函数并跑测试", goal="通过全部单元测试", task_family="code",
            events=tool_events(), has_graph=False, graph_node_kinds=[], templates=templates,
            max_templates=2, template_match_min=0.55,
        )
        baseline = core.plan_task(**kwargs)
        explicit = core.plan_task(**kwargs, quantum_decision_enabled=False)
        self.assertNotIn("superposition", baseline)
        self.assertNotIn("superposition", explicit)
        self.assertEqual(self._sha(baseline), "bbeb4489fb3dee188ce1ae584eb90dafce43648b7c9db4d3d1b1f7398fa7da05")
        self.assertEqual(self._sha(explicit), self._sha(baseline))

    def test_old_media_readback(self) -> None:
        # G1.3: memory records without the new fields retrieve without KeyError.
        memories = [
            make_memory("legacy-1", "success", reasoning="validate schema before publishing"),
            make_memory("legacy-2", "fail", reasoning="skip validation"),
        ]
        result = core._retrieve({
            "operation": "retrieve", "query": "validate schema before publishing", "events": [],
            "memories": memories, "k": 2, "route": "auto", "positiveNegativeRatio": 1.0,
            "excludeTaskId": "active-task",
        })
        self.assertTrue(result["results"])

    def test_retrieve_superposition_injection_when_enabled(self) -> None:
        # G2.4 (Python side): enabled path emits the six-key contract in order.
        memories = [
            {**make_memory("p1", "success", reasoning="validate database schema"), "lexicalScore": 1.0, "vectorScore": 0.82},
            {**make_memory("p2", "success", reasoning="check schema before deploy"), "lexicalScore": 0.55, "vectorScore": 0.91},
        ]
        result = core._retrieve({
            "operation": "retrieve", "query": "validate schema", "events": [], "memories": memories,
            "k": 2, "route": "auto", "positiveNegativeRatio": 10.0, "excludeTaskId": "active-task",
            "quantumDecisionEnabled": True,
        })
        self.assertIn("superposition", result)
        self.assertIn("quantum", result)
        entry = result["superposition"][0]
        for key in ("id", "amplitude", "phase", "p", "interferenceDelta", "coherence"):
            self.assertIn(key, entry)
        self.assertEqual([row["id"] for row in result["superposition"]], [row["memoryId"] for row in result["results"]])

    def test_superposition_size_cap_marks_rank_cutoff(self) -> None:
        # G6.1: five candidates collapse to at most 3 with rank_cutoff audit.
        memories = [
            {**make_memory(f"m{i}", "success", reasoning=f"validate schema variant {i}"), "lexicalScore": 1.0 - i * 0.05, "vectorScore": 0.9}
            for i in range(5)
        ]
        result = core._retrieve({
            "operation": "retrieve", "query": "validate schema", "events": [], "memories": memories,
            "k": 5, "route": "auto", "positiveNegativeRatio": 10.0, "excludeTaskId": "active-task",
            "quantumDecisionEnabled": True, "maxSuperpositionSize": 3,
        })
        self.assertLessEqual(len(result["superposition"]), 3)
        self.assertEqual(result["quantum"]["collapseReason"], "rank_cutoff")
        self.assertLessEqual(result["quantum"]["coherenceAfter"], result["quantum"]["coherenceBefore"])

    def test_negative_causal_phase_still_keeps_candidate(self) -> None:
        # G6.4: a negative-ATE candidate gets phase pi and is NOT deleted.
        causal_events = []
        for _ in range(90):
            causal_events.append({"memoryId": "m1", "kind": "memory", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "used", "outcome": "fail", "stepIndex": 0, "retrievalIntent": "", "modelVersion": "", "pairId": "", "createdAt": 0})
        for _ in range(10):
            causal_events.append({"memoryId": "m1", "kind": "memory", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "used", "outcome": "success", "stepIndex": 0, "retrievalIntent": "", "modelVersion": "", "pairId": "", "createdAt": 0})
        for _ in range(90):
            causal_events.append({"memoryId": "m1", "kind": "memory", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "retrieved_unused", "outcome": "success", "stepIndex": 0, "retrievalIntent": "", "modelVersion": "", "pairId": "", "createdAt": 0})
        for _ in range(10):
            causal_events.append({"memoryId": "m1", "kind": "memory", "taskId": "t", "taskPattern": "code_synthesis", "treatment": "retrieved_unused", "outcome": "fail", "stepIndex": 0, "retrievalIntent": "", "modelVersion": "", "pairId": "", "createdAt": 0})
        memories = [{
            "memoryId": "m1", "outcome": "success", "semanticText": "重构解析函数", "reasoning": "先定位失败点再修改",
            "structureSignature": [], "reasoningGraph": {"version": 1, "nodes": [], "edges": []},
            "feedback": {"helpful": 0, "harmful": 0, "neutral": 0}, "provenance": {"taskId": "prior-1"},
            "lexicalScore": 0.0, "vectorScore": 0.0, "strategyTemplateId": "", "status": "active",
        }]
        result = core._retrieve({
            "operation": "retrieve", "query": "重构", "k": 1, "route": "semantic", "memories": memories,
            "causalEventsByMemory": {"m1": causal_events}, "quantumDecisionEnabled": True,
        })
        self.assertEqual(result["superposition"][0]["phase"], math.pi)
        self.assertEqual(len(result["results"]), 1)


if __name__ == "__main__":
    unittest.main()
