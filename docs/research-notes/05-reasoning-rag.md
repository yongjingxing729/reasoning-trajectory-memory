# 05 · 推理检索 RAG：前沿方法调研与 MVP 落地建议（docs/spec-0.5.0 设计依据）

> 口径与 `02-template-algorithms.md` 一致：严格区分 **「论文原文做法」** 与 **「MVP stdlib 工程近似」**；工程近似不声称复刻论文。
> 约束：Python 标准库 only、确定性、可审计、可单测；不引入 LangChain/向量库等重依赖；向量复用现有可选 Qdrant/pgvector 适配器。
> 抓取核实：本文所有论文数字均经 `web_fetch` 复核 arXiv 摘要页（2026-11-04，全部 HTTP 200），原文引文见 §4。

---

## 0. 功能定位与硬边界（先读）

**推理检索 RAG** = 用户导入公司开发规范等**文档**，插件做**推理感知的文档检索**：不只是关键词/向量召回，而是带上当前任务的任务模式、策略模板步骤意图与推理图上下文，做过滤、查询展开与轻量多跳。

三个既有检索面（务必不互相侵入）：

| 面 | 内容 | 事实源 | 检索方式 | 生命周期 |
|---|---|---|---|---|
| 经验记忆 | 成败教训（success/fail） | DSH domain `memories` | BM25 + 可选向量 + 结构/图/模板通道 RRF + 正反配额 | 插件写入 |
| 企业规则 RAG | 受控政策片段 | **外部** Qdrant collection（管理员 ingestion） | 只读向量检索 + ruleSet/active/validFrom/validUntil 过滤 | 管理员维护，插件只读 |
| **推理检索 RAG（新）** | 用户导入的开发规范/文档语料 | **新** DSH domain（建议独立 `reasoning_memory_documents`） | BM25 主索引 + 可选向量 + 块间链接多跳 + 任务模式过滤 + 步骤对齐 | 用户导入/删除，检索只读 |

两条硬规则（沿用 v0.4.0 的教训）：
1. **文档检索不进入记忆 RRF 通道与正反配额排序**（与企业规则同理由：文档没有 outcome/utility 语义，混入会破坏配额可审计性）。
2. **索引期不用 LLM**（无摘要、无实体抽取、无社区摘要——RAPTOR/GraphRAG/HippoRAG 的索引期都需要 LLM，全部越界）。检索期同样无 LLM：查询展开必须是确定性规则。

---

## 1. 前沿方法梳理（论文原文 vs 工程近似）

### 1.1 Self-RAG（arXiv:2310.11511）

- **论文原文**：训练单个 LM **按需自适应检索**（不是固定检索 k 段），并用特殊 **reflection tokens（反思 token）** 生成式地反思检索到的段落与自己的生成（摘要原文："adaptively retrieves passages on-demand, and generates and reflects on retrieved passages and its own generations using special tokens, called reflection tokens"）。7B/13B 模型在开放域 QA、推理与事实核查上超越 ChatGPT 与 retrieval-augmented Llama2-chat（摘要原文，§4.1 有引文）。
- **可借鉴点**：①"检索是否必要"是可学习的决策——本插件对应物是**模板步骤声明的 `retrieve_document` 模块需求**（步骤级"何时检索"的声明式近似）；②反思/校验应在检索后发生——对应 L4 的 verify 步骤与 `retrieval_intent` 区分。
- **MVP 工程近似**：不训练、不生成 token。用模板步骤的模块需求声明"此步需查文档"，检索后由模型在 verify 步骤自行核验，插件侧只提供来源/版本元数据。**近似不是 Self-RAG**，文档如实声明。

### 1.2 RAPTOR（arXiv:2401.18059）

