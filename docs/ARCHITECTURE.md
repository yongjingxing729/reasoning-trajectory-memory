# 架构说明：分层、依赖与扩展点

本文是 `reasoning-trajectory-memory-design.md` 的工程落地说明，重点回答三个问题：**代码如何分层、依赖方向如何约束、扩功能时该改哪些文件**。设计意图与边界以设计文档为准（§2.3 模块职责、§15.2 插件包文件规划），本文只描述当前代码实现以及必须保持的不变量。

一句话结构：`src/index.ts` 是唯一组合根，**工具层 → 运行时容器 → 捕获/记忆库/检索/技能导出各层 → 配置/类型/纯工具函数/domain schema**，依赖只能沿这个方向走；Python 核心是随包分发的 stdlib worker，由 TypeScript 通过固定 JSON 桥调用，不持有任何持久状态。

---

## 1. 分层总览（对齐设计文档 §2.3）

| 设计 §2.3 层 | TypeScript 落点 | Python / 其它落点 | 职责与硬边界 |
|---|---|---|---|
| 捕获层 | `src/capture.ts` | — | 订阅 `session/event`（`tool/call`、`tool/result`、`assistant/message`、`assistant/chunk`），投影为 `CapturedEvent`/`ActiveTask`，维护活动任务注册表；`memory_*` 工具自身调用不进入轨迹；reasoning delta 仅在 `captureReasoningEnabled` 时**内存暂存**，`memory_end_task` 汇总返回后即丢弃。 |
| 记忆库 | `src/storage-layer.ts` + `src/domain.ts` / `document-domain.ts` / `causal-domain.ts` / `quantum-domain.ts` / `distill-causal-domain.ts` / `skill-domain.ts` | DSH `storageDomain` backend（JSON） | 打开 6 个类型化 domain（记忆/模板/检索审计、技能、文档、因果事件、量子审计、提炼因果事件），提供表访问、有界事件日志写入、结构化扫描回退、内置模板幂等播种与 `close()`；所有写入以 `storageDomain` 为事实源。 |
| 检索层 | `src/index-layer.ts` + `lexical-index.ts` / `vector-index.ts` / `document-index.ts` / `document-vector.ts` / `builtin-store.ts` / `enterprise-rules-rag.ts` / `api-config.ts` / `search-text.ts` / `embedding-client.ts` | `retrieval.py`、`doc_rag.py`（RRF 融合、正反配额、多跳） | 生成候选（BM25、可选 Qdrant、PGlite/pgvector 内置库、企业规则 RAG）、接线 API 配置、维护降级状态；**最终融合排序与配额在 Python**，TS 只负责候选与最终结果组装。 |
| 蒸馏层 | `src/tools/memory-lifecycle.ts`（`save_outcome`/`end_task` 门控）、`src/tools/memory-consolidation.ts`、`src/distillation-llm.ts` | `distillation.py`、`distill_causal.py`、`causal_utils.py` | 任务终态 + 验证证据 → 策略/结构/失败教训条目；不可归因失败不强行提炼、保留 `unknown`；来源为调用方或 LLM 的条目一律 `unverified`；原始 CoT 不入库。注意：蒸馏层**没有单一 TS 文件**，它是工具层写路径 + `distillation-llm.ts` + Python 核心的组合。 |
| 工具层 | `src/tools/*.ts`（7 个分组）+ `src/tool-schemas.ts` | — | 22 个 `memory_*` 面向模型的工具：参数校验/截断、经 runtime 调用各层、按 schema 返回带来源的数据块；记忆/规则/文档文本不得升级为指令。 |

工具分组（与 `src/tools/` 文件一一对应）：

| 文件 | 工具 |
|---|---|
| `tools/memory-lifecycle.ts` | `memory_begin_task`、`memory_end_task`、`memory_save_outcome`、`memory_feedback`、`memory_record_counterfactual` |
| `tools/memory-retrieval.ts` | `memory_retrieve`、`memory_query` |
| `tools/memory-consolidation.ts` | `memory_consolidate` |
| `tools/templates.ts` | `memory_plan_task`、`memory_list_templates`、`memory_load_template`、`memory_publish_template` |
| `tools/documents.ts` | `memory_import_document`、`memory_list_documents`、`memory_load_document`、`memory_retrieve_document`、`memory_delete_document` |
| `tools/skills.ts` | `memory_publish_skill`、`memory_list_skills`、`memory_load_skill`、`memory_load_skill_resource` |
| `tools/admin.ts` | `memory_api_status` |

