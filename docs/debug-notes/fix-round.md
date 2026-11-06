# T-debug-2 修复轮日志与对抗清单

- 建立：2026-11-02（debugger）
- 状态：**等待实现落地** —— task-16 被 task-8 / task-13 / task-14 / task-15 阻塞，未就绪；在 lead 解阻塞前**不改任何源码**。
- 本文件记录：lead 裁决、预批准修复的实施要点、对抗测试清单、修复日志表。

## 1. Lead 裁决记录（2026-11-02）

### 预批准修复（T-debug-2 轮执行，改前简报）
| ID | 位置 | 修复要求 |
| --- | --- | --- |
| B20 | src/skill-format.ts:25 | CJK 触发词（当/如果/提到/需要/时）改为独立匹配分支（参照 Python templates.py:54 `_TRIGGER_TOKENS` 的写法：CJK 词放在 `\b` 组之外），补中文描述对抗测试。⚠ v0.4.0 新增 `memory_publish_template` 的校验复用同一守卫，修复必须同时覆盖新代码路径。 |
| B21 | python/reasoning_memory_core/trajectory.py:10-22 | 工具名子串误分类（如 `thread` 含 `read`）改为词边界/分词匹配；先跑现有测试确认期望，行为变化合理则同步更新测试并在修复日志说明。 |
| B24 | scripts/run-python-tests.mjs + 插件启动路径 | 校验 Python >= 3.10，不满足时给出可读错误（脚本探测阶段与插件 `pythonExecutable` 首调阶段）。 |
| B25 | src/api-config.ts:124-132 | noProxy 匹配改为主机等值/域名后缀语义（保留 `*.domain` 通配语义），补测试（`badexample.com` 不得被 `example.com` 命中；`1.2` 不得命中任意含 `1.2` 的主机）。 |
| P13 | src/index.ts:862-879 | 内置模板播种失败后不缓存 settled 失败 Promise：后续 plan/list/load 调用重试播种（可选 backoff），或失败时同步抛错让调用者可见；二选一，注释与行为必须一致。 |
| P14 | src/index.ts:2252 + python/reasoning_memory_core/templates.py:55,801-802 | memory_publish_template 的 name 校验统一为同一正则 `^memory_tpl_[a-z0-9]+(-[a-z0-9]+)*$`（Python publish_template_validate 与工具 description 示例一致），补测试“按工具描述示例写 name 能通过”。 |
| P16 | python/reasoning_memory_core/retrieval.py（+ TS 审计透传） | include_deprecated=false 时，deprecated 记忆在正反配额选择**之前**从候选池剔除（配额契约优先于返回条数），新增审计字段 `deprecatedFilteredCount`；include_deprecated=true（默认）维持现状（参与检索 + 结果带标记）。Python 侧实现，Node 测试补断言。 |

### 裁决：不修（记录即可）
- **verify 微调权重和 1.05**（P4）：保持现状。spec 如此定义，数值有界不构成 bug。
- **挖掘幂等键 representative vs coreSignature**（P3）：保持 representative。改用 coreSignature 会使同骨架的多次发射被误判重复。
- **其余 🟢 观察项**（P1/P2/P7/P8/P15/P17 等）：先不动代码，fix-round.md 标注“已记录、待标注集校准/后续版本”，除非对抗测试证明破坏正确性。

### 不批准改动（仅测试 + 记录）
- **B1**：`ctx.get('llm')` 不加入 inject（保持防御式 try/catch，避免无 llm 服务的 profile 加载失败）。T-debug-2 在 mock 宿主验证“llm 缺失时蒸馏降级路径不抛错”；真实 DSH 宿主的 llm 蒸馏可用性记入已知局限（fix-round.md §已知局限）。
- **B2/B17/B19**：v0.4.0 不改桥协议、不改图版本。模板枚举词汇表新增后做**三处一致性检查**：TS 域 schema（src/domain.ts）/ TS 工具 output schema（src/index.ts 各 *_OUTPUT_SCHEMA）/ Python 常量（python/reasoning_memory_core/constants.py）。不一致即报 bug。

## 2. T-debug-2 对抗清单

### 2.1 新旧记忆兼容
- [ ] 无 v0.4.0 新字段的旧 v1 记忆记录：domain 打开、memory_retrieve、memory_feedback、memory_query、memory_publish_skill 全链路读回正常（对照 risk-review B28）。
- [ ] 旧检索审计行（无 structureRank 等）不引发解析异常（对照 B16）。
- [ ] 新增 additive 字段有默认值的记录与新记录混库检索（templates 表空时不影响旧行为）。
- [ ] 新模板引用已删除记忆（悬挂 strategyTemplateId/sourceMemoryIds）的读路径。

