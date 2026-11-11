# v0.8.0 规格：因果因子驱动的任务提炼（从关联性提炼到因果提炼）

- 日期：2026-11-11
- 状态：FINAL —— 已吸收 `docs/research-notes/08-causal-distillation.md` 与设计文档 §23
- 上游依据：设计文档 `reasoning-trajectory-memory-design.md` §23（奇妙因子 / 三落点 / 数据模型 / 识别假设与越界 / 测试要点）+ `08-causal-distillation.md` §5/§6
- 实现者：code-developer；调试：debugger；监听：watcher；文档：lead
- 风格对齐：`docs/spec-0.6.0.md` / `docs/spec-0.7.0.md`

---

## 1. 诊断与目标

v0.4.0 起的任务提炼由**关联性信号**驱动（helpful/harmful Beta 计数 + `insightConfidence` 自评），两者都不回答"提炼这条记忆是否**导致**下游更好"，且受**继承效用污染**（MGPO）：候选条目表观效用 `apparent(m)` 可能继承自已存库信息，而非自身贡献。

本迭代把 v0.6.0 的检索侧因果信用分配**前移到提炼侧**，用 do(distill) 的增量因果贡献 `uplift(m)` 作为提炼决策的一等信号：

```
uplift(m) = E[Y | do(distill m)] − E[Y | do(¬distill m)]
          = apparent(m) − inherited(m) = 增量贡献（奇妙因子）
```

一句话：**先因果地决定记住什么，再因果地决定检索什么**。

## 2. 奇妙因子与估计

- `apparent(m) = E[Y | 库 = M ∪ {m}]`（写 m 后的库性能）；`inherited(m) = E[Y | 库 = M]`（不写 m 的库性能）；`uplift = apparent − inherited`。
- **配对 rollout 估计（gold）**：`uplift_hat = mean(Y_with − Y_without)`，同下游任务/前缀，库只差"是否含 m"（restored-state 反事实）。
- **观察性分层估计（线上近似）**：按 L1 taskPattern 分层 2×2（Laplace），`uplift_s = ŷ_{1s} − ŷ_{0s}`，`uplift_stratified = Σ_s (n_s/N)·uplift_s`；辛普森防护同 v0.6.0。
- **收缩/封顶（保守核心）**：`uplift_shrunk = uplift_stratified × n_eff/(n_eff+shrink_prior)`；`clip(±0.20)`；`n_eff ≥ 20` 且每臂 ≥5 才启用因果分量，否则回退关联路径。

## 3. 三落点

| 落点 | 机制 | 诚实边界 |
| --- | --- | --- |
| ① 提炼门控 | `distillGateEnabled=true` 且 `uplift_shrunk < distillUpliftThreshold`（默认 0）→ 拒绝入库，`reason='below_distill_uplift_gate'` | 只拒绝新写入，不删除已有条目；因果增量估计是计数近似，不是校准质量判定 |
| ② 提炼权重 | 蒸馏条目 `confidence` = `clip(0.5 + uplift_clipped, 0, 1)`（uplift 有界映射）；`insightConfidence` 降级为辅助 | `uplift_clipped` 收缩+封顶，永不比关联路径更激进 |
| ③ 相位写入 | `distillPhase = (uplift ≥ 0 ? 0 : π)`，写时定相；检索侧 v0.7.0 直接消费 | 符号仅在反事实对/分层达标时可信，否则为 0 |

**与 insightConfidence 门控共存**：自评门控（`minInsightConfidence`）作为**自评下限**先执行，uplift 门控作为**因果下限**后执行；uplift 样本不足时权重/相位回退关联路径（v0.4.0–v0.7.0 行为不变）。

## 4. 数据模型（additive，零回归）

### 4.1 新独立 domain `reasoning_memory_distill_causal`（version 1，与 `reasoning_memory_causal` 并列）

```
distillCausalEventSchema = {
  eventId, memoryId, taskId,
  taskPattern: 7 类枚举,
  treatment: 'distilled' | 'not_distilled' | 'counterfactual_with' | 'counterfactual_without',
  outcome: 'success' | 'fail',
  pairId, stepIndex, modelVersion, createdAt,
}
```