- **论文原文**："recursively embedding, clustering, and summarizing chunks of text, constructing a tree with differing levels of summarization from the bottom up"——**LLM 摘要**构建多层树，检索时在不同抽象层取块。关键数字（摘要原文）："by coupling RAPTOR retrieval with the use of GPT-4, we can improve the best performance on the QuALITY benchmark by **20% in absolute accuracy**"。
- **可借鉴点**：①**多层表示**：高层（章节/主题）覆盖全局问题，低层（块）覆盖细节——本插件对应物是**标题层级路径 + 块链接图**；②检索从树的多层取证据。
- **MVP 工程近似**：**无 LLM 摘要**（越界）。用 markdown 标题层级（heading path）做"树的骨架"、用块间 parent/next/refs 链接做"跨层取证据"；高层检索 = 匹配 heading path 文本而非摘要。诚实标注：无摘要会损失跨块归纳能力，评测需验证。

### 1.3 GraphRAG（arXiv:2404.16130）

- **论文原文**：LLM 两阶段建图索引——"first, to derive an entity knowledge graph from the source documents, then to pregenerate community summaries for all groups of closely related entities"；查询时每个**社区摘要**生成部分回答，再汇总为最终回答（map-reduce 式）。适用对象是**全局性问题**（"整个语料讲了什么主题"）；论文在 1M token 级数据集上显示对常规 RAG 基线的 comprehensiveness/diversity 提升（摘要原文，§4.3 有引文）。
- **可借鉴点**：局部检索（vector/BM25）对全局问题天然失败——本插件文档检索应**限定在"步骤级局部证据"**，全局总结类问题明确越界（提示模型用 `memory_load_document` 全读小文档或用户自读原文）。
- **MVP 工程近似**：不建实体图、不做社区摘要（都需要 LLM）。可选轻量替代：**块间引用链接**（见 §2.4）做有界多跳，只服务局部证据链，不服务全局综述。

### 1.4 HippoRAG（arXiv:2405.14831，综述 [18] 已收录）

- **论文原文**：海马体索引理论启发，LLM 抽取实体建知识图 + **Personalized PageRank** 单步检索。摘要数字：多跳 QA 上 "outperforms the state-of-the-art methods remarkably, by **up to 20%**"；单步检索达到与 IRCoT 迭代检索相当或更好，同时 "**10-30 times cheaper and 6-13 times faster**"。
- **可借鉴点**：①图扩散单步替代多轮迭代检索（成本优势是本插件多跳设计的直接依据）；②实体级图对多跳有效。
- **MVP 工程近似**：不做 LLM 实体抽取与 KG。用**标题层级 + 显式引用链接**做轻量图，Personalized PageRank → **有界 BFS 扩散（深度 ≤2、衰减 0.6^hop）**。综述 [18] 已论述，本文仅补数字复核与落地映射。

### 1.5 Query rewriting / Step-back prompting（arXiv:2310.06117）

- **论文原文**：Step-Back Prompting 让 LLM 先从具体细节**抽象出高层概念与第一性原理**，再据之推理。摘要数字（PaLM-2L）：MMLU 物理 **+7%**、化学 **+11%**、TimeQA **+27%**、MuSiQue **+7%**（§4.5 有引文）。
- **可借鉴点**：抽象化改写提升检索命中（具体措辞 → 高层概念）。
- **MVP 工程近似**：**确定性 step-back 表**——把查询映射到 taskPattern（复用 `classify_task_pattern`，`python/reasoning_memory_core/templates.py`），用 pattern 词表与 taskFamily 表（`constants.py` `TASK_FAMILY_PATTERNS`/`PATTERN_KEYWORDS`）做术语泛化；模板步骤 `intent` 文本并入查询（模板作者的意图即"高层概念"）。**不用 LLM 生成改写**。

### 1.6 HyDE（arXiv:2212.10496）

- **论文原文**：让 instruction-following LM **生成假设文档**（可能含虚假细节），用无监督对比编码器编码后做向量检索，"encoder's dense bottleneck filtering out the incorrect details"。摘要：显著超越无监督稠密检索器 Contriever（§4.6 有引文）。
- **可借鉴点**：查询侧扩充可提升召回。
- **MVP 工程近似**：**不做假设文档生成**（需要 LLM）。对应物=确定性查询展开（§2.5）+ 可选伪相关反馈（用 top-k 结果的 heading 词项扩充一次，关闭默认）。诚实标注：近似只做词项级扩张，无生成式泛化。

