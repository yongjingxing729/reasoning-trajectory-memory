# 06 · 因果效用引擎：论文支撑与 MVP stdlib 落地建议（docs/spec-0.6.0 设计依据）

> 口径与 `02-template-algorithms.md` / `05-reasoning-rag.md` 一致：严格区分 **「论文原文做法」** 与 **「MVP stdlib 工程近似」**；工程近似不声称复刻论文。
> 约束：Python 标准库 only、确定性（聚合顺序无关）、可审计（每个估计量可拆解）、可单测。
> 核实：本文 8 篇 arXiv 论文全部经 `web_fetch` 复核摘要页（2026-11-06，HTTP 200），原文引文见 §2/§8；Pearl 层级与辛普森悖论引用书籍/期刊（无 arXiv），如实标注。另有一次误编号抓取（2305.17348 实为光学论文）已弃用。

---

## 0. 定位与识别假设（先读）

**目标**：把现有"关联性效用"（Beta(1,1) 平滑计数：`utility_mean=(helpful+1)/(helpful+harmful+2)` → 乘数 `0.8+0.4·utility_mean ∈ [0.8,1.2]`，`retrieval.py:262-267`；模板同款 `templates.py:338-347,478-482`）升级为**因果效用引擎**：以 `E[Y|do(m)] − E[Y|do(¬m)]` 为核心估计量，配合分层调整（后门）、IPTW、CATE 与辛普森悖论防护。

**必须先写进 spec 的识别假设（诚实标注，缺一不可）**：
1. **条件可交换性/无未观测混杂**：给定混杂层 S（任务模式/任务族/模型版本/检索意图/步骤位），处置 M（使用记忆 m）与潜在结局 Y(m) 条件独立。S 只含可观测的离散变量，**不声称捕捉全部混杂**。
2. **一致性（SUTVA）**：观察到的 Y = 处置对应的潜在结局；一条记忆的使用不通过别处间接改变另一条记忆的结局（单位间无干扰）。
3. **处置良定义**：M=1 = "该记忆被检索并注入上下文"，M=0 = "未检索该记忆"（配对续写直接实现此对照）。
4. **结局良定义**：Y ∈ {0,1}（任务成功/失败；配对续写可用 step-level 结局，ProactAgent 式）。
5. **Verdict 是代理标签**：helpful/harmful/任务结局由模型/调用者自报，是"有偏代理"而非 gold（Egami 2306.04746 的直接教训，§2.5）。因此估计量必须收缩、必须可审计、gold 校验只在 scripts/eval 的合成/标注数据上做。

---

## 1. 因果推断基础：可实现的确定性公式

### 1.1 Pearl 因果层级与本插件落点

| 层级 | 定义 | 本插件落点 |
|---|---|---|
| L1 观察（association） | P(Y\|m) 关联 | 现有 Beta 计数效用（保留为**回退面**） |
| L2 干预（intervention） | P(Y\|do(m))，do-calculus/调整公式 | 分层调整 + IPTW 的 ATE 估计（本节） |
| L3 反事实（counterfactual） | 个体级 "had m been different" | **配对续写记录**（同前缀有/无检索两次结局）= 最接近 L3 的一等原语（COMA 反事实基线的 agent 版，§2.1） |

来源：[Pearl 2009, *Causality*]（书籍，do-calculus 与后门准则）；[Pearl 2019, *The Seven Tools of Causal Inference*, CACM]（层级与陷阱）。非 arXiv，引用时用书籍/期刊格式。

### 1.2 后门准则与分层调整公式（确定性实现）

符号：层 s ∈ S（离散混杂取值组合）；n_{1s}/n_{0s} = 层内用/不用 m 的样本数；a_{1s}/a_{0s} = 其中 Y=1 的数量；N = 总样本数。

- **调整公式（backdoor adjustment 的估计形式）**：
  ```
  ŷ_{1s} = (a_{1s} + 1) / (n_{1s} + 2)        # Laplace 平滑 = Beta(1,1) 后验均值
  ŷ_{0s} = (a_{0s} + 1) / (n_{0s} + 2)
  ATE_hat = Σ_s (n_s / N) × (ŷ_{1s} − ŷ_{0s})   # n_s = n_{1s} + n_{0s}
  ```