### 2.2 模板挖掘退化场景
- [ ] 模板库为空：memory_plan_task 的返回形态（含“内置种子播种失败”场景——种子缺失/播种异常时计划输出必须可读、不抛错）。
- [ ] 全同类签名（所有记忆 structureSignature 相同）：挖掘不产出噪声簇/空模板，计数与 reason 正确。
- [ ] 极端序列（单事件、全 rollback、超长序列、空 events）：分类与签名不崩溃。
- [ ] 单元素簇/孤点：挖掘产出形态与 reason 文案。
- [ ] 记忆量 0 / 1 / 恰好阈值 的边界。

### 2.3 任务模式误分类
- [ ] 混淆工具名（B21 修复后回归：thread/bread/review 等）。
- [ ] 中文工具名、memory_* 前缀工具不计入签名。
- [ ] 六分类器对缺信号输入（空 taskDesc/goal/family）回落 general 且置信度可解释。
- [ ] rollback 标记位置（失败动作后）对分类/签名的影响。

### 2.4 template 检索通道数值稳定性
- [ ] 除零/NaN 注入：注入越界分数、非有限值，确认 `_rank_score` 类防护仍覆盖新通道。
- [ ] rank 并列（相同分数的多模板/多记忆）排序确定性。
- [ ] 空模板 / 空模板库时通道权重与 fusion 模式不产生 NaN。
- [ ] 阈值 0 / 1 边界；activeTemplateId 与 route 组合矩阵（auto/semantic/structure/graph/hybrid × 有无模板）。

### 2.5 Windows / 受限环境 Python 桥
- [ ] EPERM 同步 throw 注入（mock spawn 同步抛错）覆盖 pipe 与 file-io 两处入口（对照 B9）。
- [ ] stdin 非 EPERM 写错误后子进程回收（对照 B10）。
- [ ] 桥级模糊：畸形数值/类型（对照 B6）、畸形 JSON 请求、未知 operation、超长输入。
- [ ] python 缺失（ENOENT）与 Python <3.10（B24 修复后验证可读报错）。
- [ ] file-io 临时文件清理与残留（进程被 kill 场景）。

### 2.6 配额与 fallback 边界
- [ ] maxMemories 极端小（1）批量蒸馏同刻淘汰（对照 B13）。
- [ ] maxRetrievalRecords 大库下检索延迟（对照 B14）。
- [ ] positiveNegativeRatio 0.1 / 10 极端值与 fallbackUsed 标志正确性。
- [ ] builtin store 运行期 embedding 故障（对照 B15）：状态与告警行为记录。
- [ ] feedback 幂等/丢失更新场景（对照 B12）。
- [ ] 候选空时全库回退的载荷与延迟（对照 B3/B4）。

### 2.7 新增（lead 指定）
- [ ] **新工具 output schema 符合 dsh-tools enforced subset**：对象根 `additionalProperties: false`、字段类型受限（对照 dsh-tools 类型声明核实约束，禁止函数/任意类型）。
- [ ] **memory_plan_task 空模板库返回形态**（含内置种子播种失败场景，见 2.2）。
- [ ] 三处模板词汇一致性检查（TS 域 schema / TS output schema / Python constants）。

## 3. 已知局限（记录用，不修复）
- [ ] B1：DSH llm 服务未注入，distillationProvider/Model 路线在真实 DSH 宿主中的可用性待验证；mock 宿主验证“llm 缺失时降级不抛错”即通过（templates.test.mjs 全链路以 llm 缺失配置跑通，未抛错）。

### 已记录、待标注集校准/后续版本（lead 裁决：不修）
- P1 分类器全零词法信号 + task_family 命中时 agreement 虚高（confidence 可至 0.9）。已记录 templates.py:92,158-165；待标注集校准置信度语义。
- P2 mine_templates 对畸形 memory 的 int()/`.get` 崩溃面（桥级模糊输入 → worker exit 2，错误可读）。已记录 templates.py:631,737-739。
- P3 挖掘幂等键用 representative 签名（lead 裁决：保持，coreSignature 会误判重复）。已记录 templates.py:707。
- P4 verify 意图微调权重和 1.05（lead 裁决：spec 定义、数值有界，保持）。已记录 constants.py:255。
- P7 RRF 消融 overrides 对 context 键静默忽略（eval 消融器需知晓）。已记录 retrieval.py:53-57。
- P8 `builtin_templates` operation 未列 spec §3.3（无害扩展，TS 播种路径使用）。已记录 service.py:62-63。
- P15 memory_plan_task 硬编码 hasGraph:false（S4 图信号 TS 路径不可达）。已记录 index.ts:2114-2115。
- P17 检索审计 fusionWeights 无 template 字段（zod 剥离；templateChannelWeight 独立字段留存）。已记录 domain.ts:171-179。

