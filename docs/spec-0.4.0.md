# v0.4.0 规格：四层映射骨架落地（策略模板库 + 激活动作化）

- 日期：2026-11-02
- 状态：FINAL —— 已吸收 `docs/research-notes/01-dsh-contracts.md`、`02-template-algorithms.md`、`03-survey-facts.md`、`04-eval-schema.md`
- 上游依据：《面向 LLM 智能体的推理模块高效复用研究综述（终稿）》（30 篇文献 [1]–[30]）
- 实现者：code-developer；评测复核：debugger；文档：lead

---

## 1. 目标与学术定位

本迭代把综述第 2 节提出的**四层映射骨架**（任务模式 τ → 策略模板 π → 能力模块 m → 激活动作 a）在 DSH 插件边界内落地为**路径 A（上下文级）**，并补上综述第 4.2 节归纳的空白 3/4/5 的插件侧对应物：

| 综述概念 | 插件 v0.4.0 落点 |
|---|---|
| 任务模式层 τ | `classify_task_pattern()`：确定性分类器（multi_hop_qa / code_synthesis / tool_orchestration / gui_operation / long_horizon_planning / numeric_search / general），输出置信度与证据分解 |
| 策略模板层 π | `StrategyTemplate` 域记录 + 5 个内置种子模板 + 确定性挖掘（`mine_templates`）+ 调用者发布（`memory_publish_template`）；CoALA procedural memory 的结构化扩展 |
| 能力模块层 m | 插件可寻址模块 = 推理记忆（success/failure）、策略模板、Agent Skill（既有技能层）、DSH 工具、验证器；模块带元数据（适用模式、触发条件、效用统计） |
| 激活动作层 a | `memory_plan_task` 输出的**激活计划**：步骤级模块需求 d_t + 主动检索点（ProactAgent 式 proactive retrieval）+ 推荐技能；`memory_retrieve` 步骤对齐参数 |
| 联合决策（空白 4） | `memory_plan_task` 是检索+激活的联合决策面；过程级反馈（`memory_feedback` 扩展）驱动模板效用统计（EvolveR 式，低代价信号，非校准 RL） |
| 粒度对齐（空白 3） | 记忆新增 `strategyTemplateId` + `reasoningLevel`；检索新增 `active_template_id` / `step_index` / `retrieval_intent`；模板通道填补"推理步骤"粒度空档 |
| 质量门控与效用（MoT/ToE/EvolveR） | `insight_confidence` 写入门控（默认关闭）；模板/记忆效用统计 + 保守弃用标记（只标记不删除） |
| 评测（空白 5） | `scripts/eval` 离线 harness：检索质量 + 模板匹配 + 消融，合成数据 + 真实标注双轨 |

### 1.1 非目标（越界声明，写入文档）

- **路径 B（LoRA 组合）与路径 C（SAE 特征干预）不落地**：DSH 插件无权重/潜空间访问权限；综述 [24] 的拒绝方向双刃剑警示与 [25][26] 的跨模型可移植性难题使插件侧干预不具备安全/工程前提。仅保留"能力模块 m"的接口占位（`moduleKind` 字段），供未来宿主级扩展。
- **路径 E（RL 联合决策学习）不落地**：无训练数据/模型策略/运行期奖励；以确定性策略 + 过程级反馈统计近似，文档如实标注。
- 不承诺"激活干预改变模型内部计算"：本迭代的激活=上下文/工具/技能注入的可执行化，效应需评测验证。

---

## 2. 数据模型

### 2.1 域表：新增 `templates`（storage-domain v1 additive）

**定案（依据 01-dsh-contracts.md）：方案 A——在既有 `reasoningMemoryDomain`（`src/domain.ts`）v1 下新增 `templates` 表。** 契约核实结论：`defineDomain` 无表结构哈希、后端仅 `version-mismatch`/`malformed-medium` 两种拒绝、domain 层打开时逐表回填（缺失表读为空 Map），故新增表不破坏旧介质；version 保持 1。技能层用独立 domain 是回滚隔离的设计选择而非契约要求；模板直接参与 `memory_plan_task` 与检索审计，与记忆同域读写更简单，故采用方案 A。**铁律：一旦有数据写入，表记录 schema 只能 additive（新字段一律 zod `.default()`），否则下次打开 `invalid-record` 会让整个 domain 打不开。**

