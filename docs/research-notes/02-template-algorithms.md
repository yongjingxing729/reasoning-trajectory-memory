# 02 · 模板算法调研与落地建议（综述路径 A：上下文级四层映射）

> 目标：为综述路径 A（上下文级四层映射）给出**可直接实现**的确定性算法建议。约束：**Python 标准库 only、可审计（每个分数可拆解）、可单测（纯函数、无随机）**。
> 本笔记严格区分 **「论文原文做法」** 与 **「本插件推荐的工程近似」**，工程近似不声称复刻论文。
>
> 四层映射定义（本笔记采用的落地口径，若与综述终稿术语有出入，替换术语即可、算法不变）：
> 1. **L1 任务模式（taskPattern）**：把当前任务分类到 7 类任务模式之一（见 §1）；
> 2. **L2 策略模板（strategyTemplate）**：从模板库检索/匹配最适用的步骤模板；
> 3. **L3 步骤级模块需求（module requirements）**：模板每步标注所需模块能力，能力词表 = `retrieve_memory / retrieve_template / use_skill / invoke_tool / verify / rollback_check`；
> 4. **L4 工具执行**：模块能力落到 DSH 工具调用（含 verify 与 rollback 检查）。
>
> 现有可用输入（均已存在于本插件，无需新增采集）：
> - `memory_begin_task` 的 `task_desc` / `goal` / `task_family`（`src/index.ts:927-931`）；
> - 捕获的抽象工具序列 `ActiveTask.events`（`tool_call/tool_result` 的 `toolName`，`src/index.ts:148-154`、`:825-877`）；
> - 记忆的 `structureSignature` ∈ {`decompose,constrain,search,execute,verify,rollback`}（`src/domain.ts:4-11`）；
> - `reasoningGraph`（v1 DAG，节点 kind ∈ {context,premise,inference,action,evidence,conclusion,failure}，`src/domain.ts:13-31`）；
> - 记忆条目的 `outcome`、`feedback(helpful/harmful/neutral)`、`taskScope`、`preconditions`（`src/domain.ts:35-75`）。

---

## 1. 任务模式分类器（L1）

### 1.1 输出规格

```json
{
  "pattern": "multi_hop_qa | code_synthesis | tool_orchestration | gui_operation | long_horizon_planning | numeric_search | general",
  "confidence": 0.0,            // [0,1]，来自归一化分数边际
  "evidence": [                 // 审计面：每条证据的贡献
    {"signal": "task_family", "hit": "humaneval", "contribution": 0.30},
    {"signal": "structure_verify", "hit": true, "contribution": 0.20}
  ],
  "scores": {"multi_hop_qa": 0.12, "code_synthesis": 0.74, "...": 0.0}   // 全部分类分数，便于阈值调试
}
```

### 1.2 信号与权重（初值，需在评测集上校准）

七类信号（全部可确定性计算）：

| # | 信号 | 来源 | 计算方式（标准库） | 权重建议 |
|---|---|---|---|---|
| S1 | **task_family 直查** | `taskFamily` 字符串 | 大小写不敏感子串/正则查映射表（表见下），命中即加固定分 | 0.30 |
| S2 | **词法关键词** | `taskDesc + goal` | en/zh 双关键词表，`re` 匹配（英文用词边界、中文直接子串），每类命中关键词数归一化 | 0.25 |
| S3 | **结构签名统计** | 当前任务捕获的抽象工具序列 → 与 `structureSignature` 同词表映射（工具名→6 标签的映射表）；或直接用查询的 `structureSignature` | `verify` 出现→code_synthesis +；`rollback` 出现→gui/long_horizon +；`search` 比例>0.4→numeric_search/multi_hop；`decompose` 出现→long_horizon；序列长度>8→long_horizon | 0.20 |
| S4 | **图节点分布** | `reasoningGraph.nodes[].kind` | `premise+inference` 占比>0.5→multi_hop；`action` 占比>0.4→tool/gui；`evidence` 出现→numeric_search/verify 类 | 0.15 |
| S5 | **历史同类命中** | 最近 N 次（如 10 次）`memory_retrieve` 审计中选中记忆的 `taskScope`/`structureSignature` 分布 | 众数投票，加分 0.05×命中率 | 0.10 |