- **样本量门槛**：层参与估计需 `n_{1s} ≥ MIN_STRATUM_N` 且 `n_{0s} ≥ MIN_STRATUM_N`（初值 **5**）；不达标的层**剔除并审计**（`droppedStrata`），不并入其他层（可审计性优先）。
- **方差估计（二项近似 + 平滑）**：
  ```
  Var(ATE) ≈ Σ_s (n_s/N)² × [ ŷ_{1s}(1−ŷ_{1s})/(n_{1s}+2) + ŷ_{0s}(1−ŷ_{0s})/(n_{0s}+2) ]
  SE = sqrt(Var(ATE))；Wald 95% CI = ATE ± 1.96 × SE
  ```
- **CATE（异质性处理效应）**：`CATE_s = ŷ_{1s} − ŷ_{0s}` 逐层输出（L1 层直接消费）；`CATE_spread = max_s CATE_s − min_s CATE_s`（异质性量纲，供审计）。

### 1.3 倾向性重加权（IPTW，层内经验倾向）

- **倾向（分层式，纯计数）**：`e_s = P(M=1|S=s) ≈ (n_{1s} + 1) / (n_s + 2)`。
- **权重**：处理样本 `w_i = 1/e_s`；对照样本 `w_i = 1/(1−e_s)`；**截断** `w_i ∈ [W_MIN, W_MAX]`（初值 `W_MIN=0.05, W_MAX=20`；截断惯例见 Hernán & Robins *Causal Inference* 书，非 arXiv）。
- **Horvitz–Thompson 估计量**：
  ```
  ATE_IPTW = Σ_{i:M=1} w_i Y_i / Σ_{i:M=1} w_i  −  Σ_{i:M=0} w_i Y_i / Σ_{i:M=0} w_i
  ```
- 使用规则：`ATE_IPTW` 仅作 **`ATE_stratified` 的稳健性对照**输出进审计（两者差异 > 0.15 时记 `iptwDisagreement`），**排序只消费分层估计**（确定性、方差可控、无权重爆炸风险）。

### 1.4 辛普森悖论防护

```
crude = (a1_total+1)/(n1_total+2) − (a0_total+1)/(n0_total+2)      # 不分层
simpsonFlip = (sign(crude) ≠ sign(ATE_stratified))
              AND |crude| ≥ 1.96·SE_crude AND |ATE_stratified| ≥ 1.96·SE_strat
```
- 触发时：①审计字段 `simpsonFlip=true` 并携带两侧估计与 CI；②排序**采用分层估计**（与 crude 反号时不放大 crude）；③返回体提示模型"该记忆的总体效用与分层效用符号相反，请按当前任务模式分层解读"。
- 与综述 [6] ToE 观测（"conventional experience-management methods often underperform experience-free baselines"，03-survey-facts.md §1 已复核）的关联：负的总体效应可能是辛普森结构（某些任务模式内正效应被高样本负模式淹没），本检测器把该怀疑变成可审计的数值判定，**但不声称证明之**。

### 1.5 收缩与先验（保守性核心）

```
n_eff = Σ_s n_s × I(层达标)          # 有效样本量（不达标层不计）
shrink = n_eff / (n_eff + SHRINK_PRIOR)    # SHRINK_PRIOR 初值 20（等效先验样本量）
ATE_shrunk = shrink × ATE_stratified
```
- 语义：小样本时估计量**自动向 0（无效应）收缩**；`n_eff → 0` 时因果乘数退化为 1（= 现有 Beta 效用路径，§5）。
- 排序乘数（沿用现有位置与封顶，`retrieval.py:103` 的 `utilityMultiplier` 同构替换/并列）：
  ```
  causal_multiplier = 1 + clip(ATE_shrunk, −0.20, +0.20)   # ATE 是成功率差，直接进 ±20% 封顶
  ```
  仅当 `n_eff ≥ MIN_EFFECTIVE_N`（初值 **20**）且当前查询所在层**无 simpsonFlip** 时启用；否则退回 Beta 乘数并审计 `causalApplied=false, reason`。

### 1.6 确定性与审计字段

