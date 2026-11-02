# 04 · 离线评测 schema：episode 标注 JSONL（供 scripts/eval harness 使用）

> 设计依据：
> - 本仓库 `MVP_EVALUATION.md:105-134`（下一轮可复现评测：约 100 个人工构造、双人标注 episode；按任务族切分；Recall@100/200、nDCG@4、失败教训误用率、配额遵守率 100%、自检索污染 0%、配对成功率差、有害记忆消融）；
> - LongMemEval-V2（arXiv:2605.12493）的能力分类（static state recall / dynamic state tracking / workflow knowledge / environment gotchas / premise awareness，451 问、context-gathering 形式）——**借鉴其分类，但不照搬其对话记忆计分方式**（MVP_EVALUATION.md:109 的明确约束）；
> - 插件真实接口：`memory_begin_task(task_desc, goal?, task_family?)`、`memory_retrieve(query, k?, route?, include_enterprise_rules?, reasoning_graph_json?)`、`memory_save_outcome(outcome, verifier, evidence, ...)`（`src/index.ts:924-1231, 1286-1489`）；记忆字段 `memoryItemSchema`（`src/domain.ts:35-75`）；`structureSignature` 六标签（`src/domain.ts:4-11`）。
>
> 文件约定：`scripts/eval/data/*.jsonl`，每行一个 episode（UTF-8、无内嵌换行的 JSON 对象）。harness 以 `split` 过滤执行，不依赖目录布局。

---

## 1. JSONL 顶层结构（schemaVersion 1）

每行一个 JSON 对象：

```jsonc
{
  "schemaVersion": 1,
  "episode": { /* 见 §2 */ }
}
```

harness 校验：`schemaVersion === 1`，未知版本拒绝执行（便于未来演进）。

## 2. episode 字段（必填 = R，可选 = O，标注字段前缀 `ann.` 表示仅供评测、不进插件）

