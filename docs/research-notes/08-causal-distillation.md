# 08 · 因果因子驱动的任务提炼：论文支撑与 MVP stdlib 落地建议（设计文档 §23 依据）

> 口径与 `02-template-algorithms.md` / `05-reasoning-rag.md` / `06-causal-ai.md` / `07-quantum-probability.md` 一致：严格区分 **「论文原文做法」** 与 **「MVP stdlib 工程近似」**；工程近似不声称复刻论文。
> 约束：Python 标准库 only、确定性（聚合顺序无关）、可审计（每个估计量可拆解）、可单测。
> 核实：MGPO（arXiv:2609.37930）、TQG-Exp（arXiv:2610.14877）、Credit Assignment 综述（arXiv:2604.09459）经 `web_fetch` 复核 arXiv 摘要页（2026-11-11，HTTP 200）；MemTrial（arXiv:2610.11732）、COFFEQA（arXiv:2610.12837）仅 `web_search` 命中、未逐字复核，标「待复核」（见 §8）。

---

## 0. 定位与识别假设（先读）

**目标**：把 v0.6.0 的**检索侧**因果信用分配（do(retrieve) 的 ATE）**前移到提炼侧**（do(distill) 的增量因果贡献 uplift），让"提炼什么进库"本身由因果信号决定——**先因果地决定记住什么，再因果地决定检索什么**。核心估计量（"奇妙因子"）：

```
uplift(m) = E[Y | do(distill m)] − E[Y | do(¬distill m)]
          = apparent(m) − inherited(m) = 增量贡献（incremental contribution）
```

- `do(distill m)`：把候选条目 m 写入库（M → M∪{m}）；
- `do(¬distill m)`：不写 m（库保持 M）；
- `Y`：下游任务流结局（环境/验证器终态）；**verdict 是代理标签**，不作结局。

**必须写进 spec 的识别假设（诚实标注，缺一不可）**：

1. **条件可交换性**：给定混杂层 S（任务模式/任务族/模型版本），处置 D=do(distill m) 与潜在结局 Y(d) 条件独立；S 只含可观测离散变量，**不声称捕捉全部混杂**。
2. **一致性（SUTVA）**：库条目间无干扰；配对 rollout 通过固定其余条目来隔离继承效用——这要求可执行 restored-state，否则继承效用无法识别。
3. **处置良定义**：do(distill m)=写 m；do(¬distill m)=不写 m。
4. **结局良定义**：Y∈{0,1}（任务成功/失败，与 v0.6.0 同源）。
5. **Verdict 是代理标签**：自评即便准确率 80–90% 仍有偏（Egami DSL 2306.04746）；因此估计量必须收缩、gold 校验只在 scripts/eval 的合成/标注数据上做。
6. **增量因果符号在纯文本历史下可能不可识别**（Credit Assignment 2604.09459）：符号只在对齐反事实对 / 分层达标时启用，否则收缩为 0、不写相位、退回关联路径。
7. **restored-state 反事实对是 gold 的唯一来源**；观察性分层估计是低成本近似，不声称等价。

---

## 1. MGPO —— Learning What to Remember（arXiv:2609.37930）

**论文原文做法**：

- 题名 "Learning What to Remember: Long-horizon Counterfactual Memory Optimization"（Jiaming Tang, Mingyan Liu, Armin Sarabi，2026）。
- 核心命题原文："learning what to remember is fundamentally a credit-assignment problem"。
- 方法：把每次 memory rewrite 的**边际/增量贡献**从"继承自既有已存信息的效用"里隔离出来——反事实地固定"其余已存信息"，只翻转"是否写这次记忆"。
- 结果：提取提升 + **平均记忆长度相对优化前降低近 80%**。

**MVP 工程近似**：

- `uplift(m) = apparent(m) − inherited(m)` 是"增量贡献分离"的确定性形式：`apparent`=写 m 后库效用，`inherited`=不写 m 仅靠已有库的效用，二者之差即该条记忆自身的边际贡献。
- 用**配对 rollout（restored-state 反事实）** + **观察性分层估计**（L1 分层 + Laplace 平滑 + 收缩）估计 uplift；**不落地**原文的记忆改写/检索器**梯度优化**（越界 §7.1）。
- 门控"学会不记住" = uplift 低于阈值拒绝入库 → 记忆库不被近似重复条目撑大（对应原文"平均记忆长度降低近 80%"的**工程目标**，不声称复现其数值）。