- 所有估计 = 整数计数 + 四则运算，**聚合顺序无关**（求和可交换），双跑全等。
- 建议记录/审计字段（additive，全部带默认值）：
  ```
  causalRecord: { treatmentMemoryId, confounders: {taskPattern, taskFamily, modelVersion,
                  retrievalIntent, stepIndex}, outcome: 0|1, paired: bool, verdict, source }
  效用元数据（MemoryItem additive）:
  causalMeta: { ate: 0, ateVariance: 0, nTreated: 0, nControl: 0, nEff: 0,
                strata: [{key, n1, n0, cate}], simpsonFlip: false, crude: 0,
                estimatorVersion: '', lastUpdated: 0 }  → 默认 null/空
  检索审计（candidateScores 行）: causalMultiplier: 1, causalApplied: false, causalReason: ''
  ```

---

## 2. 因果 AI × LLM/Agent 前沿（9 篇：原文引文 + 可借鉴点 + 工程近似）

### 2.1 COMA（arXiv:1705.08926）

- **原文**："it uses a **counterfactual baseline that marginalises out a single agent's action, while keeping the other agents' actions fixed**."（摘要原文）
- **可借鉴点**：多智能体信用分配 = 固定其他人、只变一个动作的反事实对照——本插件"同前缀有/无检索"的配对续写是**同一逻辑**（其他一切固定、只翻转"检索 m"这一个动作），`E[Y|do(m)]−E[Y|do(¬m)]` 的 do 干预直接对应 COMA 的 marginalized baseline。
- **工程近似**：COMA 是 actor-critic 梯度训练；我们**不训练**，只把反事实对当作计数数据喂给分层估计器。

### 2.2 ProactAgent（arXiv:2604.20572，综述 [7]，03-survey-facts.md §2 已复核）

- **原文**："By comparing **paired continuations from identical interaction prefixes with and without retrieval**, ProactRL provides **step-level process rewards** that encourage retrieval only when it improves task outcomes or efficiency."（v2 摘要原文）
- **可借鉴点**：配对续写作为**一等数据原语**的出处与成功先例；step-level 结局（非仅任务级）。
- **工程近似**：ProactRL 用该信号做 RL 训练（越界）；我们用它做分层 ATE/CATE 的对照数据（计数估计）。**记录接口**：`memory_feedback` 扩展或新工具 `memory_record_counterfactual(memory_id, with_retrieval_outcome, without_retrieval_outcome, confounders?)`，每任务每记忆一次幂等（沿用现有反馈幂等纪律）。

### 2.3 文本因果推断（Egami et al.，arXiv:1802.02163）

- **原文**："nearly all text-based causal inferences depend upon a **latent representation** of the text ... estimating this latent representation ... may introduce an **identification problem or overfit**. To address these risks we describe a **split-sample framework**."（摘要原文）
- **可借鉴点**：①文本变量不能直接进因果模型（需潜在表示）；②从数据学习混杂表示会引入识别/过拟合风险，需 split-sample。
- **工程近似**：混杂层**只用预注册的离散词表变量**（L1 的 7 类 taskPattern 等），**不从文本学习混杂**；词表固定即"预注册"，规避识别问题。评测 gold 走 scripts/eval 的 split-sample 纪律（§6）。

### 2.4 代理标签偏差（Egami et al. DSL，arXiv:2306.04746，NeurIPS 2023）

- **原文**："direct use of surrogate labels in downstream statistical analyses leads to **substantial bias and invalid confidence intervals, even with high surrogate accuracy of 80-90%**. To address this, we build on debiased machine learning to propose the **design-based supervised learning (DSL)** estimator. DSL employs a **doubly-robust** procedure to combine surrogate labels with a smaller number of high-quality, gold-standard labels."（摘要原文）
- **可借鉴点（本设计最重要的一条）**：模型的 helpful/harmful 自报就是"代理标签"，**即使准确率高，直接使用也有偏**。→ ①因果乘数必须收缩且不参与 relevance 权重；②eval harness 必须提供**设计化 gold 校验**（合成已知因果结构 + 人工标注抽样），估计量正确性在 gold 上验证，线上只消费代理标签并如实标注。
- **工程近似**：不做 DML/doubly-robust（需要 ML 建模 e(X)/μ(X)）；用"层内经验倾向 + 截断"的 IPTW 与分层估计代替，并把 doubly-robust 列为越界（§3.2）。

