# v0.7.0 对抗性测试清单：多概率保持引擎（§21）

- 日期：2026-11-09
- 状态：DRAFT —— 对抗性测试清单，待 `spec-0.7.0`（函数级实现）落地后逐条转成可执行用例
- 上游依据：设计文档 `reasoning-trajectory-memory-design.md` §21（§21.2 三原语 / §21.5 数据模型·配置·多概率消费格式 / §21.6 识别假设与越界 / §21.7 术语表）+ `docs/research-notes/07-quantum-probability.md` §6（评测建议）
- 调试责任人：debugger；实现：code-developer；监听：watcher；文档：lead
- 风格对齐：`docs/spec-0.6.0.md` §7 测试要求（Python 单测 / Node mock / scripts/eval 三层 + "npm test 全绿 + scripts/eval 仍运行"）

---

## 0. 读前必读：三层落点与必测/验收判定

### 0.1 三层落点（与既有测试框架一一对应）

| 层 | 载体 | 断言习惯 | 本引擎新增落点 |
|---|---|---|---|
| **Python 单测** | `test/test_reasoning_memory.py`（`unittest`，`python/reasoning_memory_core/*` 纯函数直调） | `assertEqual` / `assertAlmostEqual(..., places=6)` / `assertRaises(ValueError)`；确定性双跑用 `==` 逐字典比较 | `qprobability.py` 的 5 个纯函数（`build_superposition` / `interference_pair` / `luders_update` / `coherence` / `decoherence`）+ `_retrieve` / `plan_task` 的 `superposition[]` 注入 |
| **Node mock** | `test/causal.test.mjs` 风格的 `*.test.mjs`（`node --test` + 内置 bundle + mock Cordis host，`storageDomain`/`tools`/`listeners` 三件套） | `assert.equal` / `assert.ok` / `assert.deepEqual`；经工具 `execute()` 断言工具契约与审计字段 | 工具契约（`superposition[]` 消费格式、审计 additive 字段、`reasoning_memory_quantum` domain、FIFO 上限、配置校验） |
| **scripts/eval** | `scripts/eval/run_eval.py`（纯 stdlib、无网络、固定 seed、直调 `reasoning_memory_core` 同一实现） | 指标聚合 + Markdown 报告；`eval_retrieval.py` 已含配额遵守率/自检索率 | 坍缩损失 / 顺序效应 / 干涉三组受控测量（07 §6.1–6.3），`eval_ablation.py` 增臂 |

### 0.2 必测 vs 验收判定

- **〔MVP 必测〕**：确定性纯函数不变量 + 零回归铁律 + 边界防护。`qprobability.py` 与 additive 配置/字段一旦落地即 gate，纳入 `npm test`（`test:python` + `test:node`），与 spec-0.6.0 §7 的"Python/Node 单测"同构。
- **〔验收项〕**：依赖完整工具契约、审计 domain、或真实下游 **outcome** 受控测量的项。属 spec-0.7.0 完整落地后的验收，落点主要在 `scripts/eval` 或 Node 集成，与 spec-0.6.0 §7 的"scripts/eval 仍运行且指标持平"同构。

### 0.3 全局前置条件（所有用例共享）

1. **冻结 v0.6.0 基线（铁律前提）**：在 `spec-0.7.0` 任何代码落地前，code-developer 于 v0.6.0 tag 处跑一次 `_retrieve` / `plan_task` 全请求，落盘 `json.dumps(result, sort_keys=True, ensure_ascii=False)` 的 **sha256**，作为零回归的字节基线。没有该冻结基线，逐字节零回归不可证伪。
2. **确定性纪律**：所有断言只允许整数/浮点四则 + 矩阵（实对称、尺寸 ≤ `MAX_SUPERPOSITION_SIZE`）；线上路径禁止 `random`/`numpy`/任何采样；采样仅出现在离线评测（固定 seed）。
3. **零回归口径**：新开关 `quantumDecisionEnabled` 默认 `false`；新审计字段 `amplitude/phase/interferenceDelta/coherence/collapseReason` 全带默认值，旧介质（无这些字段的记录）读回不报错、行为与 v0.6.0 一致。

