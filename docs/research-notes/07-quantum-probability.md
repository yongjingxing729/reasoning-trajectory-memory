# 07 · 多概率保持引擎：量子概率视角与设计落地建议（设计文档 §21 依据）

> 口径与 `02-template-algorithms.md` / `05-reasoning-rag.md` / `06-causal-ai.md` 一致：严格区分 **「论文/理论原文做法」** 与 **「MVP stdlib 工程近似」**；工程近似不声称复刻量子物理，只把**量子概率论**（作为 Kolmogorov 经典概率的推广形式体系）用作决策建模语言。
> 约束：Python 标准库 only、确定性（聚合顺序无关）、可审计（每个估计量可拆解）、可单测。
> 核实：本文 arXiv 文献以 `web_search` 复核（2026-11-09）；量子认知的核心文献为期刊/书籍（非 arXiv），按书籍/期刊格式如实标注；「因果 × 量子」经 2026-11-09 深度复核由「待复核」升级为「已复核」（NJP 18:063032 / PRX 7:031021），仍为相邻领域背景指针、不落地。
> 关键诚实声明：本引擎**不涉及量子计算、不涉及量子硬件、不宣称模型内部是量子系统**。它只借用量子概率的**形式语言**（叠加态、概率幅、干涉项、测量坍缩、密度矩阵、退相干）来描述并解决一个具体工程问题——**决策框架过早坍缩导致的信息损失**。

---

## 0. 定位与核心命题（先读）

**核心命题**：LLM 本质上是**概率模型**（在 token/候选上输出分布），但 Agent 的"壳子"（工具编排、argmax 决策、单条注入）把它**坍缩成单一输出**。这一步坍缩丢弃了分布中承载的信息，且是不符合现实世界"任务本身多值、可叠加"逻辑的。本项目要做的，不是给 LLM 加量子硬件，而是**在插件的检索—激活决策框架里保持多概率（叠加态）、延迟坍缩、显式测量**，把"坍缩"从隐式副作用变成一等可审计对象。

**与既有迭代的三级推进（诚实定位）**：

| 迭代 | 概率视角 | 估计量 | 处理的现象 |
|---|---|---|---|
| v0.5.0 | 经典（Kolmogorov）关联 | P(成功 \| 用过 m)，Beta 计数 | 相关 vs 不相关 |
| v0.6.0 | 因果（do-演算）干预 | E[Y\|do(m)]−E[Y\|do(¬m)]，分层 ATE | 相关 ≠ 因果；混杂/辛普森 |
| **v0.7.0** | **量子（广义概率论）** | **振幅 + 相位 + 干涉项；密度矩阵；延迟坍缩** | **叠加态被过早坍缩；顺序效应；组合效应 ≠ 边际和（干涉）** |

三者不是替代而是**逐级推广**：经典 ⊂ 因果 ⊂ 量子。v0.6.0 因果引擎是 v0.7.0 的**「完全退相干极限」**（见 §5）——当所有干涉项为 0、所有相位被抹平，量子引擎严格退化为因果引擎行为，这从结构上保证了**零回归**。

**必须先写进设计的识别假设（诚实标注，缺一不可）**：

1. **「多概率保持」是决策框架假设，不是模型本体假设**：我们只声称"过早坍缩损失信息、保持叠加态可保留更多决策信息"，不声称 LLM 内部遵循量子力学。
2. **相位来源 = 因果效应方向（可观测）**：相位 θ ∈ {0, π} 由 v0.6.0 因果引擎的 ATE 符号决定（正效应 = 0 相、负效应 = π 相），不是凭空假设的复数。
3. **组合效应（干涉）以可观测结局为锚**：干涉项是否真实存在，必须由配对/组合实验的**环境 outcome** 校验，不能仅靠自报 verdict（沿用 06 的代理标签教训）。
4. **坍缩是可逆性边界的诚实标记**：一旦坍缩即不可逆（信息丢失）；本引擎的核心纪律是"在最后一个负责任的时刻之前不坍缩"。
5. **确定性优先**：叠加/干涉/退相干全部是**确定性线性代数**（实数域即可），不含随机采样；随机采样只出现在显式、种子化的离线评测路径（Self-Consistency 式消融，见 §6）。