```ts
export const strategyTemplateSchema = z.object({
  templateId: z.string(),
  name: z.string(),                 // kebab-case，保留 memory_tpl_ 前缀；挖掘产物自动生成
  description: z.string(),          // 面向模型的触发式描述（"Use when…" 措辞，参照技能层守卫）
  taskPatterns: z.array(z.enum([
    'multi_hop_qa', 'code_synthesis', 'tool_orchestration',
    'gui_operation', 'long_horizon_planning', 'numeric_search', 'general',
  ])),
  steps: z.array(z.object({         // 步骤级模块需求 d_t
    index: z.number().int().positive(),
    title: z.string(),
    moduleDemands: z.array(z.object({
      kind: z.enum([
        'retrieve_memory',          // 检索经验记忆（成功/失败配额内）
        'retrieve_template',        // 细化/切换策略模板
        'use_skill',                // 加载 Agent Skill
        'invoke_tool',              // 调用 DSH 工具
        'verify',                   // 验证（测试/环境/数值验证器）
        'rollback_check',           // 回退前检索失败教训
      ]),
      intent: z.string().default(''),   // 该需求的意图说明（检索 query 构造提示）
    })),
  })),
  structureSignature: z.array(z.enum(['decompose', 'constrain', 'search', 'execute', 'verify', 'rollback'])),
  preconditions: z.array(z.string()).default([]),
  provenance: z.object({
    author: z.enum(['builtin', 'mined', 'caller']),
    sourceMemoryIds: z.array(z.string()).default([]),
    sourceTaskIds: z.array(z.string()).default([]),
  }),
  utility: z.object({
    helpful: z.number().int().nonnegative().default(0),
    harmful: z.number().int().nonnegative().default(0),
    neutral: z.number().int().nonnegative().default(0),
  }),
  version: z.number().int().positive().default(1),
  status: z.enum(['active', 'deprecated']).default('active'),
  createdAt: z.number().int().nonnegative(),
  updatedAt: z.number().int().nonnegative(),
})
export type StrategyTemplate = z.infer<typeof strategyTemplateSchema>
```

上限：`MAX_TEMPLATE_STEPS = 8`、`MAX_MODULE_DEMANDS_PER_STEP = 4`、`MAX_TEMPLATE_NAME_CHARS = 80`、`MAX_TEMPLATE_DESCRIPTION_CHARS = 400`、`MAX_TEMPLATES = 200`。

### 2.2 `MemoryItem` additive 字段（全部带默认值）

| 字段 | 类型 | 默认 | 语义 |
|---|---|---|---|
| `strategyTemplateId` | string | `''` | 该记忆对齐的策略模板（挖掘/调用者声明）；空=未对齐 |
| `reasoningLevel` | enum `'analysis' \| 'path' \| 'step'` | `'path'` | ToE 层级对齐：分析视角 / 推理路径 / 单步 |
| `insightConfidence` | number 0–1 | `0` | 调用者/LLM 蒸馏自评的洞察置信度；0=未提供（MoT 置信度过滤思想，门控见 §4） |
| `status` | enum `'active' \| 'deprecated'` | `'active'` | 保守弃用标记；deprecated 仍可检索但结果带 `deprecated: true` |

### 2.3 `RetrievalRecord` additive 字段

| 字段 | 类型 | 默认 | 语义 |
|---|---|---|---|
| `activeTemplateId` | string | `''` | 本次检索绑定的策略模板（步骤对齐检索时） |
| `stepIndex` | int | `0` | 步骤号；0=未提供 |
| `retrievalIntent` | string | `''` | 步骤级模块需求 kind（`retrieve_memory` 等） |
| `templateChannelWeight` | number | `0` | 模板通道在融合中的实际权重（0=未启用） |

候选审计 `retrievalCandidateSchema` 增加 `templateScore: number [0,1] default 0`。

---

## 3. Python 核心（`python/reasoning_memory_core/`）