### 1.7 路由式 RAG（Adaptive-RAG，arXiv:2403.14403）

- **论文原文**：训练**小分类器**预测查询复杂度，动态在 no-retrieval / single-step / iterative 之间选择（摘要原文："dynamically select the most suitable strategy ... based on the query complexity"，分类器是 "a smaller LM trained to predict the complexity level"）。
- **可借鉴点**：不是所有查询都该检索（对应 Self-RAG 的按需检索）。
- **MVP 工程近似**：不训练分类器。路由 = **模板步骤模块需求声明**（步骤没有 `retrieve_document` 就不触发文档通道）+ 空结果时明确返回"无匹配文档"并提示改用原文精读。复杂度路由的确定性别名：`retrieval_intent`（search/constrain/verify 等）驱动通道权重微调（复用 `RETRIEVAL_INTENT_WEIGHT_DELTAS` 模式，`constants.py:256-259`）。

### 1.8 Multi-hop 检索（IRCoT，arXiv:2212.10509）

- **论文原文**：把检索与 CoT **交错**（"what to retrieve depends on what has already been derived"），每步用当前推理句做查询。摘要数字（GPT-3，四数据集 HotpotQA/2WikiMultihopQA/MuSiQue/IIRC）：检索提升 "**up to 21 points**"、下游 QA "**up to 15 points**"（§4.8 有引文）。
- **可借鉴点**：多跳检索的价值被证实；但交错需要模型逐步生成。
- **MVP 工程近似**：**图扩散多跳**（HippoRAG 式的单次图遍历替代多轮）——从 BM25/向量 top-k 种子块出发，沿块链接做有界 BFS。多轮交错留给 agent 侧（agent 可以在不同步骤调 `memory_retrieve_document`，天然就是 IRCoT 的步进检索，插件只保证每步检索本身可靠）。

---

## 2. MVP 落地建议（stdlib、确定性、可单测）

### 2.1 文档导入与分块（(a)）

- **格式**：markdown（默认）/ 纯文本。markdown 优先：标题层级直接给分块结构。
- **导入工具**：`memory_import_document`（`name`(kebab, 唯一), `content`(cap 建议 200,000 字符，超出拒绝并报错), `task_patterns?`(7 类枚举子集，默认 `['general']`), `source?`, `version?`(文档版本字符串，默认 '1')）。同名再导入 = 版本 +1（与 skills/templates 的版本递增惯例一致）；或 `overwrite` 语义由 spec 定夺（本文建议：同名版本 +1，不覆盖）。
- **分块算法**（Python 纯函数 `doc_chunking.py`，全确定性）：
  1. 按行解析 `^(#{1,6})\s+(.+)$` 维护 heading path 栈（如 `# 规范` → `## 代码风格` → path `规范 / 代码风格`）；非 markdown 文本用空行分节、无 heading path。
  2. 以 heading 边界切块；块内按空行分段；单段 > `CHUNK_TARGET_CHARS`（初值 **1200**，中文与英文同样适用，接近现有 4000 字符上限的 1/3）时在句边界（`。！？!?;；.\n`）硬切；碎片 < `CHUNK_MIN_CHARS`（初值 **300**）并入前块。
  3. **不用重叠窗口，改用链接**（理由：重叠在 BM25 上制造重复计数且不可解释；链接符合 RAPTOR/标题层级精神且审计友好）。纯文本无标题时保留 prev/next 链接。
  4. 每块产出：`chunkId`、`docId`、`seq`、`headingPath[]`、`text`、`prevChunkId`、`nextChunkId`、`parentChunkId`（最近上层 heading 块）、`refs[]`（§2.4）。
- **上限常量（初值，进 `constants.py`）**：`MAX_DOCS = 200`、`MAX_DOC_CONTENT_CHARS = 200_000`、`MAX_CHUNKS_PER_DOC = 2000`、`MAX_CHUNK_TEXT_CHARS = 2000`、`MAX_DOC_NAME_CHARS = 80`、`MAX_DOC_TASK_PATTERNS = 7`。
- **元数据 schema（zod，additive 铁律——有数据后只能加默认字段）**：
  ```ts
  documentSchema = { docId, name, source: '' , version: '1', taskPatterns: [enum 7类 default ['general']],
                     chunkCount, charCount, importedAt, updatedAt }
  documentChunkSchema = { chunkId, docId, seq, headingPath: [string], text,
                          prevChunkId: '' , nextChunkId: '' , parentChunkId: '' , refs: [string] }
  ```

