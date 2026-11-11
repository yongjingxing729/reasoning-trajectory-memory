# DSH 推理轨迹记忆插件 MVP

本仓库本身是一个可安装的 DeepSeek Harness Cordis bundle。插件在 DSH 进程内注册 `memory_*` 工具、监听当前会话的 `session/event`，并通过 DSH 的 `storageDomain` 持久化蒸馏后的记忆和检索评分记录；没有单独的 Web 服务、MCP server 或任务数据库。

## 实现范围

- TypeScript：Cordis 入口、DSH 工具注册、会话与 `task_id` 关联、生命周期和 DSH 领域存储；另维护本地 BM25 倒排索引、可选 Qdrant 向量索引，以及内置 PostgreSQL（PGlite + pgvector）结构化/向量存储。
- Python：轨迹事件归纳、推理策略蒸馏、逻辑图匹配、RRF/加权检索融合、受限效用调节和正反记忆配比；按功能拆分在 `python/reasoning_memory_core/`，`reasoning_memory.py` 仅保留稳定 CLI 入口与兼容导出。
- 持久化内容是成功策略或可归因失败教训，含适用前提、来源事件序号、验证材料、信任等级与结构签名。活动任务及原始轨迹只暂存在内存。
- 蒸馏条目按 ReasoningBank 的 title/description/content/contraindications 结构化保存（每次最多 `maxMemoryItemsPerOutcome` 条）；`memory_items` 结构化提交、可选 LLM 轨迹蒸馏与 `memory_consolidate` 自对比整合共用同一套确定性守卫：失败可归因门控、去特异化（近复述任务描述被拒）与批内去重。原始 reasoning 文本仍不持久化。
- 不读取或保存工具参数/输出、完整提示词或模型提供的 reasoning/CoT。结构签名只来自可观察的工具调用及其结果。
- 用户/模型提供的 verifier 名称和 evidence 目前不能由插件独立核实；所有新条目都明确标记 `trustLevel: unverified`，不会被提升为 `verified`。
- `confidence` 是固定的蒸馏启发值，不是成功概率，也没有跨任务校准。

这符合 ReasoningBank 的关键闭环：从成功和失败经验提炼可迁移策略，按新任务检索，再将结果纳入后续记忆。成功策略和失败归因分别作为 `reasoning` 保存，任务描述只保留为情境；检索按推理教训本身排序，并融合 DSH 工具因果序列。活动任务自己的记忆会被排除，避免当前任务保存的经验立即检索回自身。只有任务描述或终态的内容不会入库。它不是任务完成记录器，也不把原始轨迹当记忆。

## 正反比例

默认一次返回 4 条，成功:失败配比为 `1:1`，由 `positiveNegativeRatio` 配置（例如 `2` 表示 `2:1`）。当某一侧数量不足时，剩余名额按融合分数由另一侧补齐，并在工具结果中返回 `fallbackUsed`；奇数 `k` 会取最接近的整数配额。`partial` 与 `unknown` 不进入正/负池；失败没有证据支持的归因时不会生成负向记忆。

默认 `auto` 在本地 BM25 找到候选后切换为 RRF 融合；若 BM25 无命中，则回退全量 Python 排序，沿用下面的透明加权公式。成功与失败各自独立召回候选，再由 Python 按正反配额挑选。显式图或动作路由会扫描全部记忆，以免 BM25 门控漏掉结构/图相似项。

`relevanceThreshold`（默认 0，关闭）在正反配额前过滤融合分数过低的候选，过滤数量计入检索审计的 `thresholdFilteredCount`。ReasoningBank 的消融显示检索 k=1 优于 k=2..4（质量高于数量），因此检索结果附带元认知引导：逐条判断哪些记忆真正适用于当前子问题、哪些不适用。

### 内置 PostgreSQL 与向量存储（可选）

- `builtinStoreEnabled`（默认关闭）启用进程内 PostgreSQL（PGlite 0.4.x，携带 pgvector 0.8 扩展）：记忆以结构化列 + JSONB 字段存储（结构化 JSON 数据），同时每行保存稠密向量并建 HNSW 余弦索引，作为内置向量数据库。`builtinStoreDataDir` 留空时运行在内存中（每次启动从 `storageDomain` 重建）；配置目录后数据库文件落盘持久化。
- 内置库是**派生存储**：`storageDomain` 仍是事实源。每行维护两个哈希——`contentHash`（嵌入输入文本 + 嵌入配置，驱动重新嵌入）与 `rowHash`（完整结构化行，驱动行刷新但不重嵌）——启动时按哈希增量同步、移除过期行；维度/嵌入配置变化会重建表。写入路径为：先提交 DSH domain，再异步透传到内置库。
- 内置库启用时接管向量候选召回（替代外部 Qdrant 的角色；两者同时开启时内置库优先，Qdrant 闲置）。同步期间或降级时检索回退 BM25；嵌入服务不可用时结构化行照常入库并降级为 json-only，仅向量检索不可用。检索不等待全量回填。
- `memory_query` 工具提供安全的结构化 JSON 查询：按 outcome / 类型 / 信任等级 / task 范围子串过滤，支持分页与排序，全部参数化、无原始 SQL；内置库不可用时自动回退为对规范存储的等价扫描。

### 用户 API 端点配置（apiConfigPath，可选）

profile 设置 `apiConfigPath` 指向一个本地 YAML 文件后，插件的所有对外 API 调用（嵌入、LLM 蒸馏、未来新增功能）都从该文件取端点配置，用户可随时编辑，无需改 profile 或重启：