taskFamily 映射表（初值，键为子串，匹配顺序 = 表序，命中即停）：

| taskFamily 子串（示例） | 分类 |
|---|---|
| humaneval, mbpp, code, swe, repo, git, debug, refactor | `code_synthesis` |
| webarena, mind2web, browser, gui, ui, click, form, ecommerce, shopping | `gui_operation` |
| gsm8k, math, arithmetic, numeric, equation, finance, 24game | `numeric_search` |
| hotpotqa, multihop, multi-hop, qa, question, wikidata, knowledge | `multi_hop_qa` |
| alfworld, sciworld, plan, long, multi-step, pipeline | `long_horizon_planning` |
| 其余 / 未提供（默认 `'general'`，`src/index.ts:948`） | `general`（不判 `tool_orchestration`，见下） |

### 1.3 聚合、阈值与置信度

```
score(c) = Σ_i w_i × f_i(c)          # f_i(c) = 该信号对类别 c 的命中分（0/1 或归一化计数），Σw_i = 1
pattern  = argmax_c score(c)
margin   = score(best) - score(second)
confidence = clip(2 × margin, 0, 1)   # 双分类边际映射；全零分数时 confidence = 0
```

- **裁决规则（优先级）**：
  1. `task_family` 命中映射表 → 直接采用该分类，`confidence = min(0.9, 0.6 + 其他信号一致性×0.3)`；
  2. `score(best) ≥ 0.35` 且 `margin ≥ 0.10` → 采用 `argmax`；
  3. 否则 → `general`，`confidence = score(general 兜底分数)`。
- `tool_orchestration` 的特殊规则：S3 中"多种互异工具且无 verify/rollback"（工具名集合 ≥3 且序列长 ≤8）→ 显式 +0.5 权重内加权；它是"编排"而非"长规划"。
- `general` 永远是兜底，不是"失败"：分类器输出 `general` + 低置信度时，L2 检索跳过 pattern 约束、退化为纯结构+词法匹配。
- 阈值理由：单信号权重 ≤0.30，故 0.35 阈值要求至少两个信号联合；`margin` 排除平局。**所有阈值均为初值**，按 MVP_EVALUATION.md:126 的思路（候选深度/阈值曲线）在标注集上扫描校准。

### 1.4 论文对照

- **论文原文**：四篇参考论文都没有"7 类任务模式分类器"这个组件——AWM 按 **website** 分组组织 workflow（"we conduct AWM on a website basis"，AWM §3），ProactAgent 用 **RL 学习何时/检索什么**（ProactRL），EvolveR/ToE 按问题实例组织经验。
- **工程近似定位**：本分类器是无监督先验（规则），作用是给 L2 检索一个可解释的类别约束 + 给评测提供 taskPattern 标签候选。与 ProactAgent 的 RL 检索决策**不等价**（我们不训练策略），与本插件"确定性、可审计"的定位一致。

---

## 2. 模板挖掘（L2/L3）

### 2.1 数据与表示

- 每条记忆 `m` 已有 `structureSignature`（6 标签序列）与 `outcome ∈ {success,fail}`。
- 模板候选 `T = (sig, steps[], stats, meta)`：
  ```python
  T = {
    "templateId": str,                 # uuid 或 hash(sig) 派生，可复现
    "structureSignature": ["decompose","search","verify","execute"],   # 簇代表签名
    "coreSignature": ["decompose","search","verify"],                  # 簇内多序列 LCS（公共骨架）
    "steps": [                          # 每步一个模块需求块
      {"position": 0, "tag": "decompose",
       "modules": ["retrieve_template"],        # 必选模块
       "optional": ["retrieve_memory"]},        # 可选模块
      ...
    ],
    "taskPattern": "long_horizon_planning",     # 簇内众数
    "description": "",                          # 从记忆 title/description 众数或模板化
    "preconditions": [],                        # 簇内 constrain 相关 precondition 交集候选
    "stats": {"members": 12, "success": 10, "fail": 2,
              "useCount": 34, "succCount": 25,
              "helpful": 30, "harmful": 2, "neutral": 2},
    "createdAt": 0, "updatedAt": 0
  }
  ```