### 2.2 索引（(b)）

- **词法主索引**：新建 `DocumentLexicalIndex`（TS，`src/document-index.ts`），复用 `LexicalMemoryIndex` 的 BM25 实现（k1=1.2, b=0.75，中文 char+bigram、英文词项，`src/lexical-index.ts:17-31,79-101`）——**不改动 `LexicalMemoryIndex` 本身**（记忆索引零回归）。文档侧索引文本 = `heading path + 块文本 + docName + taskPatterns`。
- **可选向量**：复用现有 embedding 适配器（`src/embedding-client.ts`/`builtin-store.ts`/`vector-index.ts` 的 OpenAI-compatible fetch 与 Qdrant/pgvector 同步思路），**独立 collection `reasoning_documents`**（与 `reasoning_memories`、企业规则 collection 三者互不共享）。同步沿用"双内容哈希"模式；向量不可用时自动降级 BM25（与记忆检索同款降级语义）。
- **块间链接图（轻量多跳）**：
  - 结构边（分块时必建）：`parent/child`（标题层级）、`prev/next`（文档内顺序）——存块记录字段，查询时按 docId+seq 派生。
  - 引用边（分块后确定性抽取，`refs[]`）：markdown 链接 `[text](#anchor)`/`[text](path)`（仅锚点与本地路径）、章节引用模式（`见 §1.2`、`参见 3.1`、`第 X 章/节`、`依据 2.2`）→ 解析为 chunkId（按 heading path/编号匹配）。无法解析的引用保留文本、不建边。
  - 有界多跳（§2.5）：深度 ≤ **2**、衰减 **0.6^hop**、扩展上限 **16** 块。图有环也不爆炸（BFS + visited）。孤立块（无链接）直接以自身分参赛。

### 2.3 推理感知检索（(c)）

**工具形态决策：独立检索工具 + 独立结果字段，不进记忆 RRF 通道。**
- `memory_retrieve` 的 RRF 通道（lexical/reasoning/vector/structure/graph/template，`retrieval.py:32-105`）语义 = 记忆条目排序 → 正反配额，文档混入会破坏配额审计（硬边界 §0）。**不新增 document 通道进记忆 RRF。**
- 新增 `memory_retrieve_document`，参数与 `memory_retrieve` 的步骤对齐参数同构：`query`(required), `k`(默认 4, cap 10), `active_template_id?`, `step_index?`, `retrieval_intent?`, `task_pattern?`(可覆盖分类器), `reasoning_graph_json?`, `max_hops?`(默认 2, cap 2)。返回独立 `documents[]`（chunkId/docId/docName/headingPath/text/score 分解/links 摘要），并写检索审计（§2.6）。
- **内部融合（文档域自己的 mini-RRF）**：
  ```
  通道：lexical(BM25) 0.45 / vector(cos, 可选启用) 0.35 / pattern(元数据过滤后的一致性分) 0.10 / link(多跳衰减分) 0.10
  RRF 仅当 lexical 与 vector 均有排名结果时启用；否则加权求和。通道分全部入审计（沿用 candidateScores 风格）。
  ```
