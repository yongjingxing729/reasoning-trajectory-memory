# scripts/eval · 离线评测 harness

DSH 推理记忆插件的离线评测工具。纯 Python 标准库、无网络、固定 seed、确定性；直接调用
`python/reasoning_memory_core` 的同一实现，不启动 DSH 宿主。**本目录不进 npm 发货包**。

## 运行

```sh
python scripts/eval/run_eval.py                     # 合成数据 + 全指标 + 报告 docs/eval/sample-report.md
python scripts/eval/run_eval.py --seed 7            # 换种子
python scripts/eval/run_eval.py --data my.jsonl     # 评测人工标注集
python scripts/eval/run_eval.py --write-data scripts/eval/data/synthetic-dev.jsonl  # 落盘生成数据
```

输出报告为 Markdown。数据集校验失败时评测中止（exit 1）。

## 文件

- `eval_dataset.py`：episode JSONL schema（04-eval-schema.md schemaVersion 1）+ 种子化合成生成器（含结构相似/语义不同负控制对、按任务族/任务族组防泄漏切分、gold taskPattern/goldTemplate）。
- `validate_schema.py`：构建期校验（04 §8 六类断言：可解析/必填与上限/防泄漏 7 规则/引用完整性/图与签名结构/族-分裂矩阵报告）。
- `eval_retrieval.py`：Recall@k、nDCG@k（按 outcome 分报）、配额遵守率（目标 100%）、自检索率（目标 0）、misleading 误召回、阈值臂统计。
- `eval_templates.py`：任务模式分类 accuracy（分模式）、模板匹配 top-1/top-3、挖掘纯度/覆盖率/簇数/退化场景检查。
- `eval_ablation.py`：通道消融臂 {none, semantic, structure, graph, template, fusion} + 第二组模板权重臂。
- `run_eval.py`：CLI 编排与报告渲染。

## 边界声明

- 离线 harness 直接调用 Python 核心，**不含 TypeScript 侧 BM25 与向量候选通道**；lexical/vector 分路不参与本 harness 的融合。评测的是确定性核心排序，不是插件的完整生产检索链路。
- 合成数据结果**不代表真实标注集质量**；模板挖掘纯度/覆盖率等指标必须在人工双标注集上重新测量。
- 全部阈值/权重为工程初值（见 `docs/research-notes/02-template-algorithms.md`），本 harness 提供测量手段而非校准结论。
