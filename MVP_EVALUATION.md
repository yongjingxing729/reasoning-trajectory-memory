# DSH 推理记忆插件 MVP 评估

**评估日期：2026-10-28**  
**插件版本：0.2.0**  
**范围：** 本地代码与公开论文/仓库的设计对照；本报告不声称已获得基准性能结果。

## 结论摘要

本轮把插件从“按相似任务描述找记录”推进到“以可迁移推理教训为中心的经验记忆”，加入可审计任务逻辑图和混合检索原型。TypeScript 维护本地 BM25 倒排索引，并可选同步 Qdrant HNSW 向量索引；Python 合并候选后融合推理、结构和图信号，再按正反配额共同返回。成功策略和失败归因分别保存在独立 `reasoning` 字段；有限反馈闭环可小幅影响后续排序。

架构继续符合 DSH 的插件模型：Cordis bundle、工具/存储接入、BM25 与 Qdrant REST adapter 是 TypeScript；推理记忆蒸馏、图/结构相似度、RRF、配额和反馈计数由随包 Python 核心实现。Qdrant 是可选派生索引，DSH `storageDomain` 仍是事实源。该实现具备研究型原型所需的闭环和审计面，但还没有真实 Qdrant 部署或标注集证明检索质量提升；暂不应按生产记忆系统评估。

## 参考研究与实现取舍