- **L1 任务模式过滤**：文档/块的 `taskPatterns` 与当前 `taskPattern`（`classify_task_pattern` 输出）求交；仅 `general` 文档全通过。**软回退**：过滤后候选为空 → 放开 general 文档并审计标记 `patternFallback: true`（避免硬过滤把语料掐死；对应 v0.4.0 的 fallback 哲学）。
- **步骤 intent 语义**：`retrieval_intent == 'retrieve_document'` 时该步必走文档通道；intent 文本（模板步骤 `moduleDemands[].intent`）并入查询文本；intent 微调权重（沿用 `RETRIEVAL_INTENT_WEIGHT_DELTAS` 模式）：`constrain` → pattern 权重 +0.05（约束类文档靠元数据）；`verify` → 无微调（验证走记忆/工具）；其余不动。全部写入审计。
- **确定性查询展开（无 LLM）**：
  1. `Q0 = query + reasoningGraph 节点文本`（复用 `querySearchText` 模式，`src/search-text.ts:28-52`）。
  2. step-back 泛化：查询 → taskPattern（分类器）→ 追加该 pattern 的**词表泛化词**（`PATTERN_KEYWORDS`，仅追加命中查询词的词表条目，防噪声）与 taskFamily 映射键。
  3. 模板意图并入：`active_template_id` 对应步骤的 `moduleDemands[].intent` 文本追加（模板作者声明的高层意图）。
  4. 可选伪相关反馈（默认关）：取 top-3 块的 heading 词项回填一次查询（1 轮、确定性）。
- **多跳块召回**：BM25/向量 top-k 种子 → BFS（§2.2）扩散，最终分 = 基础分 × 0.6^hop；每结果标注 `hop` 与 `viaChunkId`（审计）。

### 2.4 四层映射对齐表（(d)）

| 层 | v0.4.0 现有 | 推理检索 RAG 新增 | 落点 |
|---|---|---|---|
| L1 任务模式 | `classify_task_pattern()` 7 类 | 文档/块元数据 `taskPatterns` 适用域；检索时按当前模式过滤（软回退） | 导入元数据 + `memory_retrieve_document.task_pattern`/过滤 |
| L2 策略模板 | `moduleDemands[].kind` 6 种（`constants.py:69-76`） | **新增 kind `retrieve_document`**：`MODULE_DEMAND_KINDS` +1；`MODULE_DEMAND_BY_TAG`：`search` 的必选/可选与 `constrain` 的可选加 `retrieve_document`；`MODULE_DEMAND_TAG['retrieve_document'] = 'search'`（lossy 签名推导）；`MODULE_DEMAND_INTENT['retrieve_document'] = '检索用户导入的开发规范/文档，按步骤意图过滤'`；builtin 模板不强制改（additive 可选） | `constants.py` 词表 + 挖掘/播种逻辑 |
| L3 能力模块 | 记忆/模板/技能/工具/验证器 | **文档语料模块**：独立 domain 表 + 独立 BM25/向量索引 + 元数据（docId/source/version/taskPatterns）；块级记录含 headingPath/links | 新 domain + `document-index.ts` + `doc_chunking.py` |
| L4 激活动作 | `memory_plan_task` 激活计划；`memory_retrieve` 的 active_template_id/step_index/retrieval_intent | `memory_retrieve_document` 同构步骤对齐参数 + 上下文注入（返回块带 headingPath/source/version 供模型引用）；plan 输出对含 `retrieve_document` 需求的步骤提示"该步查文档" | 新工具 + plan 文本扩展 |

### 2.5 解耦设计（(e)）

1. **独立 domain**：`reasoning_memory_documents`（version 1，tables `documents` + `chunks`），与 `reasoning_memory`（memories/retrievals/templates，`src/domain.ts:200-206`）、`reasoning_memory_skills` 三域并列。理由：文档块数量级远大于记忆（100 页规范 → 数百块），独立域隔离内存/回收/回滚面（与 skills 独立域的既有先例一致，`src/skill-domain.ts:4-7`）；DSH 契约已核实"新增域/表不破坏旧介质"（`01-dsh-contracts.md`）。**schema 铁律：写入后只能 additive。**
2. **独立索引**：`DocumentLexicalIndex` 实例与 `LexicalMemoryIndex` 分离（不共享 postings），向量独立 collection；删除文档 = 删块记录 + 双索引同步删除（复用既有 delete 路径模式）。
3. **检索审计不混入记忆配额**：文档检索写入 `retrievals` 表需走 additive 字段（建议 `documentIds: string[] default []`、`documentPatternFallback: boolean default false`），**不**进 `selectedMemoryIds`/`candidates`（配额口径不变）；或独立审计表由 spec 定夺（本文倾向 additive 字段，改动面最小）。
4. **企业规则零改动**：`enterprise-rules-rag.ts`（只读 Qdrant、ruleSet/active/validFrom/validUntil 过滤）一行不改；文档语料不进其 collection；两者在 `memory_retrieve` 返回体中是并列字段（`enterpriseRules` 与文档检索各自独立工具/字段）。
5. **记忆零回归**：记忆 RRF 权重、正反配额、模板通道、反馈闭环全部不变；文档检索不写 memories、不触发模板效用统计。