```jsonc
{
  // ── 身份与切分（防泄漏主字段）─────────────────────────────
  "episodeId": "ep-0001",                       // R  全局唯一，^[a-z][a-z0-9_-]{2,63}$
  "order": 1,                                   // R  执行顺序（整数、全局唯一）；所有运行必须按 order 升序
  "split": "dev",                               // R  "dev" | "test"
  "taskFamily": "code_modification",            // R  防泄漏切分键（自由标签，非空，≤64 字符）
  "taskFamilyGroup": "code",                    // R  粗粒度族（变体归组用，见 §4 规则 2）

  // ── 任务与 gold 分类（L1）───────────────────────────────
  "taskPattern": "code_synthesis",              // R  gold 任务模式，枚举：
                                                //    multi_hop_qa|code_synthesis|tool_orchestration|
                                                //    gui_operation|long_horizon_planning|numeric_search|general
  "task": {                                     // R  重放 memory_begin_task 的输入
    "taskDesc": "重构 utils.py 的解析函数并跑单测",   // R  原样传给 task_desc（harness 不截断，插件会 clip 4000）
    "goal": "让全部单测通过",                       // O  null 或原样传给 goal
    "abstractEvents": ["decompose","search","execute","verify"], // O  gold 结构签名（用于 structure 路由与模板匹配评测；null=本集不评结构路）
    "reasoningGraph": { "version": 1, "nodes": [], "edges": [] } // O  gold 图（用于 graph 路由；null=本集不评图路）
  },
  "outcome": "success",                         // R  memory_save_outcome 的 outcome（success|fail；partial/unknown 的样本不入评测集，见 MVP:43）
  "verifier": "pytest 全绿",                      // R  写入 verifier（≤200 字符）
  "evidence": "运行 pytest，27 通过 0 失败",         // R  写入 evidence（≤2000 字符）

  // ── 记忆写入（供 harness 重放到插件 memory bank）────────
  "memoryWrites": [                             // R  至少 1 条；本 episode 全部写入的记忆
    {
      "memoryId": "mem-0001",                   // R  全局唯一 ID（harness 直接用作 key，保证标注可引用）
      "type": "strategy",                       // R  strategy|structure|failure
      "outcome": "success",                     // R  success|fail（须与 episode.outcome 一致）
      "taskScope": "code_modification",         // R  通常 = taskFamily
      "preconditions": ["存在可运行的单测"],          // R  数组可为空
      "title": "先定位失败单测再改实现",              // R
      "description": "当修改已有代码库时适用；全新项目不适用", // R
      "content": "先跑单测定位失败点，再最小化改动实现，最后重跑验证", // R
      "contraindications": ["目标仓库没有测试基础设施"],    // R  数组可为空
      "reasoning": "单测先行能缩小改动面并给出可核验的停止条件", // R  成功=策略；失败=归因
      "reasoningGraph": null,                   // O  与 task.reasoningGraph 相同结构
      "structureSignature": ["decompose","search","execute","verify"], // R  六标签序列（可含重复）
      "ann": {                                  // R  双人标注（MVP: 每个记忆需标注 6 项）
        "transferability": "high",              // R  high|medium|low —— 可迁移性
        "evidenceQuality": "strong",            // R  strong|weak|unattributable —— 依据质量
        "potentialHarm": "none",                // R  none|contextual|general —— 潜在危害（general 表示无条件使用可能有害）
        "deSpecified": true,                    // R  是否已去除任务特定字符串/ID（false 的样本计为写入质量不合格）
        "goldTemplateId": "tpl-003",            // O  该记忆属于哪个 gold 模板（用于模板挖掘精度评测，见 §5）
        "annotators": ["a1","a2"],              // R  两位标注人 ID
        "agreement": "agree"                    // R  agree|resolved（resolved 时保留原分歧备注）
      }
    }
  ],

  // ── 检索查询（对"截至本 episode 已写入的 bank 状态"做查询）──
  "queries": [                                  // R  至少 1 条
    {
      "queryId": "ep-0001-q1",                  // R  全局唯一
      "turn": 1,                                // R  episode 内查询顺序（1..n）
      "text": "如何稳妥地给既有 Python 仓库做重构",    // R  memory_retrieve.query
      "k": 4,                                   // R  固定 1..20
      "route": "auto",                          // R  auto|semantic|structure|graph|hybrid（消融路由在 harness 层配，见 §6）
      "reasoningGraphJson": null,               // O  graph/structure 路由用的查询图 JSON 字符串
      "expected": [                             // R  gold 相关性标注（key = memoryId，可引用任意 split 内更早写入的记忆）
        { "memoryId": "mem-0001", "relevance": 3, "misleading": false,
          "basis": "策略可直接迁移到本查询场景", "category": "premise_awareness" },
        { "memoryId": "mem-0007", "relevance": 0, "misleading": true,
          "basis": "看似相关但前提冲突（针对无测试仓库的策略）", "category": "gotcha" }
      ],
      "goldTemplate": {                         // O  L2 gold 模板匹配标注
        "templateId": "tpl-003",
        "structureSignature": ["decompose","search","execute","verify"],
        "moduleRequirements": ["retrieve_memory","invoke_tool","verify"],
        "justification": "重构类任务的公共骨架：定位→检索经验→改动→验证"
      },
      "ann": {
        "allowSelfRetrieval": false,            // R  默认 false：expected 不得包含本 episode 写入的记忆（MVP: 污染门槛 0%）
        "intent": "transfer"                    // R  transfer|recall|trap —— 查询意图（trap=专测 gotcha 误用）
      }
    }
  ],

  // ── 环境与泄漏护栏 ─────────────────────────────────────
  "ann": {
    "containsSecretAnswer": false,              // R  写入文本中是否含任务答案/verifier 输出正文（true 的 episode 退出成功率统计，MVP:122）
    "notes": ""                                 // O  标注备注（双人分歧记录等）
  }
}
```

### 相关性等级 `relevance`（retrieval 标注，nDCG 增益档位）

| 值 | 含义 | nDCG 增益 |
|---|---|---|
| 0 | 无关（含"看似相关但误用"的 gotcha） | 0 |
| 1 | 语境相关但不可直接应用（缺前提） | 1 |
| 2 | 可应用、有帮助 | 3 |
| 3 | 必需/可直接迁移（gold 核心） | 7 |