```yaml
# reasoning-memory API 配置 — 可热编辑，无效内容自动回退上一份好配置
version: 1

proxy:                    # 全局代理：国内访问国外 API 时使用
  url: http://127.0.0.1:7897
  noProxy: [127.0.0.1, localhost]

defaults:
  timeoutMs: 30000

endpoints:                # 命名端点；插件按名字引用，按 kind 校验契约
  embeddings:             # kind: openai-embeddings
    kind: openai-embeddings
    baseUrl: https://api.openai.com/v1        # 实际请求 {baseUrl}/embeddings
    apiKeyEnv: OPENAI_API_KEY                 # 推荐：从环境变量读 key
    model: text-embedding-3-small
    dimension: 1536
    # proxy: direct                            # 可选：'direct' 直连 / 具体 URL / 留空继承全局

  distillation:           # kind: openai-chat（LLM 蒸馏 / memory_consolidate 使用）
    kind: openai-chat
    baseUrl: https://api.deepseek.com         # 实际请求 {baseUrl}/chat/completions
    apiKeyEnv: DEEPSEEK_API_KEY
    model: deepseek-chat
    temperature: 1.0

  my-helper:              # kind: http-json：给未来功能的通用透传端点
    kind: http-json
    baseUrl: https://some.service.example
    headers: { X-Custom: abc }
```

规则与语义：

- **契约校验**：每个端点声明一种 `kind`（`openai-embeddings` / `openai-chat` / `http-json`）；插件按功能需要校验端点种类，不匹配时明确报错，不会把请求发错。
- **密钥来源**：`apiKeyEnv`（环境变量，推荐）与 `apiKey`（内联明文，状态页会标记）二选一；任何状态输出与日志都只显示来源、不显示值。
- **代理**：全局 `proxy` + 每端点覆盖（`direct` 关键字直连）；`noProxy` 支持精确主机、`.domain` 后缀与通配。Node fetch 不读系统代理，插件用 undici ProxyAgent 实现，代理/noProxy 改动**热生效**；密钥与 baseUrl 改动热生效；**模型/维度改动需重启**（pgvector 表维度与同步哈希在启动时绑定）。
- **热重载**：文件被监控；语法/校验错误会保留上一份好配置并在 `memory_api_status` 中显示错误。
- **优先级**：配置文件的 `embeddings` 端点覆盖 profile 里的 `embeddingUrl/embeddingModel/embeddingDimension/embeddingApiKeyEnv`；`distillation` 端点在 profile 未显式设置 `distillationProvider` 时接管 LLM 蒸馏与 `memory_consolidate`。
- **自查**：`memory_api_status` 工具返回加载状态、端点清单（含 key 来源与代理决策，全部脱敏），`reload: true` 可手动重读。

### 技能层（推理记忆 → Agent Skill）

蒸馏出的推理记忆可以进一步**发布为 Agent Skill**，遵循 Agent Skills 的工程化 prompt 规范（SKILL.md 前端元数据 + 简洁正文 + 渐进披露资源文件）：

- `memory_publish_skill`：把 1–10 条记忆折叠成一个技能。默认自动生成 kebab-case 名称、模型面向的触发式 description（"Use when…"）、带步骤与**完成标准**的正文，以及三个资源文件——`reasoning-graph.md`（带来源的推理逻辑图）、`structure-signature.md`（抽象工具动作序列）、`evidence.md`（验证证据）。同名再发布会**版本 +1**；技能通过 `sourceMemoryIds` 回指来源记忆，并继承 `unverified` 信任等级。
- **渐进披露三层**：`memory_list_skills`（第 1 层：仅元数据/触发描述/版本/资源数）→ `memory_load_skill`（第 2 层：完整 SKILL.md 正文与资源清单）→ `memory_load_skill_resource`（第 3 层：单个资源文件内容，按需加载）。
- **确定性 prompt 工程守卫**：名称 kebab-case 且保留 `memory_` 前缀；description 必须携带触发措辞（when/if/for/当/如果/提到 等）否则拒收；正文与描述禁止 em-dash；资源路径限定扁平 `.md` 文件名（无目录穿越）、单文件 ≤64KB、每技能 ≤5 个；正文/描述有硬上限。
- **文件导出**：`skillsExportDir`（默认空 = 不导出）配置后，发布即落盘为 `skills/<name>/SKILL.md` + 资源文件 + `skills/README.md` 索引，外部 harness（Claude Code 等）可直接挂载该目录消费。
- 技能是派生制品，不改变既有记忆/检索行为；删除记忆不影响已发布技能（反之亦然）。

### 策略模板库与激活动作化（v0.4.0）

本迭代依据《面向 LLM 智能体的推理模块高效复用研究综述》（30 篇文献）落地其四层映射骨架（任务模式 τ → 策略模板 π → 能力模块 m → 激活动作 a）的**路径 A（上下文级）**，把"何时检索、检索什么、用什么技能"从固定策略升级为显式的、按推理步骤对齐的联合决策：