---

## 1. 零回归（Group 1）

**目标**：证明 `quantumDecisionEnabled=false` 时，检索 / plan 输出与 v0.6.0 逐字节一致；配额遵守率 100%、自检索率 0 两个既有不变量不被破坏；旧介质（无新字段记录）读回正常。

**前置条件**：v0.6.0 基线 sha256 已冻结（§0.3.1）；additive 字段默认值与 §21.5 一致（`amplitude=0, phase=0, interferenceDelta=0, coherence=0, collapseReason=''`）。

**输入构造**：复用 `test_reasoning_memory.py` 现有 `make_memory` / `retrieve` fixture；对同一批记忆，一次不带 `quantumDecisionEnabled`、一次显式 `quantumDecisionEnabled=False`，与 v0.6.0 基线同输入。

**对接现有框架落点**：Python 单测（`RetrievalTests` / 新增 `QuantumRegressionTests`）+ `scripts/eval/eval_retrieval.py`（配额/自检索不变量）+ Node mock（`memory_retrieve` 工具契约无 `superposition` 字段）。

| # | 层级 | 判定 | 用例 | 预期断言（具体） |
|---|---|---|---|---|
| G1.1 | Python | **MVP 必测** | `_retrieve` 零回归：`quantumDecisionEnabled` 缺省与 `=false` 两种输入，输出除 additive 字段外的全部内容与 v0.6.0 基线一致 | `json.dumps(result_without_new_fields, sort_keys=True, ensure_ascii=False)` 的 sha256 == 冻结基线 sha256；且 `"superposition" not in result`（字段完全缺席，而非空数组——**见 §7 疑点 Q3**） |
| G1.2 | Python | **MVP 必测** | `plan_task` 零回归：`quantumDecisionEnabled=false` 时 `matchedTemplates` 排序/分数与 v0.6.0 一致 | `matchedTemplates` 列表逐字段等于基线；`pattern`/`confidence`/`fallback` 不变；输出无 `amplitude`/`phase` 字段 |
| G1.3 | Python | **MVP 必测** | 旧介质读回：记忆记录**不含**新字段（`amplitude`/`phase`/`interferenceDelta`/`coherence`/`collapseReason`）时检索 | 无 `KeyError`、无异常；`combinedScore` 与 v0.6.0 相同输入的 `combinedScore` 逐位相等；读回字段默认值为 0/0/0/0/'' |
| G1.4 | Node | **MVP 必测** | mock 宿主 `quantumDecisionEnabled=false` 跑完整生命周期（begin→retrieve→save→end） | `memory_retrieve.execute()` 返回体无 `superposition` 键；检索审计行 `candidates[]` 每条无 `amplitude`/`phase`/`interferenceDelta`（或全为默认值）；`memory_save_outcome` 派生事件与 v0.6.0 一致（复用 `causal.test.mjs` 的派生逻辑断言） |
| G1.5 | eval | **MVP 必测** | `scripts/eval/run_eval.py` 在 `quantumDecisionEnabled=false` 下运行 | `quotaAdherence == 1.0`、`selfRetrievalRate == 0.0`、`recall`/`nDCG` 与 v0.6.0 冻结报告**逐位相等**（`rounded` 到 4 位后字符串相等） |

---

## 2. 坍缩损失（Group 2）

**目标**：操作化 H-Q1——同 held-out 任务集，(a) top-1 坍缩 vs (b) top-k 叠加保持的下游成功率对比，判定"候选更多反而更差"是否成立且是否顺序敏感。

**前置条件**：held-out 任务集按任务族/模板切分（防泄漏，§11.3）；固定模型/工具/提示词/预算；下游 outcome 来自环境/验证器（非 verdict，§21.6 识别假设 3）。

