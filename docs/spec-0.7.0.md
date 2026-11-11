# v0.7.0 规格：多概率保持引擎（从单点坍缩到多概率保持）

- 日期：2026-11-11
- 状态：FINAL —— 已吸收 `docs/research-notes/07-quantum-probability.md` 与设计文档 §21，并落实 `docs/test-plan-0.7.0.md` §7 六条裁决（Q1–Q6）
- 上游依据：设计文档 `reasoning-trajectory-memory-design.md` §21（三原语 / 数据模型·配置·多概率消费格式 / 识别假设与越界 / 术语表）+ `07-quantum-probability.md` §3/§5/§6
- 实现者：code-developer；调试：debugger；监听：watcher；文档：lead
- 风格对齐：`docs/spec-0.6.0.md`

---

## 1. 诊断与目标

LLM 是概率模型，但 Agent 壳（工具编排 / argmax 决策 / 单条注入）把它坍缩为单一输出，过早丢失叠加信息。本引擎把检索—融合—排序后的"取 top-k"从隐式坍缩变成**显式、可审计、可再测量**的一等数据表示：

1. **叠加态（一等数据表示）**：候选以 `|ψ⟩ = Σ_i φ_i|i⟩` 并存，实数振幅 `φ_i = r_i·cos(θ_i)`，相位 `θ_i ∈ {0, π}`；概率由玻恩定则 `P(i)=|φ_i|²` 提取。
2. **干涉项（组合效应载体）**：`2·r_i·r_j·cos(θ_i−θ_j)` 承载边际因果（v0.6.0）够不到的交互/组合效应。
3. **延迟坍缩 + 显式测量 + 再测量**：把坍缩推迟到最后一个负责任的时刻；证据增量时用 Lüders 部分测量保留未观测子空间相干。
4. **退相干桥接（零回归机制化）**：`ρ → (1−rate)·ρ + rate·diag(ρ)`；完全退相干 = v0.6.0 行为。

**三级推进（逐级推广而非替代）**：经典（v0.5.0）⊂ 因果（v0.6.0）⊂ 量子（v0.7.0）。

## 2. 已裁决的设计疑点（Q1–Q6，逐条冻结）

| 疑点 | 裁决 |
| --- | --- |
| Q1 退相干语义 | 自动退相干是量子路径固有组成，**仅 `quantumDecisionEnabled=true` 时求值**；开关关闭完全跳过量子路径、不做任何退相干计算，输出与 v0.6.0 逐字节一致。`decoherenceRate`（默认 0）是叠加在自动速率之上的手动偏置，与自动速率取和后 clip 到 [0,1]。自动速率 `k/(n_eff+k)`，k=DECOHERENCE_PRIOR=20（复用 v0.6.0 SHRINK_PRIOR）。 |
| Q2 干涉 gold 边界 | 合成 gold 只验证机制算术，不验证真实联合因果；在 spec 与评测诚实标注"合成 gold ≠ 真实联合因果已识别"。 |
| Q3 `superposition` 关闭语义 | 关闭时字段**完全缺席**（`"superposition" not in result`），非空数组，保证输出字典结构与 v0.6.0 逐字节一致。 |
| Q4 `interferenceDelta` 双列 | 审计同时存原始值 `interferenceDeltaRaw` 与封顶值 `interferenceDelta`（clip ±INTERFERENCE_CAP=0.20）；封顶值进最终 multiplier，原始值供"每个估计量可拆解"，二者分离。 |
| Q5 顺序审计边界 | 插件保证 `superposition[]` 输出有序并审计顺序，**不承诺下游遵守注入顺序**（越界 §8.7）。 |
| Q6 双负建设性语义 | "建设性/破坏性"指"组合振幅平方相对边际和的偏离方向"，不指"接近/远离成功"；双负相干交叉项为正 = "两个负振幅相干后组合更负、偏离成功更远"。术语表见 §9。 |

## 3. 数据模型（additive，零回归）