- 每 memoryId 事件上限 `MAX_DISTILL_EVENTS_PER_UNIT = 200`（FIFO）。
- 观察性事件（distilled/not_distilled）喂分层/crude/配对估计；`counterfactual_with/without` 喂配对估计。

### 4.2 MemoryItem additive 字段（全部带默认值）

| 字段 | 类型 | 默认 | 语义 |
| --- | --- | --- | --- |
| `distillUplift` | number | `0` | 收缩后增量因果贡献（clip ±0.20） |
| `distillInherited` | number | `0` | 继承效用估计（apparent − uplift） |
| `distillSamples` | int | `0` | 有效样本量 n_eff |
| `distillPhase` | number | `0` | θ∈{0,π}，写时定相 |
| `distillGate` | `'passed'\|'rejected'\|'na'` | `'na'` | 门控结果；na=未启用/样本不足 |
| `distillEstimand` | string | `''` | CA-ID Card：estimand |
| `distillEvidenceProvenance` | string | `''` | CA-ID Card：证据来源 |
| `distillFalsificationTest` | string | `''` | CA-ID Card：可证伪性 |

零回归口径：Python 仅在提供 `distillCausalEvents` 时才写这 8 字段；否则 MemoryItem 与 v0.7.0 逐字节一致（旧介质由 TS `memoryItemSchema` 默认值填充读回）。

### 4.3 配置（additive，默认关闭/中性）

`distillGateEnabled`（false）、`distillUpliftThreshold`（0）、`phaseFromDistillUplift`（true）、`distillShrinkPrior`（20）、`distillMinArmSamples`（5）。

## 5. Python 核心 `distill_causal.py`（纯 stdlib、确定性）

### 5.1 常量（`constants.py` 新增）

```
DISTILL_ESTIMATOR_VERSION = "dsh-reasoning-memory/distill-causal-1"
DISTILL_UPLIFT_CAP = 0.20
DISTILL_SHRINK_PRIOR = 20
DISTILL_MIN_ARM_SAMPLES = 5
MAX_DISTILL_EVENTS_PER_UNIT = 200
```

有效样本门槛复用 v0.6.0 `MIN_EFFECTIVE_N = 20`。

### 5.2 函数签名（四函数）

```python
def estimate_distill_uplift(*, events, shrink_prior=20, min_arm_samples=5) -> dict:
    """返回 {upliftApplied, apparent, inherited, uplift, upliftShrunk, upliftClipped,
    simpsonFlip, pairedUplift, pairedPairs, nEff, cate, droppedStrata,
    estimatorVersion, reason}；分层/收缩/辛普森复用 v0.6.0 机制（do(distill) 处置）。"""

def gate_distill(*, uplift_shrunk, threshold=0.0, enabled=True, applied=False) -> dict:
    """返回 {gate: 'passed'|'rejected'|'na', reason}；只拒绝新写入，不删除。"""

def phase_from_uplift(*, uplift_shrunk) -> float:
    """符号 → {0, pi}：≥0 → 0，<0 → π。"""

def inherited_utility(*, apparent, uplift_shrunk) -> float:
    """apparent − uplift_shrunk（继承效用估计）。"""
```

### 5.3 确定性纪律

纯计数 + 四则；双跑逐字节一致；无 LLM 判断因果增量、无梯度优化、无采样。

## 6. 提炼集成（`distillation.py`）

- 读取 `distillGateEnabled` / `distillUpliftThreshold` / `phaseFromDistillUplift` / `distillShrinkPrior` / `distillMinArmSamples` / `distillCausalEvents`（全部缺省关闭/中性）。
- 无 `distillCausalEvents` → 完全跳过 v0.8.0 路径（v0.7.0 逐字节一致）。
- 有事件 → `estimate_distill_uplift` → `gate_distill`（自评下限先、因果下限后）；`rejected` 时整批拒绝；`applied` 时 `confidence` 改用 `clip(0.5 + uplift_clipped, 0, 1)`、写相位与 8 字段；`na`（样本不足）时保留关联路径并写 `distillGate='na'`。