**输入构造**：(a) 臂只注入 top-1（`collapseReason='explicit_commit'`）；(b) 臂注入 top-k（k≥2，`superposition[]` 保留振幅/相位/顺序）。同一 held-out 任务两臂配对，各跑固定 seed 的多次采样（仅离线，Self-Consistency 式）。

**对接现有框架落点**：`scripts/eval` 新增 `eval_collapse.py`（或 `eval_ablation.py` 增臂）；Python 单测覆盖 `build_superposition` 的"保振幅"纯函数不变量；Node mock 覆盖 `superposition[]` 消费契约。

| # | 层级 | 判定 | 用例 | 预期断言（具体） |
|---|---|---|---|---|
| G2.1 | eval | **验收项** | H-Q1 主测量：同 held-out 任务，(a) top-1 vs (b) top-k 下游成功率 | 报告 (b)-(a) 的成功率差与 95% CI；若 (b) 显著 < (a)（预注册显著性阈值）且顺序置换后差异显著变化（联动 G3.1），则 H-Q1 成立、引擎介入必要；否则 H-Q1 否证 |
| G2.2 | Python | **MVP 必测** | `build_superposition` 保振幅：单候选 vs 多候选 | k=1 → 归一化后 `p == 1.0`、`interferenceDelta == 0`、`coherence == 0`（单点退化为经典）；k=2 → 第二候选 `amplitude r2 > 0` 被保留（不坍缩为 0），`Σ p_i == 1.0`（玻恩归一化） |
| G2.3 | Python | **MVP 必测** | 坍缩 vs 保持的可审计对比：同一候选集分别走坍缩与叠加 | 坍缩后 `len(superposition) == 1` 且 `collapseReason` 非空；叠加后 `len(superposition) == k` 且 `coherence > 0`；两者 `p_i`（top-1）一致，证明"保持"不改变 top-1 的边际概率、只额外保留次席概率质量 |
| G2.4 | Node | **验收项** | `memory_retrieve` 在 `quantumDecisionEnabled=true` 时附带 `superposition[]` | 返回体含 `superposition[]`，每条含 `{id, amplitude, phase, p, interferenceDelta, coherence}` 六键；`p` 与 `amplitude` 满足 `p == amplitude²`；数组顺序 == 检索排序顺序（顺序即处置变量，§21.5） |

---

## 3. 顺序效应（Group 3）

**目标**：测量候选顺序置换对 outcome 的影响；把"顺序"作为可审计处置变量显式化（呼应 2308.11483）。

**前置条件**：同一候选集、同一 held-out 任务、固定 budget；下游 outcome 来自环境。

**输入构造**：对同一候选集构造两个注入顺序（`order` 字段互换），其余完全一致。

**对接现有框架落点**：`scripts/eval` 新增顺序效应测量（复用坍缩损失测量脚手架）；Node mock 断言审计记录 `order` 字段。

| # | 层级 | 判定 | 用例 | 预期断言（具体） |
|---|---|---|---|---|
| G3.1 | eval | **验收项** | 顺序置换对 outcome 的测量 | 报告 `Δoutcome = outcome(order A) − outcome(order B)` 与 CI；顺序效应作为处置变量记录在审计（`order`/`injectionOrder`），测量值可复核 |
| G3.2 | Python | **MVP 必测** | `build_superposition` 保留输入顺序语义 | `superposition[]` 输出顺序 == 输入候选顺序（**不重排**）；交换输入两候选位置 → 输出数组对应交换，但密度矩阵 ρ 对称不变量（`ρ_ij == ρ_ji`）与 `coherence(ρ)` 不变（顺序无关，见 G5.2） |
| G3.3 | Node | **验收项** | 审计记录注入顺序 | `memory_retrieve` 审计行每条候选含 `order`（或等价顺序字段）；`memory_plan_task` 的 `superposition[]` 保留模板注入顺序；顺序字段为确定性整数，双跑一致 |

---

## 4. 干涉（Group 4）

**目标**：同相（正 ATE × 正 ATE）vs 反相（正 ATE × 负 ATE）候选对的组合效应相对边际和的偏离；gold 校验（合成已知因果结构）；代理标签纪律（verdict 不作结局）。