### 3.1 新模块 `templates.py`（纯 stdlib、确定性、可审计）

**任务模式分类器**

```python
TASK_PATTERNS = ("multi_hop_qa", "code_synthesis", "tool_orchestration",
                 "gui_operation", "long_horizon_planning", "numeric_search", "general")

def classify_task_pattern(*, task_desc: str, goal: str = "", task_family: str = "",
                          structure_signature: list[str] | None = None,
                          has_graph: bool = False) -> dict[str, Any]:
    # -> {"pattern": str, "confidence": float 0-1, "evidence": [{"signal": str, "hits": [...], "weight": float}], "fallback": bool}
```

- 信号四类（采纳 02 调研的 S1–S4；S5 历史命中信号 v0.4.0 不实现，记为后续扩展），权重初值 `S1=0.35 / S2=0.30 / S3=0.20 / S4=0.15`：
  - **S1 task_family 查表**：大小写不敏感子串/正则查映射表（humaneval/mbpp/code/swe/repo/git/debug/refactor → code_synthesis；webarena/mind2web/browser/gui/ui/click/form/ecommerce/shopping → gui_operation；gsm8k/math/arithmetic/numeric/equation/finance/24game → numeric_search；hotpotqa/multihop/qa/question/wikidata/knowledge → multi_hop_qa；alfworld/sciworld/plan/long/multi-step/pipeline → long_horizon_planning；匹配顺序=表序，命中即停）。
  - **S2 词法关键词**：`taskDesc + goal` 的 en/zh 双关键词表命中（英文用词边界、中文直接子串），每类命中关键词数归一化。
  - **S3 结构签名统计**：当前捕获工具序列映射为 6 标签签名（复用 `trajectory.structure_signature`）或直接使用查询签名：verify 出现 → code_synthesis +；rollback 出现 → gui/long_horizon +；search 比例 >0.4 → numeric_search/multi_hop；decompose 出现 → long_horizon；序列长度 >8 → long_horizon；工具名集合 ≥3 且无 verify/rollback 且序列长 ≤8 → tool_orchestration 显式加权。
  - **S4 图节点分布**（仅提供 reasoningGraph 时）：premise+inference 占比 >0.5 → multi_hop；action 占比 >0.4 → tool/gui；evidence 出现 → numeric_search 类。
- 聚合与裁决（采纳 02 调研）：
  ```
  score(c) = Σ_i w_i × f_i(c)；pattern = argmax score(c)；margin = best − second
  confidence = clip(2 × margin, 0, 1)   # 全零分数时 confidence = 0
  ```
  裁决优先级：① task_family 命中 → 直接采用，`confidence = min(0.9, 0.6 + 其他信号一致性×0.3)`；② `score(best) ≥ 0.35` 且 `margin ≥ 0.10` → argmax；③ 否则 → `general`（兜底，非失败；general 时 L2 匹配跳过 pattern 约束退化为结构+词法）。
- 输出：`{pattern, confidence, evidence[{signal, hit, contribution}], scores{...}, fallback}`；全部常量进 `constants.py`，词表作为模块级 dict（注明来源=工程启发初值，待标注集校准）。

**模板匹配评分（采纳 02 调研的 match() 与 EvolveR 质量公式）**

```python
def template_match_score(*, template: dict, pattern: str, pattern_confidence: float,
                         structure_signature: list[str],
                         task_text: str) -> tuple[float, dict[str, float]]:
    # match = (0.50 × struct + 0.30 × lexical + 0.20 × pattern_consistency) × quality(T)
    # struct = sequence_similarity(query_signature, template.coreSignature 或 structureSignature)
    # lexical = 任务文本与 template.name/description/steps[].title 的确定性词项重叠
    # pattern_consistency = 1.0 若 pattern ∈ taskPatterns；general 分类时按 0.5 计；否则 0
    # quality = (0.5 + 0.5 × (succCount+1)/(useCount+2)) × (1 − 0.3 × harmRatio)，下限 0.5
    #           —— EvolveR 动态打分 (c_succ+1)/(c_use+2) 的原样复刻（见 03-survey-facts.md §4）
    # 返回 (score 0-1, 分解: {struct, lexical, pattern, quality})
```