### 一致性检查结果（B2/B17/B19 验证清单）
- ✅ 模板词汇三处一致：TASK_PATTERNS（constants.py:59-67）＝ domain.ts strategyTemplateSchema.taskPatterns ＝ index.ts PLAN_OUTPUT_SCHEMA.pattern / memory_publish_template.task_patterns 枚举。
- ✅ MODULE_DEMAND_KINDS（constants.py:69-76）＝ domain.ts templateModuleDemandSchema.kind ＝ index.ts module_demands.kind / retrieval_intent 枚举。
- ✅ 结构标签 TAGS（constants.py:3）＝ domain.ts structureTag 枚举。
- ✅ EXTRACTOR_VERSION 0.4.0（constants.py:14）＝ index.ts skill extractorVersion（2417 附近）。
- ✅ 新工具 output schema 全部对象根 additionalProperties:false，`type:'json'` 在 dsh-tools ValueSchemaSpec 支持面内。
- ✅ memory_plan_task 空模板库（播种失败）形态：matchedTemplates 空 + general-mode 提示，不抛错（adversarial.test.mjs 断言）。

## 3.5 spec-0.4.0 派生对抗面（2026-11-02 读规格后补充）

- [ ] **classify_task_pattern**：全零信号 → confidence=0 + fallback general；task_family 查表命中即停（表序决定歧义归属）；`clip(2×margin)` 边界；中文 task_desc 词法命中（S2）。
- [ ] **template_match_score.quality**：`(0.5 + 0.5×(succ+1)/(use+2)) × (1 − 0.3×harmRatio)` —— harmRatio 分母为 0（全新模板 0/0/0 计数）时是否除零；下限 0.5 是否真生效；use/succ 计数的语义核对（spec 用 useCount/succCount，EvolveR 原公式为 c_use/c_succ）。
- [ ] **匹配阈值边界**：0.75 强匹配 / 0.55 可注入 / <0.55 不注入；`templateMatchMin` 配置默认 0.55 与 `TEMPLATE_MATCH_MIN = 0.25` 提示阈值的关系与一致性问题。
- [ ] **mine_templates**：min_support 边界（0/1/超大）、θ_join=0.75/θ_form=0.55 聚类、多序列 LCS 空签名/单签名、挂起队列、fail_majority 不发射、幂等 (coreSignature,taskPattern) 判定、`success占比≥0.5` 的恰好 0.5 边界。
- [ ] **retrievalIntent 权重微调**：rollback_check → structure/template 各 +0.05、context/reasoning 同减——所有权重保持 [0,1] 且非负；verify → graph +0.05 时被减项是谁（spec 未指明）。
- [ ] **模板通道 RRF**：templateScore 三步计算（1.0 / 0.6×seq_sim / 0.0）单调性；空 activeTemplate 或记忆无签名时回退 0；deprecated 记忆参与检索但带 `deprecated:true` 标记。
- [ ] **distillation 门控**：minInsightConfidence=0（关）/ 边界 1 / 未提供 insightConfidence（=0）在门控开启时全拒；逐 item 拒绝审计字段。
- [ ] **memory_plan_task 空模板库**：内置 5 种子播种失败（如 templates 表不可写/播种异常）时返回形态可读、不抛错；全部模板分 < 0.25 时的“按 general 直接执行”提示。
- [ ] **memory_feedback 模板约束**：template_id 必须 ∈ 本任务 planTemplateIds；每任务每模板一次；同调用模板+记忆混合评分。
- [ ] **memory_query 新过滤**：status/strategyTemplateId/reasoningLevel 与旧过滤组合；builtin-store 若未建新列时的 JSONB 过滤一致性（两种来源必须同语义）。
- [ ] **deprecatedAfterHarmfulFeedback**：阈值边界、跨任务计数、只标记不删除、deprecated 后检索/计划行为。
- [ ] **新工具 output schema**：memory_plan_task / memory_list_templates / memory_load_template / memory_publish_template 的 output schema 对象根 `additionalProperties: false`，符合 dsh-tools enforced subset（引用 docs/research-notes/01-dsh-contracts.md 的结论核对）。
- [ ] **templates 表 additive 铁律**：写入后再改 schema 只能 `.default()` 新增；旧介质（无 templates 表）打开后播种路径；旧记忆/旧检索记录无新字段读回（Node 测试已计划，对抗轮复核）。
- [ ] **EXTRACTOR_VERSION 升级 0.4.0**：constants.py 与 src/index.ts 技能 extractorVersion 两处一致性（risk-review B 系列 F6 的实体化检查）。

