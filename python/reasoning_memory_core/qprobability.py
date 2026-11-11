"""quantum-probability primitives for the reasoning-memory core.

Deterministic, stdlib-only, real-amplitude superposition engine (final spec
0.7.0). Candidates are kept in a coherent superposition instead of being
argmax-collapsed to a single pick; interference, measurement (Lueders update),
and decoherence are first-class, auditable operations. Phases are restricted
to {0, pi} so every amplitude stays real and no complex arithmetic is needed.
No random, no numpy: every result is reproducible bit-for-bit.

Terminology follows design doc 21.7: "constructive/destructive" describes the
sign of the interference term relative to the marginal probability sum, NOT
closeness to success; success direction is carried by the phase itself
(theta=0 positive, theta=pi negative).
"""

from __future__ import annotations

import math
from typing import Any

from .constants import (
    INTERFERENCE_CAP,
    QUANTUM_ESTIMATOR_VERSION,
)

PHASE_TOLERANCE = 1e-12

_PI = math.pi


def _round(value: float) -> float:
    return round(value, 6)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _phase_sign(phase: float) -> int:
    """Map the {0, pi} phase to +1 / -1, raising on any other value."""
    if abs(phase) <= PHASE_TOLERANCE:
        return 1
    if abs(phase - _PI) <= PHASE_TOLERANCE:
        return -1
    raise ValueError(f"phase must be 0 or pi, got {phase!r}")


def interference_pair(*, r_i: float, r_j: float, phase_i: float, phase_j: float) -> float:
    """2*r_i*r_j*cos(phase_i - phase_j); pure four-arithmetic.

    For phases restricted to {0, pi} this is exactly 2*r_i*r_j*s_i*s_j where
    s = _phase_sign(phase), so the sign is exact (no floating-point cosine).
    """
    sign_i = _phase_sign(phase_i)
    sign_j = _phase_sign(phase_j)
    return 2.0 * r_i * r_j * sign_i * sign_j


def coherence(*, rho: list[list[float]]) -> float:
    """l1 coherence: sum of |rho[i][j]| over all off-diagonal entries.

    0 means fully decohered (classical); this is the design's "how much
    superposition is left uncollapsed" quantity.
    """
    total = 0.0
    for i, row in enumerate(rho):
        for j, value in enumerate(row):
            if i != j:
                total += abs(value)
    return total