---

## 2. 依赖规则

只允许单向依赖（自上而下）：

```
src/index.ts                        组合根：name / inject / Config 再导出 / apply()
    └──> src/tools/*.ts              工具层：7 个 register*Tools(ctx, rt)，禁止互相 import
            ├──> src/runtime.ts      依赖容器：createRuntime() + RuntimeState
            │        ├──> src/capture.ts
            │        ├──> src/storage-layer.ts
            │        ├──> src/index-layer.ts
            │        └──> src/skill-export.ts
            └──> 无副作用共享模块：config / types / helpers / tool-schemas /
                 python 桥 / domain schema / distillation-llm / skill-format
                        （各层实现同样只依赖这些共享模块）
```

约束细则：

- `src/index.ts` 是**唯一组合根**：只做 Cordis 契约导出、`createRuntime()`、按顺序调用 7 个 `register*Tools()`；不直接持有 domain、索引或工具实现。注册顺序遵循记忆生命周期：capture → retrieval → consolidation → templates → documents → admin → skills。
- `tools/*.ts` 之间**禁止互相 import**；跨组共享逻辑下沉到 `runtime.ts`、独立 domain 模块或 Python 核心。工具依赖 `Runtime`（`rt.config` / `rt.storage` / `rt.indexes` / `rt.capture` / `rt.state` / `rt.exportSkillDirectory`）与无副作用共享模块（`helpers` / `tool-schemas` / `types` / `python` 桥 / domain schema / `skill-format` / `distillation-llm`）；对层实现只允许 **type-import**（如 `ActiveTask`），值依赖一律经 runtime 容器，禁止值导入 `runtime`/`capture`/`storage-layer`/`index-layer`。
- 各层实现不得 import `tools/*`，也不得绕过 runtime 直接调用其它层；`capture.ts` 不知道存储存在，`storage-layer.ts` 不访问检索服务或模型。
- 上述规则由 `scripts/verify-structure.mjs`（`npm run verify:structure`）守护：入口 ≤60 行且不得出现 `defineTool`/`ctx.tools.register`/`storageDomain.open`；每个 `tools/*.ts` 必须导出 `register<Group>Tools(ctx: Context, rt: Runtime): void` 并被 `index.ts` 调用（且只调用一次）；工具不得 import 其它 tools（含同目录相对导入）；层不得 import tools 或 `index.ts`；TS/Python 单文件 ≤900 行；`test/` 下的 Python 文件必须命名为 `test_*.py`（或 `_support.py`）以保证默认 discovery 可预测；发货清单必须含 `dist/`、不得含 `scripts/`。
- 既有扁平模块（`domain.ts`、`python.ts`、`search-text.ts`、`lexical-index.ts`、`vector-index.ts`、`builtin-store.ts`、`api-config.ts`、`enterprise-rules-rag.ts`、`skill-format.ts`、`distillation-llm.ts`、`embedding-client.ts`、`*-domain.ts`、`document-*.ts`）位置与职责不变；本次拆分只把原 `src/index.ts` 单体拆成组合根 + 工具层 + 层模块，**不改变 domain schema、Python 协议与工具输出契约**。
- 校验方式：`tsc -p tsconfig.json` 负责类型与模块解析（会暴露解析失败的 import），分层/互相 import/文件规模等结构规则由 `verify:structure` 静态校验。唯一存在的导入环是 `index-layer.ts` 与 `runtime.ts` 之间的一处 **type-only** 循环（`RuntimeState`），编译后擦除，运行时无值层环。

---

- 工具注册顺序按记忆生命周期分组（begin → feedback → save → counterfactual → end → retrieve → query → consolidate → templates → documents → admin → skills），与原单体的逐项顺序不同；工具以**名称**为查找键，Cordis 注册表在 Code-Mode 渲染时按名称排序，因此该分组顺序只影响非 Code-Mode 工具清单的展示顺序与 prompt 缓存，不是外部契约。

