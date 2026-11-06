# v0.6.0 规格：因果效用引擎（从关联性反馈到因果信用分配）

- 日期：2026-11-06
- 状态：FINAL —— 已吸收 `docs/research-notes/06-causal-ai.md`
- 上游依据：因果 AI（Pearl 因果层级/do-calculus/后门准则/ATE/CATE/IPTW）+ 综述 §5「检索—激活的信用分配」与 §6.5 因果验证设计（ProactAgent 配对续写 [7]）
- 实现者：code-developer；调试：debugger；监听：watcher；文档：lead

---

## 1. 因果诊断与目标

**现状（关联性）**：`retrieval.py` 的效用乘数 `0.8 + 0.4×(helpful+1)/(helpful+harmful+2)` 估计的是 P(成功|用过记忆)——关联量，不回答"使用记忆是否**导致**成功"。混杂（任务难度/模式同时影响"检索了什么"与"是否成功"）与选择性偏差（好记的忆被用在简单任务上）都会让关联效用失真；ToE 报告的"朴素经验管理常低于无经验基线"正是此类失真的实例。

**目标（因果性）**：新增**因果效用引擎**，估计记忆/模板的处理效应 E[Y|do(m)]−E[Y|do(¬m)]，并让它在检索排序中以**有界、保守、可审计**的方式取代部分关联效用：

1. **反事实信用分配（一等原语）**：配对续写（同前缀有/无检索）的实验记录接口——最强因果证据。
2. **观察性因果估计**：按任务模式（L1）分层的 ATE/CATE（后门/分层调整）+ 倾向性重加权（IPTW）修正选择性偏差。
3. **辛普森悖论防护**：整体效应符号与分层效应符号相反时检测并审计。
4. **保守集成**：样本不足回退现有 Beta 效用（先验收缩）；因果分量封顶，不破坏正反配额与确定性。

## 2. 数据模型（additive，零回归）

### 2.1 新独立 domain `reasoning_memory_causal`（version 1）

```
causalEventSchema = {
  eventId,                       // 确定性 id
  memoryId,                      // 或 templateId（template 因果事件共用此表，kind 区分）
  kind: 'memory' | 'template',
  taskId,
  taskPattern: 7 类枚举,
  treatment: 'used' | 'retrieved_unused' | 'counterfactual_with' | 'counterfactual_without',
  outcome: 'success' | 'fail',
  stepIndex: int,                // 0 = 未提供
  retrievalIntent: string,
  modelVersion: string,          // 混杂控制用
  pairId: string,                // 配对续写臂标识；观察性事件为空
  createdAt: int,
}
```

- 每个 memoryId 事件上限 `MAX_CAUSAL_EVENTS_PER_UNIT = 200`（FIFO 淘汰最旧），内存护栏。
- 事件写入路径：`memory_save_outcome` 时由 TS 从活动任务状态（本任务检索过的记忆 + 是否被 feedback 标记 used + outcome + 分类 taskPattern）**派生**观察性事件；`memory_feedback` 语义不变（零回归）。
- 新工具 `memory_record_counterfactual`：显式记录配对续写双臂（experimental 事件）；参数 {memory_id, arm: with/without, outcome, pair_id?, task_pattern?}。

### 2.2 检索审计 additive 字段

`retrievalRecordSchema` 与候选审计：`causalUplift: number [0,1] default 0`、`causalSamples: int default 0`、`simpsonParadox: boolean default false`、`causalStrata: string default ''`（记录该候选因果估计使用的分层）。

## 3. Python 核心

### 3.1 新模块 `causal_utils.py`（纯 stdlib、确定性、可单测）

**输入**：目标单元的因果事件列表 + 当前任务 taskPattern。

**估计量**（采纳 06-causal-ai.md §1 的确定性公式，纯计数+四则）：

1. **分层 2×2 表**（每 stratum s=taskPattern，Laplace +1 平滑）：
   ```
   n11=used∧success, n10=used∧fail, n01=¬used∧success, n00=¬used∧fail
   ŷ1s = n11/(n11+n10)，ŷ0s = n01/(n01+n00)
   ATE_s = ŷ1s − ŷ0s；ATE_stratified = Σ_s (n_s/N)·ATE_s
   样本门槛：每臂（used/¬used）≥ MIN_ARM_SAMPLES=5 的层才参与；不达标层剔除并审计。
   方差：二项方差 Var(ŷ1s)+Var(ŷ0s) → Wald 95% CI。
   ```
2. **CATE**：逐层输出 ATE_s（即 CATE(s)，L1 对齐）。
3. **IPTW（仅审计对照，不喂排序）**：层内经验倾向 `e_s=(n1s+1)/(ns+2)`，权重截断 [0.05, 20]；`ATE_iptw` 与 `ATE_stratified` 差异 > 0.15 → 审计标记 `iptwDisagreement`。
4. **配对续写估计**：`ATE_paired = mean(Y_with − Y_without)`（同 pairId 配对）；有配对数据时优先作为对照证据（COMA 式反事实基线），与观察性估计并列审计。
5. **辛普森悖论检测**：`sign(朴素 crude ATE) ≠ sign(ATE_stratified)` 且双侧显著（Wald）→ `simpsonFlip = true` 告警；排序一律采用分层估计。
6. **保守收缩与门槛**：全局 `n_eff ≥ 20` 且分层达标 → 启用因果分量；否则回退 Beta 效用（v0.5.0 行为完全一致，零回归可测）。收缩：`ATE_shrunk = ATE_stratified × n_eff/(n_eff+20)`（先验收缩，k=20）。
7. **有界集成（永不比现状更激进）**：`utility_multiplier = 1 + clip(ATE_shrunk, −0.20, +0.20)`——与现行 Beta 乘数同区间 [0.8, 1.2]。因果乘数只作用于最终 multiplier（`retrieval.py:103` 同构位置），**不进 RRF 权重、不破坏正反配额与 outcome 池**；负效应只压低乘数不删除记忆（只升不删）。
8. **estimatorVersion 审计**：每次估计输出估算器版本号，保证可复现。