- 阈值：`match ≥ 0.75` 强匹配（返回模板 + contraindications）；`0.55 ≤ match < 0.75` 可注入；`< 0.55` 不注入。查询签名为空时 struct 记 0 并标记；prior utility 仅作并列 tie-break，不进加权（防反馈正反馈锁死）。

**模板挖掘（确定性 workflow mining；采纳 02 调研的在线代表点聚类）**

```python
def mine_templates(*, memories: list[dict], min_support: int = 3,
                   max_templates: int = 20) -> dict[str, Any]:
    # -> {"templates": [...候选...], "clusters": [{signature, core_signature, pattern, memory_ids, size}], "skipped": {reasons...}}
```

- **距离**：`difflib.SequenceMatcher(None, sigA, sigB).ratio()`（编辑相似度，纯标准库）。
- **聚类**（在线、时序确定、无随机）：`θ_join = 0.75`、`θ_form = 0.55`。按 createdAt 顺序逐条：与最近簇代表相似度 ≥ θ_join → 并入并更新统计；与所有簇代表 < θ_form → 开新簇；两者之间 → 挂起队列（不参与代表更新，防漂移）。
- **簇 → 模板**：
  - `coreSignature` = 簇内签名集合的多序列 LCS（先取最短签名逐条收缩）；
  - 步骤级模块需求由每个位置 tag 按映射表推导（必选 + 可选，簇内同位置取众数）：

    | tag | 必选模块 | 可选模块 |
    |---|---|---|
    | decompose | retrieve_template | retrieve_memory |
    | constrain | — | retrieve_memory（同步沉淀 preconditions） |
    | search | retrieve_memory | invoke_tool |
    | execute | invoke_tool | use_skill（该步记忆关联已发布技能时升级） |
    | verify | verify | — |
    | rollback | rollback_check | verify |

  - `taskPattern` = 簇内记忆分类众数（离线用 §3.1 分类器对每条记忆语义文本重跑，保证标签一致）；
  - **发射门控**：簇 `size ≥ min_support` 且签名长度 ≥ 2 且至少含一个非 `execute` 标签；**成功占比 ≥ 0.5 才发射模板**（fail 为主的簇不进模板库，记入 `skipped` 建议走既有失败记忆通道，保持正负配额语义）；不合格簇注明原因（below_support / too_short / monotone / fail_majority）；
  - `provenance.author='mined'`，`sourceMemoryIds` 记录簇内记忆；挖掘幂等：同 (coreSignature, taskPattern) 已存在模板时跳过并报告。
- 主动检索点 = 需求 kind ∈ {retrieve_memory, retrieve_template} 的步骤 index 列表。

**效用统计**

```python
def apply_template_feedback(template: dict, verdict: str) -> dict:
    # helpful/harmful/neutral 计数 +1（幂等由 TS 侧每任务每模板一次保证）
    # 返回 updated utility；Beta(1,1) 平滑效用分 = (helpful+1)/(helpful+harmful+2)
```

### 3.2 内置种子模板（5 个，`BUILTIN_TEMPLATES`）

首次使用时幂等播种（`templateId` 固定，存在即跳过），`provenance.author='builtin'`：

| templateId | 名称 | 策略（综述出处） | structureSignature | taskPatterns |
|---|---|---|---|---|
| `memory_tpl_builtin_tot` | tot-decompose-backtrack | 分解—子目标—回溯（ToT/ToE [6]） | decompose, constrain, search, execute, verify, rollback | long_horizon_planning, numeric_search |
| `memory_tpl_builtin_self_refine` | self-refine | 生成—反思—修正（Self-Refine [8]） | execute, verify, rollback, execute, verify | code_synthesis, general |
| `memory_tpl_builtin_react` | react-act-observe | 行动—观察—反思（ReAct [3]/Reflexion [1]） | search, execute, verify, search, execute | multi_hop_qa, tool_orchestration |
| `memory_tpl_builtin_voyager` | voyager-retrieve-reuse | 检索—组合—复用（Voyager [11]） | search, decompose, execute, verify | tool_orchestration, gui_operation |
| `memory_tpl_builtin_toolllm` | toolllm-dfsdt | 试错—回溯—蒸馏（ToolLLM DFSDT [30]） | search, execute, verify, rollback, execute, verify | tool_orchestration, code_synthesis |