- **任务模式分类**：`memory_plan_task` 用确定性分类器（task_family 查表 / 双语关键词 / 结构签名统计 / 图节点分布四信号，权重 0.35/0.30/0.20/0.15）把任务分到 7 类模式（multi_hop_qa / code_synthesis / tool_orchestration / gui_operation / long_horizon_planning / numeric_search / general），输出置信度与可审计的证据分解；无把握时明确回落 `general` 而不强行套模板（Tree-of-Experience 的保守录用教训）。
- **策略模板库**：`StrategyTemplate` 记录（步骤级模块需求 + 结构签名 + 适用模式 + 前提 + 效用统计 + 来源）。5 个内置种子（ToT/ToE 分解—回溯、Self-Refine 生成—验证—修正、ReAct 行动—观察—反思、Voyager 检索—组合—复用、ToolLLM DFSDT 试错—回溯—蒸馏）幂等播种；`memory_publish_template` 支持调用者发布（kebab 名 + `memory_tpl_` 前缀 + 触发式描述守卫，同名版本 +1）；模板还可由 `mine_templates` operation 从记忆的 (结构签名, 任务模式) 确定性挖掘（SequenceMatcher 代表点聚类 θ_join=0.75/θ_form=0.55 + 多序列 LCS 骨干 + min_support 与成功占比 ≥0.5 门控），挖掘入口先供离线评测使用，不注册为模型工具。
- **激活动作化**：`memory_plan_task` 返回激活计划——匹配模板（match = (0.50 结构 + 0.30 词法 + 0.20 模式一致) × EvolveR 式效用乘子，0.55/0.75 双阈值）、每步模块需求（retrieve_memory / retrieve_template / use_skill / invoke_tool / verify / rollback_check）、**主动检索点**（ProactAgent 式 proactive retrieval 的确定性近似）与推荐技能（≤3，只读匹配）。`memory_retrieve` 新增 `active_template_id` / `step_index` / `retrieval_intent` 把检索对齐到推理步骤，并启用 template 融合通道（RRF 主臂权重 0.15，备选臂 0.20 供消融）。
- **联合决策与过程级反馈**：`memory_feedback` 新增 `template_id` / `step_index`（模板须属本任务计划，每任务一次），模板效用按 Beta 平滑更新并参与匹配质量乘子；记忆侧 harmful 反馈跨 distinct 任务计数（`harmfulTaskIds` 最近 8 个窗口）达到 `deprecatedAfterHarmfulFeedback` 阈值时标记 `status='deprecated'`（只标记不删除，结果带标记）。
- **质量门控**：`memory_save_outcome` 可选 `insight_confidence`（0–1），`minInsightConfidence > 0` 时低于阈值的条目拒写并审计（MoT 置信度过滤的写入策略近似；这是策略不是校准的质量判定）。
- **离线评测**：`scripts/eval`（不进发货包）提供 episode 标注 JSONL 校验、种子化合成数据生成、检索质量（Recall@k/nDCG@k 按 outcome、配额遵守率、自检索率、misleading 误召回）、模板评测（分类 accuracy、匹配 top-k、挖掘纯度/覆盖率）与通道消融矩阵，`python scripts/eval/run_eval.py` 产出 Markdown 报告；样例报告明确标注合成 ≠ 真实标注。
- **边界声明**：综述的路径 B（LoRA 组合）/ C（SAE 特征干预）/ E（RL 联合决策学习）不在本插件落地——DSH 插件无权重与潜空间访问权限，且拒绝方向干预的双刃剑风险 [24] 要求激活保持在上下文/工具/技能注入这一可审计、可回滚的层面。本迭代的"激活"是可执行的计划动作，不改变模型内部计算；过程级反馈是低代价在线信号，不是校准的强化学习奖励。设计细节与 30 篇文献映射见设计文档 §18。

### 因果效用引擎（v0.6.0）

v0.5.0 的效用反馈是**关联性**的（Beta 计数估计 P(成功|用过记忆)），回答不了"使用记忆是否**导致**成功"。v0.6.0 把它升级为**因果信用分配**——估计处理效应 E[Y|do(m)]−E[Y|do(¬m)]，以有界、保守、可审计的方式进入检索排序：

- **反事实记录（一等原语）**：`memory_record_counterfactual` 记录配对续写双臂（同前缀有/无检索的结局对比，COMA 式反事实基线；配对由 agent/harness 执行，插件只记录与估计）。
- **观察性因果估计**：`memory_save_outcome` 自动从活动任务状态派生因果事件（用了哪些记忆 + 结局 + 任务模式等混杂），Python 按任务模式分层（后门/分层调整）估计 ATE/CATE（Laplace 平滑、每臂 ≥5 层门槛、Wald 置信区间）；IPTW 仅作稳健性对照（与分层估计差异 >0.15 审计告警，不喂排序）。
- **辛普森悖论防护**：粗效应与分层效应符号相反且双侧显著 → `simpsonFlip` 告警，排序一律采用分层估计（ToE"朴素经验管理常低于无经验基线"正是此类失真）。
- **保守集成**：有效样本 ≥20 才启用因果分量；收缩 ATE×n_eff/(n_eff+20)；乘数 `1+clip(ATE_shrunk, ±0.20)` 与 Beta 乘数同区间 [0.8,1.2]——**因果引擎永不比现状更激进**；样本不足自动回退 Beta 效用（`causalApplied=false`，与 v0.5.0 行为逐字节一致）；负效应只压低不删除。
- **审计**：检索结果逐候选带 `causalUplift/causalSamples/simpsonParadox/causalStrata/estimatorVersion`；`memory_query` 加 `include_causal` 可查因果统计摘要。
- **诚实边界**：识别假设 5 条（可忽略性/SUTVA/处置良定义/结局良定义/verdict=代理标签）——仅控制在已观测混杂（任务模式/族/模型版本/检索意图/步骤位）下成立；因果结局用环境 outcome 而非自报 verdict；估计全部由确定性计数完成，不委托 LLM；因果分不进 RRF 权重与正反配额。论文依据见设计文档 §20 与 `docs/research-notes/06-causal-ai.md`。

### 多概率保持引擎（v0.7.0）

受量子力学概率与因果 AI 启发，把"Agent 壳子把概率模型坍缩为单一输出"作为待解决问题：开源中 Top-1→Top-3 准确率下降被归因于**过早坍缩丢失叠加信息**（LLM 本是概率模型，argmax 决策/单条注入却强制单一输出，不符合现实任务多值、可叠加、顺序相关的逻辑）。v0.7.0 在四层映射上落地**多概率保持**：

- **叠加态而非单点**：候选（任务模式/模板/记忆）不再 argmax 坍缩为单点，而保持叠加态 `|ψ⟩=Σφ_i|i⟩`（振幅模长 = 检索/效用分归一化、相位 θ∈{0,π} = 因果 ATE 方向，MVP 只用实数）；概率按玻恩定则 `P(i)=|φ_i|²` 提取。
- **干涉项承载组合效应**：两候选联合使用时 `P(i∨j)=r_i²+r_j²+2r_i r_j cos(θ_i−θ_j)`——同相建设性、反相破坏性；这正是边际 ATE 够不到的**交互效应**，也解释 ToE"朴素经验管理低于无经验基线"的破坏性干涉可能。
- **延迟坍缩与显式测量**：把"取 top-k"从隐式坍缩变成显式、可审计、可再测量的操作（证据增量时 Lüders 部分测量）；坍缩推迟到最后一个负责任的时刻。
- **退相干桥接 = 零回归**：`ρ→(1−rate)ρ+rate·diag(ρ)`，速率由有效样本量驱动；**完全退相干极限即 v0.6.0 因果引擎行为**，`quantumDecisionEnabled=false`（默认）时与 v0.6.0 逐字节一致。
- **诚实边界**：不涉量子计算/硬件、不称模型内部为量子系统、不做量子因果模型、线上不采样（Self-Consistency 式采样仅离线消融）、干涉/相位不进 RRF 权重与正反配额（只作用于最终 multiplier、封顶 ±20%）。设计依据见设计文档 §21 与 `docs/research-notes/07-quantum-probability.md`；具体函数实现后续 `spec-0.7.0` 开发。