### 2.5 因果表示学习（Schölkopf et al.，arXiv:2102.11107）

- **原文**："most work in causality starts from the premise that the **causal variables are given**. A central problem for AI and causality is, thus, **causal representation learning**, the discovery of high-level causal variables from low-level observations."（摘要原文）
- **可借鉴点**：因果变量的选取是核心难点；本插件的做法属于"变量已给"（L1 词表 = 设计者给定的因果变量），CRL 明确越界。
- **工程近似**：混杂变量集合由 spec 预注册（taskPattern/taskFamily/modelVersion/retrievalIntent/stepIndex），不自动发现；如需扩展走版本化词表升级。

### 2.6 LLM 因果推理能力（Kıcıman et al.，arXiv:2305.00050，TMLR）

- **原文**："LLMs can generate text corresponding to correct causal arguments with high probability ... pairwise causal discovery task (**97%, 13 points gain**), counterfactual reasoning task (**92%, 20 points gain**) and event causality (**86% accuracy** ...)." 以及关键句："**LLMs ignore the actual data** ... a fruitful research direction of developing algorithms that combine LLMs with existing causal techniques."（摘要原文）
- **可借鉴点**：LLM 擅长生成因果论证、**不看数据**——所以**因果估计绝不能委托给 LLM**；数据上的计数/分层估计是插件职责，LLM 至多被允许（可选、越界）协助领域 setup。
- **工程近似**：全部估计为纯计数/算术；不在任何 prompt 里让模型"判断因果关系"。

### 2.7 LLM 纯因果推断短板（Jin et al. Corr2Cause，arXiv:2306.05836，ICLR 2024）

- **原文**："these models achieve **almost close to random performance** on the task ... still fail to generalize -- they can only perform causal inference in in-distribution settings."（摘要原文，200K+ 样本、17 个 LLM）
- **可借鉴点**：进一步支持 §2.6 的结论：从相关性陈述推断因果方向，LLM 近随机且不泛化——插件侧的"效应估计"必须由计数统计完成。
- **工程近似**：辛普森检测/ATE 估计完全确定性；"因果方向"不由任何模型判断。

### 2.8 反事实解释 ≠ 干预建议（Karimi et al.，arXiv:2002.06278）

- **原文**："counterfactual explanations inform an individual where they need to get to, but **not how to get there** ... we propose a shift of paradigm from recourse via nearest counterfactual explanations to **recourse through minimal interventions**."（摘要原文）
- **可借鉴点**：区分"解释"与"干预"。本插件的 do(m)/do(¬m) 记录是**干预数据**（用于估计），不是给模型的反事实解释文本；返回给模型的审计文本只陈述"效应估计与置信"，不生成"若当初…"的叙述（越界：LLM 生成反事实解释）。
- **工程近似**：审计输出 = 数值表（ATE/CI/CATE/strata），不生成解释性文本。

### 2.9 环境反馈的梯度优化（Retroformer，arXiv:2308.02151）

- **原文**："learning a retrospective model, which automatically tunes the language agent prompts from environment feedback **through policy gradient** ... considerably outperforms baselines that do not properly leverage gradients from the environment."（摘要原文）
- **可借鉴点**：环境反馈是宝贵信号——我们保留环境结局（任务成功/失败）作为 Y 的主来源，优于纯自报 verdict。
- **工程近似**：policy gradient 训练越界（无训练预算/模型访问）；以"结局计数 → 分层 ATE → 有界乘数"的非参数路径替代。

---

## 3. MVP 映射：可落地 vs 越界

### 3.1 可落地（纯 stdlib、确定性）