---

## 1. 量子概率作为广义概率论（形式工具，而非物理）

### 1.1 经典概率的边界在哪里

Kolmogorov 概率论的三大公理之一是可加性：不相容事件 A、B，`P(A∪B) = P(A) + P(B)`。由此推出两条**经验上常被违反**的性质：

- **全概率律（LTP）可加性**：`P(B) = Σ_i P(A_i)·P(B|A_i)`——没有"交叉项"。
- **条件顺序对称（可交换性）**：`P(B|A 先于 C) = P(C|A 先于 B)`——顺序无关。

量子概率论（von Neumann 1932 起）把"事件"从单一布尔代数推广到**希尔伯特空间上的投影算符**，于是：

- 状态 = **密度算符 ρ**（正半定、迹 1）；纯态 ρ=|ψ⟩⟨ψ|。
- 概率 = **玻恩定则** `P(i) = ⟨i|ρ|i⟩`。
- 交叉项（**干涉项**）与**顺序效应**（非对易观测量）自然出现。

本项目用的正是这一推广，与"量子认知"（quantum cognition）把量子概率用于**非物理系统（人类判断）**的用法同构：**当经典概率的可加性/顺序对称被观测违反时，量子概率是记录并操作这种违反的最小形式化工具**。

### 1.2 三个核心概念的形式化（本项目落地的最小集）

**① 叠加态与玻恩定则**。设候选集合 {i}（任务模式 / 策略模板 / 记忆），态：

```
|ψ⟩ = Σ_i φ_i |i⟩,   φ_i = r_i · e^{iθ_i}    (振幅)
P(i) = |φ_i|² = r_i²,   Σ_i r_i² = 1          (玻恩定则归一化)
```

- `r_i`（振幅模长）= 既有检索/匹配/效用分（RRF 融合分 + Beta + 因果幅度）归一化。
- `θ_i`（相位）= 因果效应方向（§4）：正 ATE → θ=0；负 ATE → θ=π。**MVP 只用实数振幅 φ_i ∈ {+r_i, −r_i}**（相位 {0,π}），不需要复数，纯 stdlib 可算。

**② 干涉项**。把候选 i、j **相干叠加**（振幅相加，而非概率相加）时，组合振幅平方为：

```
|φ_i + φ_j|² = |φ_i|² + |φ_j|² + 2·Re(φ_i φ_j*) = r_i² + r_j² + 2·r_i·r_j·cos(θ_i − θ_j)
```

经典（非相干）融合只加概率 `r_i² + r_j²`（RRF 通道融合正是如此）；量子叠加多出的交叉项 `2·r_i·r_j·cos(θ_i−θ_j)` 即**干涉项**：

- 同相（θ_i=θ_j）：干涉项 = +2·r_i·r_j（**建设性**——组合强于边际和）。
- 反相（θ_i=0, θ_j=π）：干涉项 = −2·r_i·r_j（**破坏性**——组合弱于边际和）。

这一项**正是边际因果估计（v0.6.0）够不到的地方**：ATE 是每单元的**边际**效应，而"记忆 i 与记忆 j 一起用"的**交互/组合效应**（因果效应修饰 interaction / effect modification）不在边际 ATE 里。干涉项就是它的确定性载体。这同时解释了 ToE 的"朴素经验管理低于无经验基线"：多条正边际效用记忆组合后，破坏性干涉可能把组合效应压到负值。

**③ 测量坍缩与延迟测量**。投影测量把态坍缩到本征子空间，**不可逆**：

```
测量 {P_i}:  ρ → Σ_i P_i ρ P_i   （对纯态 = 坍缩到某一 |i⟩）
```