### 2.6 新增工具清单（建议，供 spec 定夺）

| 工具 | 职责 | 关键参数 | 幂等/安全 |
|---|---|---|---|
| `memory_import_document` | 导入/版本升级文档 | name, content, task_patterns?, source?, version? | 同名版本 +1；内容超限拒绝 |
| `memory_list_documents` | 元数据清单（渐进披露 L1） | pattern? | 只读 |
| `memory_load_document` | 全读小块文档（≤N 块） | doc_id, chunk_offset?, limit? | 只读；大文档提示用检索 |
| `memory_retrieve_document` | 步骤对齐推理检索（§2.3） | query, k, active_template_id?, step_index?, retrieval_intent?, task_pattern?, reasoning_graph_json?, max_hops? | 只读 |
| `memory_delete_document` | 删除文档及其块/索引 | doc_id | 双索引同步删；审计记录 |

---

## 3. 与企业规则 RAG 的边界与差异

| 维度 | 企业规则 RAG（既有） | 推理检索 RAG（新） |
|---|---|---|
| 语料性质 | 受控政策片段（合规/安全导向） | 用户导入的开发规范/技术文档（工程指导导向） |
| 导入方 | **管理员**外部 ingestion，插件只读 | **用户**经插件工具导入（可版本升级/删除） |
| 事实源 | 外部 Qdrant collection（`enterprise-rules-rag.ts:47-126`） | 新 DSH domain `reasoning_memory_documents` + 独立索引 |
| 检索方式 | 仅向量（Qdrant REST + payload 过滤 + 二次校验） | BM25 主 + 可选向量 + 块链接多跳 + 模式过滤 |
| 过滤语义 | ruleSetId/status=active/validFrom≤now≤validUntil/priority（硬过滤） | taskPatterns 交集（**软回退**，审计 fallback） |
| 与推理的耦合 | 与记忆/模板完全解耦，required 模式强制 | 与四层映射耦合（L1 过滤、L2 需求、L4 步骤对齐） |
| 失效语义 | 过期规则硬性不返回 | 文档无有效期；以 version 与 importedAt 表达时效 |
| 安全提示 | 检索文本可能带间接 prompt injection，返回带来源/范围提醒（`src/index.ts` v0.3 语义） | **同款**：文档块是外部内容，返回带 docName/source/version + 不得执行内嵌指令的提示 |
| 信任语义 | 企业权威内容，required 可阻断 | 参考资料，不强制、不进正反配额 |

**共同点**：都是"与经验记忆解耦的外部文本检索"；都是只读检索面；都不进入记忆 RRF/配额；都需要来源与版本元数据。

---

## 4. 关键数字/论断复核（web_fetch 核实，2026-11-04）

