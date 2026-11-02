# 只读风险审查清单（T-debug-1 · 阶段二）

- 日期：2026-11-02
- 审查人：debugger（调试与质量负责人）
- 范围：`src/index.ts`、`src/*.ts`、`python/reasoning_memory_core/*.py`、`python/reasoning_memory.py`、`test/*`
- 性质：**只读审查，未修改任何源码**。条目供 v0.4.0 实现与 T-debug-2 对抗测试轮使用。
- 严重度约定：🔴 高（可导致数据/功能损坏或工具全线不可用）｜🟡 中（特定条件下功能降级/性能劣化/潜在崩溃）｜🟢 低（边界噪声、体验、理论风险）

## 总览

| # | 类别 | 条目 | 关键位置 | 严重度 |
| --- | --- | --- | --- | --- |
| B1 | 域 schema / 服务接线 | `ctx.get('llm')` 未在 `inject` 中声明，DSH llm 蒸馏路径大概率永远不可用 | src/index.ts:54,1388,1507 | 🟡 |
| B2 | 桥协议 | 桥请求无协议版本握手，TS/Python 版本混装时输出 schema 解析失败使工具抛错 | src/python.ts:16-20; python/reasoning_memory.py:41-46 | 🟡 |
| B3 | 边界条件 | 候选为空时的全量回退：检索桥单次载荷可达数 MB~数十 MB | src/index.ts:1071-1079,1116-1127 | 🟡 |
| B4 | 边界条件 | 每次 retrieve 全量序列化活跃轨迹 events；maxTrajectoryEvents 无上限 | src/index.ts:1119; src/index.ts:106 | 🟡 |
| B5 | 检索数值 | 检索审计 `retrievalRecordSchema.parse` 失败会让整个 retrieve 调用抛错 | src/index.ts:1177-1195 | 🟡 |
| B6 | 检索数值 | Python 侧 int()/float() 强转对畸形数值输入直接崩溃（exit 2） | retrieval.py:76-82; distillation.py:152-155; trajectory.py:27 | 🟢 |
| B7 | 检索数值 | 除零/NaN 防护总体良好（已逐处核实），无已知触发路径 | retrieval.py:58,185-209; reasoning_graph.py:185-209 | ✅ 无风险 |
| B8 | 检索数值 | RRF rank 竞争：combinedScore 相同的行按 memoryId 字典序定序，无 NaN 排序崩溃面 | retrieval.py:26-66 | 🟢 |
| B9 | 桥降级 | file-io 降级的 spawn 若同步抛错（理论上），error 回调路径会变成未捕获异常 | src/python.ts:52,109-111 | 🟢 |
| B10 | 桥降级 | stdin 非 EPERM 写错误 finish 后不杀子进程，存在 python 僵尸进程泄漏窗口 | src/python.ts:98-104 | 🟢 |
| B11 | 桥降级 | 桥无看门狗超时；管道 stdout/stderr 无上限累积 | src/python.ts:91-97,16-40 | 🟢 |
| B12 | 生命周期 | 并行 feedback 对同一记忆的读-改-写无乐观锁，存在丢失更新 | src/index.ts:1260-1276 | 🟢 |
| B13 | 生命周期 | 并行 save_outcome 的溢出淘汰可能双倍删除（银行缩水但不损坏） | src/index.ts:1456-1468 | 🟢 |
| B14 | 生命周期 | 每次 retrieve 做 O(n log n) 全量排序淘汰旧检索记录 | src/index.ts:1200-1206 | 🟡 |
| B15 | 生命周期 | builtin store 运行期 embedding 失败后状态仍为 ready，检索静默降级无告警 | src/builtin-store.ts:322-330; src/index.ts:1018-1025 | 🟢 |
| B16 | schema 兼容 | MemoryItem 的 additive 默认值设计良好；但 structureRank 缺默认值（不对称） | src/domain.ts:79-99 | 🟢 |
| B17 | schema 兼容 | 图词汇表三处重复（TS schema、TS zod、Python 常量），v0.4.0 扩展易漂移 | src/index.ts:226-256; src/domain.ts:13-31; constants.py:46-50 | 🟡 |
| B18 | schema 兼容 | fusionWeights.context 默认值 0.45 与 Python 现值 0.30 不一致（仅影响旧审计行） | src/domain.ts:109-117; retrieval.py:101-112 | 🟢 |
| B19 | schema 兼容 | reasoningGraph 版本钉死 v1（z.literal(1) / enum:[1] / version==1 三处） | src/domain.ts:28; src/index.ts:230; reasoning_graph.py:21 | 🟡 |
| B20 | 边界条件 | 中文 skill 描述触发词正则 `\b当\b` 永不匹配（\b 对 CJK 无效），纯中文描述必被拒 | src/skill-format.ts:25 | 🟡 |
| B21 | 边界条件 | 工具名子串分类误判（如含 “read” 的 “thread” 被标 search） | python/reasoning_memory_core/trajectory.py:10-22 | 🟢 |
| B22 | 边界条件 | LLM 蒸馏输出条目数在 TS 侧未截断（Python 侧会截断到 ≤3，桥载荷受模型输出上限约束） | src/index.ts:1380,1409; distillation.py:72,122 | 🟢 |
| B23 | 边界条件 | 检索语义把 active.semanticText 前缀进每个查询，泛化任务描述可能稀释查询信号 | src/index.ts:989 | 🟢 |
| B24 | 环境 | run-python-tests 与默认 pythonExecutable 均不校验 Python 版本下限 | scripts/run-python-tests.mjs; src/index.ts:100-103 | 🟢 |
| B25 | 其它 | api-config noProxy 子串匹配过宽（规则 “1.2” 可匹配任意含 “1.2” 的主机） | src/api-config.ts:124-132 | 🟢 |
| B26 | 其它 | 每次 publish_skill 同步全量重写导出目录；单条记忆 upsert 每次触发独立 embedding HTTP 调用 | src/index.ts:712-734; src/builtin-store.ts:402 | 🟢 |
| B27 | 其它 | assistant/chunk usage 若收到 NaN，JSON 输出为 null 违反 END_OUTPUT_SCHEMA | src/index.ts:898-901 | 🟢 |
| B28 | 域 schema | 每次 save 写入前 `memoryItemSchema.parse`：旧 v1 记录中若有非默认必填字段缺失（provenance/evidence 等）会解析失败 | src/index.ts:1450,1584 | 🟢 |