| 组件 | 实现要点 | 落点 |
|---|---|---|
| 反事实记录原语 | 配对续写记录：同前缀有/无检索双结局 + 混杂标注；或单臂记录（仅当 paired=false 时进观察池） | 工具扩展 `memory_feedback` 或新 `memory_record_counterfactual`（spec 定夺）；写新 domain 表或 `retrievals` additive 区 |
| 分层 ATE/CATE | §1.2 公式；`collections.Counter` + 字典聚合 | `python/reasoning_memory_core/causal_utils.py`（新模块） |
| IPTW 对照 | §1.3；仅审计对照，不喂排序 | 同上 |
| 辛普森检测 | §1.4；审计 + 排序采用分层估计 | 同上 |
| 收缩与封顶 | §1.5；`1+clip(ATE_shrunk,±0.20)`，仅达标层启用 | 同模块；`retrieval.py` 乘数接入点（`utilityMultiplier` 同构） |
| 确定性 | 整数计数聚合、无浮点累加次序依赖（用 `Fraction`/定点或按层先算再加权求和，禁止流式浮点累积） | 测试断言双跑全等 |
| 审计 | §1.6 字段；进 candidateScores 与检索审计 | `domain.ts` additive + `retrieval.py` 输出 |

### 3.2 越界（写入 spec 的"不做什么"）

1. **在线因果发现**（PC/FCI 等约束算法、从数据学图）：变量预注册，不做结构学习（Schölkopf §2.5）；
2. **LLM 生成反事实/因果论证/效应判断**：Kıcıman + Jin 的直接教训（§2.6/2.7），估计器为纯计数；
3. **梯度式策略优化**（COMA/Retroformer/ProactRL 的训练侧）：无训练预算；
4. **DML / doubly-robust 完整实现**（DSL 的 ML 建模部分）：IPTW 用层内经验倾向近似并如实标注差异；
5. **从文本嵌入学习混杂表示**（Egami 1802.02163 的 latent representation）：混杂只用预注册词表；
6. **因果乘数进入 relevance/配额**：因果效用只作用于 final multiplier（`combinedScore × multiplier` 位置不变），**不进 RRF 通道权重、不改变正负配额与 outcome 池归属**（与 v0.4.0/v0.5.0 的硬边界一致）。

---

## 4. 四层映射对齐表

| 层 | 现有组件 | 因果引擎落点 |
|---|---|---|
| L1 任务模式 | `classify_task_pattern` 7 类 + taskFamily | **混杂分层主键**：CATE 按 taskPattern 分层输出（`strata[]` 表的 key）；辛普森检测在"模式间效应异号"时告警；估计结果按当前任务模式取用 |
| L2 策略模板 | 模板匹配/挖掘/效用（Beta 计数） | **处置扩展**：模板选择 = 处置 `do(template)`；模板效用升级为分层因果估计（`E[Y\|do(template)]−E[Y\|do(¬template)]`，同样分层于 taskPattern）；步骤级 `retrieval_intent`/`step_index` 是次级混杂 |
| L3 能力模块 | 记忆/模板/技能/文档元数据 + 效用 | **效用元数据升级**：`causalMeta` additive 字段（ATE/方差/样本量/strata/simpsonFlip/estimatorVersion）；Beta 计数保留为回退先验 |
| L4 激活动作 | `memory_retrieve`（步骤对齐检索+注入） | **do() 干预**：检索并注入 = 干预 M=1；配对续写记录（同前缀有/无检索）是 L4 的干预数据采集原语；排序处应用 `causalMultiplier`（封顶 ±20%，样本不足回退 Beta） |

---

## 5. 保守性设计（样本不足时的行为链）

1. `n_eff ≥ MIN_EFFECTIVE_N`（20）且当前层达标（每臂 ≥5）→ 用因果乘数（§1.5）；
2. 层不达标 → 剔除该层（`droppedStrata` 审计）；若全部层不达标 → `n_eff=0` → `causalApplied=false`，**回退现有 Beta 乘数**（`0.8+0.4·(h+1)/(h+h+2)`，行为与 v0.5.0 完全一致）；
3. 收缩：`ATE_shrunk = ATE × n_eff/(n_eff+20)`——任何有限样本下乘数都被拉向 1，永不外推；
4. 封顶：乘数 ∈ [0.8, 1.2]（±20%，与现行 Beta 乘数同区间——**因果引擎永不比现状更激进**）；
5. 辛普森 flip 于当前层 → 该层禁用因果乘数并提示分层解读；
6. 因果估计**只升不删**：负效应只压低乘数、不删除记忆（沿用 `status='deprecated'` 只标记不删除纪律）；负效应不改变 outcome 池归属（配额语义不变）；
7. `estimatorVersion` 进审计：估计公式/门槛变化必须升版本，旧估计可追溯；
8. 幂等：每任务每记忆至多一条反事实记录（沿用 feedback 幂等约束）。