- `misleading=true` 只允许与 `relevance: 0` 搭配：表示"检索器**容易误召回**、模型**用了会出错**"的记忆（对应 MVP 的"失败教训误用率"与 LongMemEval 的 gotcha 类）。误用率 = 返回结果中 `misleading=true` 且被注入上下文的条数占比。
- `category`（LongMemEval-V2 五能力到本评测的映射）枚举：
  - `workflow` ← workflow knowledge（重结构：期待 structure/template 通道命中）；
  - `gotcha` ← environment gotchas（反例：期待失败记忆通道/contraindications 命中，且不误导）；
  - `premise_awareness` ← premise awareness（期待 preconditions/contraindications 匹配）；
  - `state_recall` ← static/dynamic state recall 的**工具侧类比**（回忆任务上下文/环境状态类经验；注明：与 LongMemEval 的对话状态回忆不同域，MVP_EVALUATION.md:31）；
  - `generic`（不映射）。

## 3. harness 执行语义（scripts/eval 的读法）

1. 加载所有 `*.jsonl` → 按 `order` 升序执行（跨文件合并排序；`order` 冲突报错）。
2. 每个 episode 依次：
   a. 若启用重放：`memory_begin_task(taskDesc, goal, taskFamily)` → 按 `abstractEvents` 注入等价工具事件（或跳过，见 b）→ `memory_save_outcome(outcome, verifier, evidence, memory_items=memoryWrites)`；
   b. 逐条 `queries`：以 `(text, k, route, reasoningGraphJson)` 调 `memory_retrieve`，记录返回的 `memories`（含全部分数）与 `candidateScores`（**必须落盘**，用于候选深度曲线，MVP:126）；
   c. 对照 `expected` 计算命中/误召回；对照 `goldTemplate` 计算模板命中（若模板通道已实现）。
3. 插件侧自检索排除（`excludeTaskId`，`src/index.ts:1121`）必须开启；harness 同时断言"返回集中不得出现本 episode 写入的 memoryId"，违反即污染事件（目标 0%，MVP:130）。
4. 重放可选（`--no-replay`）：直接以预构建 bank + 手工写入的 JSON 快照运行查询侧评测；此时 `memoryWrites` 仅作标注引用。

## 4. 按任务族防泄漏切分规则（构建期强制，校验脚本必须实现）

1. **族级唯一归属**：`taskFamily` 相同的所有 episode 必须同 split（`assert: len({split of family}) == 1`）。目标量级：每族 ≥5 个 episode；总量 ~100；族数 ≥4（覆盖代码修改/网页操作/信息检索/环境状态四类，MVP:109）。
2. **粗粒度变体隔离**：同一 `taskFamilyGroup` 不得跨 split（防 `code_refactor_v1`/`code_refactor_v2` 变体泄漏）。
3. **变体相似度检查**：跨 split 的 episode 两两做 `taskDesc` 规范化相似度（lowercase + 去标点 + 空格折叠，`difflib.SequenceMatcher ≥ 0.85` 或共享实体 ID）→ 构建期报错，人工复核。
4. **模板挖掘隔离**：test split 上可用的模板/记忆，其来源 episode 必须全部在 dev split 或该 test episode **之前**的 test episodes（在线模式，仿 AWM online 语义）；禁止用 test 集未来 episode 挖掘的模板评测当前 episode。
5. **答案隔离**：`ann.containsSecretAnswer == true` 的 episode 只参与检索质量指标，不参与"配对成功率差"（MVP:122 的答案泄漏禁令）。
6. **自检索**：`expected` 中引用本 episode 自身写入的记忆必须显式 `allowSelfRetrieval: true` 且标注 `intent` 解释（仅污染专项测试允许）。
7. **双人标注**：`memoryWrites[].ann` 六项全部齐备；`agreement: resolved` 的条目保留 `notes`。标注人不得是生成 episode 的模型。

## 5. 指标 → 字段映射（harness 报告清单）

