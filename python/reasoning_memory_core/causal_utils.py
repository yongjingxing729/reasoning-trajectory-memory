"""causal utility estimation for the reasoning-memory core.

Deterministic, stdlib-only stratified effect estimation (final spec 0.6.0):
per-stratum 2x2 tables with Laplace smoothing, the backdoor-style stratified
ATE, per-stratum CATE, an IPTW robustness check (audit only), paired
continuation estimation, Simpson's-paradox detection, and conservative
shrinkage toward no effect. No LLM, no structure discovery, no gradients.

Observational events (treatment used / retrieved_unused) feed the stratified,
crude, and IPTW estimators; counterfactual pair events feed the paired
estimator only. Strata come from each event's recorded taskPattern.
"""

from __future__ import annotations

import math
from typing import Any

from .constants import (
    CAUSAL_ESTIMATOR_VERSION,
    CAUSAL_MULTIPLIER_CAP,
    CAUSAL_TREATMENTS,
    IPTW_DISAGREEMENT,
    IPTW_W_MAX,
    IPTW_W_MIN,
    MIN_ARM_SAMPLES,
    MIN_EFFECTIVE_N,
    SHRINK_PRIOR,
    TASK_PATTERNS,
)

_WALD = 1.96

# Only observational events enter the stratified/crude/IPTW estimators; the
# counterfactual pair arms feed the paired estimator exclusively.
_OBSERVATIONAL_TREATMENTS = {"used", "retrieved_unused"}