## 详细条目

### 一、边界条件（空输入 / 超大输入 / 畸形 JSON / 协议断流）

**B3 🟡 候选为空时的全量回退放大桥载荷**
src/index.ts:1071-1079。当 lexical/vector 候选为空且记忆库非空时，`retrievalMemories` 回退为**全库扫描**，随后整个数组经 `runReasoningCore` 序列化为 JSON 交给 Python。默认 `maxMemories=5000`，单条 searchText 数 KB 时单次桥载荷可达数 MB~数十 MB，且每个无词法命中的查询都会触发。T-debug-2 建议：构造“词法零命中 + 大库”场景测延迟与内存，评估是否应缩小回退池或分页。

**B4 🟡 每次 retrieve 全量携带轨迹 events**
src/index.ts:1119。`events: active?.events ?? []` 随每次检索传给 Python（用于 structure 通道），`maxTrajectoryEvents` 默认 1000 且 schema 无上限（src/index.ts:106，仅 `min(10)`）。若 profile 把它调大，每次检索都全量序列化整条轨迹。T-debug-2 建议：极端事件量 + 高检索频率下的延迟曲线；评估 events 是否只在 route=structure/hybrid 时传递或设硬上限（Python 侧 MAX_CAPTURED_EVENTS=2000 已在签名计算处截断，但载荷在桥传输前未截断）。

**B22 🟢 LLM 蒸馏条目数 TS 侧未截断**
src/index.ts:1380,1409（`items = parsed.map(...)`）；Python 侧 `_normalize_items` 只处理 `raw_items[:max_items*2]` 且 `accepted[:max_items]`（distillation.py:72,122），所以最终落库仍 ≤3，桥载荷受模型单次输出长度约束。风险仅限超大恶意输出时的瞬时内存/载荷。

**B23 🟢 查询前缀稀释**
src/index.ts:989。`query = task_desc + 当前 query` 合并（task_desc 来自 begin_task 的语义文本）。`semantic_similarity` 是 Jaccard 并集，前缀越长对短查询的信号稀释越明显；同时 Python 端 `context_score` 与 `reasoning_score` 使用同一合并字符串，任务描述泛化时检索质量可能退化。T-debug-2 建议：固定长 task_desc + 短 query 的命中率回归。