| 论文 | 复核数字（摘要原文引文） |
|---|---|
| RAPTOR (2401.18059) | "by coupling RAPTOR retrieval with the use of GPT-4, we can improve the best performance on the **QuALITY** benchmark by **20% in absolute accuracy**"；方法 = 递归 **embedding + clustering + summarizing** 自底向上建树。⚠️ 增益依赖 LLM 摘要层，纯 stdlib 近似拿不到该增益，文档须如实声明。 |
| GraphRAG (2404.16130) | 两阶段 LLM 建图：**entity knowledge graph** → **community summaries**；查询时每社区摘要生成 partial response 再汇总；在 "datasets in the **1 million token** range" 上对常规 RAG 基线在 **comprehensiveness 与 diversity** 上 "substantial improvements"；定位是全局性问题（QFS）。⚠️ 数字是定性表述，论文未在摘要给量化百分点；若 spec 引用请用该定性原文。 |
| Self-RAG (2310.11511) | **reflection tokens**：训练 LM 用特殊 token 反思检索段落与自身生成，实现按需检索（"adaptively retrieves passages on-demand"）；7B/13B 超越 ChatGPT 与 retrieval-augmented Llama2-chat（开放域 QA、推理、事实核查）。⚠️ 该机制需要训练模型，插件侧不复制。 |
| HippoRAG (2405.14831) | 多跳 QA 超越 SOTA "by up to **20%**"；单步检索与 IRCoT 迭代检索相当或更优，同时 "**10-30 times cheaper and 6-13 times faster**"；整合进 IRCoT 仍有增益。（综述 [18] 已收录；此处补数字。） |
| Step-Back (2310.06117) | PaLM-2L：MMLU 物理 **+7%**、化学 **+11%**、TimeQA **+27%**、MuSiQue **+7%**；做法 = LLM 抽象出高层概念/第一性原理再推理。 |
| HyDE (2212.10496) | LLM 生成假设文档 → 无监督对比编码器检索，显著超越 Contriever（零样本稠密检索）。 |
| Adaptive-RAG (2403.14403) | 训练小分类器预测查询复杂度，动态选 no-retrieval / single-step / iterative；"enhances the overall efficiency and accuracy ... compared to relevant baselines"（摘要为定性表述）。 |
| IRCoT (2212.10509) | 检索与 CoT 交错；GPT-3 在 HotpotQA/2WikiMultihopQA/MuSiQue/IIRC 上检索提升 "up to **21 points**"、下游 QA "up to **15 points**"。 |

全部 8 篇 arXiv 摘要页抓取成功（HTTP 200），无网络失败记录。

---

## 5. 越界声明（写入 spec 的"不做什么"）

1. 不做 LLM 摘要/实体抽取/社区摘要（RAPTOR/GraphRAG/HippoRAG 索引期越界）；
2. 不做假设文档生成与 LLM 查询改写（HyDE/Step-Back 的生成侧越界；只做确定性词表泛化）；
3. 不训练检索路由器/反思 token（Adaptive-RAG/Self-RAG 越界；以模板模块需求声明替代）；
4. 不做全局性问题总结（GraphRAG 的目标场景越界；文档检索只服务步骤级局部证据）；
5. 文档检索不进入记忆 RRF 通道与正反配额（可审计性硬边界）；
6. 不修改企业规则 RAG 与记忆检索的任何现有语义（零回归要求）。

## 6. 评测与验收建议（供 T-rag-spec 引用）

- 复用 `scripts/eval` 体系：新增文档检索评测维（chunk 级 gold relevance、pattern 过滤消融、多跳命中率、patternFallback 审计率、与记忆配额零干扰回归）。标注 schema 建议在 `04-eval-schema.md` 基础上加 `documentQueries` 段（下一迭代由 spec 落实）。
- MVP 门槛建议：文档通道 Recall@10 ≥ 无过滤基线；`patternFallback` 率有审计且可解释；记忆检索指标（Recall@k/nDCG@4/配额/自检索）与 v0.4.0 基线**完全持平**（解耦回归）。
- 已知规模风险（沿用 MVP_EVALUATION 局限 #6）：块全量进内存，`MAX_DOCS × MAX_CHUNKS_PER_DOC` 上限即内存护栏；超限导入拒绝而非静默截断。

## 7. 实施顺序建议（供 spec/impl 参考）

1. `doc_chunking.py`（分块+链接抽取，纯函数）→ Python unittest；
2. domain `reasoning_memory_documents` + `document-index.ts`（BM25）；
3. `memory_import_document / memory_list_documents / memory_delete_document`（先让语料进得来、删得掉）；
4. `memory_retrieve_document`（L1 过滤 + 查询展开 + 多跳 + 内部 mini-RRF）；
5. `MODULE_DEMAND_KINDS` 加 `retrieve_document`（词表 + 挖掘/播种/校验三处联动）+ `memory_plan_task` 文本提示；
6. 可选向量（复用现有适配器，独立 collection）与评测维落地。