**前置条件**：候选相位来自因果 ATE 符号（`phaseFromCausal=true`，θ=0 正 / θ=π 负）；gold 合成结构已知其"边际和 + 交互项"真值。

**输入构造**：两候选对，固定 `r_i, r_j`，构造 (a) 同相 (θ_i=θ_j)、(b) 反相 (θ_i=0, θ_j=π)、(c) 双负 (θ_i=θ_j=π)；gold 校验用已知因果结构合成。

**对接现有框架落点**：Python 单测（`interference_pair` / `build_superposition` 纯算术）+ `scripts/eval`（组合 outcome 受控测量）+ Node mock（审计字段纪律）。

| # | 层级 | 判定 | 用例 | 预期断言（具体） |
|---|---|---|---|---|
| G4.1 | Python | **MVP 必测** | `interference_pair(i,j)` 纯算术符号 | 同相 `cos(0)=+1` → `+2·r_i·r_j`；反相 `cos(π)=−1` → `−2·r_i·r_j`；双负（θ_i=θ_j=π）→ `+2·r_i·r_j`。用 `assertAlmostEqual(..., places=6)` 断言精确值（确定性、无浮点随机） |
| G4.2 | Python | **MVP 必测** | 相干叠加 vs 经典（非相干）叠加 | `\|φ_i+φ_j\|² == r_i² + r_j² + 2·r_i·r_j·cos(θ_i−θ_j)`，且与经典边际和 `r_i²+r_j²` 的偏离恰等于 `interference_pair`；`decoherence(ρ, rate=1)` 后偏离 → 0（退化为经典，见 G6.2） |
| G4.3 | Python | **MVP 必测** | gold：相位来源 = 因果 ATE 符号 | 用合成因果事件（正 ATE）→ `build_superposition` 相位 θ=0；负 ATE → θ=π；`phaseFromCausal=true` 时相位仅由 ATE **符号**决定、幅度仅由效用幅度决定（ATE 幅度不放大相位，§07 §5.1） |
| G4.4 | eval | **验收项** | gold：合成已知因果结构的组合效应恢复 | 已知"边际和 + 交互项"真值下，测量组合 outcome 偏离边际和的方向：同相 → 建设性（组合 > 边际和）、反相 → 破坏性（组合 < 边际和）；注入的交互符号被恢复，符号误判率 0（合成无结构时误报 0，对齐 spec-0.6.0 §7 的辛普森"无结构误报 0"口径） |
| G4.5 | Node | **验收项** | 代理标签纪律：verdict 不作结局 | 干涉/组合效应的 outcome 锚只能来自环境 `outcome`（`memory_save_outcome` 的 verifier/evidence），**不得**来自 `memory_feedback` 的 helpful/harmful verdict；审计 `interferenceDelta` 由 outcome 事件派生，与 verdict 字段解耦（`memory_feedback` 语义零回归） |

---

## 5. 确定性（Group 5）

**目标**：同 seed 双跑逐字节一致；聚合顺序无关；实数域（相位 {0,π}）无复数、无随机。

**前置条件**：确定性纪律（§0.3.2）；所有 qprobability 函数纯 stdlib。

**输入构造**：固定输入 + 固定 seed 双跑；候选顺序置换的多组输入。

**对接现有框架落点**：Python 单测（纯函数双跑/顺序无关/实数域断言）+ Node mock（同 seed 双跑审计）+ `scripts/eval`（`--seed` 双跑报告一致）。