## 4. 修复日志表

| 时间 | Bug/条目 | 发现方式 | 根因（文件:行） | 修复（文件:行） | 测试 | npm test | lead 简报 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 11-02 | B20 中文触发词永不匹配 | 代码审查 + 探测复现 | src/skill-format.ts:25 `\b(...当|如果...)\b` 对 CJK 无效 | 正则改 `\b(when\|if\|for\|mention\|ask\|say)\b\|当\|如果\|提到\|需要\|时`（与 templates.py:54 同款） | test/adversarial.test.mjs "skill guard accepts Chinese trigger phrasing..." | ✅ | ✅ |
| 11-02 | B21 工具名子串误分类（thread/bread→search） | 探测复现 | trajectory.py:10-22 `part in lowered` 子串匹配 | token 等值/前缀匹配（re.split 分词 + token==part/startswith + 全名 startswith）；check_scope 保留双 token 语义 | test_reasoning_memory.py AdversarialTests 2 项 | ✅ | ✅ |
| 11-02 | B24 Python 版本未校验 | 基线轮记录 | scripts/run-python-tests.mjs 仅探测可执行；插件无校验 | run-python-tests.mjs 加退出码门（0/3）；src/python.ts 新增 checkPythonVersion（spawnSync stdio:ignore 退出码探测，沙箱安全）；src/index.ts apply() 启动时校验并 error 日志 | test/adversarial.test.mjs "python version gate..." | ✅ | ✅ |
| 11-02 | B25 noProxy 子串匹配过宽 | 代码审查 | src/api-config.ts:124-132 `candidate.includes(normalized)` | 裸规则改全主机等值；`.domain`/`*.domain` 后缀语义保留；函数导出供测试 | test/adversarial.test.mjs "noProxy rules require whole-host equality..." | ✅ | ✅ |
| 11-02 | P13 播种失败不重试 | 代码审查 | src/index.ts:862-879 缓存已 settled 的失败 Promise | seedTemplates 拆出；catch 中重置 templatesSeeded=undefined，下次模板调用重试 | test/adversarial.test.mjs "seeding failure degrades readably and the next template call retries"（含空模板库 plan 形态断言） | ✅ | ✅ |
| 11-02 | P14 发布名描述/校验矛盾 | 探测发现后确认已被开发者修复 | templates.py 旧 `_KABAB_NAME` 纯 kebab | 开发者已修：templates.py:57 正则统一 `^memory_tpl_[a-z0-9]+(-[a-z0-9]+)*$`、挖掘名改前缀（templates.py:722）；本轮补 TS 侧一致性测试 | test/adversarial.test.mjs "publish name follows the description..." + 既有 test_reasoning_memory.py:1196 | ✅ | ✅ |
| 11-02 | P16 deprecated 后置过滤破坏配额契约 | 代码审查 | index.ts 旧 1495-1496 在配额后过滤；Python 无 includeDeprecated | retrieval.py 候选池在配额前剔除 + 返回 deprecatedFilteredCount（含两个 early-return 分支）；index.ts 透传并移除后置过滤；domain.ts retrievalRecordSchema 增 additive 字段 deprecatedFilteredCount | test_reasoning_memory.py AdversarialTests 3 项 + test/adversarial.test.mjs "deprecated filtering precedes the quota" | ✅ | ✅ |

修复后回归：prepare ✅ ｜ test:python **100/100** ✅ ｜ Node 直跑 **39/39** ✅（含新增 adversarial 5 项）｜ scripts/eval ✅（recall 0.5714/ndcg 0.4334/quota 1.0/selfRetrieval 0/classify 1.0/top1 1.0，与修复前一致）。

## 4.5 Python 核心只读预审记录（2026-11-02，task-8 落地后；待 task-13/14 落地复核）

