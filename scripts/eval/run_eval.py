"""Offline evaluation CLI.

Deterministic, stdlib-only, network-free. Defaults to generating the seeded
synthetic dataset in memory, validating it against the episode schema, running
retrieval and template metrics plus channel ablations, and writing a Markdown
sample report.

Usage:
    python scripts/eval/run_eval.py [--seed 42] [--episodes-per-family 8]
                                    [--data PATH] [--write-data PATH]
                                    [--out docs/eval/sample-report.md]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.eval.eval_ablation import render_ablation_markdown, run_ablations
from scripts.eval.eval_dataset import EpisodeGenerator, load_jsonl, write_jsonl
from scripts.eval.eval_retrieval import evaluate_retrieval
from scripts.eval.eval_templates import (
    check_degenerate_scenarios,
    evaluate_classification,
    evaluate_matching,
    evaluate_mining,
)
from scripts.eval.validate_schema import validate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the offline reasoning-memory evaluation.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for the synthetic generator.")
    parser.add_argument("--episodes-per-family", type=int, default=8, help="Episodes per synthetic family (>= 6).")
    parser.add_argument("--data", type=str, default="", help="Evaluate an existing JSONL dataset instead of generating one.")
    parser.add_argument("--write-data", type=str, default="", help="Also write the generated JSONL to this path.")
    parser.add_argument("--out", type=str, default=str(ROOT / "docs" / "eval" / "sample-report.md"), help="Markdown report path.")
    return parser.parse_args()


def load_episodes(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if args.data:
        lines = load_jsonl(args.data)
        return [line["value"] for line in lines], []
    generator = EpisodeGenerator(seed=args.seed, episodes_per_family=args.episodes_per_family)
    episodes, templates = generator.generate()
    if args.write_data:
        write_jsonl(args.write_data, episodes)
    return episodes, templates


def _fail_value(value: float, coverage: int) -> str:
    if coverage == 0:
        return "n/a（无相关失败预期）"
    return str(value)


def render_report(
    episodes: list[dict[str, Any]],
    gold_templates: list[dict[str, Any]],
    validation: dict[str, Any],
    retrieval: dict[str, Any],
    threshold_run: dict[str, Any],
    classification: dict[str, Any],
    matching: dict[str, Any],
    mining: dict[str, Any],
    degenerate: dict[str, Any],
    ablations: dict[str, Any],
) -> str:
    family_counts = validation["stats"]["familyCounts"]
    family_lines = "\n".join(f"- {family}: {count} episodes" for family, count in family_counts.items())
    degenerate_lines = "\n".join(f"- {name}: {status}" for name, status in sorted(degenerate.items()))
    pattern_lines = "\n".join(f"- {pattern}: {accuracy}" for pattern, accuracy in classification["perPattern"].items())
    return f"""# 离线评测样例报告（合成数据）

- 生成时间：本报告由 `scripts/eval/run_eval.py` 自动生成
- 数据集：**合成**（`eval_dataset.py` 种子化生成，seed 固定，可复现）
- 诚实声明：**合成数据结果不代表真实标注集质量**。词表、教训文本与 gold 标注全部来自固定模板池；真实环境中的召回率、模板匹配精度与误用率必须用人工双标注集（MVP_EVALUATION.md 下一轮评测计划）重新测量。
- 防泄漏校验：{len(validation["violations"])} 处违规（构建期强制，违规时评测中止）

## 数据集与切分

- episodes：{validation["stats"]["episodes"]}
- 任务族：{family_lines}
- split 矩阵：{", ".join(f"{key}: {value}" for key, value in validation["stats"]["familySplitMatrix"].items())}
- 标注一致性：{validation["stats"]["agreements"]}

## 检索质量（默认融合臂，threshold=0）

| 指标 | 值 |
|---|---|
| Recall@k | {retrieval["recall"]} |
| Recall@k (success) | {retrieval["recallSuccess"]} |
| Recall@k (fail) | {_fail_value(retrieval["recallFail"], retrieval["recallFailCoverage"])} |
| nDCG@k | {retrieval["ndcg"]} |
| nDCG@k (success) | {retrieval["ndcgSuccess"]} |
| nDCG@k (fail) | {_fail_value(retrieval["ndcgFail"], retrieval["ndcgFailCoverage"])} |
| 配额遵守率（目标 100%；可测查询 {retrieval["quotaEligibleQueries"]} 条） | {retrieval["quotaAdherence"]} |
| 自检索率（目标 0） | {retrieval["selfRetrievalRate"]} |
| misleading 误召回条数 | {retrieval["misleadingRetrieved"]} |

阈值臂（relevanceThreshold=0.3）：nDCG {threshold_run["ndcg"]}，thresholdFiltered 均值 {threshold_run["thresholdFilteredMean"]}。合成数据的融合分普遍低于 0.3（离线核心无 BM25/向量通道参与），因此该臂过滤了绝大多数候选；阈值本身仍为初值，需在标注集上扫描。

## 模板层

- 任务模式分类 accuracy：{classification["overall"]}
  {pattern_lines}
- 模板匹配 top-1：{matching["top1"]}；top-3：{matching["top3"]}（查询 {matching["queries"]} 条）
- 挖掘：簇 {mining["clusterCount"]} 个，发射模板 {mining["emittedTemplates"]} 个，纯度 {mining["purity"]}，覆盖率 {mining["coverage"]}，skipped {mining["skipped"]}。合成各族的签名两两相似，θ_join=0.75 初值下被合并为单一簇，纯度因此偏低；聚类阈值与发射门控是待校准初值。
- 退化场景检查：
  {degenerate_lines}

## 通道消融

{render_ablation_markdown(ablations)}

说明：`none` 为无记忆基线；`template` 只保留模板通道；`fusion` 为 v0.4.0 默认融合（含模板通道）；`fusion_template_v2` 为第二组模板权重臂（structure 0.05 / template 0.20）。离线 harness 直接调用 Python 核心，不含 TS 侧 BM25/向量候选通道，因此 lexical/vector 分路在本报告中不参与融合；这是 harness 的已知边界，不是插件的生产行为。
"""


def main() -> int:
    args = parse_args()
    episodes, gold_template_list = load_episodes(args)

    validation = validate([
        {"__line__": index + 1, "__raw__": "", "value": episode}
        for index, episode in enumerate(episodes)
    ])
    if not validation["ok"]:
        print("dataset validation failed:", file=sys.stderr)
        for violation in validation["violations"]:
            print(f"  - {violation}", file=sys.stderr)
        return 1

    gold_templates = {template["templateId"]: template for template in gold_template_list}
    retrieval = evaluate_retrieval(episodes, bind_gold_template=True, gold_templates=gold_templates)
    threshold_run = evaluate_retrieval(
        episodes, threshold=0.3, bind_gold_template=True, gold_templates=gold_templates,
    )
    classification = evaluate_classification(episodes)
    matching = evaluate_matching(episodes, gold_template_list, max_templates=3)
    mining = evaluate_mining(episodes, min_support=3)
    degenerate = check_degenerate_scenarios(episodes)
    ablations = run_ablations(episodes, gold_templates)

    report = render_report(
        episodes, gold_template_list, validation, retrieval, threshold_run,
        classification, matching, mining, degenerate, ablations,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"report written to {out_path}")
    print(f"  recall={retrieval['recall']} ndcg={retrieval['ndcg']} "
          f"quota={retrieval['quotaAdherence']} selfRetrieval={retrieval['selfRetrievalRate']} "
          f"classify={classification['overall']} templateTop1={matching['top1']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