| # | 层级 | 判定 | 用例 | 预期断言（具体） |
|---|---|---|---|---|
| G5.1 | Python | **MVP 必测** | 五原语双跑逐字节一致 | `build_superposition` / `interference_pair` / `luders_update` / `coherence` / `decoherence` 对同一输入双跑，输出 `==` 全等（`assertEqual` 逐字典/逐值）；模块内 `import random` / `import numpy` 不存在（源码级断言或运行时 `sys.modules` 检查） |
| G5.2 | Python | **MVP 必测** | 聚合顺序无关 | 候选以不同排列输入 → 归一化密度矩阵 ρ 规范形（按候选 id 排序）全等；`coherence(ρ)` 全等；`Σ P(i) == 1`（trace 1）；`ρ_ij == ρ_ji`（实对称）；`interference_pair(i,j) == interference_pair(j,i)` |
| G5.3 | Python | **MVP 必测** | 实数域：无复数、无随机 | 输出中所有 `phase ∈ {0.0, math.pi}`；所有 `amplitude`/`interferenceDelta`/`coherence` 为 `float` 或 `int`；`not any(isinstance(v, complex) for v in flatten(output))` 为真；`p = amplitude²` 无虚部 |
| G5.4 | Node | **MVP 必测** | 同 seed 双跑 mock 审计一致 | 同一 mock host 场景双跑，`JSON.stringify(retrieval_audit, null, 0)` 双跑全等（无时间戳/随机 id 注入审计——如有 `createdAt` 需用固定时钟注入） |
| G5.5 | eval | **验收项** | 同 seed 双跑报告一致 + Self-Consistency 采样消融固定 seed | `run_eval.py --seed N` 两次 → 指标 dict 与 Markdown 报告逐字节一致；Self-Consistency 式采样消融列必须显式 `--seed`，双跑全等 |

---

## 6. 边界 / 对抗（Group 6）

**目标**：叠加尺寸封顶、样本不足退相干回退、辛普森 flip × 相位符号交互、负效应只压低不删除、非法输入/除零/空候选/单候选、密度矩阵数值稳定性、`interferenceDelta` 封顶 ±20%。

**前置条件**：常量 `MAX_SUPERPOSITION_SIZE=3`、`decoherence` 速率 `k/(n_eff+k)`（复用 v0.6.0 `SHRINK_PRIOR=20`）、`CAUSAL_MULTIPLIER_CAP=0.20` 均已落地。

**输入构造**：超尺寸候选集、n_eff 不足样本、辛普森构造（粗负/分层正）、负 ATE 候选、非法相位/零振幅/空/单候选、近退化振幅、极端干涉。

**对接现有框架落点**：Python 单测（边界纯函数）+ Node mock（配置校验、审计 domain、FIFO）+ `scripts/eval`（干涉/相位不进 RRF/配额的不变量）。