### 3.1 新独立 domain `reasoning_memory_quantum`（version 1，与 `reasoning_memory_causal` 并列）

```
quantumEventSchema = {
  eventId,                 // 确定性 id
  retrievalId,             // 关联的检索审计；空串表示 plan_task 侧坍缩
  kind: 'memory' | 'template',
  unitIds: string[],       // 参与叠加的候选 id（按注入顺序）
  amplitudes: number[],    // 归一化振幅 r
  phases: number[],        // {0, pi}
  coherence: number,       // 坍缩前 l1 相干度
  coherenceAfter: number,  // 坍缩后 l1 相干度
  collapseReason: 'rank_cutoff' | 'sample_insufficient' | 'explicit_commit' | 'config_disabled',
  estimatorVersion: string,
  createdAt: int,
}
```

- 每 retrieval 最多 `MAX_QUANTUM_EVENTS_PER_RETRIEVAL = 4` 条坍缩审计；域内 FIFO 上限 `MAX_QUANTUM_EVENTS = 2000`。
- MVP：坍缩点仅在叠加尺寸截断（rank_cutoff）时产生一条审计记录；样本不足回退不产生新记录（其行为即 v0.6.0 路径）。

### 3.2 检索审计候选 additive 字段（全部带默认值）

`retrievalCandidateSchema` 新增：`amplitude: number default 0`、`phase: number default 0`、`interferenceDelta: number default 0`、`coherence: number default 0`、`collapseReason: string default ''`。旧介质（无这些字段）读回不报错、默认值填充。

### 3.3 多概率消费格式（一等契约）

`quantumDecisionEnabled=true` 时，`memory_retrieve` / `memory_plan_task` 除传统 top-k 摘要外附带 `superposition[]`，每条：

```
{ id, amplitude r, phase θ, p=|φ|², interferenceDeltaRaw, interferenceDelta, coherence, collapseReason }
```

顺序 == 检索/匹配排序顺序（顺序即处置变量，仅插件侧可审计）。开关关闭时字段完全缺席。

## 4. Python 核心 `qprobability.py`（纯 stdlib、实数域、确定性、聚合顺序无关、无 random/numpy）

### 4.1 常量（`constants.py` 新增）

```
QUANTUM_ESTIMATOR_VERSION = "dsh-reasoning-memory/quantum-1"
MAX_SUPERPOSITION_SIZE = 3
INTERFERENCE_CAP = 0.20
DECOHERENCE_PRIOR = 20          # 自动速率 k/(n_eff+k)，复用 v0.6.0 SHRINK_PRIOR
MAX_QUANTUM_EVENTS = 2000
MAX_QUANTUM_EVENTS_PER_RETRIEVAL = 4
```

相位域仅 `{0, π}`；`PHASE_TOLERANCE = 1e-12` 用于相位合法性判定（超出容忍即 `ValueError`）。

### 4.2 函数签名（五原语）

```python
def interference_pair(*, r_i: float, r_j: float, phase_i: float, phase_j: float) -> float:
    """2·r_i·r_j·cos(θ_i−θ_j)，纯四则；相位 {0,π} 下即 2·r_i·r_j·s_i·s_j（s=sign，精确无浮点误差）。"""

def build_superposition(*, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """candidates: [{id, amplitude(r>=0), phase({0,pi})}]。
    返回 {superposition[], densityMatrix, coherence, trace}：
    - 先按候选序求 Σr²，再归一化 r_i' = r_i/sqrt(Σr²)（聚合顺序无关）。
    - φ_i = r_i'·s_i；ρ_ij = φ_i·φ_j（实对称、迹≈1）。
    - 每条 superposition 项含 id/amplitude/phase/p/interferenceDeltaRaw/interferenceDelta/coherence。
    - interferenceDeltaRaw_i = φ_i·Σ_{j≠i}φ_j；interferenceDelta_i = clip(±INTERFERENCE_CAP)。
    - 每候选 coherence_i = Σ_{j≠i}|ρ_ij|；全局 coherence = Σ_{i≠j}|ρ_ij|。
    - 全零振幅 → 退化态（全 0，无 NaN/Inf，不除零）。"""

def coherence(*, rho: list[list[float]]) -> float:
    """Σ_{i≠j}|ρ_ij|（l1 相干度）；0 = 完全退相干 = 经典。"""

def decoherence(*, rho: list[list[float]], rate: float) -> dict[str, Any]:
    """ρ → (1−rate)·ρ + rate·diag(ρ)。rate clip [0,1]。
    返回 {densityMatrix, coherenceBefore, coherenceAfter}。"""

def luders_update(*, rho: list[list[float]], ids: list[str], observed_ids: list[str]) -> dict[str, Any]:
    """Lüders 部分测量：观测/未观测子空间之间相干坍缩为 0，各自内部相干保留。
    返回 {densityMatrix, coherenceBefore, coherenceAfter}。"""
```