## 3. 运行时生命周期

```
apply(ctx, config)
  └─> createRuntime(ctx, config)
        ├─ 初始化 RuntimeState（各通道初始状态）
        ├─ Python 版本门：checkPythonVersion(config.pythonExecutable)
        │    失败只记 ctx.logger.error，不阻塞插件加载（后续 bridge 调用才失败）
        ├─ createStorageLayer：打开 6 个 domain、建 BM25/文档索引、异步播种内置模板
        ├─ createIndexLayer：启动 ApiConfigService；初始化可选 Qdrant/内置库并后台回填；
        │    建企业规则 RAG client；所有失败写入 RuntimeState 降级标记，不 throw
        ├─ createCaptureLayer(config)
        ├─ createSkillExport(config, storage)
        ├─ ctx.effect(() => () => { storage.close(); return indexes.close() })
        └─ capture.attach(ctx)  → 注册 session/event 与 session/disposed 监听
  └─> 按序注册 7 组工具
```

生命周期要点：

- **创建顺序即依赖顺序**：storage → indexes（indexes 需要 storage 的表引用）→ capture → skill-export；工具注册在 runtime 就绪之后。
- **销毁**通过 Cordis `ctx.effect` 的 disposer 完成：`storage.close()` 关闭 6 个 domain；`indexes.close()` 停止 API 配置热加载并关闭内置库（返回可 await 的 PGlite teardown，宿主忽略返回值时退化为 fire-and-forget）。`session/disposed` 时清理该会话的活动任务与忽略集合。
- **写入语义**：`storageDomain` 写入先成功，派生索引（Qdrant/内置库/技能导出文件）再 best-effort 异步跟进；派生索引失败只降级，不回滚规范存储。
- **降级状态**是 `runtime.ts` 中一个生命周期内稳定的可变对象，检索层写、工具层读，最终通过工具结果告知模型；它不是异常通道。

### 3.1 降级标记语义

| 标记 | 取值 | 含义 |
|---|---|---|
| `state.vectorIndexSynchronized` | boolean | Qdrant 记忆索引是否完成启动回填；`vectorIndexEnabled=false` 时恒为 true |
| `state.vectorIndexDegraded` | boolean | Qdrant 初始化/同步/检索失败；检索回退 BM25 |
| `state.builtinStoreStatus` | `disabled` / `initializing` / `ready` / `json-only` / `degraded` | 内置 PGlite 库状态；`json-only` 表示结构化行可用但无 embedding，`degraded` 时向量检索不可用、结构化查询回退规范存储扫描 |
| `state.documentVectorDegraded` | boolean | 文档向量索引失败；文档检索回退文档 BM25 |
| `state.enterpriseRuleRagInitError` | string | 企业规则 RAG 配置错误；工具返回 `invalid_configuration`，required 模式下提示先核验规则源 |

统一原则：**规范存储永远可回答，索引只是可重建派生层**；降级 = 换通道/换扫描，不是数据丢失；工具结果的 `note`/status 字段说明实际由哪个通道回答。

---

## 4. 扩展点

### 4.1 新增一个 `memory_*` 工具

1. 在 `src/tool-schemas.ts` 新增输出 JSON schema（纯常量，按工具族命名），需要跨层响应类型时在 `src/types.ts` 增加 type。
2. 在对应分组的 `src/tools/<group>.ts` 的 `register*Tools(ctx, rt)` 内 `ctx.tools.register(defineTool({ ... }))`；参数截断/校验用 `src/helpers.ts`，只通过 `rt` 访问各层，**不要** import 其它 `tools/*` 模块。
3. 需要新的可变通道状态时加到 `src/runtime.ts` 的 `RuntimeState`；只读依赖加到 `createRuntime` 返回对象。若需要新表/新 domain，先走 4.2。
4. 在 `src/index.ts` 的 `apply()` 中确认所属 `register*Tools` 已被调用（组合根是唯一注册处，不要在其他文件里 `defineTool` 注册）。
5. 工具名以 `memory_` 开头：`capture.ts` 会自动忽略这些调用，避免工具自身流量进入轨迹。
6. 测试：在 `test/*.test.mjs` 覆盖输出 schema 与边界（必要时扩展 `test/plugin-smoke.test.mjs`）；同步更新 README 的模型侧生命周期与设计文档的工具清单。