| MVP 指标（MVP_EVALUATION.md:126-134） | 数据来源字段 |
|---|---|
| Recall@100 / Recall@200（按 outcome 分桶） | `queries[].expected[].relevance ≥ 1` × harness 落盘的 `candidateScores` |
| nDCG@4 | `relevance` 增益表（§2） × 返回 Top-k |
| 失败教训误用率 | `expected[].misleading` 在注入上下文中的占比 |
| 人工适用性分 | `memoryWrites[].ann.transferability` × 查询 `basis` 复核 |
| 配额遵守率 / fallback | harness 落盘的 `balance`（目标 100% / 单独报告） |
| 自检索污染 | §3.3 断言（目标 0%） |
| 配对成功率差 | 有/无记忆两轮运行 × `outcome`（answer 保密规则 §4.5） |
| 有害记忆消融 | `ann.potentialHarm != none` 的记忆过滤前后对比 |
| 写入质量（拒绝率等） | `ann.deSpecified`、`ann.evidenceQuality` |
| 模板匹配（P/R） | `queries[].goldTemplate` × 模板通道输出 |
| 模板挖掘精度 | `memoryWrites[].ann.goldTemplateId` × 挖掘簇分配（§2.2 聚类） |
| LongMemEval 五能力分项 | `queries[].expected[].category` 分组聚合 |

## 6. 消融矩阵（harness 层配置，不写进数据）

固定：模型、工具集、提示、任务顺序（`order`）、随机种子；至少 3 seed。对比臂：①无记忆；②task-context-only；③reasoning-lesson semantic；④+action-structure 的 auto；⑤auto+反馈重排；⑥成功 only vs 正反平衡；⑦（模板落地后）+template 通道（两组 RRF 权重，见 02-template-algorithms.md §3.1）。`route` 字段按臂覆盖，`k` 来自数据。

## 7. 最小可行示例（一行，缩进仅示意）

```json
{"schemaVersion":1,"episode":{"episodeId":"ep-0001","order":1,"split":"dev","taskFamily":"code_modification","taskFamilyGroup":"code","taskPattern":"code_synthesis","task":{"taskDesc":"重构 utils.py 的解析函数并跑单测","goal":"全部单测通过","abstractEvents":["decompose","search","execute","verify"],"reasoningGraph":null},"outcome":"success","verifier":"pytest 全绿","evidence":"27 通过 0 失败","memoryWrites":[{"memoryId":"mem-0001","type":"strategy","outcome":"success","taskScope":"code_modification","preconditions":["存在可运行的单测"],"title":"先定位失败单测再改实现","description":"修改已有代码库时适用；全新项目不适用","content":"先跑单测定位失败点，再最小化改动实现，最后重跑验证","contraindications":["仓库没有测试基础设施"],"reasoning":"单测先行缩小改动面并给出可核验的停止条件","reasoningGraph":null,"structureSignature":["decompose","search","execute","verify"],"ann":{"transferability":"high","evidenceQuality":"strong","potentialHarm":"none","deSpecified":true,"goldTemplateId":"tpl-003","annotators":["a1","a2"],"agreement":"agree"}}],"queries":[{"queryId":"ep-0001-q1","turn":1,"text":"如何稳妥地给既有 Python 仓库做重构","k":4,"route":"auto","reasoningGraphJson":null,"expected":[{"memoryId":"mem-0001","relevance":3,"misleading":false,"basis":"策略可直接迁移","category":"premise_awareness"}],"goldTemplate":{"templateId":"tpl-003","structureSignature":["decompose","search","execute","verify"],"moduleRequirements":["retrieve_memory","invoke_tool","verify"],"justification":"重构类任务公共骨架"},"ann":{"allowSelfRetrieval":false,"intent":"transfer"}}],"ann":{"containsSecretAnswer":false,"notes":""}}}
```

## 8. 校验脚本职责（scripts/eval/validate_schema.py，Python 标准库 only）

1. JSONL 可解析、`schemaVersion==1`、行内无多余顶层键；
2. 必填字段/枚举/长度上限（与插件 clip 上限一致：taskDesc ≤4000、verifier ≤200、evidence ≤2000、content ≤4000 等）；
3. §4 全部防泄漏断言（构建期即失败，非运行时警告）；
4. `expected[].memoryId` 必须存在于更早 episode 的 `memoryWrites`（引用完整性）；
5. `structureSignature`/`reasoningGraph` 与插件 zod schema 一致（`src/domain.ts:4-31` 同构校验）；
6. 输出报告：族-分裂矩阵、每族 episode 数、标注一致性汇总、违规清单。