### 混合检索与可选向量索引

- 本地 BM25 倒排索引索引推理教训、任务情境、前提、工具动作和图标签；中文按单字/二元组匹配，英文及数字按词项匹配。成功和失败分别取 Top-N，默认各 100 条。
- 可选 Qdrant HNSW 稠密检索按 outcome 分别取候选，并排除当前 `task_id`。BM25 与向量候选取并集后交给 Python；Python 再计算推理文本、工具结构和逻辑图信号，并用加权 RRF 融合。默认权重为 lexical `0.30`、reasoning `0.10`、vector `0.35`、structure `0.10`、graph `0.15`；没有结果的通道不参与归一化。成功/失败配额在融合排序后执行。
- `storageDomain` 仍是记忆事实源。Qdrant 仅存向量及 `memoryId`、`outcome`、`taskId`、内容哈希等索引元数据，不存记忆原文；启动后台按哈希同步并移除过期点，嵌入模型/端点/维度变化会触发重新嵌入。回填期间检索不等待全量同步，会先使用 BM25/必要时全量 Python 回退；新写入先提交 DSH domain，再异步更新派生索引。
- 向量索引默认关闭。启用后，记忆语义文本（情境、推理教训、前提、动作和图标签）会发送到所配置的 OpenAI-compatible embeddings endpoint；查询也会发送到同一端点。请按你的数据策略选择本地或远端 endpoint。Qdrant/embedding 故障时插件降级到 BM25；若 BM25 也无候选，则用全量 Python 排序兜底。
- `RRF` 不比较 BM25 分值与 cosine 分值的绝对大小，而融合各通道排名。当前没有 cross-encoder 二阶段模型；如需评估，应单独统计质量、延迟和服务成本后再接入。

### 企业规则 RAG（可选）

企业规则是独立检索通道，不写入推理记忆，也不参与成功/失败配额。管理员可在插件 profile 开启能力；默认关闭。开启后 `memory_retrieve` 默认带回规则，模型也可通过 `include_enterprise_rules: true` 请求；只有 `enterpriseRuleRagRequired: true` 时，模型不能在单次调用关闭检索。

```yaml
reasoning-memory:
  enterpriseRuleRagEnabled: true
  enterpriseRuleRagRequired: true
  enterpriseRuleCollection: enterprise_reasoning_rules
  enterpriseRuleSetId: org-prod-v3
  enterpriseRuleCandidatePoolSize: 20
  enterpriseRuleResultLimit: 5
  enterpriseRuleScoreThreshold: 0.3
  enterpriseRuleQdrantApiKeyEnv: ENTERPRISE_RULES_QDRANT_READ_KEY
  # 未填写以下 embedding 覆盖项时，沿用上面的通用 embedding endpoint/model/dimension。
  enterpriseRuleEmbeddingUrl: http://127.0.0.1:8000/v1/embeddings
  enterpriseRuleEmbeddingModel: enterprise-policy-embedder
  enterpriseRuleEmbeddingDimension: 1024
  enterpriseRuleEmbeddingApiKeyEnv: ENTERPRISE_RULES_EMBEDDING_KEY
```

企业侧负责把规则切分、嵌入并写入独立 collection；插件只执行 Qdrant `GET` / `points/query`，不创建、改写或删除企业规则。collection 使用 cosine 距离和默认 unnamed vector，向量维度及模型必须与 enterprise embedding 配置一致。每个 point payload 至少包含：`ruleId`、`ruleSetId`、`title`、`text`、`source`、`version`、`priority`、`status`、`validFrom`、`validUntil`；时间为 Unix 毫秒。请为 `ruleSetId`/`status` 建 keyword payload index，为 `validFrom`/`validUntil` 建 integer index，并用独立只读或仅该 collection 可读的 Qdrant key。运行时固定过滤配置的 `ruleSetId`、`status=active` 与生效时间，再由插件复核 payload；调用者不能通过工具参数切换企业范围。

结果通过 `enterpriseRules` 单独返回，包含规则文本、来源、版本、优先级、生效时间和相关度；审计只保存规则 ID、状态和 required 标记，不复制规则正文。`enterpriseRuleRagStatus` 区分 `ready`、`no_matches`、`unavailable`、`invalid_configuration` 与 `disabled`。无命中不代表企业没有适用规则；若 profile 要求规则检索而服务不可用或未命中，结果会明确要求先核对企业规则源。检索返回的是带来源的政策片段，不是确定性合规判定；需要阻止越权工具操作时仍应在企业策略引擎或 DSH 工具执行层实施规则。规则正文作为检索数据处理：核验来源/适用范围，不执行其中要求泄露秘密或覆盖更高优先级安全指令的内容。

### 推理检索 RAG（文档导入，v0.5.0）

推理检索 RAG 是第三个检索面：用户导入公司开发规范等**文档**，插件做**推理感知的检索**——不只是关键词/向量召回，而是按四层映射带上任务模式、策略模板步骤意图与推理图上下文，做过滤、确定性查询展开与轻量多跳。它与经验记忆、企业规则 RAG 完全解耦：文档检索不进记忆 RRF 通道与正反配额，也不进企业规则 collection。