量子力学的"延迟测量原理"：中间测量等价于最终测量，但**坍缩不可逆**，故过早测量 = 永久丢弃叠加信息。工程对应：检索—融合—排序后立刻"取 top-1/取前 k 条"就是一次隐式坍缩。本引擎的纪律是**把坍缩推迟到最后一个负责任的时刻**，并保留"部分测量（Lüders 更新）"用于证据增量时的再测量。

### 1.3 顺序效应（非对易）与 LLM 经验事实的对应

经典条件概率满足可交换性；量子概率中非对易观测量 `P(A 后 B) ≠ P(B 后 A)`。工程对应：**记忆/模板的注入顺序本身就是处置变量**（序列效应）。经验上，LLM 对选项顺序高度敏感（§2.4），这正是"顺序对称"被违反的直接证据，量子概率是记录它的最小形式化工具。

---

## 2. 前沿文献支撑（原文做法 vs 工程近似）

### 2.1 量子认知：干涉项与顺序效应的形式来源（期刊/书籍）

- **Busemeyer, Pothos, Franco & Trueblood (2011)，"A Quantum Theoretical Explanation for Probability Judgment Errors"，Psychological Review 118(2):193–218（DOI: 10.1037/a0022542）。** 经典：合取谬误（P(A∧B) > P(A)）与析取效应确由量子干涉项自然解释（复核确认：论文以量子概率统一解释一系列"违反经典概率"的判断错误，合取/析取谬误为核心案例）。**可借鉴点**：干涉项 `2·r_A·r_B·cos(Δθ)` 是"组合效应偏离边际和"的通用载体——本引擎把这一项用于**记忆/模板的组合效用**。**工程近似**：论文用复数 Hilbert 空间拟合人类判断；我们用**实数振幅（相位 {0,π}）** + 确定性计数，不拟合人、不拟合模型，只做有界组合校正。
- **Pothos & Busemeyer (2013)，"Can quantum probability provide a new direction for cognitive modeling?"，Behavioral and Brain Sciences 36(3):255–274（DOI: 10.1017/S0140525X12001525）。** 纲领性综述：量子概率在非物理系统（判断/决策/概念）的合法性。**可借鉴点**：为"在 Agent 决策框架里使用量子概率语言"提供方法论背书。**工程近似**：不引入完整量子决策理论，只取叠加/干涉/坍缩三件套。
- **Busemeyer & Bruza (2012)，*Quantum Models of Cognition and Decision*，Cambridge University Press；Khrennikov (2010)，*Ubiquitous Quantum Structure: From Psychology to Finance*，Springer。** 两本专著为"量子结构普遍存在于概率系统中"提供系统论述。引用为书籍（非 arXiv），如实标注。Khrennikov 书名含副题 "From Psychology to Finance"（2010）；Busemeyer & Bruza 书 ISBN 978-1-107-01199-1（2012）。
- **Wang & Busemeyer (2013)，"A Quantum Question Order Model Supported by Empirical Tests of an A Priori and Precise Prediction"，Topics in Cognitive Science 5(4):689–710（DOI: 10.1111/tops.12040）。** 顺序效应（question order effect）的量子模型，含先验精确预测的经验检验。**可借鉴点**：非对易测量 = 顺序效应。**工程近似**：注入顺序作为可审计维度，不训练顺序模型。
- **Yearsley & Busemeyer (2016)，"Quantum cognition and decision theories: A tutorial"，Journal of Mathematical Psychology 74:99–116（DOI: 10.1016/j.jmp.2015.11.005）。** 量子认知与决策理论教程（注意：发表年为 2016，非 2013）。**可借鉴点**：叠加/干涉/坍缩的完整形式化教程。**工程近似**：仅取三件套最小形式化，不引入完整量子决策理论。

### 2.2 Self-Consistency：多路径采样的经典先驱（arXiv:2203.11171，已复核）