每模板 steps 由签名逐标签生成（同 §3.1 推导规则），title/description 手写（学术表述，无 em-dash）。

### 3.3 `service.py` 新 operation

| operation | 输入要点 | 输出要点 | 幂等/安全 |
|---|---|---|---|
| `plan_task` | task_desc, goal?, task_family?, events?, hasGraph?, templates[] | pattern+confidence+evidence、matchedTemplates[]（score 降序、含 steps/主动检索点）、plan 摘要文本、audit | 只读 |
| `mine_templates` | memories[], minSupport? | 候选模板 + 聚类审计 + skipped | 只读（写库由 TS 决定） |
| `template_feedback` | template, verdict | updated utility | 幂等由 TS 侧约束 |
| `publish_template_validate` | 调用者模板草稿 | 校验通过/错误清单（复用技能层守卫风格：名称 kebab、描述含触发措辞、无 em-dash、上限） | 只读校验 |

### 3.4 `retrieval.py`：模板通道

- 请求新增可选：`activeTemplateId`、`activeTemplate`（完整模板对象）、`stepIndex`、`retrievalIntent`、`memories[]` 各条新增 `strategyTemplateId`/`status`。
- `templateScore` 计算（每条记忆）：
  1. `memory.strategyTemplateId == activeTemplateId` → `1.0`
  2. 否则若有 activeTemplate 且 memory 有非空结构签名 → `0.6 × sequence_similarity(memory.signature, template.signature)`
  3. 否则 `0.0`
- RRF 通道扩展：有 activeTemplate 时 channels 权重为 `lexical 0.25 / reasoning 0.10 / vector 0.30 / structure 0.10 / graph 0.10 / template 0.15`（structure/graph 仅在有对应信号时进 channels，保持"无命中通道不归一化"规则）；无 activeTemplate 时维持现状权重。**消融要求**：eval harness 需对第二组权重（structure 0.05 / template 0.20 的拆法，见 02 调研 §3.1）做双臂对比，最终以评测为准。
- 加权回退（无 BM25/向量候选）：有 activeTemplate 时 `context 0.15 / reasoning 0.40 / structure 0.20 / graph 0.10 / template 0.15`；无时维持现有公式。`retrievalIntent` 微调：`rollback_check` → structure/template 各 +0.05（context/reasoning 同减）；`verify` → graph +0.05；其余不动。全部写入 fusionWeights 审计。
- `status='deprecated'` 记忆：不改变参与检索，但结果条目带 `deprecated: true`（配额照旧）。
- 数值稳定性：所有通道分 `_rank_score` 裁剪 [0,1]；空模板/空签名/除零均回退 0 分（debugger 复核）。

### 3.5 `distillation.py`：洞察置信度门控

- `_make_memory` 接受每 item 可选 `insightConfidence`（0–1）与请求级 `minInsightConfidence`（0=关）。
- 门控规则：`minInsightConfidence > 0` 且 `insightConfidence < minInsightConfidence` → 该 item 拒绝，`reason='below_confidence_gate'`，计入返回审计；未提供 insightConfidence（=0）时按 0 处理（即门控开启时无自评条目不通过）。文档如实声明：该门控是写入策略而非校准的质量判定（MoT 置信度过滤的工程近似）。

### 3.6 `constants.py`

`EXTRACTOR_VERSION = "dsh-reasoning-memory/0.4.0"`；新增 §2.1 上限常量、模式词表常量区、`CLUSTER_SIMILARITY`、`MIN_PATTERN_SCORE`、模板通道默认权重。

---

## 4. TypeScript 插件层（`src/`）

### 4.1 `domain.ts`

- 按 §2.1 落地 `templates` 表（或独立 domain，取决于 01 契约结论）；`MemoryItem` / `RetrievalRecord` / 候选按 §2.2/§2.3 加 additive 默认字段。

### 4.2 新工具