### 4.2 新增一个 storage domain

1. 仿 `src/domain.ts` / `src/causal-domain.ts` 新建 `src/<name>-domain.ts`：`defineDomain({ name: 'reasoning_memory_<name>', version: 1, tables: { ... } })`，schema 字段带默认值或可选（additive）。
2. 在 `src/storage-layer.ts` 的 `createStorageLayer` 中 `await ctx.storageDomain.open(...)`，取得表引用；需要上限的事件日志仿 `writeCausalEvents` 写 FIFO 守卫；把表引用/写入函数放进返回对象。
3. 在 `storage-layer.ts` 返回的 `close()` 中关闭该 domain，确保 `ctx.effect` disposer 覆盖。
4. 工具层通过 `rt.storage.<table>` 使用；**不要**在工具模块里直接 `ctx.storageDomain.open`。
5. 兼容性（必须）：不重命名/删除既有字段、不 bump `version`、新字段必须有 default；新表对旧介质启动时为空，由 storage-domain 层补齐（参见设计文档 §21.5 / §23.5 的 additive 约束）。
6. 测试：旧记录可读 + 新字段默认值 + FIFO/上限 + 重启后仍在。

### 4.3 新增一个检索通道

1. 新建独立索引模块（如 `src/<name>-index.ts`），接口保持"query → `[{ id, score }]`"候选语义，不写记忆、不做最终排序。
2. 在 `src/index-layer.ts` 的 `createIndexLayer` 中初始化：启动回填、增量写、失败写 `RuntimeState` 降级标记（不 throw）并加入 `close()`。
3. 在 `src/tools/memory-retrieval.ts` 中合并候选（现有通道携带 `lexicalScore`/`vectorScore` 等分数字段）并随请求传给 Python；若需要新的 RRF 臂，在 `python/reasoning_memory_core/retrieval.py` 增加确定性权重（可通过配置关闭）。
4. 在工具结果 warning/status 中体现降级路径，保证 `storageDomain` 仍是事实源、通道可重建。
5. 测试：候选合并、降级回退、默认关闭时行为零回归。新增通道不得改变记忆 RRF 通道与正反配额语义（文档/企业规则检索面同理，彼此隔离）。

---

## 5. Python 核心边界

- **唯一入口**：`python/reasoning_memory.py` 是稳定 worker 路径，由 `src/python.ts` 的 `runReasoningCore()` 以可取消的 JSON stdin/stdout 子进程协议调用；受限环境（管道 stdio 被拒）自动降级为 `--file-io <in> <out>` 临时文件传输。不是 FastAPI、MCP server 或独立应用。
- **无状态**：Python 不持久化、不访问网络、不读 `storageDomain`；所有状态由 TypeScript 持有并随单次请求传入，单请求单响应。
- **版本门**：需要 Python 3.10+。`runtime.ts` 启动时 `checkPythonVersion()` 记日志；真正不兼容时 bridge 调用失败并显式报错。
- **模块划分**（`python/reasoning_memory_core/`，纯 stdlib）：`constants.py` 共享词汇/上限、`trajectory.py` 轨迹与结构签名、`text_similarity.py` 文本/序列相似度、`reasoning_graph.py` 图校验/构建/匹配、`distillation.py` 成功/失败蒸馏、`retrieval.py` 多路融合/排序/正反配额、`feedback.py` 效用计数、`causal_utils.py` 因果估计、`qprobability.py` 多概率（叠加/干涉/退相干）、`distill_causal.py` 提炼侧 uplift、`doc_chunking.py` 文档分块、`doc_rag.py` 文档检索、`templates.py` 模板库门面 + `template_build/classify/match/mining/plan/publish.py` 内部实现、`service.py` 操作分发。
- **operation 清单**（`service.py`）：`distill`、`retrieve`、`feedback`、`causal_estimate`、`classify_task_pattern`、`plan_task`、`mine_templates`、`template_feedback`、`publish_template_validate`、`builtin_templates`、`import_document`、`retrieve_document`、`delete_document_plan`。新增 operation 需同时在 `service.py` 分发、`reasoning_memory.py` 兼容导出（如需要）、对应 TypeScript 调用方与测试中登记。
- `templates.py` 是稳定门面，跨包调用只依赖它；`template_*.py` 是内部实现模块，可继续按职责拆分而不改变门面。