- **原文**："self-consistency boosts the performance of chain-of-thought prompting with a striking margin ... including GSM8K (+17.9%), SVAMP (+11.0%), AQuA (+12.2%), StrategyQA (+6.4%) and ARC-challenge (+3.9%)"（Wang et al. 2022，arXiv:2203.11171，ICLR 2023；为相对 CoT greedy decoding 的提升）。
- **可借鉴点**：**单条贪心解码（= 坍缩到一条路径）丢弃概率质量，采样多路径并边缘化（多数投票）稳定地更好**——这正是"保持多概率优于单点坍缩"的经典（经典概率）证据。
- **工程近似**：Self-Consistency 需多次采样（显式随机）；本引擎的**线上路径不采样**，只把叠加态（振幅+相位）作为确定性数据保留并供下游消费；采样只出现在离线消融（种子化）里，用于量化"坍缩损失"。

### 2.3 Tree of Thoughts（arXiv:2305.10601，综述已列为 [6]）

- **可借鉴点**：显式多路径搜索树 = 经典概率的"多概率"搜索版；已是本项目 ToT 内置模板（`memory_tpl_builtin_tot`）的来源。
- **工程近似**：ToT 是采样+搜索；本引擎把"多候选叠加"沉淀为**数据表示**，而非运行时搜索。

### 2.4 LLM 选项顺序敏感性（arXiv:2308.11483，已复核）

- **原文**："we demonstrate a considerable performance gap of approximately 13% to 75% in LLMs on different benchmarks, when answer options are reordered, even when using demonstrations in a few-shot setting"（Pouya Pezeshkpour & Estevam Hruschka 2023，arXiv:2308.11483；另：敏感性源于模型在 top-2/3 候选间不确定 + 位置偏差，校准可带来至多 +8 个百分点提升）。
- **可借鉴点**：LLM 决策对顺序高度敏感 = 顺序对称被违反的经验证据 → 量子概率（非对易）而非经典概率（可交换）是更贴近的建模语言；同时为"注入顺序是处置变量"提供论据。
- **工程近似**：不声称修复 LLM 的顺序偏差，只把注入顺序显式化、可审计化，并把它纳入组合/序列效应实验。

### 2.5 因果 × 量子（相邻领域，已复核为背景指针，不落地）

- **Costa & Shrapnel (2016)，"Quantum causal modelling"，New Journal of Physics 18:063032（DOI: 10.1088/1367-2630/18/6/063032）。** 基于 process matrix 形式化的量子因果模型框架，定义因果 Markov 条件与忠实的量子类比。**可借鉴点**：为"因果 × 量子"提供理论相邻性（本引擎的相位=ATE 方向即其最浅层对应物）。
- **Allen, Barrett, Horsman, Lee & Spekkens (2017)，"Quantum common causes and quantum causal models"，Physical Review X 7:031021（DOI: 10.1103/PhysRevX.7.031021；arXiv:1609.09487）。** 形式化"量子共同原因"与量子因果模型。**可借鉴点**：同上，为共同原因/因果结构的量子化提供理论相邻性。**诚实标注**：纯理论、本引擎不实现任何量子因果模型，仅作背景指针（2026-11-09 已复核，仍不落地）。

### 2.6 Top-1 过自信 / LLM 校准（"坍缩损失"的旁证，2026-11-09 新增）

- **Guo, Pleiss, Sun & Weinberger (2017)，"On Calibration of Modern Neural Networks"，ICML 2017（PMLR 70:1321–1330；arXiv:1706.04599）。** 现代神经网络普遍**过自信**：top-1 softmax 置信度系统性高于真实准确率（温度缩放可修正）。**可借鉴点**：单点 argmax（= top-1 坍缩）不仅"选一条路径"，还携带**被系统性高估的置信度**——为"坍缩损失"（信息 + 校准双重损失）提供经典旁证。**工程近似**：本引擎不训练校准模型，仅把 top-1 坍缩的置信度与叠加态相干度对照审计。
- **Kadavath et al. (2022)，"Language Models (Mostly) Know What They Know"，arXiv:2207.05221。** 提出 P(True) 自评估（模型对自身答案正确性的概率判断），校准随模型规模提升而改善。**可借鉴点**：把"未坍缩分布 / 自评估"作为量化坍缩损失的对照旁证。**工程近似**：仅作评测对照列的旁证，不实现 P(True) 自评估。

