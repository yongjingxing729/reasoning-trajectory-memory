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

## 本地测试与 CI/CD

在仓库根目录运行 `npm test`，依次执行 TypeScript 构建、Python 核心 `unittest` 和 Node 测试；也可分别运行 `npm run test:python`、`npm run test:node`（索引/内置库/API 配置 + 插件部署冒烟）。Node 用例通过 mock HTTP 验证 Qdrant/embedding REST 请求，并通过真实进程内 PGlite（含 pgvector/HNSW）验证内置库的同步、向量检索、结构化查询与降级，不要求本机启动任何服务。测试用例已随仓库提交（早期 `.gitignore` 排除测试的约定已解除，CI 需要测试入库）。

- **CI**（`.github/workflows/ci.yml`）：push/PR 在 ubuntu 与 windows 双平台跑构建、Python 31 项与全部 Node 测试（含插件部署冒烟测试——用 mock Cordis 宿主加载打包产物、驱动完整记忆生命周期、跑真实 Python worker 与进程内 PGlite）。
- **CD**（`.github/workflows/cd.yml`）：tag `v*` 或手动触发时构建测试 → `npm pack` 打包 → 校验发货 tarball 内容（`npm run verify:bundle`）→ **部署冒烟**：解包后从 tarball 内的 `dist/index.js` 引导插件（`PLUGIN_BUNDLE_ENTRY` 指向解包产物），证明发货包在 DSH 宿主之外即可安装部署。真实 `dsh plugin add` 安装仍为手动步骤（DSH 桌面运行时未在 runner 上无头安装）。
- Python 桥接在受限环境（沙箱/服务上下文）自动降级：管道 stdio 被拒（EPERM）时切换为 `--file-io` 临时文件传输。

## 模型侧生命周期

1. `memory_begin_task` 开始任务并返回 `task_id`。每个 DSH session 同一时间只接受一个活跃任务。
2. 需要时调用 `memory_retrieve`。结果含策略内容、前提、成功/失败标签、分路分数、信任等级和事件来源；把它当作参考证据，并在当前环境重新验证。也可以用 `memory_query` 按 outcome/类型/信任等级/任务范围做结构化查询（审计、去重、缩小候选）。
3. 可选调用 `memory_feedback`，只提交当前任务检索到且调用者确认实际用过的记忆；给出短证据，但插件不会保存证据文本或独立核实评价。
4. 调用 `memory_save_outcome`，提交 outcome、verifier、evidence；成功时还要提交一句可迁移的 reasoning insight，失败时只在归因明确时提交 failure_mode。也可以提交最多 3 条结构化 `memory_items`（title/description/content/contraindications），或当 profile 开启 `llmDistillationEnabled` 时让插件对捕获轨迹做 LLM 蒸馏（`distill_with_llm: false` 可单次关闭；复用本任务所用模型，除非显式配置 `distillationProvider`/`distillationModel`）。
5. 调用 `memory_end_task` 清理活动轨迹。若没有 outcome 或失败无法归因，不会写入伪造的记忆条目。
6. 可选：对同一类任务多次尝试得到的记忆调用 `memory_consolidate`（需 profile 开启 `memoryConsolidationEnabled` 并配置蒸馏模型），用自对比推理把成败经验对比蒸馏成更高质量条目（MaTTS 并行扩展的落点）；源记忆保留，新条目逐条标记 success/fail。
7. 可选：把反复验证过的记忆用 `memory_publish_skill` 发布为技能（`memory_list_skills` / `memory_load_skill` / `memory_load_skill_resource` 按需消费），供本插件与外部 harness 复用。

`reasoning_memory` 使用 DSH JSON storage domain 的 `per-record` 布局。新增的记忆/审计字段有默认值，兼容已有 v1 记录；旧记忆若没有独立 `reasoning` 字段，Python 会从旧版 `content` 前缀尽力恢复教训用于检索。DSH storage-domain 本身不提供自动数据迁移。MVP 不实现自动逐轮注入、自动重试、LLM-as-judge、在线 bandit 或推理预算控制。完整研究取舍、局限和评测计划见 [MVP_EVALUATION.md](./MVP_EVALUATION.md)。

## 研究依据

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