def build_superposition(*, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a normalized real superposition and its density matrix.

    candidates: [{id, amplitude(r>=0), phase({0, pi})}]. Input order is
    preserved in the output (order is a treatment variable). Normalization is
    order-independent: magnitudes are summed first, then each r_i is divided
    by sqrt(sum r^2).

    Returns:
      superposition: per-candidate audit rows in input order with
        amplitude / phase / p(=amplitude^2) / interferenceDeltaRaw /
        interferenceDelta(capped) / coherence.
      densityMatrix: real symmetric rho_ij = phi_i * phi_j, trace ~= 1.
      coherence: l1 coherence of rho.
      trace: sum of diagonal entries (should be 1 for a non-degenerate state).
      estimatorVersion: frozen estimator version.
    """
    ids: list[str] = []
    signs: list[int] = []
    magnitudes: list[float] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("every candidate must be an object")
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("every candidate needs a non-empty string id")
        raw_amplitude = candidate.get("amplitude", 0.0)
        if not isinstance(raw_amplitude, (int, float)) or not math.isfinite(float(raw_amplitude)):
            raise ValueError(f"candidate {candidate_id!r} has a non-finite amplitude")
        amplitude = float(raw_amplitude)
        if amplitude < 0.0:
            raise ValueError(f"candidate {candidate_id!r} has a negative amplitude")
        raw_phase = candidate.get("phase", 0.0)
        if not isinstance(raw_phase, (int, float)) or not math.isfinite(float(raw_phase)):
            raise ValueError(f"candidate {candidate_id!r} has a non-finite phase")
        phase = float(raw_phase)
        sign = _phase_sign(phase)
        ids.append(candidate_id)
        signs.append(sign)
        magnitudes.append(amplitude)

    size = len(ids)
    if size == 0:
        return {
            "superposition": [],
            "densityMatrix": [],
            "coherence": 0.0,
            "trace": 0.0,
            "estimatorVersion": QUANTUM_ESTIMATOR_VERSION,
        }

    total = sum(value * value for value in magnitudes)
    if total <= 0.0:
        # All-zero amplitudes: return the degenerate state without dividing.
        normalized = [0.0] * size
        phi = [0.0] * size
        rho = [[0.0] * size for _ in range(size)]
    else:
        scale = math.sqrt(total)
        normalized = [value / scale for value in magnitudes]
        phi = [normalized[i] * signs[i] for i in range(size)]
        rho = [[phi[i] * phi[j] for j in range(size)] for i in range(size)]

    rows: list[dict[str, Any]] = []
    for i in range(size):
        amplitude = _round(normalized[i])
        probability = _round(normalized[i] * normalized[i])
        interference_raw = normalized[i] * signs[i] * sum(
            normalized[j] * signs[j] for j in range(size) if j != i
        )
        candidate_coherence = sum(abs(rho[i][j]) for j in range(size) if j != i)
        rows.append({
            "id": ids[i],
            "amplitude": amplitude,
            "phase": 0.0 if signs[i] > 0 else _PI,
            "p": probability,
            "interferenceDeltaRaw": _round(interference_raw),
            "interferenceDelta": _round(_clip(interference_raw, -INTERFERENCE_CAP, INTERFERENCE_CAP)),
            "coherence": _round(candidate_coherence),
        })

    return {
        "superposition": rows,
        "densityMatrix": [[_round(value) for value in row] for row in rho],
        "coherence": _round(coherence(rho=rho)),
        "trace": _round(sum(rho[i][i] for i in range(size))),
        "estimatorVersion": QUANTUM_ESTIMATOR_VERSION,
    }


def decoherence(*, rho: list[list[float]], rate: float) -> dict[str, Any]:
    """Deterministic decoherence channel: rho -> (1-rate)*rho + rate*diag(rho).

    rate is clipped to [0, 1]. Diagonal entries are unchanged; off-diagonal
    entries are scaled by (1-rate). At rate=1 the state is fully decohered
    (off-diagonal zero, coherence zero) = the v0.6.0 classical-causal limit.
    """
    clipped_rate = _clip(float(rate), 0.0, 1.0)
    size = len(rho)
    coherence_before = coherence(rho=rho)
    updated: list[list[float]] = []
    for i in range(size):
        row = rho[i]
        updated.append([
            row[j] if i == j else (1.0 - clipped_rate) * row[j]
            for j in range(size)
        ])
    return {
        "densityMatrix": [[_round(value) for value in row] for row in updated],
        "coherenceBefore": _round(coherence_before),
        "coherenceAfter": _round(coherence(rho=updated)),
    }


def luders_update(*, rho: list[list[float]], ids: list[str], observed_ids: list[str]) -> dict[str, Any]:
    """Lueders partial measurement: keep coherence inside each subspace, kill it
    between the observed and unobserved subspaces (the "re-measure" primitive).
    """
    observed = set(observed_ids)
    size = len(rho)
    observed_indices = {index for index in range(size) if ids[index] in observed}
    coherence_before = coherence(rho=rho)
    updated: list[list[float]] = []
    for i in range(size):
        row = rho[i]
        updated_row: list[float] = []
        for j in range(size):
            if (i in observed_indices) == (j in observed_indices):
                updated_row.append(row[j])
            else:
                updated_row.append(0.0)
        updated.append(updated_row)
    return {
        "densityMatrix": [[_round(value) for value in row] for row in updated],
        "coherenceBefore": _round(coherence_before),
        "coherenceAfter": _round(coherence(rho=updated)),
    }