---

## 3. 三个原语（MVP stdlib 确定性实现要点）

> 落地函数签名与常量以后续 `spec-0.7.0` 定稿为准，本节只给设计级要点（呼应"具体实现后续再开发"）。

1. **`build_superposition`**：输入候选（memoryId/templateId）+ 模长 `r`（来自 RRF+Beta+因果幅度归一化）+ 相位 `θ∈{0,π}`（来自 ATE 符号）→ 输出归一化振幅向量与**实对称密度矩阵 ρ**（`ρ_ij = φ_i·φ_j`，实数域，纯态）。
2. **`interference_pair(i,j)`**：`2·r_i·r_j·cos(θ_i−θ_j)`，纯四则；作为组合效用的有界校正项，进入候选对审计。
3. **`luders_update(ρ, evidence)`**：证据增量（新步骤反馈）时的部分测量更新，保留未观测子空间内的相干性——"再测量"能力。
4. **`coherence(ρ)`**：`Σ_{i≠j}|ρ_ij|`（l1 相干度），量化"还剩多少叠加未被坍缩"；0 = 完全退相干 = 经典。
5. **`decoherence(ρ, rate)`**：`ρ → (1−rate)·ρ + rate·diag(ρ)`——确定性退相干通道（§5 的零回归桥接）。
6. **确定性纪律**：全部整数/浮点四则 + 矩阵（实对称、小尺寸 ≤ `MAX_SUPERPOSITION_SIZE`），聚合顺序无关（先按候选序求和再归一化），双跑全等。

---

## 4. 四层映射与因果引擎的交融

| 层 | v0.6.0 现状 | v0.7.0 量子落点 | 与因果的交融 |
|---|---|---|---|
| L1 任务模式 | argmax → 单类 + 置信度/边际 | **叠加态**：输出 7 类的概率（振幅）向量而非单一类；`confidence/margin` 升级为振幅；模式只在需要单一模式的动作点"测量" | 模式 = 混杂分层主键（v0.6.0）；其**分布**成为叠加态，坍缩点可审计 |
| L2 策略模板 | argmax match → 单模板注入 | **模板叠加 + 干涉**：top-k 模板以振幅并存；结构重叠的模板产生干涉项 | 模板 = 处置 do(template)（v0.6.0）；多模板联合 = 干涉项承载的组合处置效应 |
| L3 能力模块 | Beta 效用 + causalMeta（ATE 幅度） | **振幅（模长）+ 相位**：模长 = 效用幅度，相位 = ATE 符号；密度矩阵非对角元 = 模块间相干 | **ATE 方向 → 相位**（θ=0 正 / π 负）；干涉项 = 交互效应（效应修饰） |
| L4 激活动作 | 检索排序 → 注入 top-k（隐式坍缩） | **延迟坍缩 + 显式测量 + 再测量**：注入的是带振幅/相位的叠加分布；坍缩推迟到最终承诺点 | do(retrieve) 的承诺点 = 测量；配对续写（v0.6.0）仍是坍缩 vs 不坍缩的对照原语 |

**交融的一句话**：v0.6.0 回答"使用 m 是否**导致**成功"（边际因果）；v0.7.0 回答"把多条候选**放在一起**（叠加）时，组合效应为何不等于边际和，以及过早坍缩丢掉了什么"（干涉 + 顺序）。相位把两条线**焊在一起**：因果方向决定干涉是建设还是破坏。

---

## 5. 保守性设计与退相干桥接（保证零回归）