| 工具 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `memory_plan_task` | `task_desc`(required), `goal?`, `task_family?`, `max_templates?`(默认 2) | pattern/confidence/evidence、matchedTemplates（含 steps、主动检索点、utility）、recommendedSkills（≤3，来自技能层按 pattern/template 关键词匹配）、plan 文本、audit | 调 Python `plan_task`；recommendedSkills 由 TS 技能清单匹配；记录到活动任务状态（planTemplateIds）供 feedback 校验 |
| `memory_list_templates` | `pattern?`, `include_deprecated?`(默认 true) | 模板元数据清单（第 1 层：id/name/description/patterns/version/status/utility） | 渐进披露第 1 层 |
| `memory_load_template` | `template_id` | 完整模板（steps、结构签名、preconditions、provenance 摘要、utility） | 渐进披露第 2 层；deprecated 带标记 |
| `memory_publish_template` | `name`, `description`, `task_patterns[]`, `steps[{title, module_demands[{kind,intent}]}]`, `preconditions?` | 校验（Python publish_template_validate + TS 守卫）后落库；同名版本 +1 | 调用者作者模板；不删除历史版本 |

### 4.3 既有工具扩展（additive 参数，缺省行为与 v0.3 完全一致）

- `memory_retrieve`：新增 `active_template_id?`、`step_index?`、`retrieval_intent?`（枚举 module demand kind，非法值忽略并审计）、`include_deprecated?`（默认 true）。传入 active_template_id 时 TS 从 templates 表取模板随请求发给 Python；审计写入 §2.3 新字段。
- `memory_feedback`：新增 `template_id?`、`step_index?`。约束：`template_id` 必须 ∈ 本任务 `memory_plan_task` 返回的模板（否则拒绝并说明）；每任务每模板一次；同一调用可只评模板或只评记忆或两者。模板反馈走 `template_feedback` operation 更新模板 utility。
- `memory_save_outcome`：新增 `insight_confidence?`（0–1，作用于本次所有 item）+ 传递 profile `minInsightConfidence` 给 Python 门控；返回逐 item 拒绝审计。
- `memory_query`：支持按 `status` / `strategyTemplateId` / `reasoningLevel` 过滤（additive 可选参数）。
- `memory_begin_task` / `memory_end_task`：活动任务状态扩展 `planTemplateIds` / `templateFeedbackRecorded`（Map），end 时清理。

### 4.4 内置模板播种与挖掘落库

- 插件启动（或首个 plan/mine 调用前）幂等播种 §3.2 五个内置模板（存在同 templateId 即跳过）。
- `memory_plan_task` 返回文本提示中，若本任务可用模板得分均低于 `TEMPLATE_MATCH_MIN = 0.25`，明确告知模型"无高置信模板，按 general 模式直接执行"（避免强行套用模板；对应综述"保守录用"教训 [6]）。
- 挖掘入口：`memory_mine_templates` 工具？——**不在 v0.4.0 注册为工具**，仅通过 `scripts/eval` 与 Python operation 使用（避免模型触发昂贵/惊喜写库）。TS 保留内部调用接口供评测。

### 4.5 索引与内置库

- 模板不做 BM25/向量索引（数量 ≤200，`memory_list_templates`/`plan_task` 全量内存匹配即可）；`lexical-index.ts` 不需改动，但记忆新增字段（strategyTemplateId 等）不进索引（避免与模板通道双重计分）。
- `builtin-store.ts`：可选将 `strategyTemplateId`/`status` 加入结构化列与 `memory_query` 过滤（spec 允许，实现按现有哈希同步逻辑；若改动面大，可只在 JSONB 内存过滤，不建列——developer 按最小改动原则决定并在任务简报说明）。

---

## 5. 配置（additive，全部默认关闭/中性）

| 配置 | 默认 | 语义 |
|---|---|---|
| `minInsightConfidence` | `0` | >0 时启用洞察置信度写入门控 |
| `deprecatedAfterHarmfulFeedback` | `0` | >0 时，同一记忆在 ≥N 个不同任务收到 harmful 反馈后标记 `status='deprecated'`（只标记不删除；结果仍返回但带标记） |
| `templateMatchMin` | `0.55` | plan_task 提示的最低模板分（< 0.55 不注入模板，告知模型按 general 执行） |