### 2.2 聚类：确定性在线代表点聚类（Python 标准库可做）

- **距离**：`difflib.SequenceMatcher(None, sigA, sigB).ratio()`（规范化编辑相似度）。备选：LCS-F1 = 2·|LCS|/(|A|+|B|)，与本插件现有"结构相似度 = LCS/F1 风格"（MVP_EVALUATION.md:76）保持一致；两者都纯标准库，建议默认 `SequenceMatcher.ratio()`（对调序更宽容、实现零依赖）。
- **算法**（在线、时序确定、无随机）：
  ```
  θ_join = 0.75        # 相似度 ≥ θ_join 并入最近簇
  θ_form = 0.55        # 与所有簇代表相似度 < θ_form 才开新簇（防簇爆炸）
  对每条新记忆 m（按 createdAt 顺序）:
    best = argmax_T sim(m.structureSignature, T.structureSignature)
    if best 存在且 sim ≥ θ_join:  把 m 并入 best（更新 stats、coreSignature、steps 模块需求）
    elif sim < θ_form:            开新簇（m 为初始代表）
    else:                         记入"挂起队列"，挂起条目不参与代表更新（避免漂移）
  ```
- **簇 → 模板**：
  - `coreSignature` = 簇内签名集合的多序列 LCS（实现：先取最短签名，逐个与其余签名做 LCS 收缩；签名都很短（≤几十），O(n·k²) 可接受）。
  - 每个位置 tag → 模块需求映射（见 §2.3），簇内同位置模块需求取**众数**（必选）/ 其余为可选。
  - `taskPattern` = 簇内记忆 taskScope/分类众数（用 §1 分类器对每条记忆的语义文本离线重跑一次，保证标签一致）。
  - 保留 `outcome` 分桶（success/fail 都参与聚类，模板 `stats` 记录比例；fail 占比 >0.5 的簇标记为"反例模板"，检索时降权或作为 contraindication 模板返回）。

### 2.3 步骤级模块需求推导（L3）

从簇内每步 tag + 该步记忆对应的工具/反馈信息，确定性推导模块能力（词表固定 6 种）：

| 步骤 tag | 必选模块 | 可选模块 | 依据（本插件信号） |
|---|---|---|---|
| `decompose` | `retrieve_template` | `retrieve_memory` | 拆解大任务时先查已有模板/记忆（路径 A 的核心复用点） |
| `constrain` | — | `retrieve_memory` | 约束来源通常是前提/教训；同步写入模板 `preconditions` |
| `search` | `retrieve_memory` | `invoke_tool` | 信息/证据缺口 → 查记忆或调搜索类工具 |
| `execute` | `invoke_tool` | `use_skill` | 动作执行；若该步记忆关联了已发布 skill（`memory_publish_skill`）则升级为 `use_skill` |
| `verify` | `verify` | — | 验证步骤；记忆 `evidence.verifier` 非空为该步加分 |
| `rollback` | `rollback_check` | `verify` | 回退检查 |

> 注意：模块需求是**模板内步骤的声明**，不是运行时强制——运行时由 agent 依模板提示自行决定是否真的调用 `memory_retrieve`/`verify`；插件只提供可检索的模板文本（与 AWM"workflow 作为引导文本注入记忆"一致）。

### 2.4 模板匹配评分（L2 检索）

对当前任务 `q`（taskDesc+goal、当前捕获序列、taskPattern、可选 reasoningGraph）与模板 `T`：

```
match(q, T) =
    0.50 × struct(q.structureSignature, T.coreSignature)     # LCS-F1 或 SequenceMatcher.ratio
  + 0.30 × lex(BM25(q.text), T.description ∪ 步骤标签文本)   # 复用现有 BM25（TS 侧），中文 char/bigram
  + 0.20 × pattern(q.taskPattern == T.taskPattern ? 1 : 0)   # 分类一致加分（q 为 general 时该项按 0.5 计）
  然后 × quality(T)                                         # 质量乘子
quality(T) = 0.5 + 0.5 × (T.stats.succCount+1)/(T.stats.useCount+2)   # EvolveR 式平滑成功率，映射到 [0.5,1]
             × (1 - 0.3 × harmRatio)                         # harmful 反馈降权，下限 0.5 兜底保证新模板可见
```