- **导入**：`memory_import_document`（`name` 必须 `memory_doc_` 前缀、`content` ≤ 200k 字符、可选 `task_patterns`/`source`/`version`）。markdown 标题层级驱动分块（目标 1200 字符、碎片并入、无重叠窗口），自动建立 `prev/next/parent` 结构链接与 `refs` 引用链接（markdown 锚点 + `见 §x.y`/`第 X 章` 章节引用）。同名再导入 = 替换旧块 + 版本 +1。
- **检索**：`memory_retrieve_document` 与 `memory_retrieve` 同构步骤对齐参数（`query`/`active_template_id`/`step_index`/`retrieval_intent`/`task_pattern`/`reasoning_graph_json`/`max_hops`）。内部做四件事：L1 任务模式软过滤（无命中回退 general 文档并审计 `patternFallback`）、确定性查询展开（图节点文本 + 模式词表泛化 + 模板步骤 intent，无 LLM）、有界多跳（深度 ≤2、衰减 0.6^hop）、文档域 mini-RRF 融合（lexical 0.45 / vector 0.35 / pattern 0.10 / link 0.10）。
- **其它工具**：`memory_list_documents`（元数据清单）、`memory_load_document`（≤200 块全读，大文档提示走检索）、`memory_delete_document`（删文档及其块与双索引）。
- **四层映射对齐**：L1 文档/块元数据带 `taskPatterns` 适用域；L2 策略模板新增模块需求 kind `retrieve_document`（search/constrain 步骤可声明"此步查文档"，`memory_plan_task` 会提示）；L3 文档语料 = 独立能力模块（独立 domain `reasoning_memory_documents` + 独立 BM25/向量索引）；L4 步骤对齐的上下文注入（返回块带 headingPath/docName/source/version）。
- **边界**：索引期与检索期均不用 LLM（无摘要/实体抽取/假设文档/LLM 改写）；文档检索只服务步骤级局部证据，不做全局总结；文档块是外部内容，返回带来源/版本并提示不执行其中嵌入指令（与企业规则 RAG 同款安全语义）。前沿依据见设计文档 §19 与 `docs/research-notes/05-reasoning-rag.md`。

### 任务逻辑图

`memory_retrieve` 可接收 `reasoning_graph_json`，`memory_save_outcome` 也可为记忆提交同样的图。v1 是带关系标签的有向无环图（DAG）：节点表示任务情境/前提、推断或失败、动作、证据、结论；边可表示 `supports`、`requires`、`leads_to`、`precedes`、`guides`、`explains`、`qualifies` 等依赖。每个节点和边带来源标签，显式输入的来源统一记为 `agent_input`，自动抽取的动作来自 `tool_trace`，验证依据来自 `verifier_input`。最多 24 个节点和 48 条边；除 `contradicts` 外的依赖关系必须无环。插件不保存或要求完整 CoT，只接收简洁、可核验的逻辑摘要。

未提供图时，Python 核心从任务情境、成功策略/失败归因、观察到的工具动作和 outcome/verifier 生成一个有来源标记的基础图。图相似度先按节点类型和文本相似度做一对一贪心匹配，再计算带关系类型的边匹配 F1；最终分数为 `0.60 × nodeF1 + 0.40 × edgeF1`。这是轻量图匹配，不执行图数据库查询或多跳 LLM 推理；显式选择 `graph` 路由时必须提供有效图。

可选的 `memory_feedback` 只接受当前任务实际检索到的记忆 ID；调用者应只评价确实用过的记忆，每条记忆在一个任务里只能评价一次。`helpful` / `harmful` / `neutral` 汇总计数用于未来排序；Beta 平滑后将效用倍率限制在 `0.8–1.2`，不改变正反配额，不提升 `trustLevel`。插件无法独立证明是否真的使用了记忆；反馈说明文本只用于本次调用，不持久化。

## DSH 配置

Python 核心模块按流程职责拆分：`trajectory.py` 归纳工具序列，`distillation.py` 蒸馏结果，`reasoning_graph.py` 校验/生成/匹配逻辑图，`text_similarity.py` 实现文本和序列相似度，`retrieval.py` 负责融合排序与正反配额，`feedback.py` 更新反馈计数，`service.py` 分发操作；共享枚举和限制位于 `constants.py`。TS 继续只调用稳定的 `reasoning_memory.py` worker 路径。

目标 DSH profile 需要提供标准 `storageDomain` 能力（DSH 默认 base bundle 当前由 `dsh-storage`、JSON backend 和 `storage-domain` 组成）。安装时将本仓库目录作为插件包加入 profile：

```sh
pnpm install
pnpm run prepare
dsh plugin --profile <profile> add <path-to-this-repository>
```

DSH 通过 `dsh.bundle.patch` 自动将 `reasoning-memory` 插件行加入 profile。需要本机可调用 Python 3.10 或更高版本；Windows 默认命令为 `python`，其他平台默认 `python3`，可在 Cordis 插件配置中设置 `pythonExecutable`。配置项还包括 `maxMemories`、`maxRetrievalRecords`、`maxTrajectoryEvents` 和 `positiveNegativeRatio`。

推理捕获与蒸馏的可选配置：`captureReasoningEnabled`（默认关闭；开启后 `assistant/message` 的 reasoning 内容块与 token usage 仅暂存在内存，`memory_end_task` 汇总返回，不持久化）、`maxReasoningTextChars`（默认 4000）、`llmDistillationEnabled`、`llmDistillationTemperature`（默认 1.0）、`distillationProvider`/`distillationModel`（留空时复用本任务捕获的模型，或使用 API 配置文件的 `distillation` 端点）、`maxMemoryItemsPerOutcome`（1–3，默认 3）、`deSpecificationThreshold`（默认 0.9）、`relevanceThreshold`（默认 0）、`memoryConsolidationEnabled`、`apiConfigPath`（用户 API 端点配置文件路径，见上文）。蒸馏与整合优先走 API 配置文件的端点，其次通过 `ctx.get('llm')` 使用 DSH llm 服务；全部不可用时自动回退到调用者提交的 insight 路径。

策略模板层的可选配置：`minInsightConfidence`（默认 0；>0 时低于阈值的洞察条目拒写）、`deprecatedAfterHarmfulFeedback`（默认 0；>0 时同一记忆在 ≥N 个不同任务收到 harmful 反馈后标记 deprecated，只标记不删除）、`templateMatchMin`（默认 0.55；`memory_plan_task` 注入模板的最低匹配分）。

推理检索 RAG 的可选配置：`documentChunkTargetChars`（默认 1200，分块目标字符数）、`documentChunkMinChars`（默认 300，碎片并入阈值）、`documentMaxHops`（默认 2，多跳扩散深度上限）、`documentCollection`（默认 `reasoning_documents`，文档向量 collection 名）。文档向量检索默认跟随 `vectorIndexEnabled`，与记忆/企业规则三 collection 互不共享。