---

## 2. TQG-Exp —— Beyond Outcome Feedback（arXiv:2610.14877）

**论文原文做法**：

- 题名 "Beyond Outcome Feedback: Trajectory-Quality-Guided Experience-based Learning"（Bingjie Zhang 等，2026）。
- Trajectory Quality Loss（TQL）**联合任务完成 + 过程质量**；用**配对 rollout（有/无检索经验）**估计经验贡献并更新效用。
- 结果：ALFWorld/WebShop/ScienceWorld 上 Avg.@3 相对最强基线 **+4.50 / +8.55 pp**（DeepSeek-V4-Flash / Qwen3.5-Flash）。

**MVP 工程近似**：

- 借其**"配对 rollout（有/无经验）估计经验贡献"**的估计思想，把"有/无检索经验"改写为"有/无蒸馏该条 m"（do(distill) vs do(¬distill)），作为提炼侧 uplift 的 gold 证据。
- **不训练 TQL / 过程质量模型**（越界 §7.2）；"过程质量"仅用既有域内 verifier/evidence 代理，不引入过程评分模型（沿用 MVP"结果/过程分离"内建防御）。
- Avg.@3 增益数字仅作"经验贡献可被配对 rollout 估计并带来增益"的旁证，**不声称本插件复现其数值**。

---

## 3. Credit Assignment 综述 —— From Reasoning to Agentic（arXiv:2604.09459）

**论文原文做法**：

- 题名 "From Reasoning to Agentic: Credit Assignment in RL for LLMs"（Chenchen Zhang，2026，69 篇综述）。
- 关键句原文："text-only histories can leave even the sign of credit unidentified"。
- restored-state 对照识别协议特定因果对比；**CA-ID Card**（estimand + evidence provenance + falsification test）。

**MVP 工程近似**：

- "符号不可识别" → uplift 符号只在对齐反事实对 / 分层达标时启用，否则收缩为 0（§0 假设 6）；restored-state 反事实对 = gold（§0 假设 7）。
- **CA-ID Card 三字段**进 MemoryItem 的 distillCausalMeta：`distillEstimand` / `distillEvidenceProvenance` / `distillFalsificationTest`（§23.5），把每条因果声明链到可证伪性。
- 这是设计文档 §22 已收录的同一篇；本节把它在**提炼侧**落地（§22.3 的"相位符号可能不可识别"在写路径上的兑现）。

---

## 4. 补充文献（待复核，不落地）

| 论文 | 主题 | 与本项目关联 | 复核状态 |
|---|---|---|---|
| MemTrial（arXiv:2610.11732） | Learning When to Trust Memory in LLM Portfolio Agents | "何时信任/不信任记忆"与门控"学会不记住"同源；可作提炼门控的信任侧旁证 | ⚠️ 待复核（web_search 命中，未逐字复核） |
| COFFEQA（arXiv:2610.12837） | Counterfactual-Guided Few-Shot Distillation for LLM Question Answering | "反事实引导蒸馏"与本节"反事实引导提炼"思想同源 | ⚠️ 待复核（web_search 命中，未逐字复核） |

两条均**不落地**：仅在复核通过并确有必要时，作为门控/反事实引导的补充论据。

---

## 5. 三落点与四层映射（对齐设计文档 §23.3/§23.4）

| 落点 | MVP 机制 | 论文对应 |
|---|---|---|
| ① 提炼门控 | `uplift_shrunk < distillUpliftThreshold` → 拒绝入库（`reason='below_distill_uplift_gate'`） | MGPO 的"learn what to remember" = 信用分配问题；拒绝无增量条目 |
| ② 提炼权重 | 蒸馏 `confidence`/效用 = `uplift_shrunk` 有界映射；`insightConfidence` 降级为辅助 | 增量贡献替代自评/关联作为效用主信号 |
| ③ 相位写入 | `distillPhase = (uplift_shrunk ≥ 0 ? 0 : π)`，写时定相 | 增量符号 → v0.7.0 量子相位（写时即定） |