---

## 6. 评测建议（scripts/eval：已知因果结构的合成数据）

在 `eval_dataset.py` 增加**因果结构生成器**（种子化、确定性）：

1. **反事实对生成**：每个 (记忆, 混杂层) 生成成对结局 `(Y(1), Y(0))`，真值 CATE 由结构注入（如层 A：P(Y(1))=0.7, P(Y(0))=0.5 → 真 ATE=+0.2）。
2. **辛普森结构注入**：两层真 ATE 同号（如 +0.2 / +0.1），但采样失衡（80% 样本来自基线成功率低、效应小的层）使**粗效应为负**——检测器应报 `simpsonFlip=true` 且分层估计恢复正号。
3. **选择性偏差注入**：处置概率随层变化（P(M=1|s) 从 0.1 到 0.9），验证 IPTW 对照与分层估计接近、与未加权差显著不同。
4. **验证断言**：
   - 分层 ATE 与真值偏差 ≤ 0.05 或落在 95% CI（覆盖率目标 ≥ 90% across seeds）；
   - 辛普森检测器在注入集上检出率 100%、无注入集上误报率 0；
   - 收缩：n_eff=10 时乘数比 n_eff=200 时更接近 1（单调性）；
   - 确定性：同 seed 双跑输出逐字节一致；
   - **零回归**：causalApplied=false 时检索输出与 v0.5.0 基线完全一致；配额遵守率 100%、自检索率 0 不变。
5. 标注扩展：`04-eval-schema.md` 的 episode 增加可选 `counterfactualPairs` 段（pairId/confounders/Y1/Y0/goldCate），由校验脚本强制防泄漏与引用完整性（沿用现有 7 条规则）。

---

## 7. 实施顺序建议（供 spec/impl 参考）

1. `causal_utils.py`：分层 ATE/CATE + IPTW 对照 + 辛普森检测 + 收缩（纯函数）+ unittest；
2. 反事实记录原语：`retrievals` 或新表 additive 字段 + `memory_record_counterfactual`（或 feedback 扩展）；
3. `retrieval.py` 乘数接入：`causalMultiplier` 与回退链（§5 行为链）+ 审计字段；
4. `domain.ts` additive（`causalMeta`）+ 工具契约；
5. `scripts/eval` 因果结构生成器与断言（§6）；
6. 模板效用（L2）复刻同一套估计（`templates.py` 的 `_prior_utility` 升级点）。

## 8. 引用与复核记录

| 论文/来源 | arXiv/出处 | 复核状态 |
|---|---|---|
| COMA: Counterfactual Multi-Agent Policy Gradients | 1705.08926 | ✅ 摘要原文引文 §2.1 |
| ProactAgent | 2604.20572 | ✅（03-survey-facts.md §2，同批复核） |
| Egami et al., How to Make Causal Inferences Using Texts | 1802.02163 | ✅ §2.3 |
| Egami et al., Using Imperfect Surrogates for Downstream Inference (DSL) | 2306.04746 | ✅ §2.4 |
| Schölkopf et al., Towards Causal Representation Learning | 2102.11107 | ✅ §2.5 |
| Kıcıman et al., Causal Reasoning and Large Language Models | 2305.00050 | ✅ §2.6 |
| Jin et al., Can LLMs Infer Causation from Correlation? (Corr2Cause) | 2306.05836 | ✅ §2.7 |
| Karimi et al., Algorithmic Recourse | 2002.06278 | ✅ §2.8 |
| Retroformer | 2308.02151 | ✅ §2.9 |
| Pearl, Causality (2009)；Pearl, The Seven Tools of Causal Inference (CACM 2019) | 书籍/期刊 | 引用格式已注明（非 arXiv） |
| Blyth, On Simpson's Paradox and the Sure-Thing Principle (JASA 1972)；Hernán & Robins, Causal Inference (权截断惯例) | 期刊/书籍 | 引用格式已注明（非 arXiv） |

8 篇 arXiv 全部抓取成功；误编号抓取 1 次（2305.17348 为无关光学论文）已弃用并如实记录。