| # | 层级 | 判定 | 用例 | 预期断言（具体） |
|---|---|---|---|---|
| G6.1 | Python | **MVP 必测** | 叠加尺寸封顶 `MAX_SUPERPOSITION_SIZE=3` | 输入 5 候选 → `len(superposition) <= 3`；`collapseReason == 'rank_cutoff'`；`coherenceBefore` / `coherenceAfter` 均有值且 `coherenceAfter <= coherenceBefore`（坍缩不可逆） |
| G6.2 | Python | **MVP 必测** | 样本不足退相干回退 | n_eff 低于门槛时 `decoherence(ρ, rate)` 速率 `rate = k/(n_eff+k)` 趋近 1 → `collapseReason == 'sample_insufficient'`；`decoherence(ρ, rate=1)` → 非对角元 `ρ_ij == 0`（i≠j）、`coherence(ρ) == 0`、行为 == 经典-因果路径（v0.6.0 完全退相干极限，`assertEqual` 与 v0.6.0 multiplier 一致） |
| G6.3 | Python | **MVP 必测** | 辛普森 flip × 相位符号交互 | 构造粗效应为负/每层效应为正（复用 `causal_utils` 的 `simpsonFlip` 构造）→ `simpsonFlip == true`；相位**必须**取分层（正确）符号 θ=0，而非粗效应符号；`interference_pair` 用分层符号计算；排序一律采用分层估计（沿用 v0.6.0 铁律） |
| G6.4 | Python | **MVP 必测** | 负效应只压低不删除 | 负 ATE 候选 → 相位 θ=π、仍出现在 `superposition[]`（**未被删除**）；其组合贡献被压低（破坏性干涉方向）；最终 multiplier ∈ [0.8, 1.2]（负效应只降不删，对齐 spec-0.6.0 §3.1.7） |
| G6.5 | Python | **MVP 必测** | 非法相位 / 除零 / 空 / 单候选 | 非法相位（θ ∉ {0,π}，如 θ=0.5）→ `ValueError` 或确定性 clamp（spec-0.7.0 定稿后固定一种）；全零振幅 → 归一化不除零、无 `NaN`/`Inf`（返回退化态或全 0）；空候选列表 → 空 `superposition[]`、无异常；单候选 → `p==1`、`interferenceDelta==0`、`coherence==0` |
| G6.6 | Python | **MVP 必测** | 密度矩阵数值稳定性 | 近退化振幅（`r_i ≈ r_j` 到 1e-12）→ 对角元 `ρ_ii ≥ 0`、`\|ρ_ij\| ≤ sqrt(ρ_ii·ρ_jj)`、`trace ≈ 1`（`assertAlmostEqual(..., places=6)`）；玻恩定则 `P(i)=r_i² ≥ 0` 恒成立；所有输出 `math.isfinite` 为真 |
| G6.7 | Python | **MVP 必测** | `interferenceDelta` 封顶 ±20% | 极端 `r_i=r_j` 使原始干涉 `2·r_i·r_j` 超界 → `clip(interferenceDelta, −0.20, +0.20)`；进入最终 multiplier 的干涉项绝对值 ≤ 0.20；multiplier ∈ [0.8, 1.2]；**审计字段记录的是封顶值还是原始值需 spec 定稿（见 §7 疑点 Q4）** |
| G6.8 | Node | **MVP 必测** | 配置校验 | `maxSuperpositionSize` 非法（0/负/非整数）→ 拒绝或 clamp 到 [1,3]；`quantumDecisionEnabled` 必须 boolean；additive 配置默认值存在（缺省时 `quantumDecisionEnabled=false` 等），不破坏旧 profile |
| G6.9 | Node | **验收项** | 量子审计 domain + FIFO | `reasoning_memory_quantum` domain 独立存在、与 `reasoning_memory_causal` 并列；每次坍缩写 `{collapseReason, coherenceBefore, coherenceAfter, estimatorVersion}`；FIFO 上限（同 v0.6.0 `MAX_CAUSAL_EVENTS_PER_UNIT=200` 风格）淘汰最旧；旧介质（无量子字段）读回正常 |
| G6.10 | eval | **验收项** | 干涉/相位不进 RRF 权重与正反配额 | `quantumDecisionEnabled=true` 下 `quotaAdherence == 1.0`、`selfRetrievalRate == 0.0`；`fusionWeights` 与 v0.6.0 一致（相位/干涉只作用于最终 multiplier）；RRF 权重、outcome 池、正反配额均未被量子分量侵入（越界 §21.6.5 的可测化） |

---

## 7. 对 §21 设计本身的疑点（供 lead 与 code-developer 在 spec-0.7.0 定稿前裁决）

> 以下疑点不阻塞本清单成立，但会直接影响"零回归逐字节一致"与"审计可复现"两条铁律的断言写法，需在 `spec-0.7.0` 函数级定稿时一并冻结。

**Q1（最影响零回归）——`decoherenceRate` 配置默认 0 与 n_eff 自动退相干的语义关系不清。**
§21.5 配置 `decoherenceRate`（默认 0），但 §21.2 又说退相干速率由 n_eff 驱动 `rate = k/(n_eff+k)`、样本不足自动抹平。二者是**叠加**（手动偏置 + 自动）、**替代**，还是 `decoherenceRate` 只是自动速率的**上限/乘数**？v0.6.0 没有"退相干"概念，若自动退相干在 `quantumDecisionEnabled=false` 时仍被求值，就可能破坏零回归；必须明确"关闭开关 = 完全跳过量子路径、不触发任何退相干计算"。