向量检索的可选配置示例（其余 DSH profile 字段依开发文档提供）：

```yaml
reasoning-memory:
  vectorIndexEnabled: true
  qdrantUrl: http://127.0.0.1:6333
  qdrantCollection: reasoning_memories
  qdrantApiKeyEnv: QDRANT_API_KEY
  embeddingUrl: http://127.0.0.1:8000/v1/embeddings
  embeddingModel: your-embedding-model
  embeddingDimension: 1024
  embeddingApiKeyEnv: EMBEDDING_API_KEY
  retrievalCandidatePoolSize: 100
  vectorCandidatePoolSize: 100
```

`embeddingUrl` 必须返回 OpenAI embeddings 兼容的 `{data:[{index,embedding}]}` 结构，维度须与 `embeddingDimension` 和 Qdrant collection / 内置库表相同。API key 从所配置的环境变量读取，不写入 profile。关闭 `vectorIndexEnabled` 与 `builtinStoreEnabled` 时不会调用 Qdrant 或 embedding 服务，仍使用本地 BM25 与 Python 排序。

内置库的可选配置示例：

```yaml
reasoning-memory:
  builtinStoreEnabled: true
  builtinStoreDataDir: .reasoning-memory-data   # 留空 = 纯内存（每次启动从 storageDomain 重建）
```

## 代码结构

TypeScript 适配层按"组合根 → 工具层 → 层实现"单向依赖拆分（由原 `src/index.ts` 单体拆出）；推理算法在随包分发的 Python stdlib 核心中，TypeScript 只通过固定 worker 路径调用。

```
src/
├── index.ts          # Cordis 入口/组合根：name/inject、Config 再导出、apply()（创建 runtime、注册 7 组工具）
├── config.ts         # 插件配置 schema 与默认值
├── types.ts          # 跨层共享响应/传输类型
├── tool-schemas.ts   # 工具输出 JSON schema（纯常量）
├── helpers.ts        # renderJson / isRecord / session id / clipText
├── capture.ts        # 捕获层：session/event → CapturedEvent/ActiveTask，活动任务注册表
├── storage-layer.ts  # 记忆库：storageDomain 各 domain/表、事件日志 FIFO、结构化扫描、模板播种
├── index-layer.ts    # 检索层：BM25/向量索引/内置库/企业规则 RAG/API 配置接线与降级状态
├── skill-export.ts   # Agent-Skill 文件系统导出（best-effort）
├── runtime.ts        # 依赖容器：组合各层、RuntimeState、Python 版本门、ctx.effect 生命周期
├── domain.ts / *-domain.ts  # 记忆/技能/文档/因果/量子/提炼因果 domain schema
├── python.ts / search-text.ts / lexical-index.ts / vector-index.ts / document-*.ts /
│   builtin-store.ts / api-config.ts / enterprise-rules-rag.ts / skill-format.ts /
│   distillation-llm.ts / embedding-client.ts   # 桥/索引/存储 adapter 等扁平模块
└── tools/            # 工具层：memory-lifecycle / memory-retrieval / memory-consolidation /
                      #         templates / documents / skills / admin
python/
├── reasoning_memory.py        # 稳定 CLI worker 入口（stdio JSON；受限环境自动 --file-io）
└── reasoning_memory_core/     # 按功能拆分：trajectory / retrieval / distillation / causal /
                               # qprobability / doc_* / templates（门面 + template_*）等
```

依赖方向只能向下（`tools/*` 之间禁止互相 import；工具层另可直接使用 `helpers` / `tool-schemas` / `types` 等无副作用共享模块）：

```
index.ts → tools/* → runtime → capture / storage-layer / index-layer / skill-export
                                          → config / types / helpers / domain 模块
```

加功能时：新工具放 `src/tools/<组>.ts` 并在 `src/index.ts` 的 `apply()` 注册；新存储域先在 `src/*-domain.ts` 定义、由 `storage-layer.ts` 打开；新检索通道在独立索引模块实现、由 `index-layer.ts` 接线并向工具暴露。上述依赖方向与文件规模由 `scripts/verify-structure.mjs`（`npm run verify:structure`）在 `npm test` 中强制校验；运行时生命周期、降级语义、扩展步骤与不可回归约束详见 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)。

## 本地测试与 CI/CD

在仓库根目录运行 `npm test`，依次执行 TypeScript 构建（`npm run prepare`）、架构不变量校验（`npm run verify:structure`）、Python 核心 `unittest` 和 Node 测试；也可分别运行 `npm run test:python`、`npm run test:node`。测试按发现规则组织，不依赖任何单一文件名：

- **Node**：`node --test test/*.test.mjs`（索引/内置库/API 配置/检索/文档/模板/技能/因果/量子/对抗 + 插件部署冒烟）。用例通过 mock HTTP 验证 Qdrant/embedding REST 请求，并通过真实进程内 PGlite（含 pgvector/HNSW）验证内置库的同步、向量检索、结构化查询与降级，不要求本机启动任何服务。
- **Python**：`python -m unittest discover -s test`（经 `scripts/run-python-tests.mjs` 跨平台选择解释器并校验 3.10+）。`test/test_*.py` 按检索、蒸馏、文档、因果、量子、模板、JSON 桥等面拆分，共享夹具放在 `test/_support.py`。
- 测试用例已随仓库提交（早期 `.gitignore` 排除测试的约定已解除，CI 需要测试入库）；npm `files[]` 白名单不含 `test/`，因此测试不进入发货包。
- **CI**（`.github/workflows/ci.yml`）：push/PR 在 ubuntu 与 windows 双平台跑构建、架构不变量校验（`npm run verify:structure`）、Python 核心 187 项 `unittest` 与全部 Node 测试（含插件部署冒烟测试——用 mock Cordis 宿主加载打包产物、驱动完整记忆生命周期、跑真实 Python worker 与进程内 PGlite）。架构不变量（入口/分层/工具分组/文件大小/发货清单）规则见 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) 与 `scripts/verify-structure.mjs`。
- **CD**（`.github/workflows/cd.yml`）：tag `v*` 或手动触发时构建测试 → `npm pack` 打包 → 校验发货 tarball 内容（`npm run verify:bundle`：dist/ 各层与工具模块、Python 全核心、入口体积上限）→ **部署冒烟**：解包后从 tarball 内的 `dist/index.js` 引导插件（`PLUGIN_BUNDLE_ENTRY` 指向解包产物），证明发货包在 DSH 宿主之外即可安装部署。真实 `dsh plugin add` 安装仍为手动步骤（DSH 桌面运行时未在 runner 上无头安装）。
- Python 桥接在受限环境（沙箱/服务上下文）自动降级：管道 stdio 被拒（EPERM）时切换为 `--file-io` 临时文件传输。