1. **相位来源有界**：θ 仅取 {0,π}，由 ATE 符号决定；ATE 幅度不放大相位，只决定模长（沿用 v0.6.0 的 ±20% 封顶）。
2. **干涉项封顶**：`interference_pair` 只作为**有界校正项**（clip 到 ±0.20，与既有乘数区间一致），不进 RRF 权重、不破坏正反配额与 outcome 池。
3. **退相干 = 证据不足时的自动回退**：`decoherence(ρ, rate)` 的 rate 由有效样本量驱动（`rate = k/(n_eff+k)`，复用 v0.6.0 收缩先验）——样本不足 → 相位/干涉被抹平 → 退化为经典-因果路径。**「完全退相干极限」= v0.6.0 行为**，结构上保证零回归（`quantumDecisionEnabled=false` 时逐字节一致）。
4. **叠加尺寸封顶**：`MAX_SUPERPOSITION_SIZE`（初值 3），超过则按振幅取前 k 后**显式坍缩**（坍缩点进审计）。
5. **坍缩可审计、可追溯**：每次坍缩记录 `collapseReason`（rank_cutoff / sample_insufficient / explicit_commit / config_disabled）+ `coherenceBefore/After`。
6. **确定性**：无随机采样于线上路径；随机采样只在离线消融（固定 seed）。

---

## 6. 评测建议（scripts/eval：坍缩损失与干涉的受控测量）

1. **坍缩损失测量**：同一 held-out 任务，对比 (a) 传统 top-1 坍缩 vs (b) 保持 top-k 叠加（带振幅）供下游消费——量化"多概率保持"是否带来可测增量（主研究假设 H1 的 v0.7.0 版本）。
2. **顺序效应测量**：同一组候选，交换注入顺序，测量 outcome 变化（呼应 2308.11483；把顺序显式化为处置变量）。
3. **干涉测量**：构造同相（正 ATE×正 ATE）与反相（正 ATE×负 ATE）候选对，测量组合 outcome 与边际和的偏离——干涉项是否可被数据支持（gold 校验，代理标签纪律沿用 06 §2.4）。
4. **零回归**：`quantumDecisionEnabled=false` → 与 v0.6.0 检索输出逐字节一致；配额遵守率 100%、自检索率 0 不变。
5. **确定性**：同 seed 双跑全等；Self-Consistency 式采样消融（固定 seed、显式随机）作为独立对照列。

---

## 7. 越界（写入设计文档 §21.6 的"不做什么"）

1. **不涉及量子计算/量子硬件**：无 qubit、无量子电路、无复数域（MVP 实数振幅即可）；"量子"仅指概率论形式语言。
2. **不声称模型内部是量子系统**：不观测、不干预模型内部；叠加态是插件决策框架的**数据表示**。
3. **不做量子因果结构发现/量子因果模型**（Costa & Shrapnel / Allen et al. 越界，§2.5 仅指针）；
4. **不采样做线上 Self-Consistency**（采样 = 显式随机，仅在离线消融）；
5. **不把干涉/相位进 RRF 通道权重与正反配额**（沿用 v0.6.0 硬边界：只作用于最终 multiplier，封顶）；
6. **不生成"量子/坍缩"的解释性文本给模型**（审计输出 = 数值表，与 06 的"不生成反事实叙述"同款纪律）。

---

## 8. 引用与复核记录