**Q2（干涉 gold 的可证伪边界）——合成 gold 只验证机制、不验证现实联合因果。**
G4.4 的"合成已知因果结构"能验证干涉项的**算术**与符号映射，但两个记忆的真实联合因果结构（effect modification）在真实标注集上需要组合/配对实验才能"已知"，成本高。§21.6 识别假设 3（干涉以环境 outcome 为锚）在离线合成可测、在真实集上只能靠配对实验逐步积累——需在文档诚实标注"合成 gold ≠ 真实联合因果已识别"，否则会把机制正确误读为因果正确。

**Q3（断言写法待定）——`superposition[]` 在关闭时是"字段缺席"还是"空数组"。**
G1.1 断言写成 `"superposition" not in result`，但 §21.5 只说"默认关闭时与 v0.6.0 行为逐字节一致"。若 spec-0.7.0 选择"始终输出空数组 `[]`"，则 `== []` 与 `not in` 都满足"零回归"但断言必须二选一；建议 spec 定稿为**关闭时字段缺席**（`not in`），最能保证与 v0.6.0 输出字典结构逐字节一致。

**Q4（审计可复现性）——`interferenceDelta` 审计记录原始值还是封顶值。**
§21.5 审计字段 `interferenceDelta` 与 §21.6.5"干涉封顶 ±20%、只作用于最终 multiplier"并存。`interference_pair` 原始值 `2·r_i·r_j·cos(Δθ)` 在归一化后最大可到 0.5（r_i=r_j=0.707 时 2·0.5=1 附近更甚，取决于归一化），远超 ±0.2。审计若记录原始值、最终 multiplier 用封顶值，二者不一致会破坏"每个估计量可拆解"的可审计原则；需 spec 明确：**审计存原始值 + 封顶值两列**，或**审计只存封顶后值**。

**Q5（越界风险）——"顺序作为处置变量"依赖下游 Agent 遵守，插件无法强制。**
§21.5 说 `superposition[]`"保留候选顺序语义（顺序即处置变量）"，但下游模型在"最后一个承诺点之前"如何消费该数组由模型决定，插件无法保证模型按给定顺序推理。若下游可重排/忽略顺序，则 G3.1 的"顺序效应可审计"只在插件侧成立、在端到端 outcome 侧不成立；需把"插件只保证输出有序、不保证下游遵守顺序"写进 §21.6 越界，否则会夸大可审计性。

**Q6（物理直觉需澄清）——同相负 ATE × 负 ATE 的"建设性"干涉。**
G4.1 断言双负（θ_i=θ_j=π）→ `cos(0)=+1` → 建设性。这在"干涉项符号"层面正确（偏离边际和为正），但两个"负面"单元相干反而**组合概率更高**的直觉反直觉，易被误读为"负面记忆组合后更有用"。需在 §21.7 术语表明确：干涉项是**相对边际和的偏离**，不是**相对成功的方向**；双负建设的正确解读是"两个负振幅相干后，组合成功振幅更负（更远离成功），而非更接近成功"。这条若不清，测试断言本身虽对，但论文/文档表述会埋雷。

---

## 8. 落地顺序建议（与 spec-0.7.0 开发节奏对齐）

1. **先冻结 v0.6.0 基线 sha256**（§0.3.1）——零回归不可证伪前，任何量子代码不得合入。
2. **Python 纯函数 + 边界**：`qprobability.py` 落地即跑 G4.1–G4.3、G5.1–G5.3、G6.1–G6.7（全 MVP 必测），`test:python` 全绿为准。
3. **additive 配置/字段 + Node 契约**：跑 G1.4、G2.4、G3.3、G4.5、G5.4、G6.8–G6.9，`test:node` 全绿为准。
4. **scripts/eval 受控测量**：跑 G2.1、G3.1、G4.4、G5.5、G6.10，并证明 `quantumDecisionEnabled=false` 时指标与 v0.6.0 持平。
5. **验收**：`npm test` 全绿 + `scripts/eval` 指标持平（零回归）+ §7 六条疑点在 spec-0.7.0 中裁决落笔。