def _round(value: float) -> float:
    return round(value, 6)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _stratum_tables(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per-taskPattern 2x2 counts over OBSERVATIONAL events only."""
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
        treated = treatment in CAUSAL_TREATMENTS
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


def _laplace_rate(success: int, total: int) -> float:
    return (success + 1.0) / (total + 2.0)


def _stratum_estimate(table: dict[str, int]) -> dict[str, float] | None:
    n1 = table["n11"] + table["n10"]
    n0 = table["n01"] + table["n00"]
    if n1 < MIN_ARM_SAMPLES or n0 < MIN_ARM_SAMPLES:
        return None
    y1 = _laplace_rate(table["n11"], n1)
    y0 = _laplace_rate(table["n01"], n0)
    ate = y1 - y0
    variance = (
        y1 * (1.0 - y1) / (n1 + 2.0)
        + y0 * (1.0 - y0) / (n0 + 2.0)
    )
    return {"n1": n1, "n0": n0, "y1": y1, "y0": y0, "ate": ate, "variance": variance}


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
        if treatment in CAUSAL_TREATMENTS:
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
    return {
        "crude": y1 - y0,
        "variance": variance,
        "n1": treated_total,
        "n0": control_total,
    }


def _iptw_estimate(tables: dict[str, dict[str, int]]) -> tuple[float | None, int, int]:
    """Stratum-empirical propensity IPTW (audit-only robustness check).

    Returns (HT estimate, strata used, strata whose weights hit the clip
    bounds), so extreme-propensity strata stay auditable.
    """
    weighted_treated = 0.0
    weight_sum_treated = 0.0
    weighted_control = 0.0
    weight_sum_control = 0.0
    used = 0
    clipped = 0
    for table in tables.values():
        n1 = table["n11"] + table["n10"]
        n0 = table["n01"] + table["n00"]
        total = n1 + n0
        if total == 0:
            continue
        propensity = (n1 + 1.0) / (total + 2.0)
        raw_treated = 1.0 / propensity
        raw_control = 1.0 / (1.0 - propensity)
        w_treated = _clip(raw_treated, IPTW_W_MIN, IPTW_W_MAX)
        w_control = _clip(raw_control, IPTW_W_MIN, IPTW_W_MAX)
        if w_treated != raw_treated or w_control != raw_control:
            clipped += 1
        treated_success = table["n11"]
        control_success = table["n01"]
        weighted_treated += w_treated * treated_success
        weight_sum_treated += w_treated * n1
        weighted_control += w_control * control_success
        weight_sum_control += w_control * n0
        used += 1
    if weight_sum_treated == 0.0 or weight_sum_control == 0.0:
        return None, used, clipped
    return (weighted_treated / weight_sum_treated) - (weighted_control / weight_sum_control), used, clipped


def _paired_estimate(events: list[dict[str, Any]]) -> tuple[float | None, int]:
    """Paired continuation estimate over complete counterfactual pairs."""
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


def causal_estimate(*, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate the causal effect of one unit from its recorded events.

    Returns a fully decomposed audit dict. causalApplied gates whether the
    causal multiplier may replace the legacy Beta multiplier; when false the
    caller keeps the v0.5.0 behavior verbatim.
    """
    tables = _stratum_tables(events)
    qualified: dict[str, dict[str, float]] = {}
    dropped: list[str] = []
    for pattern in sorted(tables):
        estimate = _stratum_estimate(tables[pattern])
        if estimate is None:
            dropped.append(pattern)
        else:
            qualified[pattern] = estimate

    n_eff = sum(int(estimate["n1"] + estimate["n0"]) for estimate in qualified.values())
    strata: list[dict[str, Any]] = []
    for pattern in sorted(qualified):
        estimate = qualified[pattern]
        strata.append({
            "stratum": pattern,
            "n1": int(estimate["n1"]),
            "n0": int(estimate["n0"]),
            "y1": _round(estimate["y1"]),
            "y0": _round(estimate["y0"]),
            "cate": _round(estimate["ate"]),
        })

    ate = 0.0
    variance = 0.0
    if n_eff > 0:
        for pattern in sorted(qualified):
            estimate = qualified[pattern]
            weight = (estimate["n1"] + estimate["n0"]) / n_eff
            ate += weight * estimate["ate"]
            variance += weight * weight * estimate["variance"]
    se = math.sqrt(max(0.0, variance))
    ci_lower = ate - _WALD * se
    ci_upper = ate + _WALD * se

    crude_result = _crude_estimate(events)
    crude = crude_result["crude"] if crude_result else 0.0
    crude_se = math.sqrt(max(0.0, crude_result["variance"])) if crude_result else 0.0

    simpson_flip = False
    if crude_result is not None and n_eff > 0:
        crude_significant = abs(crude) >= _WALD * crude_se
        stratified_significant = abs(ate) >= _WALD * se
        simpson_flip = (
            (crude > 0.0) != (ate > 0.0)
            and crude_significant
            and stratified_significant
        )

    iptw_ate, _, iptw_clip_count = _iptw_estimate(tables)
    iptw_disagreement = (
        iptw_ate is not None and abs(iptw_ate - ate) > IPTW_DISAGREEMENT
    )

    paired_ate, paired_pairs = _paired_estimate(events)

    applied = n_eff >= MIN_EFFECTIVE_N and bool(qualified)
    shrink = n_eff / (n_eff + SHRINK_PRIOR) if n_eff > 0 else 0.0
    ate_shrunk = shrink * ate
    multiplier = 1.0 + _clip(ate_shrunk, -CAUSAL_MULTIPLIER_CAP, CAUSAL_MULTIPLIER_CAP)

    treated_total = sum(int(table["n11"] + table["n10"]) for table in tables.values())
    control_total = sum(int(table["n01"] + table["n00"]) for table in tables.values())

    return {
        "causalApplied": applied,
        "causalMultiplier": _round(multiplier),
        "ate": _round(ate),
        "ateVariance": _round(variance),
        "se": _round(se),
        "ciLower": _round(ci_lower),
        "ciUpper": _round(ci_upper),
        "ateShrunk": _round(ate_shrunk),
        "crude": _round(crude),
        "crudeSe": _round(crude_se),
        "simpsonFlip": simpson_flip,
        "iptwAte": _round(iptw_ate) if iptw_ate is not None else 0.0,
        "iptwDisagreement": iptw_disagreement,
        "iptwClipCount": iptw_clip_count,
        "pairedAte": _round(paired_ate) if paired_ate is not None else 0.0,
        "pairedPairs": paired_pairs,
        "nEff": n_eff,
        "nTreated": treated_total,
        "nControl": control_total,
        "cate": strata,
        "droppedStrata": dropped,
        "estimatorVersion": CAUSAL_ESTIMATOR_VERSION,
        "reason": "" if applied else (
            f"n_eff {n_eff} is below the {MIN_EFFECTIVE_N} sample gate; the Beta utility fallback applies"
            if bool(qualified) else "no stratum reached the per-arm sample gate"
        ),
    }


def batch_causal_estimates(*, events_by_unit: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Batch form: one audit dict per unit key."""
    return {
        "estimates": {
            unit: causal_estimate(events=events)
            for unit, events in sorted(events_by_unit.items())
        },
    }