### 4.3 确定性纪律

全部整数/浮点四则 + 实对称矩阵（尺寸 ≤ MAX_SUPERPOSITION_SIZE）；归一化先按候选序求和再除，输出不重排（`superposition[]` 保序）；密度矩阵实对称 `ρ_ij==ρ_ji`、迹≈1；双跑逐字节一致；模块内无 `import random` / `import numpy`。

## 5. 检索 / 计划注入

- `_retrieve`（`retrieval.py`）与 `plan_task`（`templates.py`）新增只读配置入参（缺省关闭）：`quantumDecisionEnabled=False`、`maxSuperpositionSize=3`（clamp [1,3]）、`phaseFromCausal=True`、`decoherenceRate=0.0`（clamp [0,1]）。
- 关闭时**不注入任何量子字段**、不改任何既有字段（与 v0.6.0 逐字节一致）。
- 开启时，对已选定的 top-k 候选（`_retrieve` 的 `selected` / `plan_task` 的 `matchedTemplates`）：
  1. 振幅 `r_i` = `combinedScore`（`_retrieve`）/ `score`（`plan_task`）；相位 `θ_i`：`phaseFromCausal=true` 时由 `causalUplift`（=ateShrunk）符号决定（≥0 → 0、<0 → π），否则或样本不足 → 0；`plan_task` 无检索侧因果 → θ=0。
  2. 超过 `maxSuperpositionSize` → 截断至 top-k'，`collapseReason='rank_cutoff'`，审计 `coherenceBefore/After`。
  3. `build_superposition` 得到 `superposition[]` + `quantum` 审计摘要（`coherence/coherenceBefore/coherenceAfter/collapseReason/estimatorVersion`）。
- **干涉不进 RRF 权重与正反配额**；`interferenceDelta`（封顶）仅作为叠加审计中的最终 multiplier 校正值（供下游在承诺点消费），MVP 不改 `results[]`/`candidateScores[]` 的排序与 multiplier（延迟坍缩：检索不被迫坍缩，叠加态作为附加审计面输出）。

## 6. TypeScript 插件层

- `src/quantum-domain.ts`：独立 domain `reasoning_memory_quantum`（§3.1）。
- `src/domain.ts`：`retrievalCandidateSchema` 增 §3.2 五字段（全带默认值）。
- `src/index.ts`：配置 `quantumDecisionEnabled`（false）、`maxSuperpositionSize`（3，clamp [1,3]）、`phaseFromCausal`（true）、`decoherenceRate`（0，clamp [0,1]）；`memory_retrieve` / `memory_plan_task` 转发量子配置到 Python 核心并把 `superposition[]` 透出到工具输出（关闭时字段缺席）；开启且发生坍缩时写入一条量子审计（FIFO 上限）。
- 工具输出 schema 增可选 `superposition` 字段（非 required；关闭时不出现）。

## 7. 四层映射对齐