| 来源 | 出处 | 复核状态 |
|---|---|---|
| Busemeyer, Pothos, Franco & Trueblood, "A Quantum Theoretical Explanation for Probability Judgment Errors" | Psychological Review 118(2):193–218 (2011) · [DOI 10.1037/a0022542](https://doi.org/10.1037/a0022542) | ✅ 期刊 · 2026-11-09 深度复核（题名/作者/卷期/DOI；合取/析取谬误确由干涉项解释） |
| Pothos & Busemeyer, "Can quantum probability provide a new direction for cognitive modeling?" | Behavioral and Brain Sciences 36(3):255–274 (2013) · [DOI 10.1017/S0140525X12001525](https://doi.org/10.1017/S0140525X12001525) | ✅ 期刊 · 2026-11-09 深度复核（题名/卷期/DOI） |
| Busemeyer & Bruza, *Quantum Models of Cognition and Decision* (2012)；Khrennikov, *Ubiquitous Quantum Structure: From Psychology to Finance* (2010) | Cambridge University Press (ISBN 978-1-107-01199-1)；Springer | ✅ 书籍（非 arXiv）· 2026-11-09 深度复核（书名/出版社/副题） |
| Wang & Busemeyer, "A Quantum Question Order Model Supported by Empirical Tests of an A Priori and Precise Prediction" | Topics in Cognitive Science 5(4):689–710 (2013) · [DOI 10.1111/tops.12040](https://doi.org/10.1111/tops.12040) | ✅ 期刊 · 2026-11-09 深度复核（补全题名/卷期） |
| Yearsley & Busemeyer, "Quantum cognition and decision theories: A tutorial" | Journal of Mathematical Psychology 74:99–116 (2016) · [DOI 10.1016/j.jmp.2015.11.005](https://doi.org/10.1016/j.jmp.2015.11.005) | ✅ 期刊 · 2026-11-09 深度复核（补全题名/卷期；发表年 2016，非 2013） |
| Wang, Wei, Schuurmans, Le, Chi, Narang, Chowdhery & Zhou, "Self-Consistency Improves Chain of Thought Reasoning in Language Models" | [arXiv:2203.11171](https://arxiv.org/abs/2203.11171)（ICLR 2023） | ✅ 2026-11-09 深度复核（题名/作者全名/提升数字：GSM8K +17.9%、SVAMP +11.0%、AQuA +12.2%、StrategyQA +6.4%、ARC-challenge +3.9%，相对 CoT greedy） |
| Yao et al., "Tree of Thoughts: Deliberate Problem Solving with Large Language Models" | [arXiv:2305.10601](https://arxiv.org/abs/2305.10601) | ✅（综述 [6]，已复核，沿用前次） |
| Pezeshkpour & Hruschka, "Large Language Models Sensitivity to The Order of Options in Multiple-Choice Questions" | [arXiv:2308.11483](https://arxiv.org/abs/2308.11483) | ✅ 2026-11-09 深度复核（作者全名/核心发现：选项重排导致约 13%–75% 性能差距） |
| Costa & Shrapnel, "Quantum causal modelling" | New Journal of Physics 18:063032 (2016) · [DOI 10.1088/1367-2630/18/6/063032](https://doi.org/10.1088/1367-2630/18/6/063032) | ✅ 2026-11-09 深度复核（由"待复核"升级；仍仅作背景指针、不落地） |
| Allen, Barrett, Horsman, Lee & Spekkens, "Quantum common causes and quantum causal models" | Physical Review X 7:031021 (2017) · [DOI 10.1103/PhysRevX.7.031021](https://doi.org/10.1103/PhysRevX.7.031021)（arXiv:1609.09487） | ✅ 2026-11-09 深度复核（由"待复核"升级；仍仅作背景指针、不落地） |
| Guo, Pleiss, Sun & Weinberger, "On Calibration of Modern Neural Networks" | ICML 2017, PMLR 70:1321–1330 · [arXiv:1706.04599](https://arxiv.org/abs/1706.04599) | ✅ 2026-11-09 新增（"坍缩损失/过自信"旁证） |
| Kadavath et al., "Language Models (Mostly) Know What They Know" | [arXiv:2207.05221](https://arxiv.org/abs/2207.05221) | ✅ 2026-11-09 新增（self-evaluation 旁证） |

复核口径（2026-11-09 深度复核）：量子认知 6 条（期刊/书籍）经 web_search/web_fetch 逐条核对题名、作者、卷期、DOI；Self-Consistency、顺序敏感性 2 条 arXiv 经 abs 页核对摘要原文数字；"因果 × 量子" 2 条（NJP / PRX）由"待复核"升级为"已复核"，但仍仅作背景指针、不落地；新增 Guo et al. (2017) 与 Kadavath et al. (2022) 作为"坍缩损失 / 过自信"旁证。所有题名/卷期/数字均以 DOI 或 arXiv abs 页为准；除 ToT（沿用此前已复核，本轮未重复抓取）外，已无"待复核"项。
