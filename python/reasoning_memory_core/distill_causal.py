"""causal distillation uplift estimation for the reasoning-memory core.

Deterministic, stdlib-only estimation of the incremental contribution of
distilling one candidate (final spec 0.8.0): uplift(m) = apparent(m) -
inherited(m), where apparent is the library-with-m success rate and inherited
is the library-without-m success rate. This isolates MGPO-style "inherited
utility pollution" so a near-duplicate candidate with no incremental value can
be gated out ("learning what NOT to remember").

The estimator reuses the v0.6.0 stratified + shrinkage + Simpson machinery
(causal_utils), applied to the do(distill) treatment instead of do(retrieve).
No LLM, no gradients: pure counting plus arithmetic.
"""

from __future__ import annotations

import math
from typing import Any

from .constants import (
    DISTILL_ESTIMATOR_VERSION,
    DISTILL_UPLIFT_CAP,
    MIN_EFFECTIVE_N,
    TASK_PATTERNS,
)

_WALD = 1.96

# Observational events (distilled / not_distilled) feed the stratified, crude,
# and paired estimators; counterfactual pair arms feed the paired estimator.
_OBSERVATIONAL_TREATMENTS = {"distilled", "not_distilled"}
_TREATED = {"distilled", "counterfactual_with"}
_CONTROL = {"not_distilled", "counterfactual_without"}


def _round(value: float) -> float:
    return round(value, 6)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _laplace_rate(success: int, total: int) -> float:
    return (success + 1.0) / (total + 2.0)