| 层 | v0.6.0（检索侧） | v0.8.0（提炼侧） |
|---|---|---|
| L1 任务模式 | 混杂分层主键（检索 ATE stratum） | 混杂分层主键（提炼 uplift stratum） |
| L2 策略模板 | 处置 do(template) | 模板蒸馏处置 do(distill template) |
| L3 能力模块 | causalMeta additive | distillCausalMeta additive（uplift/inherited/样本量/相位/CA-ID Card） |
| L4 激活动作 | do(retrieve)+配对续写 | do(distill)+restored-state 反事实（配对 rollout） |

---

## 6. 保守性设计（样本不足时的行为链）

1. `n_eff ≥ 20` 且每臂 ≥5 → 启用 uplift 因果分量（门控/权重/相位）；
2. 层不达标 → 剔除该层（`droppedStrata` 审计）；全部层不达标 → `n_eff=0` → 门控回退关联路径、权重回退 `insightConfidence`、相位不写（=0），行为与 v0.7.0 完全一致；
3. 收缩：`uplift_shrunk = uplift_stratified × n_eff/(n_eff+20)`——任何有限样本下被拉向 0；
4. 封顶：`clip(uplift_shrunk, ±0.20)`（与现行 Beta/因果乘数同区间，**永不比现状更激进**）；
5. 辛普森 flip 于当前层 → 该层禁用因果分量并提示分层解读；
6. **只升不删**：负 uplift 只拒绝新写入、不删除已有条目；不改 outcome 池归属（正反配额语义不变）；
7. `estimatorVersion` 进审计：估计公式/门槛变化必须升版本，旧估计可追溯；
8. 幂等：每任务每候选条目至多一条蒸馏因果事件（沿用 feedback 幂等约束）。

---

## 7. 越界（不落地项）

1. **MGPO 的 memory-rewrite 梯度优化 / 检索器微调**（无训练基础设施）；
2. **TQG-Exp 的 TQL 训练 / 过程质量模型**（无训练；用域内 verifier 代理过程质量）；
3. **LLM 判断因果增量**（Kıcıman 2305.00050 "LLMs ignore the actual data" / Jin 2306.05836 教训）——纯确定性计数；
4. **uplift 进 RRF 通道权重与正反配额**（沿用 v0.6.0 硬边界：只作用于提炼门控/权重/相位）；
5. **线上 restored-state rollout**（识别成本高；配对 rollout 只在离线评测/标注集做，线上用观察性分层 + 收缩）；
6. **删除低 uplift 已有条目**（只拒绝新写入，不改 outcome 池归属）。

---

## 8. 引用与复核记录

| 论文/来源 | arXiv | 复核状态 |
|---|---|---|
| MGPO, "Learning What to Remember: Long-horizon Counterfactual Memory Optimization"（Tang, Liu, Sarabi 2026） | 2609.37930 | ✅ web_fetch 摘要页（2026-11-11，HTTP 200） |
| TQG-Exp, "Beyond Outcome Feedback: Trajectory-Quality-Guided Experience-based Learning"（Bingjie Zhang 等 2026） | 2610.14877 | ✅ web_fetch 摘要页（2026-11-11，HTTP 200） |
| Zhang, "From Reasoning to Agentic: Credit Assignment in RL for LLMs"（69 篇综述，2026） | 2604.09459 | ✅ web_fetch 摘要页（2026-11-11，HTTP 200；设计文档 §22 上一轮已复核） |
| MemTrial, "Learning When to Trust Memory in LLM Portfolio Agents" | 2610.11732 | ⚠️ 待复核（web_search 命中，未逐字复核） |
| COFFEQA, "Counterfactual-Guided Few-Shot Distillation for LLM Question Answering" | 2610.12837 | ⚠️ 待复核（web_search 命中，未逐字复核） |
| 沿用依据：COMA（1705.08926）/ ProactAgent（2604.20572）/ Egami DSL（2306.04746）/ Kıcıman（2305.00050）/ Jin Corr2Cause（2306.05836）/ Karimi（2002.06278）/ Retroformer（2308.02151） | — | ✅ 已由 `06-causal-ai.md` §8 复核（2026-11-06），本节沿用 |

**复核口径**：3 篇主论文（MGPO / TQG-Exp / Credit Assignment）经 `web_fetch` 逐字核对摘要页关键句与数字（2026-11-11，HTTP 200）；2 篇补充文献（MemTrial / COFFEQA）仅 `web_search` 命中、未逐字复核，故标「待复核」且不落地。其余沿用论文以 `06-causal-ai.md` §8 的既有复核为准，不重复抓取。
