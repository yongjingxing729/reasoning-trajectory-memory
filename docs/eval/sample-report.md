# 离线评测样例报告（合成数据）

- 生成时间：本报告由 `scripts/eval/run_eval.py` 自动生成
- 数据集：**合成**（`eval_dataset.py` 种子化生成，seed 固定，可复现）
- 诚实声明：**合成数据结果不代表真实标注集质量**。词表、教训文本与 gold 标注全部来自固定模板池；真实环境中的召回率、模板匹配精度与误用率必须用人工双标注集（MVP_EVALUATION.md 下一轮评测计划）重新测量。
- 防泄漏校验：0 处违规（构建期强制，违规时评测中止）

## 数据集与切分

- episodes：48
- 任务族：- code_bugfix: 8 episodes
- code_modification: 8 episodes
- env_state_tracking: 8 episodes
- info_retrieval: 8 episodes
- web_form_fill: 8 episodes
- web_navigation: 8 episodes
- split 矩阵：code_bugfix [dev]: 8, code_modification [dev]: 8, env_state_tracking [test]: 8, info_retrieval [dev]: 8, web_form_fill [test]: 8, web_navigation [test]: 8
- 标注一致性：{'agree': 48}

## 检索质量（默认融合臂，threshold=0）

| 指标 | 值 |
|---|---|
| Recall@k | 0.5714 |
| Recall@k (success) | 0.5714 |
| Recall@k (fail) | n/a（无相关失败预期） |
| nDCG@k | 0.4334 |
| nDCG@k (success) | 0.4334 |
| nDCG@k (fail) | n/a（无相关失败预期） |
| 配额遵守率（目标 100%；可测查询 35 条） | 1.0 |
| 自检索率（目标 0） | 0.0 |
| misleading 误召回条数 | 18 |

阈值臂（relevanceThreshold=0.3）：nDCG 0.0，thresholdFiltered 均值 23.15。合成数据的融合分普遍低于 0.3（离线核心无 BM25/向量通道参与），因此该臂过滤了绝大多数候选；阈值本身仍为初值，需在标注集上扫描。

## 模板层

- 任务模式分类 accuracy：1.0
  - code_synthesis: 1.0
- gui_operation: 1.0
- long_horizon_planning: 1.0
- multi_hop_qa: 1.0
- 模板匹配 top-1：1.0；top-3：1.0（查询 48 条）
- 挖掘：簇 1 个，发射模板 1 个，纯度 0.3333，覆盖率 1.0，skipped {'below_support': 0, 'too_short': 0, 'monotone': 0, 'fail_majority': 0, 'duplicate': 0, 'pending': 0}。合成各族的签名两两相似，θ_join=0.75 初值下被合并为单一簇，纯度因此偏低；聚类阈值与发射门控是待校准初值。
- 退化场景检查：
  - empty_bank_emits_nothing: True
- mining_deterministic: True
- monotone_cluster_skipped: True
- single_cluster_emits_one: True

## 通道消融

| arm | recall | recall(success) | recall(fail) | nDCG | nDCG(success) | nDCG(fail) | misleading | self-retrieval | quota |
|---|---|---|---|---|---|---|---|---|---|
| none | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0.0 | 1.0 |
| semantic | 0.5476 | 0.5476 | 0.0 | 0.4008 | 0.4008 | 0.0 | 17 | 0.0 | 0.9429 |
| structure | 0.6667 | 0.6667 | 0.0 | 0.5023 | 0.5023 | 0.0 | 18 | 0.0 | 1.0 |
| graph | 0.3095 | 0.3095 | 0.0 | 0.2282 | 0.2282 | 0.0 | 18 | 0.0 | 1.0 |
| template | 0.4286 | 0.4286 | 0.0 | 0.3257 | 0.3257 | 0.0 | 18 | 0.0 | 1.0 |
| fusion | 0.5714 | 0.5714 | 0.0 | 0.4334 | 0.4334 | 0.0 | 18 | 0.0 | 1.0 |
| fusion_template_v2 | 0.5714 | 0.5714 | 0.0 | 0.4189 | 0.4189 | 0.0 | 18 | 0.0 | 1.0 |

说明：`none` 为无记忆基线；`template` 只保留模板通道；`fusion` 为 v0.4.0 默认融合（含模板通道）；`fusion_template_v2` 为第二组模板权重臂（structure 0.05 / template 0.20）。离线 harness 直接调用 Python 核心，不含 TS 侧 BM25/向量候选通道，因此 lexical/vector 分路在本报告中不参与融合；这是 harness 的已知边界，不是插件的生产行为。