| # | 状态 | 严重度(暂) | 发现 | 位置 | 说明 |
| --- | --- | --- | --- | --- | --- |
| P1 | 待验证 | 🟢 | task_family 命中 + 词法信号全零时置信度虚高：`_argmax` 全零时按词表顺序返回首模式（multi_hop_qa），若与 family_hit 一致则 agreement 计入该信号权重 → confidence 可到 0.9 | python/reasoning_memory_core/templates.py:92,158-165 | 需对抗样例确认；建议全零信号不算 agreement |
| P2 | 待验证 | 🟢 | mine_templates 对畸形 memory 的崩溃面：`int(memory.get("createdAt") or 0)` 遇非数字字符串 ValueError；provenance 非 dict 时 `.get` AttributeError → worker exit 2 | templates.py:631,737-739 | TS 侧受 schema 约束，仅桥级模糊输入触发；错误信息可读性验证 |
| P3 | 观察 | 🟢 | 挖掘幂等键用 representative 签名而非 spec §3.1 的 coreSignature | templates.py:707 | 同 core 不同代表签名时可能重复发射；与 spec 不一致，提请 lead 确认口径 |
| P4 | 观察 | 🟢 | `verify` 意图微调只有 +0.05 无减项，加权审计权重和 = 1.05（combined 有 min(1.0) 钳制，数值安全） | constants.py:255; retrieval.py:178-181 | 记录语义，T-debug-2 验证 combined 不越界 |
| P5 | 确认安全 | — | harmRatio 除零已防护（total_feedback>0 才除）；quality 下界 0.5 已实现 | templates.py:340,344 | lead 关注点之一，实现正确 |
| P6 | 确认 | — | Python 侧 `_TRIGGER_TOKENS` 已正确实现 CJK 独立分支（非 \b） | templates.py:54 | B20 修复剩余范围：TS skill-format.ts:25 + 待 task-13 落地的 TS 模板发布守卫 |
| P7 | 观察 | 🟢 | RRF 消融 overrides 钩子：仅含 context 的 override 在 RRF 下被静默忽略（固定名字循环不含 context）；六通道全零时 normalizer=0 提前返回、保留加权公式 combinedScore | retrieval.py:53-57,89-96 | eval 消融器需知晓该语义，避免误读消融结果 |
| P8 | 观察 | 🟢 | `builtin_templates` operation 未在 spec §3.3 列出（spec 说播种由 TS 首次使用触发） | service.py:62-63 | 无害扩展；与 TS 播种路径的关系待 task-13 核对 |
| P9 | 确认 | — | deprecated 标记贯穿 retrieval：results 行携带 `deprecated` 布尔；templateScore 三步计算（1.0 / 0.6×seq_sim / 0.0）正确；rank 审计无 templateRank（与 spec §2.3 一致，只加 templateScore） | retrieval.py:230-245,281,344-348 | |
| P10 | 待验证 | 🟢 | retrieval.py:114 的 `int(k)`/`float(ratio)` 强转对字符串输入仍可崩溃（v0.3 遗留 B6 面，模板通道未扩大该面） | retrieval.py:113-114 | 桥级模糊项，随 B6 一并验证 |
| P11 | 确认 | — | 蒸馏门控语义与 spec 一致：未提供 insightConfidence 继承请求级值（默认 0），门控开启时全拒并计 gateRejectedCount | distillation.py:104-115,181-188,241-243 | lead 关注点之二，实现正确 |
| P12 | 观察 | 🟢 | `_normalize_items` 的 signature 形参未使用 | distillation.py:59-66 | 纯 lint 级 |

### TS 层只读预审记录（2026-11-02，task-13 落地后）

| # | 状态 | 严重度(暂) | 发现 | 位置 | 说明 |
| --- | --- | --- | --- | --- | --- |
| P13 | 待验证 | 🟢 | 内置模板播种失败后不会真正重试：`templatesSeeded` 缓存的 Promise 内部吞掉异常后已 resolved，后续 plan/list/load 的 `ensureTemplatesSeeded()` 不再重跑，与注释“retried by the next call”不符 | src/index.ts:862-879 | 启动瞬间 python 不可用时模板层永久缺失（直到插件重启）；T-debug-2 验证 |
| P14 | 确认 | 🟡 | memory_publish_template 工具描述要求 “memory_tpl_ 前缀”（下划线），但 Python `_KABAB_NAME` 纯 kebab 校验会拒绝任何下划线名 → 按描述写的调用方必被拒；挖掘产物名用 `memory-tpl-mined-*`（连字符）可过 | src/index.ts:2252; templates.py:55,801-802 | 描述/校验矛盾，属文档口径问题；修复=统一命名契约（改描述或改校验，需 lead 定口径） |
| P15 | 观察 | 🟢 | memory_plan_task 硬编码 `hasGraph:false, graphNodeKinds:[]`，分类器 S4 图信号在 TS 工具路径不可达（Python API 支持） | src/index.ts:2114-2115 | ActiveTask 未收集图节点 kind；记入已知局限或后续扩展 |
| P16 | 观察 | 🟢 | include_deprecated=false 是在配额选定之后的可见性过滤：返回的 memories 数量可少于 k，而 balance.positive/negativeCount 仍按过滤前统计 → 返回计数与 balance 不一致 | src/index.ts:1495-1496,1554-1559 | 记录语义；对抗轮验证并评估是否把配额统计改为按 visible 计 |
| P17 | 观察 | 🟢 | 检索审计 fusionWeights 无 template 字段（zod 剥离未知键）；模板权重仅存于独立 templateChannelWeight 字段 | src/domain.ts:171-179; src/index.ts:1512 | 信息未全丢，记录 |
| P18 | 确认 | — | 新工具 output schema 全部对象根 `additionalProperties: false`；`type:'json'` 在 dsh-tools ValueSchemaSpec 支持面内（01-dsh-contracts.md:83） | src/index.ts:521-706 | lead 指定检查项，合规 |
| P19 | 确认 | — | builtin-store 新增 4 列 + SCHEMA_VERSION 版本戳重建（持久 dataDir 升级自动 DROP/重建，canonical domain 不受影响） | src/builtin-store.ts:180-187,215-218 | 升级路径处理正确 |
| P20 | 确认 | — | scanMemories 与 builtin-store 两源均回传新字段（strategyTemplateId/reasoningLevel/insightConfidence/status），memory_query 新过滤参数两源语义一致 | src/index.ts:1038-1074; src/builtin-store.ts:526-528,571-574 | |
| P21 | 确认 | — | skill extractorVersion 已同步 0.4.0，与 Python EXTRACTOR_VERSION 一致 | src/index.ts:2417; constants.py:14 | 原 B 系列 F6 检查通过 |
| P22 | 确认 | — | B20 修复范围收敛：Python 模板校验（templates.py:54）已正确实现 CJK 分支；剩余待修仅 TS skill-format.ts:25（memory_publish_skill 守卫路径） | src/skill-format.ts:25 | T-debug-2 预批准修复 B20 |