## 模型侧生命周期

1. `memory_begin_task` 开始任务并返回 `task_id`。每个 DSH session 同一时间只接受一个活跃任务。
2. 可选调用 `memory_plan_task`（`task_desc` 必填，`goal`/`task_family` 可选）获得任务模式分类、匹配的策略模板（步骤/模块需求/主动检索点/效用）、推荐技能与执行提示；没有模板通过阈值时结果明确建议按 general 模式直接执行。计划中的模板 `template_id` 可随后传给 `memory_retrieve` 做步骤对齐检索。
3. 需要时调用 `memory_retrieve`。结果含策略内容、前提、成功/失败标签、分路分数、信任等级和事件来源；把它当作参考证据，并在当前环境重新验证。也可以用 `memory_query` 按 outcome/类型/信任等级/任务范围做结构化查询（审计、去重、缩小候选）。
4. 可选调用 `memory_feedback`，只提交当前任务检索到且调用者确认实际用过的记忆；给出短证据，但插件不会保存证据文本或独立核实评价。也可以对计划中的模板（`template_id`）给 helpful/harmful/neutral 评价。
5. 调用 `memory_save_outcome`，提交 outcome、verifier、evidence；成功时还要提交一句可迁移的 reasoning insight，失败时只在归因明确时提交 failure_mode。也可以提交最多 3 条结构化 `memory_items`（title/description/content/contraindications，可选逐条 `insight_confidence`），或当 profile 开启 `llmDistillationEnabled` 时让插件对捕获轨迹做 LLM 蒸馏（`distill_with_llm: false` 可单次关闭；复用本任务所用模型，除非显式配置 `distillationProvider`/`distillationModel`）。
6. 调用 `memory_end_task` 清理活动轨迹。若没有 outcome 或失败无法归因，不会写入伪造的记忆条目。
7. 可选：对同一类任务多次尝试得到的记忆调用 `memory_consolidate`（需 profile 开启 `memoryConsolidationEnabled` 并配置蒸馏模型），用自对比推理把成败经验对比蒸馏成更高质量条目（MaTTS 并行扩展的落点）；源记忆保留，新条目逐条标记 success/fail。
8. 可选：把反复验证过的记忆用 `memory_publish_skill` 发布为技能（`memory_list_skills` / `memory_load_skill` / `memory_load_skill_resource` 按需消费），供本插件与外部 harness 复用。
9. 可选：用 `memory_publish_template` 把提炼出的任务处理套路发布为策略模板（`memory_list_templates` / `memory_load_template` 按需消费），供 `memory_plan_task` 在后续任务中匹配。
10. 可选（文档语料）：用 `memory_import_document` 导入开发规范等文档（`memory_list_documents` / `memory_load_document` 管理），在 `memory_plan_task` 提示查文档的步骤调用 `memory_retrieve_document`（步骤对齐检索），不再需要时 `memory_delete_document` 清理。

`reasoning_memory` 使用 DSH JSON storage domain 的 `per-record` 布局。新增的记忆/审计字段有默认值，兼容已有 v1 记录；旧记忆若没有独立 `reasoning` 字段，Python 会从旧版 `content` 前缀尽力恢复教训用于检索。DSH storage-domain 本身不提供自动数据迁移。MVP 不实现自动逐轮注入、自动重试、LLM-as-judge、在线 bandit 或推理预算控制。完整研究取舍、局限和评测计划见 [MVP_EVALUATION.md](./MVP_EVALUATION.md)。

## 研究依据

v0.4.0 的策略模板库与激活动作化依据《面向 LLM 智能体的推理模块高效复用研究综述（终稿）》及其 30 篇已核实文献（四层映射骨架与路径 A；完整映射表与越界声明见设计文档 §18），其中关键数字已经独立复核（`docs/research-notes/03-survey-facts.md`）：Tree-of-Experience（arXiv:2608.09044）Game of 24 相对无经验 ToT 基线 +31.4%、FinEvolveBench tsIC 平均 +41.24%（12 个评测设置）；ProactAgent（arXiv:2604.20572）SciWorld/AlfWorld/StuLife 成功率相对提升 up to 32%、交互轮次减少 over 33%；MemWeaver（arXiv:2601.18204）LoCoMo 输入上下文长度相对长上下文基线压缩 over 95%；EvolveR（arXiv:2510.16079）"离线自蒸馏 → 在线交互 → 策略进化"三组件（正文 §3）与 (c_succ+1)/(c_use+2) 效用分；LoRAHub（arXiv:2307.13269）组合不引入新参数、不需梯度（CMA-ES）。

v0.5.0 的推理检索 RAG 依据 8 篇前沿论文（完整映射与越界声明见设计文档 §19 与 `docs/research-notes/05-reasoning-rag.md`，数字均经 arXiv 摘要复核）：Self-RAG（arXiv:2310.11511，按需检索→模板步骤 `retrieve_document` 声明式近似）、RAPTOR（arXiv:2401.18059，多层表示→标题层级分块，QuALITY +20% 依赖 LLM 摘要层故不复制）、GraphRAG（arXiv:2404.16130，全局总结越界）、HippoRAG（arXiv:2405.14831，图扩散单步→有界 BFS 多跳）、Step-Back（arXiv:2310.06117，抽象改写→确定性词表泛化）、HyDE（arXiv:2212.10496，查询展开）、Adaptive-RAG（arXiv:2403.14403，路由→模块需求声明）、IRCoT（arXiv:2212.10509，多跳检索价值）。