**B27 🟢 恶意流式 usage 引发 NaN→null**
src/index.ts:898-901。`typeof chunk.usage.inputTokens === 'number'` 对 NaN 也成立（typeof NaN === 'number'），`active.usage.* += NaN` 后 `memory_end_task` 的 `JSON.stringify` 把 NaN 序列化为 `null`，违反 END_OUTPUT_SCHEMA 的 number 约束。真实 DSH 流几乎不可能产生 NaN，属理论防御面。

**B6 🟢 Python 强转对畸形输入崩溃**
retrieval.py:76 `int(request.get("k", 4))`、:77 `float(request.get("positiveNegativeRatio", 1.0))`、distillation.py:152-155 `int(raw_max_items)`（NaN 会抛 ValueError）、trajectory.py:27 `int(row.get("seq", 0))`。TS 侧这些字段都经 clamp/floor 后传入，正常路径安全；但桥是 JSON 明文协议，任何非本插件调用方（或未来新调用点）传字符串/NaN 都会使 worker exit 2。T-debug-2 建议：直接对 Python worker 发送畸形数值的桥级模糊测试，确认错误信息可读、TS 侧降级不崩溃。

**协议断流处理（已核实，状态良好）**
- Python 管道模式一次性读 stdin、写 stdout 后退出；stdout 中出现非 JSON 前缀或进程半途崩溃时，TS 侧 `JSON.parse` 失败并给出 “returned invalid JSON”（src/python.ts:121-125,70-72）。✅
- 无效 UTF-8 输入在 reasoning_memory.py:78 抛 UnicodeDecodeError → 统一捕获 → stderr + exit 2。✅
- file-io 模式下错误写入 error 文件，TS 侧 code≠0 时读取（src/python.ts:60-67）。✅
- stdin 中途断流（EPERM 以外错误）的进程回收缺口见 B10。

### 二、Python 桥降级路径（EPERM file-io）

**B9 🟢 file-io 降级 spawn 的同步抛错未防护（理论）**
src/python.ts:52（`startFileTransport` 内 `child = spawn(...)` 未包 try/catch）、:109-111（pipe child `'error'` 回调里调用它）。本沙箱实测：pipe spawn **同步抛 EPERM**，而 `stdio:'ignore'` 的 spawn 可成功（plugin-smoke 已真实走通降级），所以现网路径安全。但若某宿主连 ignore spawn 都同步拒绝：:109 路径的同步异常发生在事件回调内 → 未捕获异常；:133 路径会沿 executor 拒绝 Promise（可接受）。T-debug-2 建议：mock spawn 同步抛错覆盖两处入口，确认进程不崩、错误可读。

**B10 🟢 stdin 非 EPERM 错误后子进程未回收**
src/python.ts:98-104。stdin `'error'` 非 EPERM 时直接 `finish(error)`，不 `child.kill()`；stdin 已 `end()` 但写入失败意味着管道破损，python 可能永远等不到 EOF/数据而挂起 → 僵尸进程（占内存与解释器启动开销）。T-debug-2 建议：模拟 stdin 错误后检查进程句柄是否退出。

**B11 🟢 无看门狗超时、stdout 无上限累积**
src/python.ts:91-97（`stdout += chunk` 无长度上限）、:16-40（仅依赖外部 AbortSignal，无内部超时）。若 Python 核心因 O(n²) 输入（如全库回退 × 大图）长时间运行，只有调用方取消才能终止；正常规模下响应量受 k/pool 约束，累积风险小。

**EPERM 主路径（已核实，状态良好）**
- 同步 throw：try/catch 捕获并降级（src/python.ts:130-138）。✅
- 异步 error：child `'error'` EPERM → `startFileTransport()`；`pipeErrored` 标记防止随后的 `'close'` 二次结算（src/python.ts:105-116）。✅
- stdin EPERM 不结算、交由 child error 拥有降级权（src/python.ts:98-104 注释与逻辑一致）。✅
- abort 期间：settled 单次结算 + `{ once: true }` 监听器清理（src/python.ts:27-40）。✅
- 临时文件清理：close 回调 finally 中 best-effort 删除（src/python.ts:73-80）；进程崩溃时 `%TEMP%\rm-req-*.json` 会残留，属可接受（低）。

### 三、检索通道数值稳定性（除零 / NaN / rank 竞争）