## 5. 执行纪律
1. 发现 bug：先记录到本文件 §4 再修。
2. 每次源码修改（src/、python/、test/）：`send_message` 简报 lead 并更新任务板。
3. 修复完成后：`npm test` 全绿（本沙箱用逐文件直跑 + `npm run prepare` + `npm run test:python` 等价验证）+ `scripts/eval` 可运行。
4. B20/B21/B24/B25 之外的新增源码修复需 lead 再次批准。

---

# T-rag-debug（task-24，v0.5.0 推理 RAG MVP）轮记录

- 开始：2026-11-04。基线：prepare ✅ ｜ test:python 123/123 ✅ ｜ Node 直跑 40/40 ✅（含 documents.test.mjs）。
- 读审范围：doc_chunking.py、doc_rag.py、service.py、constants.py（v0.5 增量）、document-domain.ts、document-index.ts、document-vector.ts、index.ts 文档工具段与输出 schema。

## 对抗探测结果（先记录后修）

| # | 状态 | 严重度 | 发现 | 位置 | 处置建议 |
| --- | --- | --- | --- | --- | --- |
| R1 | 确认 bug | 🟡 | mini-RRF 全空信号（lexical+vector 均无正分）时，pattern(=1.0 for general)+link(=1.0 hop0) 使每个块得满分 1.0：无匹配查询返回“满分块”，误导模型 | doc_rag.py:141-182（weighted 分支）+ 269-270 seeds | 修复：无任何文本信号（lexical/vector 全零）时返回空结果 + reason；TS note 已有 “No document chunk matched” 文案承接 |
| R2 | 确认偏离 | 🟡 | spec §3.2 “最终分 = 基础分 × 0.6^hop” 未字面实现：衰减由 link 通道（权重 0.10）承载；等分时 hop-1 仅比 hop-0 低约 6%（加权）/rank 级（RRF），hop-1 高分块可反超 hop-0 种子 | doc_rag.py:303-328 | 请 lead 裁决：a) 字面 ×0.6^hop（多跳基本出不了 top-k，与召回意图冲突）；b) 保持通道实现，spec/注释/审计描述对齐实际语义（推荐） |
| R3 | 确认缺陷 | 🟢 | “见第三章” vs 标题“第三章 详述”无法解析（section_index 只收 ASCII 数字前缀标题；中文章节号未注册） | doc_chunking.py:253-262（heading_index/section_index 构建） | 小修：标题 `第\s*([0-9一二三…]+)\s*章` 前缀注册进 section_index（数字键） |
| R4 | 确认 off-by-one | 🟢 | `content_truncated = len(content) >= MAX_DOC_CONTENT_CHARS`：恰好等于上限时报 truncated 不实 | doc_chunking.py:119-120 | 改 `>` |
| R5 | 观察 | 🟢 | 伪相关反馈在插件路径不可达（TS 工具无参数/无配置；Python 默认关） | service.py:95；index.ts:2807-2818 | 记录为已知局限（spec §3.2 “默认关”成立且恒关） |
| R6 | 观察 | 🟢 | 查询展开中“命中查询词的词表条目”追加是空操作（词已在查询中）；真正的泛化来自 TASK_FAMILY_PATTERNS 键 | doc_rag.py:73-86 | 记录；待标注集校准 |
| R7 | 观察 | 🟢 | 句边界含 `.`，超长段落切块时边界可能落在小数/版本号中间（内容不丢，仅切分位置） | doc_chunking.py:29 | 记录；MVP 可接受 |
| R8 | 观察 | 🟢 | retrieve_document 全语料（chunks+documents）随桥传输；上限 200 文档×2000 块时载荷可达数百 MB | index.ts:2849-2852 | 已由开发者注释承认；记录为规模风险 |
| R9 | 观察 | 🟢 | 文档检索审计 fusionWeights 硬编码 {lexical .45, vector .35}，不含 pattern/link（及 constrain 微调后的实际权重） | index.ts:2884 | 审计保真度不足；记录 |
| R10 | 观察 | 🟢 | 锚点归一化不一致：标题 “Code Style” → codestyle 而锚点 #code-style 保留连字符 → 不匹配（同形不同分隔符） | doc_chunking.py:38,256-259 | 记录；待校准 |
| ✅ | 验证通过 | — | 环安全（a↔b 仅 visited 一次）、visited/expand 上限、孤立块以种子身份参赛、pattern 软过滤 + patternFallback 审计（无 general 文档时返回空 + 可读 reason）、refs 跨块解析（锚点/§1.2）、constrain 意图微调、伪相关默认关、解耦零回归（记忆/模板/企业规则 123 项 Python 测试全绿）、旧审计 additive 读回（templates.test legacyAudit） | — | — |