| 工作 | 可复用思想 | 本插件采用 | 暂不采用 |
|---|---|---|---|
| [ReasoningBank (ICLR 2026)](https://arxiv.org/abs/2509.25140)；[Google Research 实现](https://github.com/google-research/reasoning-bank) | 从成功和失败经验提炼可迁移推理策略；之后检索、使用并回写经验 | 必须有成功 `reasoning_insight` 或失败 `failure_mode` 才能写入；成功和失败池都参加检索；当前任务排除自己刚写入的记忆 | MaTTS 测试时扩展、多轮并行轨迹生成、论文的 WebArena/SWE-Bench harness |
| [Graph of Thoughts (AAAI 2024)](https://arxiv.org/abs/2308.09687)；[官方实现](https://github.com/spcl/graph-of-thoughts) | 将思考单元表示为节点、依赖表示为边，支持非线性推理结构 | 保存带类型/来源的情境、前提、推断、动作、证据和结论节点；有向依赖做 DAG 校验；检索时匹配节点与关系边 | 通用图搜索、图状态聚合/变换算子、动态构造多条推理路径；当前只做确定性单图匹配 |
| [MRAgent: Memory is Reconstructed, Not Retrieved (2026)](https://arxiv.org/abs/2606.06036)；[官方实现](https://github.com/Ji-shuo/MRAgent) | 通过 Cue–Tag–Content 关联图，从相关线索重建代理记忆 | 借鉴显式关联和来源标记；本插件依照当前任务提供的逻辑图匹配已有推理记忆 | 主动迭代查询、LLM 驱动图扩展和多轮证据重建；这些增加成本，且尚未对 DSH 经验任务验证 |
| [Agent Workflow Memory](https://arxiv.org/abs/2409.07429) | 发现并复用多步 Agent 工作流模式 | 将抽象工具动作序列与策略/失败教训并列建模；可在 auto 路由与图分数共同排序 | 从轨迹自动归纳高层工作流、跨记忆模式挖掘；当前没有足够标注数据校准模式质量 |
| [Reflexion (NeurIPS 2023)](https://arxiv.org/abs/2303.11366)；[官方代码](https://github.com/noahshinn/reflexion) | 利用环境/测试反馈形成语言经验，影响下一次决策 | `memory_feedback` 记录 helpful/harmful/neutral 次数，供后续检索做小幅排序调整 | 持久化完整反思文本、自动重试或把自评当成验证真值 |
| [A-MEM (NeurIPS 2025)](https://arxiv.org/abs/2502.12110)；[官方代码](https://github.com/agiresearch/A-mem) | 结构化记忆笔记、索引和持续演化 | 将任务情境、推理教训、前提、验证来源、轨迹签名和效用反馈分字段保存 | 动态记忆图与跨条目改写；当前 DSH KV domain 没有图索引和跨记录事务 |
| [AgeMem (ACL 2026 SAC Highlight)](https://arxiv.org/abs/2601.01885)；[官方代码](https://github.com/y1y5/AgeMem) | 以工具操作管理长期/短期记忆，强调模型自主决定何时读写 | 插件以 DSH 工具提供显式检索、蒸馏、反馈和活动任务轨迹清理 | 其强化学习训练控制器；本项目没有相应训练数据、模型策略或运行期奖励 |
| [Mem0](https://arxiv.org/abs/2504.19413)；[官方代码](https://github.com/mem0ai/mem0) | 抽取/巩固流程、混合检索和可复现评测工程 | 借鉴多信号可拆分审计的方向；当前只保留情境、教训、工具结构三路透明分数 | 作为运行时依赖；Mem0 面向通用/对话记忆并依赖 LLM/embedding 组件，当前插件优先保持 DSH 原生、Python 标准库核心 |
| [Hindsight (ACL 2026 Demo)](https://aclanthology.org/2026.acl-demo.27/)；[论文 PDF](https://aclanthology.org/2026.acl-demo.27.pdf) | 并行向量、BM25、图、时间检索，之后用 RRF 与 reranker 合并/精排 | 采用本地 BM25、可选 Qdrant 向量候选，并在 Python 中融合推理、动作结构、逻辑图和稀疏/稠密排名 | 暂不实现时间检索与 cross-encoder；记忆目前没有足够的时间有效性标签和验证数据 |
| [H-Mem (2026)](https://arxiv.org/abs/2605.15701) | 时间/语义树与知识图并行组织，使用树/图混合检索 | 以其混合表示方向作为设计参照；现有推理逻辑图可参加召回后的融合排序 | 暂不构建层级时间语义树或动态巩固，先验证当前候选召回和 RRF 是否有效 |
| [Reciprocal Rank Fusion (SIGIR 2009)](https://doi.org/10.1145/1571941.1572114) | 通过各路排名合并结果，避免直接比较量纲不同的原始分值 | RRF 排名常数 60；启用 lexical、reasoning、vector、structure、graph 五路，且仅对实际有结果的路归一化 | 权重是启发式初值，不代表最优；需要标注集消融与候选深度扫描 |
| [Qdrant Hybrid Queries](https://qdrant.tech/documentation/hybrid-queries/)；[官方开源仓库](https://github.com/qdrant/qdrant) | HNSW 向量检索、payload 过滤、hybrid query 与 RRF API | Qdrant 作为 opt-in 派生索引；payload 只含 ID、outcome、taskId、哈希；按 outcome 过滤并排除活跃任务 | 暂不让 Qdrant 成为唯一事实源；不开启 Qdrant 内置 BM25/融合，以保持 Python 内部推理/图分路可审计 |
| 企业策略 RAG 与 OWASP RAG 安全指南 | 按组织范围召回规则片段、标注来源/版本，并把检索文档视为可能含间接 prompt injection 的外部输入 | 新增独立企业规则 collection；固定 `ruleSetId`、active 状态和有效时间过滤；只读 API key、payload 复核、独立审计状态；规则不混入经验记忆排序 | 不在本插件实现企业规则编辑/同步、确定性合规判定或工具授权；由企业索引流水线和 DSH 执行策略层负责 |
| [Zep: Temporal Knowledge Graph](https://arxiv.org/abs/2501.13956)；[Graphiti 开源项目](https://github.com/getzep/graphiti) | 事实的时间有效性、溯源和历史保留 | 作为后续处理过时/相互矛盾策略的设计参照 | 当前不构建实体/事实图；策略记忆不是持续变化的用户事实，DSH storage-domain 没有图索引/跨记录事务 |
| [LongMemEval](https://arxiv.org/abs/2410.10813)；[LongMemEval-V2](https://arxiv.org/abs/2605.12493) | 评估长期、多会话、时序、知识更新、工作流经验、环境陷阱和前提意识 | 评测计划加入跨任务工作流、失败陷阱、前提匹配和拒绝不适用记忆 | 直接把对话记忆成绩当作工具推理记忆成绩；两种任务设定不同 |

DSH 的插件和存储边界按[官方 Harness 文档](https://github.com/deepseek-ai/deepseek-harness)、[添加工具指南](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cookbook/adding-a-tool.md)及[storage-domain 规范](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/storage/storage-domain/README.md)实现。storage-domain 在打开时按版本校验，且当前不提供自动迁移，所以本轮只添加带默认值的字段并保留域版本；部署环境仍需用实际 profile 验证兼容性。

ReasoningBank 官方仓库当前还公开了[同任务检索污染防护的讨论](https://github.com/google-research/reasoning-bank/issues/17)。该 issue 明确说明这是一项评估边界建议，并未声称论文成绩受到污染；插件据此排除活动 `taskId` 的记忆，防止任务先写入再立即取回自身经验。

## 已实现内容

### 推理记忆，而非任务存档

- 成功记忆要求一条简短、可迁移的推理策略；失败记忆要求有依据的失败归因。任务描述仅作检索情境，不能单独变成一条记忆。
- 记忆分别存储 `reasoning` 与 `semanticText`，检索可审计地返回两种语义分数。
- `partial`、`unknown` 或没有可分类工具动作的样本不会进入正负池；模型/调用者提交的 verifier/evidence 仍无法由插件独立核实，所有条目标记为 `unverified`。
- 轨迹只在会话内暂存有限事件元数据；长期存储不包含工具参数、工具输出、原始提示或 CoT。

### 逻辑图与图检索

- `reasoningGraph` 是 v1 带标签 DAG，节点类型包括情境、前提、推断、动作、证据、结论和失败；边类型表达支持、依赖、先后、引导、解释、限定或矛盾关系。显式图限定 2–24 个节点、1–48 条边，要求包含情境/前提、推断/失败和结论，依赖边不得成环；矛盾边不参与因果环检查。
- 节点和边分别记录 `agent_input`、`task_input`、`tool_trace`、`verifier_input`、`outcome_input` 或 `derived` 来源。调用者输入的来源标签会被覆盖，避免输入伪造工具/验证来源。省略图时，Python 按任务情境、蒸馏教训、抽象工具序列与结果生成基础图。
- `graph_similarity` 先对相同类型节点做词项相似度的一对一匹配，再比较映射后的有类型边，按 `0.60 × nodeF1 + 0.40 × edgeF1` 给分。`graph` 路由要求有效查询图；`auto` 收到有效图时提高图路权重，结构路权重按是否有观察到的工具序列调整。这样可将“相同词汇但依赖关系不同”的图分开排序。
- 该实现是启发式候选排序器，不是完整图推理引擎：不做子图同构、路径规划、LLM 查询扩展或矛盾消解；文本匹配仍依赖确定性词项重叠，节点对应采用贪心策略。没有离线评测证明图分数能提升下游任务成功率。

### 企业规则 RAG

- 新增默认关闭的单独规则检索通道。profile 开启后，`memory_retrieve` 默认检索规则，也可在可选模式下用 `include_enterprise_rules` 控制单次调用；`enterpriseRuleRagRequired` 会锁定检索开关。
- 规则向量存放在管理员维护的独立 Qdrant collection。插件只做 collection 信息读取和向量查询；固定 `ruleSetId`、`status=active`、`validFrom <= now <= validUntil` 过滤，并再次核对返回 payload。管理员负责 ingestion、chunking、版本切换与过滤字段索引；插件不写规则库。
- 返回结果与经验记忆分开，包含规则 ID、标题、正文片段、来源、版本、优先级、生效时间和向量分数。检索审计只记录 rule IDs、检索状态和 required 标记，不把规则正文复制进 DSH 域。规则 collection 可使用独立 embedding 配置和只读 Qdrant 凭据。
- 状态包括 `ready`、`no_matches`、`unavailable`、`invalid_configuration` 和 `disabled`。required 模式下异常和无命中都会给出核验企业原始规则源的提示；无命中不等于没有适用规则。RAG 结果只是政策片段检索，不会在插件侧强制工具权限，正式合规门禁必须由策略引擎或 DSH 工具执行层实现。
- retrieved rule text 仍是外部内容，可能带间接 prompt injection。输出带来源/范围并提醒模型不要执行无关嵌入指令；这类说明不是可靠的安全边界，企业规则写入审批、分 collection 凭据和执行层授权更关键。

### 检索排序与正反比例

新增混合召回流程：本地 TypeScript BM25 在情境、推理教训、前提、动作、图标签上建倒排索引；英文/数字按词项，中文按字符和二元组。成功/失败分别取 `retrievalCandidatePoolSize`（默认 100）候选。可选 Qdrant 向量索引也按 outcome 过滤，分别取 `vectorCandidatePoolSize` 候选；向量候选并入 BM25 候选后交给 Python 重排。启用 RRF 时权重为 lexical `0.30`、reasoning `0.10`、vector `0.35`、structure `0.10`、graph `0.15`，无命中通道不参与归一化；RRF 使用排名而非直接比较 BM25/cosine 分值。正反配额在融合排序后应用。若 BM25/向量均无候选，系统回退全量 Python 排序；`graph` 与 `structure` 路由始终全量排序，避免索引门控影响专用路由。

Qdrant 只保留嵌入与索引 payload，DSH `storageDomain` 保存完整记忆。同步基于记忆文本、embedding URL/model/dimension 的哈希，在后台增量补齐或更新并删除孤儿点。回填期间检索不等待，先使用 BM25/必要时全量 Python 回退；新记忆先写 canonical domain，再异步更新 Qdrant。向量功能默认关闭；启用后，待检索记忆语义文本和查询文本都会发送到配置的 OpenAI-compatible embedding endpoint。Qdrant 或 embedding 请求失败时标记向量层降级并继续 BM25；后续不会在每次检索中重复尝试故障向量服务，直到插件重启。当前实现没有 cross-encoder，也未测线上服务的超时、成本或质量曲线。

`auto` 有可分类动作序列时采用：

```text
score = (0.20 × task_context
       + 0.50 × reasoning_lesson
       + 0.30 × action_structure)
       × utility_multiplier
```

这些分数权重只用于全量加权回退。`auto`/`hybrid` 有 BM25 或向量候选时使用上面的 RRF，`semantic` 在 BM25 候选上使用教训加权，`structure`/`graph` 使用全量专用评分。语义是确定性 Unicode token/中文字符和二元组重叠；结构是抽象动作标签序列的 LCS/F1 风格相似度，不是向量嵌入，也不理解完整自然语言含义。

正:负默认 `1:1`，由 `positiveNegativeRatio` 调整。先在各自 outcome 池中按分排序，再按配额取样；一侧候选不足时才由另一侧补位并报告 `fallbackUsed`。有害反馈只影响同一侧的排序，不能把失败记忆从失败池变成正例，也不能破坏正反配额。

`memory_feedback` 每任务只接受本任务检索出的记忆，每条记忆一次；调用者应只提交实际用过的记忆，但插件无法核实是否使用。helpful/harmful 采用 Beta(1,1) 平滑，映射为 `0.8–1.2` 的乘数，最终分数裁剪至 `[0,1]`；权重和实际乘数进入候选审计。neutral 不改变排序。反馈说明文本不入库，不改变 `confidence` 或 `trustLevel`。这是低成本在线信号，不是可校准的强化学习奖励。

### DSH 插件边界

- `memory_begin_task`、`memory_retrieve`、`memory_feedback`、`memory_save_outcome`、`memory_end_task` 均注册为 DSH 工具；插件通过 `dsh.bundle.patch` 装入 profile。
- TS 负责 Cordis 生命周期、session/event 监听、工具参数、存储和 Python 子进程桥；Python 负责推理记忆算法。
- 检索时仅把排序必需字段传给 Python，不传 verifier/evidence、sourceSessionId 或任务事件原文；工具返回需要的来源和证据由 TS 按 memoryId 从 DSH domain 补齐。
- 记忆与检索记录写入 DSH `storageDomain`，不创建独立 TaskRecord，不把插件变成独立应用。
- `reasoning` 和 feedback 计数带 schema 默认值，用来读取旧记录；storage-domain 版本维持 v1。旧记忆没有独立 `reasoning` 字段时，Python 会尝试从原 `content` 的旧版固定前缀恢复策略/失败教训；无法识别的内容按空教训参与检索。

## 评估结果与成熟度

本轮本地验证 `npm test` 全部通过：TypeScript 构建成功，Python **20/20**，Node **3/3**。Node mock 覆盖 BM25、记忆 Qdrant 同步/检索，以及企业规则的只读请求、ruleSet/生效期过滤、低分/跨范围/过期 payload 拒绝。mock 不等于真实服务、企业 ingestion 或 DSH profile 集成。尚无标注集与线上任务数据，因此没有策略召回率、误命中率、规则覆盖率、成功率、embedding 成本、真实延迟或 Qdrant 性能数据；论文结果不能外推到本插件。

| 维度 | 当前评估 | 原因 |
|---|---|---|
| DSH 插件形态 | 设计对齐 | bundle patch、Cordis tools、`storageDomain`，无旁路服务 |
| 推理记忆语义 | 改进明显，仍依赖调用者 | 存的是策略/失败教训和工具动作结构；洞见质量、归因真实性没有自动判定 |
| 成功与失败覆盖 | 设计完整，效果未量化 | 正反配额、候选不足回退、partial/unknown 排除均有显式状态 |
| 检索可解释性 | 原型级 | 候选通道、分路分数/排名和融合权重写入审计；BM25 与 Qdrant 扩展候选，但确定性词项相似度及未校准 RRF 仍会漏召回或错排 |
| 企业规则检索 | 可选原型 | 固定 rule-set/时间过滤、来源和状态有审计；企业 ingestion、检索覆盖率与政策冲突解析未验证 |
| 反馈闭环 | 原型级 | bounded self-report 可影响重排；没有外部校验，反馈可能偏差或被污染 |
| 持久化/隐私 | 范围较小，尚需宿主验证 | 不保存原始工具输入输出或 CoT；跨进程行为、实际权限和保留策略未部署验证 |
| 生产就绪度 | 尚未达到 | 本地 TS 构建与 mock 测试不替代宿主安装验证；无真实向量服务验证、检索基准、并发写入压力与安全评审 |

## 下一轮可复现评测

### 数据集

建立 DSH 专属的跨任务经验集，先人工构造并双人标注约 100 个任务 episode，覆盖代码修改、网页操作、信息检索和环境状态任务。每个记忆需标注 outcome、前提、策略/失败原因、依据质量、目标任务适用性和潜在危害。按任务族切分开发集与留出集，确保同一任务变体不会同时进入两侧。可参考 LongMemEval-V2 的 workflow / gotcha / premise-awareness 分类，但不要直接把其对话问题计分方式当成动作轨迹评测。

### 对照与消融

固定模型、工具环境、提示模板、任务顺序和随机种子，至少比较：

1. 不用记忆；
2. task-context-only 检索；
3. reasoning-lesson semantic 检索；
4. 加入 action-structure 的 `auto`；
5. `auto` 加反馈重排；
6. 成功记忆 only 与正反平衡检索。

至少重复 3 个 seed；所有 memory write 都依据环境/verifier 结果，不让测试任务的答案泄露到记忆中。成功率只作为最终结果，不能代替记忆检索正确率。

### 指标与 MVP 门槛

- **推理检索：** 对 BM25、向量、图/动作以及融合候选分别统计 Recall@100、Recall@200、nDCG@4、人工适用性分、失败教训误用率；按 outcome 报告，并对候选池深度做曲线，避免只测最终 Top-k。
- **企业规则 RAG：** 按 ruleSet/部门/角色构造留出 query，报适用规则 Recall@k、precision@k、无规则查询的误召回率、过期/跨租户泄漏率（目标 0）、required 模式故障时阻断率；测试版本回滚与同规则冲突呈现。
- **经验价值：** 使用/不使用检索记忆的配对任务成功率差，另报有害记忆触发后成功率变化。
- **配额：** 当两侧都满足候选数时，统计正负条目配额遵守率；候选不足的 fallback 率和覆盖率单独报告。配额遵守率门槛为 100%。
- **污染：** 当前 taskId 自检索率门槛为 0%；注入与记忆写入文本中的恶意指令/伪造 verifier 样例，检查来源与信任标记是否保留。
- **写入质量：** 统计只提交任务总结、空洞洞见、无归因失败和无效 verifier 时的拒绝率；双人标注写入的可迁移性和可证据性。
- **运行成本：** DSH 工具调用 p50/p95 延迟、BM25 倒排内存、Python 子进程启动/序列化开销、Qdrant 查询延迟、启动回填时间、embedding API 调用量/费用、JSON 存储增长和过期清理行为。

建议只有在留出集上 `nDCG@4` 高于 context-only 基线、配额遵守率达标、没有自检索污染且 harmful-feedback 消融不降低任务安全/成功表现后，才进入更大范围的 profile 试用。门槛是建议，不代表当前已达到。

## 已知局限与后续排序

1. **首要：检索质量。** 在真实 Qdrant 与 embedding 服务及标注任务上分别测 lexical、dense、动作结构、逻辑图和融合路线；评估候选召回与最终重排，扫描候选池深度和 RRF 权重；图匹配需与子图/路径级方法消融比较。
2. **写入质量。** 添加证据来源类型和可审核的独立验证接口；当前 `verifier`/`evidence` 是调用者自述。
3. **记忆组织。** 研究同类记忆去重、相反结论并存、适用前提冲突、失效/过期和跨记忆链接，再决定是否需要 A-MEM 风格关联或 Zep/Graphiti 时间图；当前不能安全地自动覆盖历史策略。
4. **反馈稳健性。** 按任务或主体分区，防止一个来源反复自评改变全局排名；当前只限制每个活动 task 对同一条记忆计一次。
5. **宿主验证。** 安装到真实 DSH profile，验证 bundle patch、Python 3.10+ 路径、事件字段、域版本兼容和关闭时子进程取消。
6. **规模验证。** DSH 当前会把 storage-domain 记录载入运行内存，BM25 也位于插件进程；Qdrant 减少 Python 全量候选处理，但不能消除主存占用。需在 5k/50k/500k 记忆量级测 RSS、启动同步、过滤召回和写入一致性，再考虑迁移 canonical storage 或分块/分层索引。
7. **规则治理。** 通过授权流水线导入规则并审核来源/版本/生效时间；部署只读、按 collection 限权 key；对规则冲突、过期、缺失、跨租户过滤及检索文本注入做红队评测。若策略需要强制执行，另接确定性 policy engine，不能依赖模型遵守 RAG 片段。

## 研究来源

- Ouyang et al., [ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory](https://arxiv.org/abs/2509.25140), ICLR 2026；[Google Research 开源实现](https://github.com/google-research/reasoning-bank)。
- Besta et al., [Graph of Thoughts: Solving Elaborate Problems with Large Language Models](https://arxiv.org/abs/2308.09687), AAAI 2024；[官方开源实现](https://github.com/spcl/graph-of-thoughts)。
- [MRAgent: Memory is Reconstructed, Not Retrieved: Graph Memory for LLM Agents](https://arxiv.org/abs/2606.06036)；[官方开源实现](https://github.com/Ji-shuo/MRAgent)。其关联图与主动重建思路仅作为设计参照，本项目未复刻其多轮检索算法。
- [Agent Workflow Memory](https://arxiv.org/abs/2409.07429)：多步 Agent 工作流记忆研究，作为结构化动作模式的参考。
- Shinn et al., [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366), NeurIPS 2023；[官方开源实现](https://github.com/noahshinn/reflexion)。
- Xu et al., [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110), NeurIPS 2025；[官方开源实现](https://github.com/agiresearch/A-mem)。
- Yu et al., [Agentic Memory (AgeMem)](https://arxiv.org/abs/2601.01885), ACL 2026 SAC Highlight；[官方开源实现](https://github.com/y1y5/AgeMem)。
- Chhikara et al., [Mem0](https://arxiv.org/abs/2504.19413)；[官方开源实现](https://github.com/mem0ai/mem0)。
- Rasmussen et al., [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956)；[Graphiti 开源实现](https://github.com/getzep/graphiti)。
- Wu et al., [LongMemEval](https://arxiv.org/abs/2410.10813)；Wu et al., [LongMemEval-V2](https://arxiv.org/abs/2605.12493)。
- [DeepSeek Harness 官方仓库和开发指南](https://github.com/deepseek-ai/deepseek-harness)、[工具添加指南](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cookbook/adding-a-tool.md)、[storage-domain 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/storage/storage-domain/README.md)。
- [Hindsight: Structured Agent Memory that Retains, Recalls, and Reflects](https://aclanthology.org/2026.acl-demo.27/)；[论文 PDF](https://aclanthology.org/2026.acl-demo.27.pdf)。多策略召回/RRF 作为架构参考，本插件未实现其 temporal retrieval 与 cross-encoder。
- [H-Mem: A Novel Memory Mechanism for Evolving and Retrieving Agent Memory via a Hybrid Structure](https://arxiv.org/abs/2605.15701)：树/图混合记忆检索，作为长期结构化演化方向参考。
- Cormack, Clarke, Büttcher, [Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods](https://doi.org/10.1145/1571941.1572114), SIGIR 2009；[Qdrant Hybrid Queries 官方文档](https://qdrant.tech/documentation/hybrid-queries/)。
- [Qdrant Security 与 read-only API keys](https://qdrant.tech/documentation/security/)、[payload indexes](https://qdrant.tech/documentation/manage-data/indexing/)；[OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)。