**B7 ✅ 除零与 NaN 防护逐处核实，无已知触发路径**
- retrieval.py:58 `normalizer == 0.0` 提前返回；:164-171 `utility_mean` 有 +1/+2 平滑且 int() 前 max(0,…)。
- reasoning_graph.py:185-187 `node_precision + node_recall` 为零保护；:191-192 无边提前返回；:207-209 `max(1, comparable_query_edges)` 保护。
- text_similarity.py:29-30 空集返回 0；sequence_similarity:61 空序列返回 0。
- retrieval.py:15-19 `_rank_score` 对非数值/非有限值归零；:171 `min(1.0, …)` 钳制 utility 放大后的 combined。
- lexical-index.ts:82 `… || 1` 保护空库均值；:113 `maxScore > 0 ? … : 0` 保护归一化。

**B5 🟡 审计 schema 解析失败使整个检索调用抛错**
src/index.ts:1177-1195。`retrievalRecordSchema.parse` 对 `result.candidateScores` 全量校验（含 `min(0).max(1)` 数值域）；任何一行出现越界/非有限值（例如未来版本 Python 输出变化）都会让 `memory_retrieve` 在**已选定结果之后**抛错，agent 拿不到记忆。T-debug-2 建议：改造 Python 输出注入越界分数，确认错误形态；评估审计写入是否应 best-effort 化。

**B8 🟢 RRF rank 竞争确定性**
retrieval.py:26-66。相同分数按 `(-score, memoryId)` 字典序排序，rank 分配确定性；`channel_ranks` 只收录 `field > 0.0` 的行，`normalizer` 用 rank_constant+1 基准。无 NaN 排序崩溃面（NaN 已在 `_rank_score` 归零）。

**B16 关联 🟢**：`retrievalCandidateSchema.structureRank`（src/domain.ts:93）没有 `.default(null)`，而 contextRank/reasoningRank/graphRank/lexicalRank/vectorRank 都有。当前 Python 总是输出该字段，故仅当 TS/Python 版本混装（见 B2）时才可能触发解析失败——不对称默认值疑似疏漏。

### 四、域 schema additive 兼容隐患

**B28 🟢 save 路径对旧记录的 parse 依赖默认值**
src/index.ts:1450,1584 `memoryItemSchema.parse(item)`。MemoryItem 的 additive 字段（reasoning/title/description/contraindications/extractionMethod/distillationModel/sourceMemoryIds/reasoningGraph/feedback）都有默认值（src/domain.ts:42-57），旧 v1 记录可读；但 provenance/evidence/confidence/trustLevel/extractorVersion/createdAt 等**无默认值**。若历史库中存在 v1 早期版本缺少这些字段的记录，`memory_feedback`（:1271）与写入路径的 parse 会抛错。T-debug-2 建议：用最小化旧记录（仅 memoryId/type/outcome/taskScope/preconditions/content/structureSignature/semanticText）做兼容读测试，确认域加载与 feedback 的行为。

**B17 🟡 图词汇表三处重复**
- src/index.ts:226-256（工具输出 schema 枚举）
- src/domain.ts:13-31（zod 枚举）
- python/constants.py:46-50（GRAPH_NODE_KINDS/GRAPH_RELATIONS/GRAPH_SOURCES）

v0.4.0 若新增 node kind / relation，三处必须同步；漏一处即出现“Python 产出合法、TS 拒绝解析”或“TS 声明了 Python 不认的枚举值”的漂移。T-debug-2 建议：验证清单中加“新增枚举值三处一致性”检查项；评估是否应做单源生成。

**B19 🟡 reasoningGraph 版本钉死 v1**
src/domain.ts:28 `z.literal(1)`、src/index.ts:230 `enum: [1]`、reasoning_graph.py:21 `version != 1`。任何图结构演进都被版本字段锁死；要么 v0.4.0 保持 v1 语义只做 additive 词汇扩展（需同步 B17 三处），要么引入 v2 并保留 v1 读取兼容（schema 需改为联合，存储层也要能容纳两种版本）。

**B18 🟢 fusionWeights.context 默认值漂移**
src/domain.ts:109-117 给旧审计行默认 `context: 0.45`；Python 现值（weighted 无签名无图路径）为 `context: 0.30`（retrieval.py:112）。仅影响读取历史审计行的显示，不影响新写入；但默认值与语义现状不一致，容易误导审计读者。

