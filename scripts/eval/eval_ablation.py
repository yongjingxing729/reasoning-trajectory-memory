"""Channel ablation runner for the offline evaluation harness.

Arms follow MVP_EVALUATION.md section 6 and 04-eval-schema.md section 6:
none / semantic / structure / graph / template / fusion. Channel control is
exercised through the core's fusionWeightsOverride entry point, so the same
code path the plugin uses is what gets ablated. The second template RRF arm
(structure 0.05 / template 0.20 from 02-template-algorithms.md 3.1) is
included as a comparison row.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from eval_retrieval import evaluate_retrieval  # noqa: E402

# (arm name, weighted-mode channel weights, bind gold template)
ABLATION_ARMS = (
    ("none", {"context": 0.0, "reasoning": 0.0, "structure": 0.0, "graph": 0.0, "template": 0.0}, False),
    ("semantic", {"context": 0.30, "reasoning": 0.70, "structure": 0.0, "graph": 0.0, "template": 0.0}, False),
    ("structure", {"structure": 1.0}, False),
    ("graph", {"graph": 1.0}, False),
    ("template", {"template": 1.0}, True),
    ("fusion", None, True),
    ("fusion_template_v2", {
        "lexical": 0.25, "reasoning": 0.10, "vector": 0.30,
        "structure": 0.05, "graph": 0.10, "template": 0.20,
    }, True),
)


def run_ablations(
    episodes: list[dict[str, Any]],
    gold_templates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for name, weights, bind_template in ABLATION_ARMS:
        metrics = evaluate_retrieval(
            episodes,
            weights_override=weights,
            bind_gold_template=bind_template,
            gold_templates=gold_templates,
        )
        rows.append({
            "arm": name,
            "recall": metrics["recall"],
            "recallSuccess": metrics["recallSuccess"],
            "recallFail": metrics["recallFail"],
            "ndcg": metrics["ndcg"],
            "ndcgSuccess": metrics["ndcgSuccess"],
            "ndcgFail": metrics["ndcgFail"],
            "misleadingRetrieved": metrics["misleadingRetrieved"],
            "selfRetrievalRate": metrics["selfRetrievalRate"],
            "quotaAdherence": metrics["quotaAdherence"],
        })
    return {"rows": rows}


def render_ablation_markdown(result: dict[str, Any]) -> str:
    lines = [
        "| arm | recall | recall(success) | recall(fail) | nDCG | nDCG(success) | nDCG(fail) | misleading | self-retrieval | quota |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['arm']} | {row['recall']} | {row['recallSuccess']} | {row['recallFail']} "
            f"| {row['ndcg']} | {row['ndcgSuccess']} | {row['ndcgFail']} "
            f"| {row['misleadingRetrieved']} | {row['selfRetrievalRate']} | {row['quotaAdherence']} |",
        )
    return "\n".join(lines)