## 待 lead 裁决
- R2（衰减语义：字面实现 vs 通道实现+文档对齐——推荐后者）。
- 批准后实施 R1/R3/R4（小改）+ R2 按裁决执行；R5-R10 只记录。

## 裁决与修复执行（2026-11-04）

| 条目 | 裁决 | 执行 |
| --- | --- | --- |
| R1 | ✅ 批准修复 | doc_rag.py：pattern/link 通道计算后新增文本信号门——lexical+vector 全零时返回空结果（reason “no chunk matched the query text…”），audit seeds=0/visited=0。测试：DocumentAdversarialTests 3 项（零信号空结果×2 + 正信号正常检索）+ adversarial.test.mjs “document retrieval with zero text signal…”（TS 集成：documents [] + note “No document chunk matched” + 审计行 route='document' 可解析） |
| R2 | ✅ 采纳通道实现（lead 同步 spec/设计文档措辞） | 不改代码；测试冻结语义：weighted 等分时种子胜过 hop-1；RRF 模式向量强的 hop-1 块可反超种子且 breakdown.link=0.6 可审计（2 项） |
| R3 | ✅ 批准修复 | doc_chunking.py：新增 `_CHAPTER_HEADING_RE`，标题“第X章”（含中文数字）注册进 section_index 数字键 → “见第三章”解析成功。测试：test_chinese_chapter_heading_resolves_chapter_reference |
| R4 | ✅ 批准修复 | doc_chunking.py：`content_truncated = original_length > MAX_DOC_CONTENT_CHARS`（原 >= 在恰好等于上限时误报）。测试：test_content_truncation_flag_is_strict（恰好 200000 不报、200001 报且 charCount=200000） |
| R5–R10 | 📝 只记录 | 已记入上表观察项，未改代码 |

**回归**：prepare ✅ ｜ test:python **131/131** ✅（新增 DocumentAdversarialTests 8 项）｜ Node 直跑 8 文件全绿（adversarial 6/6、documents、smoke 等）｜ scripts/eval ✅ 指标不变（recall 0.5714 / ndcg 0.4334 / quota 1.0 / selfRetrieval 0 / classify 1.0 / templateTop1 1.0）。解耦零回归确认：记忆/模板/企业规则路径未改一行（retrieval.py、templates.py、enterprise-rules-rag.ts 本轮零改动），domain 仅 retrievalRecordSchema additive 字段（developer 在 task-22 已加）。

---

# T-causal-debug（task-31，v0.6.0 因果效用引擎）轮记录

- 开始：2026-11-06。基线：prepare ✅（0.6.0）｜ test:python 145/145 ✅ ｜ Node 直跑 9 文件全绿 ✅ ｜ scripts/eval 指标与 v0.5.0 持平（recall 0.5714 / ndcg 0.4334 / quota 1.0 / selfRetrieval 0 / classify 1.0 / templateTop1 1.0）。
- 读审范围：causal_utils.py、service.py（causal/classify operation）、retrieval.py 因果接入、causal-domain.ts、domain.ts additive 审计字段、index.ts（写事件/FIFO/save 派生/record_counterfactual/memory_query include_causal）。