---

## 6. 不可回归的不变量

改动任何分层/工具/通道时，下列约束必须保持；review 与测试按此清单核对。

### 6.1 storage-domain v1 additive 兼容

- 现有 6 个 domain（`reasoning_memory`、`reasoning_memory_skills`、`reasoning_memory_documents`、`reasoning_memory_causal`、`reasoning_memory_quantum`、`reasoning_memory_distill_causal`）全部为 `version: 1`；不 bump 版本。
- 只允许**加字段/加表**：新字段必须 optional 或带 default；不重命名、不删除、不改变既有字段语义。
- 旧记录必须可读：缺失字段在读取时补默认（参见各 schema 的 `.default(...)` 与 `memoryItemSchema` 的兼容注释）；旧记忆缺少独立 `reasoning` 字段时 Python 仍从旧 `content` 前缀恢复教训。
- 新事件日志必须有 FIFO 上限（因果 200/unit、量子 2000、提炼因果 200/unit），避免派生审计无限增长。

### 6.2 不持久化原始 CoT / 敏感原文

- 不保存工具参数、工具输出、完整提示词、模型 reasoning/CoT；捕获层只留抽象工具名、事件序号、成败、token usage。
- `captureReasoningEnabled` 开启时 reasoning 文本也仅暂存内存，`memory_end_task` 汇总返回后丢弃；不落 `storageDomain`。
- 检索审计只存记忆 ID、分数、路由、配额等元数据；企业规则正文不写入记忆或审计，文档块只在检索结果中返回。
- `reasoning_graph_json` 是简洁、可核验的推理摘要，不是完整 CoT；技能是派生制品，删除记忆不影响已发布技能（反之亦然）。

### 6.3 工具面与测试不变量

- 22 个 `memory_*` 工具名与输出 schema 保持稳定（`src/tool-schemas.ts`）；新增字段同样遵循 additive 原则。
- `memory_*` 工具调用不进入捕获轨迹（`capture.ts` 的前缀排除）。
- 只有 `success`/`fail` 生成正/负记忆；`partial`/`unknown` 不入正反池；正反配额、`fallbackUsed`、`trustLevel: unverified` 语义不变。
- 派生索引降级只改变通道、不回滚规范存储，也不抛错打断工具调用。
- 新引擎默认关闭时输出零回归（如 `quantumDecisionEnabled=false`、`distillGateEnabled=false` 与旧版本逐字节一致）。
- 测试与结构入口固定：`npm test` = `prepare`（tsc）+ `verify:structure`（架构不变量）+ `test:python` + `test:node`；Node 通过 `node --test test/*.test.mjs`，Python 通过 `python -m unittest discover -s test`（`scripts/run-python-tests.mjs` 负责跨平台选解释器与 3.10+ 校验）；CI 在 ubuntu/windows 双平台分步执行 `prepare` / `verify:structure` / `test:python` / `test:node`。
- 发货边界：npm `files[]` 白名单只含 `dist/` + `python/reasoning_memory*.py` 核心 + 文档；`test/`、`scripts/` 不进包；`npm run verify:bundle` 校验 tarball 内容（`dist/` 各层与工具模块、Python 全核心模块、`dist/index.js` 入口体积上限），`test/plugin-smoke.test.mjs` 用 mock DSH 宿主加载解包产物证明可部署。

---

## 7. 相关文档

- [设计文档 §2.3 模块职责](../reasoning-trajectory-memory-design.md)：层级职责与边界的研究依据。
- [设计文档 §15.2 插件包文件规划](../reasoning-trajectory-memory-design.md)：本仓库文件树与依赖方向。
- [README](../README.md)：安装、配置、模型侧生命周期与测试命令。
- `docs/spec-*.md`：各版本迭代的函数级规格（四层映射、混合检索、企业规则 RAG、因果/量子/提炼因果引擎）。