**B2 🟡 桥协议无版本握手**
src/python.ts:16-20 只传 operation/数据；无 `protocolVersion` 字段。若插件升级只更新了 JS 或 Python 一侧（部分部署、缓存、旧 dist + 新 python 目录），两侧 schema 漂移会使输出解析失败（B5/B28 的具体触发面）。T-debug-2 建议：模拟“新版 TS + 旧版 Python”与“旧版 TS + 新版 Python”两组混合桥调用，记录失败形态；评估 v0.4.0 是否加 `protocolVersion` 与兼容矩阵。

### 五、并发写入与生命周期泄漏

**B12 🟢 feedback 丢失更新**
src/index.ts:1260-1276。`memory_feedback` 的读-改-写序列（读 item → Python 增量 → `memories.put`）没有 CAS/版本号；两个并发 feedback（或 feedback 与 save 的 builtinWrite 穿插）对同一记忆可能丢失一次计数。反馈本身是 best-effort 排名信号，影响有限。

**B13 🟢 并行 save 的溢出淘汰可能双倍删除**
src/index.ts:1456-1468（与 1590-1602 同构）。两个并发 `memory_save_outcome` 各自计算 `overflow = memories.size - maxMemories` 并按 createdAt 删除最旧记录，交叉执行时可能删出多于需要的记录——银行缩水但不损坏（delete 幂等、lexicalIndex.delete 对不存在 id 无害）。同批蒸馏的条目 createdAt 相同（distillation.py:210 同一 `now`），边界上淘汰谁由 Map 插入序决定，行为未文档化。

**B14 🟡 每次检索全量排序淘汰**
src/index.ts:1200-1206。每完成一次 retrieve 就 `[...retrievals.entries()].sort(...)` 全量扫描再删超量最旧记录；`maxRetrievalRecords=10000` 时每次检索多出 O(n log n) 排序。高检索频率下是隐性性能税。T-debug-2 建议：大记录量下的检索延迟测量。

**B15 🟢 builtin store 运行期 embedding 失败后状态静默漂移**
src/builtin-store.ts:322-330 `embedOrNull` 失败置 `embeddingsDisabled=true` 但不外抛；src/index.ts:1018-1025 只按 `builtinStoreStatus`（初始同步时定死）给告警。运行期 embedding 端点故障后 store 仍显示 ready、`search` 静默返回 []，检索退化到词法/全库回退且无 vectorWarning 提示。功能正确性无损，可观测性受损。

**B1 🟡 `ctx.get('llm')` 未声明注入**
src/index.ts:54 `export const inject = ['tools', 'storageDomain']`；:1388、:1507 用 `ctx.get('llm')` 获取 LlmRuntime。Cordis 中未被 inject 的服务通常不会保证存在于上下文，因此 **DSH llm 蒸馏路径（distillationProvider/distillationModel 配置路线）大概率永远命中 “llm service is unavailable” 分支**，实际只有 api-config 的 openai-chat 端点路线可用。T-debug-2 建议：真实 DSH 宿主验证 memory_save_outcome(distill_with_llm) 与 memory_consolidate 在“仅配置 distillationProvider/Model、无 apiConfigPath”时是否真的能走通；若不能，v0.4.0 应把 'llm' 加入 inject 或移除该分支。

**其余生命周期项（已核实，状态良好）**
- 插件 dispose：domain.close / skillsDomain.close / apiConfig.stop / builtinStore.close 串入 store 队列（src/index.ts:816-823; src/builtin-store.ts:545-558）；closed store 拒绝后续使用（builtin-store.ts:144）。✅
- builtinStoreReady / vectorIndexReady 的 catch 均兜底，不会产生未处理 rejection（src/index.ts:651-655,683-687）。✅
- 会话状态清理：session/disposed 删除 activeBySession/activeByTaskId/ignoredMemoryCalls（src/index.ts:916-922）；跨会话 task_id 结束被拒（:1871）。✅
- ignoredMemoryCalls 在 tool/result 不成对时会保留到会话结束，有界。✅
- vectorIndex 无 close 方法但无状态句柄（纯 fetch 客户端），无可泄漏资源。✅

### 六、其它

**B20 🟡 中文触发词正则失效**
src/skill-format.ts:25 `TRIGGER_TOKENS = /\b(when|if|for|mention|ask|say|当|如果|提到|需要|时)\b/i`。JS 的 `\b` 基于 `\w=[A-Za-z0-9_]`，CJK 字符两侧永远不构成边界，因此 `当/如果/提到/需要/时` 这组“双语”触发词实际永不匹配；纯中文描述（如“当任务涉及发布产物时”）会被 `validateSkillInput` 以缺 trigger 为由拒绝。自动生成的默认描述是英文（“Use when …”）故默认路径不受影响。T-debug-2 建议：中文描述对抗用例 + 修复（把 CJK 分支改为非边界断言或单独的字符类匹配）。