## 对抗探测结论：估计量全部边界通过，无代码缺陷

| 对抗面 | 结果 | 验证方式 |
| --- | --- | --- |
| 每臂 4/5/6 门槛 | ✅ 4 剔除（droppedStrata + reason sample gate）、5/6 合格 | 探测 + 本轮新增精确断言（开发者已测 4 侧） |
| n_eff 19/20/21 | ✅ 19 回退 Beta、20/21 应用（causalApplied 精确翻转） | 探测 + 新增测试 |
| 收缩封顶 ±0.20 | ✅ 极端效应处 causalMultiplier 恰好 1.2 / 0.8；shrunk 单调于 n_eff | 探测 + 新增测试 |
| 配对缺臂/奇数臂 | ✅ 缺臂 pairedPairs=0/pairedAte=0；重复臂 setdefault first-wins 确定性 | 探测 + 新增测试 |
| IPTW 极端倾向/除零 | ✅ 1/199 裁剪计数≥1、disagreement 标记；全处理组（无对照）weight_sum=0 → None→0.0，无除零 | 探测 + 开发者既有测试 |
| Laplace 零格 | ✅ 开发者已测（zero cells 不崩、CI 有序） | 既有 |
| 辛普森检出/误报 0 | ✅ 构造检出、无结构不误报（双侧显著门槛） | 开发者既有 + 探测复核 |
| ATE 恢复与 CI 覆盖 | ✅ 10 种子合成结构 CI 覆盖 ≥90% | 开发者既有 |
| 确定性双跑逐字节一致 | ✅ json sort_keys 全等 | 开发者既有 |
| causalApplied=false 零回归 | ✅ Beta 乘数逐值相等（retrieval 路径）；无事件键时走 v0.5.0 路径 | 开发者既有 + 探测 |
| FIFO 裁剪边界 | ✅ 210 → 200，最旧剔除、createdAt 有序 | 开发者既有 |
| save_outcome 派生事件重入幂等 | ✅ 同任务二次 save 不再派生（causalEventsRecorded 门），首记 outcome 胜出 | **本轮新增** adversarial.test.mjs |
| 记忆/模板/文档/企业规则零回归 | ✅ 全量 145 项 Python + 9 文件 Node 全绿 + eval 持平 | 全量回归 |

## 观察项（记录不修，供后续迭代）
- C-O1 🟢：save_outcome 派生观察性事件的 stepIndex/retrievalIntent 硬编码 0/''——spec 把二者列为混杂元数据，但 TS 未从活动任务状态填充（与检索步骤对齐参数脱节）。index.ts:2223-2224。
- C-O2 🟢：memory_record_counterfactual 在无活动任务时无幂等防护（counterfactualRecorded 仅活动任务内生效），同一 (pair,arm) 可重复落库；配对估计 setdefault first-wins 使估计不受污染，仅占 FIFO 配额。index.ts:2324。
- C-O3 🟢→✅ 已修：无任务时 pairId 默认 `standalone:{memoryId}` 全局稳定 → 两次独立 standalone 调用（一 with 一 without）会被视为同一对。lead 裁决小修：**standalone 模式必须显式 pair_id**，缺失拒绝并返回可读错误；活动任务内保留默认（任务+记忆幂等）。index.ts（memory_record_counterfactual execute + 工具描述 + pair_id 参数描述）。测试：adversarial.test.mjs “standalone counterfactual arms require an explicit pair_id”（缺 pair_id 拒绝且零落库、显式 pair_id 接受、不同 pair_id 不误配）。
- C-O4 🟢：派生事件 eventId 同毫秒同参数时 put 覆盖（幂等无害）；FIFO 排序 createdAt+eventId 双键确定性良好。index.ts:2216。

## 新增测试
- test_reasoning_memory.py `CausalBoundaryTests` 7 项（臂门槛 5/6+4、n_eff 20/21、封顶极值、shrunk 单调、配对缺臂/重复臂、全处理组保守）。
- test/adversarial.test.mjs 新增 “save_outcome derives causal events once per task”（重入幂等 + 首记 outcome 胜出）。

## 结论
因果引擎 MVP 经受住全部对抗面；估计量与接线零缺陷。唯一修复为 lead 裁决的 C-O3（standalone 配对防护）。最终回归：prepare ✅ ｜ test:python **151/151** ✅（新增 CausalBoundaryTests 6 项）｜ Node 直跑 9 文件全绿（adversarial 8/8：CausalBoundary 相关 + save 重入幂等 + standalone pair 防护）｜ scripts/eval ✅ 指标与 v0.5.0 持平。