## 7. TypeScript 插件层

- `src/distill-causal-domain.ts`：独立 domain `reasoning_memory_distill_causal`（§4.1）。
- `src/domain.ts`：`memoryItemSchema` 增 §4.2 八字段（全带默认值）。
- `src/index.ts`：配置 §4.3（默认关闭/中性）；`memory_save_outcome` / `memory_consolidate` 转发 `distillGateEnabled`/`distillUpliftThreshold`/`phaseFromDistillUplift`/`distillShrinkPrior`/`distillMinArmSamples` 到 Python `distill` 操作（`distillCausalEvents` 可选转发）。

## 8. 识别假设与越界

**识别假设（沿 v0.6.0 5 条 + 提炼侧 2 条）**：① 可忽略性；② SUTVA（配对 rollout 固定其余条目隔离继承效用）；③ 处置良定义（do(distill m)=写 m；do(¬distill m)=不写 m）；④ 结局良定义（Y=环境/验证器终态）；⑤ verdict=代理标签；⑥ 增量因果符号在纯文本历史下可能不可识别 → 符号仅在对齐反事实对/分层达标时启用，否则收缩为 0；⑦ restored-state 反事实对是 gold 唯一来源。

**越界（6 条）**：① 不落地 MGPO memory-rewrite 梯度优化/检索器微调；② 不落地 TQG-Exp TQL 训练；③ 不委托 LLM 判断因果增量（全部确定性计数）；④ 不把 uplift 进 RRF 权重与正反配额（只作用于提炼门控/权重/相位）；⑤ 线上不做 restored-state rollout（仅离线评测）；⑥ 不删除低 uplift 已有条目（只拒绝新写入）。

## 9. 与 v0.6.0 / v0.7.0 的交融（术语注）

- v0.6.0 = 检索侧因果（do(retrieve) ATE 幅度）；v0.8.0 = 提炼侧因果（do(distill) uplift 幅度）；同一套分层/收缩/封顶作用在不同处置上，互不替代。
- v0.7.0 = 检索侧相位（ATE 符号推导）；v0.8.0 = 写时定相（uplift 符号在蒸馏时写入）。`phaseFromDistillUplift=true` 时检索侧优先用写时相位，否则回退 v0.7.0 检索侧推导。
- 术语：θ=0 称"成功放大器"（正增量贡献）；θ=π 称"负增量条目"（表观效用继承自已存信息、自身无正增量乃至有害），**不是**"帮助规避失败的正面教训"（后者增量为正、应落 θ=0）。

## 10. 测试要求

- Python（`test/test_reasoning_memory.py` 新增 `DistillCausalTests` / `DistillCausalRegressionTests`）：合成已知因果结构（真 uplift 恢复、继承效用污染分离、近重复条目被拒）、门控边界（阈值上下、收缩单调性、`n_eff<20`/每臂<5 回退）、相位映射（符号→{0,π}、`phaseFromDistillUplift=false` 回退）、零回归（无 `distillCausalEvents` → 与 v0.7.0 逐字节一致）、确定性双跑、辛普森检出、additive 兼容（旧 MemoryItem 读回）。
- Node（`test/distill-causal.test.mjs`）：配置默认、mock 宿主 distill 门控派生、8 字段 additive 默认、domain 存在 + FIFO。
- `npm test` 全绿；`scripts/eval` 在 `distillGateEnabled=false` 且无 distill 事件时指标与 v0.7.0 持平。

## 11. 验收清单

1. `npm test` 全绿；`scripts/eval` 指标与 v0.7.0 持平（零回归）。
2. 合成已知结构上 uplift 恢复正确（符号/量级、继承污染分离、辛普森检出）。
3. 文档同步（README/MVP_EVALUATION v0.8.0 段，诚实标注因果提炼假设）。
4. git 提交（逻辑拆分）。