**B21 🟢 工具名子串分类误判**
python/reasoning_memory_core/trajectory.py:10-22。`any(part in lowered for part in (...))` 是子串匹配：`thread_manager` 含 “read” 被标 search、`review` 标 constrain、`bread` 标 search 等。影响 structure 通道与 structureSignature 的纯度（模板挖掘的任务模式分类会继承该噪声）。T-debug-2 建议：构造混淆工具名序列验证误分类传播；评估是否改词边界匹配或工具名白名单。

**B24 🟢 Python 版本下限未校验**
scripts/run-python-tests.mjs 只探测 `--version` 成功即用；插件默认 `pythonExecutable: 'python'`（src/index.ts:100-103）同样无下限校验。代码使用 `dict[str, Any]`（3.9+）与 `X | None`（3.10+）语法，3.8 环境会 ImportError/SyntaxError。建议脚本与插件启动时校验 `>= 3.10`。

**B25 🟢 noProxy 子串匹配过宽**
src/api-config.ts:124-132 `hostMatchesRule` 对无通配符规则用 `candidate.includes(normalized)`：规则 “1.2” 会命中任何含 “1.2” 的主机名；“example.com” 会命中 “badexample.com”。绕过代理的判定过宽，敏感环境里可能导致本应走代理的流量直连。T-debug-2 建议：验证规则语义是否应改为主机等值/域名后缀匹配。

**B26 🟢 同步 IO 与每记录单发 embedding 请求**
src/index.ts:712-734 `exportSkillDirectory` 每次 publish 同步重写全部技能目录（O(skills) 写盘，阻塞工具执行线程）；src/builtin-store.ts:402 `upsertInner` 对每条新记忆单独发起一次 embedding HTTP 调用（无批处理）。规模增长后的延迟与限流风险。T-debug-2 建议：批量 save 时统计 embedding 请求数；评估 builtinWrite 是否应攒批。

**B22 关联 🟢 / B3 关联**：`querySearchText`（src/search-text.ts:36-47）对 graph 节点 text 无长度钳制，但调用方在 TS 侧已对 reasoning_graph_json 做 30000 字符 clip（src/index.ts:1007,1122），总体有界。

## 针对 T-debug-2 的对抗测试优先级建议

1. **模板挖掘退化场景**（空库 / 全同类 / 极端序列）+ 词法零命中全库回退（B3/B4/B21）——验证输出与延迟，而不只是正确性。
2. **新旧记忆兼容**：最小化 v1 记录（B28）读路径、旧审计行（B16/B18）读路径、feedback 更新旧记录。
3. **任务模式误分类**：混淆工具名（B21）、rollback 标记位置、中文工具名。
4. **template 检索通道数值稳定性**：注入越界/NaN 分数（B5）、重复并列分数（B8）、阈值边界 0/1。
5. **Windows/受限环境 Python 桥**：EPERM 同步 throw 注入（B9）、stdin 错误回收（B10）、畸形数值模糊（B6）、无 python 可执行（ENOENT）、file-io 临时文件残留。
6. **配额与 fallback 边界**：maxMemories=1 批量蒸馏同刻淘汰（B13）、retrieval 记录淘汰性能（B14）、positiveNegativeRatio 0.1/10 极端值、builtin store 运行期 embedding 故障（B15）。
7. **DSH llm 注入验证**（B1）与中文 skill 描述触发词（B20）——这两个更接近 v0.4.0 修复项而非纯测试项，需 lead 定夺。

## 结论

主链路（蒸馏→检索→反馈→技能→审计）未发现立即崩溃级缺陷；数值防护与 additive schema 设计整体扎实。风险集中在四块：(a) **桥与运行器在受限环境的行为**（已在基线轮真实验证过 EPERM 主路径）；(b) **规模相关的性能与载荷放大**（B3/B4/B14）；(c) **版本混装与词汇表漂移**（B2/B17/B19）；(d) **若干真 bug 候选**（B1 llm 注入缺失、B20 中文触发词正则、B25 noProxy 过宽匹配），建议 v0.4.0 修复轮由 lead 确认优先级后修复。