def _stratum_tables(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    tables: dict[str, dict[str, int]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        treatment = event.get("treatment")
        if treatment not in _OBSERVATIONAL_TREATMENTS:
            continue
        pattern = event.get("taskPattern")
        if pattern not in TASK_PATTERNS:
            pattern = "general"
        table = tables.setdefault(pattern, {"n11": 0, "n10": 0, "n01": 0, "n00": 0})
        treated = treatment in _TREATED
        success = event.get("outcome") == "success"
        if treated and success:
            table["n11"] += 1
        elif treated:
            table["n10"] += 1
        elif success:
            table["n01"] += 1
        else:
            table["n00"] += 1
    return tables


def _stratum_estimate(table: dict[str, int], min_arm_samples: int) -> dict[str, float] | None:
    n1 = table["n11"] + table["n10"]
    n0 = table["n01"] + table["n00"]
    if n1 < min_arm_samples or n0 < min_arm_samples:
        return None
    y1 = _laplace_rate(table["n11"], n1)
    y0 = _laplace_rate(table["n01"], n0)
    uplift = y1 - y0
    variance = y1 * (1.0 - y1) / (n1 + 2.0) + y0 * (1.0 - y0) / (n0 + 2.0)
    return {"n1": n1, "n0": n0, "y1": y1, "y0": y0, "uplift": uplift, "variance": variance}


def _crude_estimate(events: list[dict[str, Any]]) -> dict[str, float] | None:
    treated_success = 0
    treated_total = 0
    control_success = 0
    control_total = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        treatment = event.get("treatment")
        if treatment not in _OBSERVATIONAL_TREATMENTS:
            continue
        success = event.get("outcome") == "success"
        if treatment in _TREATED:
            treated_total += 1
            if success:
                treated_success += 1
        else:
            control_total += 1
            if success:
                control_success += 1
    if treated_total == 0 or control_total == 0:
        return None
    y1 = _laplace_rate(treated_success, treated_total)
    y0 = _laplace_rate(control_success, control_total)
    variance = y1 * (1.0 - y1) / (treated_total + 2.0) + y0 * (1.0 - y0) / (control_total + 2.0)
    return {"crude": y1 - y0, "variance": variance, "n1": treated_total, "n0": control_total}


def _paired_estimate(events: list[dict[str, Any]]) -> tuple[float | None, int]:
    with_outcomes: dict[str, float] = {}
    without_outcomes: dict[str, float] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        treatment = event.get("treatment")
        pair_id = event.get("pairId")
        outcome = event.get("outcome")
        if not isinstance(pair_id, str) or not pair_id or outcome not in ("success", "fail"):
            continue
        value = 1.0 if outcome == "success" else 0.0
        if treatment == "counterfactual_with":
            with_outcomes.setdefault(pair_id, value)
        elif treatment == "counterfactual_without":
            without_outcomes.setdefault(pair_id, value)
    differences: list[float] = []
    for pair_id in sorted(with_outcomes):
        if pair_id in without_outcomes:
            differences.append(with_outcomes[pair_id] - without_outcomes[pair_id])
    if not differences:
        return None, 0
    return sum(differences) / len(differences), len(differences)


def estimate_distill_uplift(
    *,
    events: list[dict[str, Any]],
    shrink_prior: int = 20,
    min_arm_samples: int = 5,
) -> dict[str, Any]:
    """Estimate the incremental contribution uplift(m) from distill events.

    Returns a fully decomposed audit dict. applied gates whether the causal
    uplift may drive the gate/weight/phase; when false the caller keeps the
    v0.4.0 association path verbatim (zero regression).
    """
    tables = _stratum_tables(events)
    qualified: dict[str, dict[str, float]] = {}
    dropped: list[str] = []
    for pattern in sorted(tables):
        estimate = _stratum_estimate(tables[pattern], min_arm_samples)
        if estimate is None:
            dropped.append(pattern)
        else:
            qualified[pattern] = estimate

    n_eff = sum(int(estimate["n1"] + estimate["n0"]) for estimate in qualified.values())
    strata: list[dict[str, Any]] = []
    apparent = 0.0
    inherited = 0.0
    uplift = 0.0
    variance = 0.0
    if n_eff > 0:
        for pattern in sorted(qualified):
            estimate = qualified[pattern]
            weight = (estimate["n1"] + estimate["n0"]) / n_eff
            apparent += weight * estimate["y1"]
            inherited += weight * estimate["y0"]
            uplift += weight * estimate["uplift"]
            variance += weight * weight * estimate["variance"]
            strata.append({
                "stratum": pattern,
                "n1": int(estimate["n1"]),
                "n0": int(estimate["n0"]),
                "y1": _round(estimate["y1"]),
                "y0": _round(estimate["y0"]),
                "uplift": _round(estimate["uplift"]),
            })
    se = math.sqrt(max(0.0, variance))
    ci_lower = uplift - _WALD * se
    ci_upper = uplift + _WALD * se

    crude_result = _crude_estimate(events)
    crude = crude_result["crude"] if crude_result else 0.0
    crude_se = math.sqrt(max(0.0, crude_result["variance"])) if crude_result else 0.0

    simpson_flip = False
    if crude_result is not None and n_eff > 0:
        crude_significant = abs(crude) >= _WALD * crude_se
        stratified_significant = abs(uplift) >= _WALD * se
        simpson_flip = (
            (crude > 0.0) != (uplift > 0.0)
            and crude_significant
            and stratified_significant
        )

    paired_uplift, paired_pairs = _paired_estimate(events)

    applied = n_eff >= MIN_EFFECTIVE_N and bool(qualified)
    shrink = n_eff / (n_eff + shrink_prior) if n_eff > 0 else 0.0
    uplift_shrunk = shrink * uplift
    uplift_clipped = _clip(uplift_shrunk, -DISTILL_UPLIFT_CAP, DISTILL_UPLIFT_CAP)

    return {
        "upliftApplied": applied,
        "apparent": _round(apparent),
        "inherited": _round(inherited),
        "uplift": _round(uplift),
        "upliftVariance": _round(variance),
        "se": _round(se),
        "ciLower": _round(ci_lower),
        "ciUpper": _round(ci_upper),
        "upliftShrunk": _round(uplift_shrunk),
        "upliftClipped": _round(uplift_clipped),
        "crude": _round(crude),
        "crudeSe": _round(crude_se),
        "simpsonFlip": simpson_flip,
        "pairedUplift": _round(paired_uplift) if paired_uplift is not None else 0.0,
        "pairedPairs": paired_pairs,
        "nEff": n_eff,
        "cate": strata,
        "droppedStrata": dropped,
        "estimatorVersion": DISTILL_ESTIMATOR_VERSION,
        "reason": "" if applied else (
            f"n_eff {n_eff} is below the {MIN_EFFECTIVE_N} sample gate; the association path applies"
            if bool(qualified) else "no stratum reached the per-arm sample gate"
        ),
    }


def gate_distill(
    *,
    uplift_shrunk: float,
    threshold: float = 0.0,
    enabled: bool = True,
    applied: bool = False,
) -> dict[str, Any]:
    """Distill gate decision: pass / reject / not-applicable.

    The gate only REJECTS new writes; it never deletes existing entries.
    """
    if not enabled:
        return {"gate": "na", "reason": ""}
    if not applied:
        return {"gate": "na", "reason": "insufficient distill causal samples; the association path applies"}
    if uplift_shrunk < threshold:
        return {"gate": "rejected", "reason": "below_distill_uplift_gate"}
    return {"gate": "passed", "reason": ""}


def phase_from_uplift(*, uplift_shrunk: float) -> float:
    """Map the (shrunk) uplift sign to the v0.7.0 phase {0, pi}."""
    return 0.0 if uplift_shrunk >= 0.0 else math.pi


def inherited_utility(*, apparent: float, uplift_shrunk: float) -> float:
    """Inherited utility estimate: apparent - uplift (the incremental-remainder)."""
    return apparent - uplift_shrunk