---

## 6. 离线评测 harness（`scripts/eval/`，不进 npm 发货包）

文件：`validate_schema.py`（数据集校验）、`eval_dataset.py`（schema 常量 + 种子化合成生成器）、`eval_retrieval.py`、`eval_templates.py`、`eval_ablation.py`、`run_eval.py`（CLI）、`README.md`。纯 Python stdlib、无网络、固定 seed。

- **数据集**：JSONL schema 与防泄漏 7 条规则以 04-eval-schema.md 为规范（schemaVersion 1；episode 含 task/gold taskPattern/memoryWrites 双人标注六项/queries 的 relevance 0–3 增益档 + misleading 轴 + LongMemEval 五能力 category 映射 + goldTemplate；`validate_schema.py` 按 04 §8 实现全部构建期断言：族级唯一归属、粗粒度组隔离、变体相似度检查、引用完整性、答案隔离、自检索显式标注）。合成生成器产出：多任务族 episodes、**结构相似/语义不同**负控制对、train/heldout 按任务族切分（同族不跨侧）。
- **检索指标**：Recall@k / nDCG@k（按 outcome 分报）、配额遵守率（目标 100%）、自检索率（目标 0）、deprecated 过滤统计。
- **模板指标**：模式分类 accuracy（按模式分报）、模板匹配 top-1/top-3 accuracy、挖掘纯度/覆盖率/簇数量、退化场景（空库/单簇/极端签名）。
- **消融 runner**：通道组合 {none, semantic, structure, graph, template, fusion}，输出 Markdown 表（每配置一行：指标列）。
- **报告**：`docs/eval/sample-report.md` 提交一份基于合成数据的样例结果，**必须声明：合成数据结果不代表真实标注集质量**。

---

## 7. 测试要求（T-impl-3）

- Python：分类器（每模式正例 + 歧义 + 空输入 + fallback + S4 图信号）、匹配评分（各信号缺省、quality 乘子区间、阈值 0.55/0.75 边界）、挖掘（min_support 边界、θ_join/θ_form 聚类、多序列 LCS、挂起队列、fail_majority 不发射、幂等）、模板反馈幂等、(c_succ+1)/(c_use+2) 单调性、retrieval 模板通道（含/不含 activeTemplate、deprecated 标记、数值稳定、第二组权重消融入口）、蒸馏门控（0/边界/缺省）、validate_schema 校验器（合法集 + 每类防泄漏违规）。追加进 `test/test_reasoning_memory.py`。
- Node：`test/templates.test.mjs` —— mock Cordis 宿主全生命周期（begin → plan → retrieve(with template) → feedback(template+memory) → save_outcome(门控) → end），旧记忆记录（无新字段）读回/检索/反馈不抛错，模板工具校验与渐进披露。
- `npm test` 全绿；CI 双平台不变。

---

## 8. 文档要求（T-docs，lead）

- README：v0.4.0 章节（四层映射、模板库、plan/feedback 生命周期、门控、评测）。
- 设计文档：追加 2026-11-02 评审记录 + §18（四层映射落地说明、综述 [1]–[30] 映射表、路径 B/C/E 越界声明）。
- MVP_EVALUATION：成熟度表更新 + 本轮评估（引用 03-survey-facts.md 复核数字；合成评测样本诚实标注）。

---

## 9. 验收清单（T-release）

1. `npm test` 全绿（Windows 本机 + CI 配置不变）。
2. `python scripts/eval/run_eval.py` 产出样例报告；报告含防泄漏检查与合成数据声明。
3. `npm pack` 内容含 `python/reasoning_memory_core/templates.py`；不含 `scripts/eval`。
4. domain additive 兼容：旧记忆/旧检索记录读回正常（Node 测试覆盖）。
5. 全部新工具/参数在 README 与设计文档有对应说明；综述引用数字与 03-survey-facts.md 一致。
6. git 提交（逻辑拆分 ≤3 个 commit，由 lead 执行）。