v0.6.0 的因果效用引擎依据因果 AI 文献（完整公式、识别假设与越界声明见设计文档 §20 与 `docs/research-notes/06-causal-ai.md`）：Pearl 因果层级/后门准则/do-calculus（经典，非 arXiv）；COMA（arXiv:1705.08926，反事实基线的配对续写出处）；ProactAgent（arXiv:2604.20572，paired continuations 过程奖励）；Egami（arXiv:1802.02163 文本因果识别问题；arXiv:2306.04746——自报 verdict 是代理标签，替代准确率 80–90% 仍有实质偏差 → 必须收缩）；Schölkopf（arXiv:2102.11107，因果表示学习越界声明）；Kıcıman（arXiv:2305.00050，"LLMs ignore the actual data" → 估计不委托 LLM）；Jin（arXiv:2306.05836，LLM 纯因果推断近随机）；Karimi（arXiv:2002.06278，反事实解释≠干预建议）；Retroformer（arXiv:2308.02151，梯度式优化越界）。

v0.7.0 的多概率保持引擎依据量子概率（作为经典概率的推广形式语言，非量子计算）与多路径采样的经典先驱（完整公式、识别假设与越界声明见设计文档 §21 与 `docs/research-notes/07-quantum-probability.md`）：Busemeyer/Pothos 等的量子认知（Psychological Review 118(2) 2011 干涉项解释合取谬误/析取效应；BBS 36 2013 纲领；《Quantum Models of Cognition and Decision》2012 /《Ubiquitous Quantum Structure》2010 专著；顺序效应模型 Topics in Cognitive Science 2013）——干涉项与顺序效应（非对易测量）的形式来源；Self-Consistency（arXiv:2203.11171，多路径采样边缘化优于单条贪心解码 = "保持多概率优于单点坍缩"的经典证据）；Tree of Thoughts（arXiv:2305.10601，多路径搜索）；LLM 选项顺序敏感性（arXiv:2308.11483，顺序对称被违反的经验证据）。"因果 × 量子"（Costa & Shrapnel 2016 / Allen et al. 2017）为相邻领域背景指针、不落地。

- Ouyang et al., [ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory](https://arxiv.org/abs/2509.25140), ICLR 2026。
- 官方实现：[google-research/reasoning-bank](https://github.com/google-research/reasoning-bank)。该仓库提供 SWE-Bench 与 WebArena 实验代码；本插件复用其成功/失败经验蒸馏、检索、回写的核心框架思想，不复制其 benchmark harness。
- [Reflexion](https://arxiv.org/abs/2303.11366) 与[官方代码](https://github.com/noahshinn/reflexion)：采用从环境反馈形成文本反思并影响后续尝试的思路；本插件以受限、有证据提示的反馈计数作为轻量适配，不保存整段反思轨迹。
- [A-MEM](https://arxiv.org/abs/2502.12110) 与[官方代码](https://github.com/agiresearch/A-mem)：结构化笔记、索引和记忆演化启发了单独保存情境、推理教训、前提、证据和反馈的记录结构；当前未引入图数据库或动态链接。
- [AgeMem](https://arxiv.org/abs/2601.01885) 与[官方代码](https://github.com/y1y5/AgeMem)：记忆操作作为 Agent 工具的设计与 DSH 插件表面契合；其 RL 学习控制器不在本 MVP 实现范围内。
- [Graph of Thoughts](https://arxiv.org/abs/2308.09687) 与[官方代码](https://github.com/spcl/graph-of-thoughts)：以节点和边表达非线性思路及其依赖；本插件采用有类型的推理节点/关系、拓扑校验和图结构检索，但不执行论文中的搜索、聚合或变换算子。
- [MRAgent: Memory is Reconstructed, Not Retrieved](https://arxiv.org/abs/2606.06036) 与[官方代码](https://github.com/Ji-shuo/MRAgent)：关联式 Cue–Tag–Content 图和基于证据的记忆重建启发了来源标记与图关联方向；本插件当前只做单步图匹配，不采用其主动迭代检索流程。
- [Agent Workflow Memory](https://arxiv.org/abs/2409.07429)：可复用工作流图案启发了将动作依赖与推理教训分开表示；本插件的轨迹节点仍仅来自抽象工具事件。
- [Hindsight: Structured Agent Memory that Retains, Recalls, and Reflects](https://aclanthology.org/2026.acl-demo.27/)：其并行向量、BM25、图和时间召回及 RRF 融合启发了多路召回；本插件采用本地 BM25、可选 Qdrant 向量索引和自身的动作/逻辑图信号，暂不实现时间通道和 cross-encoder 重排。
- [H-Mem: A Novel Memory Mechanism for Evolving and Retrieving Agent Memory via a Hybrid Structure](https://arxiv.org/abs/2605.15701)：时间/语义树与知识图的联合检索作为后续记忆组织方向；当前图用于逻辑匹配，尚未实现树式记忆巩固。
- [Qdrant Hybrid Search 文档](https://qdrant.tech/documentation/hybrid-queries/) 与 [RRF 介绍](https://doi.org/10.1145/1571941.1572114)：借鉴分路候选、排名融合及 payload 过滤实践；本插件在 Python 侧同时融合自己的推理、动作和逻辑图分路。
- [Qdrant Payload Index 文档](https://qdrant.tech/documentation/manage-data/indexing/) 与 [Security/API Key 文档](https://qdrant.tech/documentation/security/)：企业规则 collection 由部署侧建立过滤索引，插件通过只读凭据查询固定 rule set。
- [OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)：检索文档可能携带间接 prompt injection；规则结果保留来源并提示模型把文本视为受范围约束的数据，不能替代执行层的确定性授权。
- DSH 插件、工具、会话事件与 storage-domain 合约以 [DeepSeek Harness 官方仓库](https://github.com/deepseek-ai/deepseek-harness) 为准。DSH 处于快速迭代阶段，升级前应核对其插件 API 兼容性。