- 阈值建议：`match ≥ 0.55` 视为可注入上下文；`≥ 0.75` 视为强匹配（同时返回模板 `contraindications`）。初值，需在评测集扫描。
- 反例模板（fail 为主）不注入正向模板流，而并入现有失败记忆通道（保持正负配额语义，MVP_EVALUATION.md:78）。

### 2.5 论文对照（本小节核心）

| 参考论文 | 论文原文做法 | 本插件工程近似（及差异） |
|---|---|---|
| **AWM** (2409.07429) | **LLM 归纳**：prompt 模型从经验中抽取**更细粒度 sub-routine**，并把具体值抽象为占位符（"dry cat food"→"{product-name}"）；workflow = 描述 d + 步骤序列 p=(状态描述, 推理, 可执行动作程序)；离线（按 website 拼接训练例一次性归纳）/在线（成功经验即时归纳，失败不归纳）双模式；归纳结果**整包注入记忆**，不做相似度检索 | 近似：用**标签序列聚类**替代 LLM 归纳（`structureSignature` 是 AWM 的"抽象动作轨迹"的低配版）；"占位符抽象"简化为**只保留标签序列 + 众数文本**，不生成 NL 占位符；"在线只归纳成功经验"→ 我们 success/fail 都入簇但分桶统计；"整包注入"→ 我们改为**按 match() 选择性注入**（受 ProactAgent/MemWeaver 检索思想的修正）。差异点：原文是 LLM 生成工作流文本，我们全程无 LLM、纯标准库。 |
| **Tree-of-Experience** (2608.09044) | 经验组织成**共享的"分析视角×推理路径"树**，节点可靠性由环境结果校准，支持更新/迁移/高效检索；Game of 24 +31.4%（相对）、FinEvolveBench tsIC +41.24%（12 设置平均） | 近似：不做视角树；以**模板内 `coreSignature`（主干）+ verify/rollback 分支（成功路径/失败回退两条）**充当受限的"路径树"；可靠性校准照搬了"环境结果计数"思想（success/use 计数、harmful 降权）。 |
| **ProactAgent** (2604.20572) | ExpOnEvo 把交互组织为 **factual/episodic/skill 三类仓库**；ProactRL 把**检索当作显式策略动作**，用"同前缀轨迹的配对续写（有检索 vs 无检索）"构造**步级过程奖励**，让模型学会只在有用时检索；成功率相对提升 up to 32%、交互轮次减少 over 33%（SciWorld/AlfWorld/StuLife） | 近似：分类仓库→我们已有 memories/skills 两个 domain 加模板表（事实/情节两级在记忆条目字段内）；RL 检索策略→我们用**确定性的 match() 阈值触发 + 模板步骤声明 retrieve_* 模块**；不做 RL、不做配对续写奖励（无训练数据）。原论文的"省轮次"是我们希望复现的**目标指标**（评测 schema 已纳入交互轮次/步数统计）。 |
| **EvolveR** (2510.16079) | 三组件生命周期：Offline Self-Distillation（LLM 把轨迹蒸馏成"描述 + 知识三元组"原则）→ Online Interaction（`<search_experience>` 动作检索原则）→ Policy Evolution（GRPO 闭环）；去重 = 嵌入余弦相似 + 模型二值语义等价判断（阈值 θ_sim）；动态打分 **s(p)=(c_succ+1)/(c_use+2)**，低于 θ_prune 剪枝 | 近似：三组件→ 我们的"挖掘（聚类）→ 匹配检索 → 反馈计数回写"闭环（无 RL，GRPO 不可用标准库实现也违背约束）；去重→ 簇并入即去重（阈值 θ_join/θ_form）；**动态打分公式 (c_succ+1)/(c_use+2) 原样复刻**（纯算术，可单测）；剪枝阈值 θ_prune 建议初值 0.2（低于即移出注入集合，不物理删除，保留审计）。 |
| **LoRAHub** (2307.13269) | 把多个 LoRA 模块**逐元素加权组合**（m̂=(ΣwᵢAᵢ)(ΣwᵢBᵢ)），组合**不引入新参数、不需要梯度**；系数 w 用梯度自由方法搜索（Shiwa，主要 **CMA-ES**，目标 loss + L1 正则） | 近似（思想迁移到"模块组合"而非权重）：L3 模块需求的**组合不训练任何模型参数**——模板 = 模块序列的确定性组合；若后续需要"模板融合"，用**坐标轮换/网格搜索**替代 CMA-ES（标准库可做，且系数空间小）。 |