| 层 | v0.6.0 | v0.7.0 量子落点 |
| --- | --- | --- |
| L1 任务模式 | argmax → 单类 | 叠加态：7 类概率向量，仅在需单类动作点"测量" |
| L2 策略模板 | argmax match → 单模板 | 模板叠加 + 干涉（`plan_task.superposition[]`） |
| L3 能力模块 | Beta 效用 + causalMeta | 振幅（模长=效用幅度）+ 相位（=ATE 符号） |
| L4 激活动作 | 排序 → 注入 top-k | 延迟坍缩 + 显式测量 + 再测量 |

## 8. 识别假设与越界

**识别假设（5 条，`07-quantum-probability.md` §0 原样）**：① 多概率保持是决策框架假设，非模型本体假设；② 相位来源 = 可观测因果方向；③ 干涉以环境 outcome 为锚（合成 gold 只验证机制算术，不验证真实联合因果）；④ 坍缩是不可逆信息丢失边界；⑤ 确定性优先。

**越界（7 条）**：① 不涉及量子计算/量子硬件，MVP 实数振幅；② 不声称模型内部是量子系统；③ 不做量子因果结构发现/量子因果模型；④ 不采样做线上 Self-Consistency（仅离线评测固定 seed）；⑤ 不把干涉/相位进 RRF 通道权重与正反配额（只作用于最终 multiplier，封顶 ±20%，负效应只压低不删除）；⑥ 不生成"量子/坍缩"解释性文本给模型；⑦ 不承诺下游遵守注入顺序（插件只保证输出有序并审计顺序）。

## 9. 术语速查（工程含义）

| 术语 | 工程含义 |
| --- | --- |
| 叠加态 \|ψ⟩ | 多候选并存、不坍缩为单点的决策表示 |
| 振幅 φ=r·cos(θ) | 模长 r=效用/相关幅度；相位 θ=因果方向（{0,π}） |
| 玻恩定则 P(i)=r_i² | 由振幅提取"被采用"概率（归一化） |
| 干涉项 2·r_i·r_j·cos(θ_i−θ_j) | 相干叠加相对概率相加的偏离；建设/破坏指"组合强于/弱于边际和"，**成功方向由相位承载**（双负建设=更远离成功） |
| 测量/坍缩 | 把叠加态固定为单一输出的不可逆操作 |
| 部分测量（Lüders） | 证据增量时更新态、保留未观测相干 |
| 密度矩阵 ρ | 态的一等表示；非对角元=相干 |
| 退相干 | 干涉/相位被抹平 → 退化为经典-因果路径 |

## 10. 测试要求

- Python（`test/test_reasoning_memory.py` 新增 `QuantumProbabilityTests` / `QuantumRegressionTests`）：五原语纯算术（G4.1–G4.3）、确定性双跑与顺序无关（G5.1–G5.3）、边界（G6.1–G6.7：尺寸封顶、退相干 rate=1 非对角元归零、辛普森 × 相位符号、负效应只压低不删除、非法相位/除零/空/单候选、数值稳定、interferenceDelta 封顶 ±20%）、零回归（G1.1–G1.3：开关缺省与 `=false` 输出与 v0.6.0 基线 sha256 一致、`"superposition" not in result`、旧介质读回）。
- Node（`test/quantum.test.mjs`）：配置校验（G6.8）、mock 宿主生命周期零回归（G1.4）、`quantumDecisionEnabled=true` 时 `superposition[]` 六键 + 顺序（G2.4/G3.3）、同 seed 双跑一致（G5.4）、量子审计 domain + FIFO（G6.9）。
- `npm test` 全绿；`scripts/eval` 在 `quantumDecisionEnabled=false` 时指标与 v0.6.0 持平。

## 11. 验收清单

1. `npm test` 全绿；`scripts/eval` 指标与 v0.6.0 持平（零回归）。
2. 五原语在合成已知结构上符号/量级正确（干涉符号、玻恩归一化、退相干极限）。
3. 文档同步（README/MVP_EVALUATION v0.7.0 段，诚实标注量子假设与越界）。
4. git 提交（逻辑拆分）。