### 3.2 service.py 新 operation

`causal_estimate`（输入事件列表 + 当前 pattern → 输出 ATE/CATE/IPTW/paired/辛普森标记/样本量/分解审计）。事件落库由 TS 决定，Python 只算。

## 4. TypeScript 插件层

- `src/causal-domain.ts`：独立 domain `reasoning_memory_causal`。
- `memory_save_outcome`：写观察性因果事件（从活动任务状态派生 treatment/confounders；FIFO 上限）。
- 新工具 `memory_record_counterfactual`（配对续写双臂记录；每任务每记忆配对上限，防滥用）。
- 检索排序：`memory_retrieve` 候选经 Python `causal_estimate` 结果计算 `causalUplift` 进 utility 融合；审计写入 §2.2 字段。
- `memory_query` 可选 `include_causal` 展示单元的因果统计摘要（ATE/样本量/辛普森标记）。

## 5. 四层映射对齐

| 层 | 因果落点 |
|---|---|
| L1 任务模式 | **混杂分层变量**：stratification/backdoor 的 stratum s |
| L2 策略模板 | **处置分配**：模板选择即"策略处置"；模板也可入因果事件（kind='template'） |
| L3 能力模块 | 单元元数据新增因果统计（ATE/CATE/样本量/辛普森标记） |
| L4 激活动作 | **干预 do(retrieve)**：treatment 定义；配对续写 = 显式反事实干预对照 |

## 6. 识别假设（诚实标注，写入文档）与越界声明

**识别假设（5 条，06-causal-ai.md §0 原样）**：
1. **可忽略性**：仅控制在已观测混杂（taskPattern/family/modelVersion/retrievalIntent/stepIndex）下成立；存在未观测混杂则估计有偏——如实声明。
2. **SUTVA**：单元间无干扰（一个记忆的使用不影响另一个记忆的结局）；配对续写双臂共享前缀除外（该对照本身即干预定义）。
3. **处置良定义**：`used`（被反馈确认使用）vs `retrieved_unused` 是可区分的干预。
4. **结局良定义**：`outcome` 来自环境/验证器终态，与 v0.5.0 同一来源。
5. **verdict = 代理标签**：helpful/harmful 是自报代理，即便替代准确率 80–90% 仍可能产生实质偏差（Egami DSL 2306.04746 的教训）→ 必须收缩 + 以 outcome 为因果结局变量（不用 verdict 当结局）。

**越界（不做什么）**：
1. 不做在线因果结构发现（PC/FCI 等算法不落地）；
2. 不做 LLM 生成反事实/LLM 效应判断（Kıcıman 2305.00050："LLMs ignore the actual data"——估计全部由确定性计数完成，不委托 LLM）；
3. 不做梯度式/神经网络优化（Retroformer 2308.02151 越界）；
4. 不做 DML/双重稳健等需要学模型倾向与结局的估计器（MVP 只用分层+IPTW 对照）；
5. 不把文本嵌入进混杂模型（Egami 1802.02163 的识别问题）；
6. 因果分不进 RRF 权重与正反配额（只作用于 final multiplier，封顶 [0.8,1.2]）；
7. 配对续写由 agent/harness 执行，插件只记录与估计（反事实解释 Karimi 2002.06278：干预建议≠反事实解释）。

## 7. 测试要求

- Python：合成已知因果结构验证——分层 ATE 恢复真实处理效应（CI 覆盖 ≥90%）、CATE 分层正确、辛普森构造（粗效应为负/每层效应为正）检出 100% 且无结构时误报 0、IPTW 极端倾向裁剪与 iptwDisagreement、Laplace 平滑、每臂 ≥5 与 n_eff ≥20 门槛、收缩单调性、配对估计、确定性双跑逐字节一致、`causalApplied=false` 时与 v0.5.0 基线行为零回归。
- Node：mock 宿主 save_outcome 派生事件、record_counterfactual 配对、检索审计 causal 字段（causalUplift/causalSamples/simpsonParadox/causalStrata/estimatorVersion）、FIFO 上限、零回归（旧路径不变）。
- `npm test` 全绿；`scripts/eval` 仍运行且指标与 v0.5.0 持平。

## 8. 验收清单

1. `npm test` 全绿；`scripts/eval` 指标与 v0.5.0 持平（零回归）。
2. 因果估计在合成已知结构上恢复正确（ATE 符号/量级、辛普森检出）。
3. 文档同步（README/设计文档 §20/MVP_EVALUATION v0.6.0 段，诚实标注因果假设）。
4. git 提交（逻辑拆分）。