---

## 3. 检索融合：template 通道相似度度量（L2 融入现有 retrieve）

### 3.1 建议：独立 `template` 通道 + RRF

现有融合（MVP_EVALUATION.md:63）：RRF 通道 = lexical 0.30 / reasoning 0.10 / vector 0.35 / structure 0.10 / graph 0.15，仅对有结果的路归一化。建议：

- 新增 `template` 通道，与 `structure` 并列（初值权重建议：把 structure 0.10 拆为 structure 0.05 + template 0.05，或 template 直接 0.10、structure 保持——二选一，**必须由评测决定**，先在 04 评测 schema 中记录两组权重做消融）。
- 通道内排序分（对模板集合，非记忆集合）：
  ```
  score_template(q, T) =
      0.50 × LCS-F1(q.sig, T.coreSignature)
    + 0.30 × BM25(q.text, T.description)          # 复用 TS 侧 BM25（无需新索引：模板进现有倒排或独立倒排）
    + 0.20 × patternConsistency(q.taskPattern, T.taskPattern)
  ```
  再乘 `quality(T)`（§2.4）。该分数只决定**模板之间的排名**，随后模板排名进入 RRF 与其他通道（记忆）合并。
- 模板注入上下文的方式：top-1 强匹配（match≥0.75）把模板文本+模块需求+contraindications 并入 `memory_retrieve` 返回的 `note`/新字段；`match<0.55` 不注入。
- 度量选择依据（相对论文的定位）：
  - **AWM 不做检索**（整包注入），所以没有现成相似度可抄；
  - **MemWeaver** 的 dual-channel（结构化知识 + 证据）启示：模板通道 = "结构化知识"角色，与记忆的证据文本通道并列，**两通道都要进融合**；
  - **Hindsight**（多路检索 + RRF + reranker）：我们已用 RRF，模板通道是"多一路"的最小改动；
  - **EvolveR** 的检索相似度 = 嵌入余弦 + 二值语义等价（θ_sim 二段式）；工程近似 = LCS-F1（确定性）替代余弦 + 阈值二段式（θ_join 并入簇 / match 阈值注入）保留二段式结构。

### 3.2 审计与单测要求

- 每个模板通道分必须拆解成 `{struct, lex, pattern, quality}` 四分量写入候选审计（沿用 `candidateScores` 模式，`src/index.ts:192-212`）。
- 单测清单（`python/tests`）：聚类确定性（同输入两跑结果全等）、θ 阈值边界、LCS-F1 与 SequenceMatcher 的等价性参照、`(c_succ+1)/(c_use+2)` 单调性、quality 乘子裁剪区间、match() 阈值、RRF 新增通道后的归一化行为、中文 char/bigram 词法分。
- 全部实现仅允许标准库模块：`collections.Counter/defaultdict`、`difflib.SequenceMatcher`、`re`、`math`、`json`、`heapq`、`itertools`；TS 侧 BM25 复用现有 `LexicalMemoryIndex` 风格倒排（新建模板倒排，勿改记忆倒排）。

## 4. 实施顺序建议（供 code-developer 参考）

1. `templates` 表 schema（zod，additive 字段预留）→ 落 `src/domain.ts` 或独立 domain（见 01-dsh-contracts.md 方案 A/B）。
2. Python 纯函数包：`classifier.py`（§1）→ `template_miner.py`（§2.2/2.3）→ `template_match.py`（§2.4/§3）。
3. TS：模板倒排 + `memory_find_template`（或并入 `memory_retrieve` 的 route 扩展）+ 审计字段。
4. 评测：用 04-eval-schema.md 的标注集校准全部阈值/权重（初值已在本文给出，均为待校准建议值）。
