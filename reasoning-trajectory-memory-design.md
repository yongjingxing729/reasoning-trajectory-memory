# 推理轨迹记忆系统（Reasoning Trajectory Memory）设计文档

- 日期：2026-10-27
- 状态：合并版（研究设计 + DeepSeek Harness 插件落地）
- 团队：5 人研究生团队，开源项目
- 目标宿主：DeepSeek Harness（DSH）

---

## 1. 项目概述

### 1.1 背景与动机

Agent 宿主通常不会把跨任务经验自动整理为可复用记忆；单次模型调用是否保留推理状态取决于模型 API、宿主上下文和会话管理，不能一概视为任务结束即丢弃：

- 成功任务中可能有价值的策略、工具动作和验证方式没有结构化复用；
- 失败任务的教训无法累积，同类错误反复出现。

本项目构建**面向 Agent 任务流的经验记忆系统**：记录可观察的模型调用、工具交互和任务结果；在模型接口提供时保存模型推理文本；再把经验证的经验提炼为结构化记忆，用于后续任务。

> 项目不承诺观察模型内部推理，也不把增加思维文本长度等同于"保证深度思考"。"提高端到端模型的深度推理能力"是本项目的长期研究愿景，但**本次交付的诚实目标是：一个可复现的实验平台，用于检验"结构化的推理记忆能否带来可测的增量收益"这一假设**。

### 1.2 项目定位

**工程交付 + 单一主研究问题**：

- **工程交付**：一个 DeepSeek Harness 原生插件（下文简称"本插件"），先实现可复现的端到端实验平台；
- **主研究问题**：任务流中，推理结构检索能否在固定模型、工具和推理预算下，提高结构相近而表面语义不同的留出任务成功率；
- **支撑研究**：思考预算控制、记忆提取和可靠性估计先作为实验条件或消融，暂不作为三条并列的独立贡献。主问题得到稳定结果后，再决定是否拆分为独立研究线。

### 1.3 关键决策记录

| 决策点 | 结论（合并版更新） | 依据 |
|---|---|---|
| 运行形态 | **作为 DeepSeek Harness 的原生插件（Cordis 插件包）**，非自研应用 | DSH 本身就是插件系统，可直接复用其会话、LLM、工具与存储能力 |
| 形态选择 | **DSH 原生插件统一原"网关 + MCP Server"两个通道的职责** | 见 1.5；DSH 已内置协议适配与事件捕获，插件只需注册工具 |
| 任务域 | 分阶段双域：Agent/编程任务先行，数学/逻辑基准补充 | 工程落地与研究评测分阶段 |
| 推理控制 | 按模型适配器控制可用的 reasoning effort / thinking mode / token 上限；不承诺跨 API 强制产生或公开思维文本 | 模型接口与宿主行为不同，先测量能力边界 |
| 研究范围 | 推理结构检索为主线；思考预算与记忆提取作为受控条件/消融 | 先确保主假设可识别，再扩展论文问题 |
| 评测策略 | 固定模型、工具、提示词和预算；比较无记忆、语义检索、策略记忆、结构检索 | 任务成功率为主指标，成本、失败类型与记忆误用为并列指标 |

### 1.4 目标宿主：DeepSeek Harness 及其插件架构

DeepSeek Harness（DSH，`@deepseek-ai/dsh-desktop-runtime`）是一个基于 **Cordis 插件系统**
（`@deepseek-ai/cordis`，Koishi 框架的分支）构建的 Agent 宿主。每个能力都是一个
`@deepseek-ai/dsh-*` 包，导出四要素：

```ts
export const name = "tool-ask-user";        // 插件名（kebab-case）
export const inject = ["tools", "userQuestions"]; // 要注入到 ctx 的服务
export const Config = z.object({ ... });    // Schemastery 配置 schema
export function apply(ctx, config) { ... }  // 插件入口：注册服务/工具/skill
```

本插件直接复用 DSH 现成的四个能力，而不自建一套宿主：

| 本插件需要的能力 | DSH 现有组件 |
|---|---|
| 捕获模型调用 / 工具事件（等价原"网关"） | `dsh-session` 的有序事件日志（`session/event` 事件流） |
| 控制推理预算 / 推理配置（M4 落点） | `dsh-llm` 的适配器与 `reasoningEffort`（`ConfigureCall`） |
| 面向模型的记忆工具（等价原"MCP Server"） | `dsh-tools` 的 `defineTool` |
| 记忆持久化（等价原"核心库"存储） | `dsh-storage`（JSON / SQLite 后端） |
| 会话历史读取（评测期基线/离线对照） | `dsh-session-query` / `dsh-tool-session-query` |

### 1.5 形态决策：为什么是 DSH 原生插件，而非"网关 + MCP"

原设计（面向 Trae/Cline 等通用宿主）采用"MCP Server + 网关"混合双通道，理由是通用宿主不暴露模型 API 参数、也不保证暴露 reasoning 字段。**目标改为 DSH 后，这些理由发生根本变化**：

- **DSH 已内置"网关"职责**：`dsh-llm` 适配器负责协议适配与调用分发，`dsh-session` 负责把请求/响应/工具事件有序落盘。捕获层无需另建网关，只需订阅会话事件流。
- **DSH 的工具即"MCP"职责**：插件用 `ctx.tools.register(defineTool(...))` 注册面向模型的工具，承担原 MCP 的 `begin_task / retrieve_memory / save_outcome / end_task` 职责。
- **原生插件避免了双通道的整合成本**：不需要跨进程协议、不需要把 `task_id` 通过 header 传到另一个服务；会话事件、工具调用和记忆存储同处一个进程，关联更可靠。

因此合并版**否决**独立网关与独立 MCP Server，改为**一个原生插件**统一捕获、检索与回写。这同时继承了原设计的两条否决理由（防盲注、防闭环缺角）：

- 隐藏式逐轮注入（原"网关逐轮注入"）仍作为后续独立实验开关，MVP 关闭，避免与显式检索混淆；
- 任务终态仍需宿主/用户/评测器显式提交（`memory_save_outcome`），不从模型回答可靠推断。

**边界（沿用）**：推理模型没有一个可从 API 独立调用的"推理子模块"。推理能力由模型权重、训练/后训练、推理时计算策略、上下文和工具交互共同决定；插件只能调节适配器支持的推理配置，不能观察或保证模型内部过程。项目记录的 `reasoning_content` / `reasoning` 应称为"模型提供的推理记录"，不能作为真实内部决策的完整证据。

### 1.6 主研究假设与边界

**主假设 H1**：在任务流中，经过验证的推理结构表示可补充任务语义相似度；相较无记忆、语义检索和策略记忆基线，结构检索能提升"结构相似、表面语义不同"的留出任务成功率，同时不超出预设的 token、延迟和失败率预算。

**必要负控制**：加入"语义相似但推理结构不同"的任务，检验结构路是否只是重复语义路；按任务家族/仓库/题目模板切分训练与留出集，避免近重复泄漏。

**新颖性边界**：从成功和失败经验提炼策略、检索并回写已被 ReasoningBank 等工作系统研究；因果图/结构化记忆也已有长期 Agent 工作。项目的潜在贡献应集中于可操作、可复核的推理结构表示及其在受控任务流上的增量效应，不能仅以"推理记忆"或"三路索引"作为新颖性主张。

### 1.7 现有 DSH 记忆插件与本项目定位

DSH 插件生态已有多个面向"记忆"的开源插件。它们与本项目是**互补**而非竞争：现有插件主要解决"记住什么"（内容记忆），本项目解决"如何推理、如何操作"（结构记忆）。

| 插件 | 特点 | 局限（相对本项目） |
|---|---|---|
| `dsh-memory-evolve` | 五轨记忆、技能自我进化、Git 分支感知，沉淀项目决策与踩坑经验 | 偏长期项目记忆，不是专门围绕工具执行因果链 |
| `graph-memory` | 从对话提取 SPO 三元组，用知识图谱做上下文压缩与跨会话召回 | 偏"记住了什么事实"，对工具反馈、错误修正的显式建模有限 |
| `dsh-meow-memory` | 七层 SQLite 记忆（fact/lesson/rules 等），支持空闲时整理 | 记忆结构偏用户/项目/事实，推理轨迹建模不算核心 |
| `dsh-project-memory` | "读到即索引"，把模型读过的文件沉淀为带 path:line 引用的记忆 | 偏项目上下文，不直接捕获工具执行结果 |
| `dsh-mnemon` / `dsh-memento` | 分层记忆、可审计写入、用户画像沉淀 | 更像长期记忆控制平面 |

**共同缺口（本项目切入点）**：上述插件都围绕"内容记忆"（事实、文件、决策、画像），**没有一个显式建模"工具执行因果链 / 推理结构"**——即"这一连串工具调用与验证如何导向成功结果"。这恰是本项目主研究线（M3 结构检索）要填补的位置：从 `tool_call → tool_result → 后续动作 → 验证` 的事件序列抽象有限标签签名，用于"结构相似、语义不同"任务的跨任务迁移。

这与 §1.6 的新颖性边界对齐：本项目不重复"策略记忆/三路索引"这类已被 ReasoningBank 与上述插件覆盖的贡献；可辩护的新增是"可操作、可复核的推理结构表示及其在受控任务流上的增量效应"。

> 说明：上述插件为第三方社区插件，其确切 API 与数据格式需在集成前逐一核对；本表依据已有调研，不代表已完成接口对接。

---

## 2. 总体架构

### 2.1 架构（DSH 插件视角）

```
DeepSeek Harness 宿主（已存在，本插件不修改）
├─ dsh-session：有序事件日志（turn/step/assistant/tool 等，reasoning 文本可缺失）
├─ dsh-llm / dsh-llm-deepseek：模型适配器、reasoningEffort、usage
├─ dsh-tools：工具注册表
└─ dsh-storage / dsh-storage-json / dsh-storage-domain：持久化后端与类型化数据域

本插件 dsh-reasoning-memory（新增 DSH bundle）
├─ 捕获层：订阅 session/event → 投影为 TrajectoryEvent → 按 task_id 关联
├─ 记忆库：MemoryItem 存储（语义路 + 结构路双索引）+ 检索日志
├─ 检索层：语义路 + 结构路 → 固定融合 → Top-k 摘要
├─ Python 推理核心：outcome → 推理策略/结构蒸馏 + 双路检索 + 正反配比
└─ 工具层：memory_begin_task / memory_retrieve / memory_save_outcome / memory_end_task
```

MVP 只启用"捕获 + 按需检索"；隐藏式逐轮注入、自动重试、在线 bandit 暂不启用。

### 2.2 架构映射：研究概念 → DSH 组件

| 研究设计概念 | DSH 落点 | 说明 |
|---|---|---|
| 网关层（捕获请求/响应/工具事件） | `dsh-session` 的 `session/event` 事件流 + `dsh-llm` 适配器字段 | 事件顺序是首要观测对象；reasoning 文本是可缺失字段 |
| TrajectoryEvent | `SessionEvent`（信封 `{ seq, type, data }`） | 本插件按类型投影为记忆侧事件 |
| MCP Server（任务生命周期/检索/回写） | 本插件注册的 4 个工具 | `memory_begin_task` / `memory_retrieve` / `memory_save_outcome` / `memory_end_task` |
| 核心逻辑（提取/检索） | 随插件 bundle 分发的 Python 核心，由 TypeScript 适配层调用；持久化通过 `ctx.storageDomain` | 单请求 JSON stdin/stdout 协议；不启动独立服务 |
| 语义路（必备基线） | 任务描述/目标的文本相似度 | MVP 用本地可复现相似度（见 §7） |
| 结构路（主研究） | 从工具调用/验证事件抽象"有限标签集 + 步骤序列签名" | 标签集：分解/约束/搜索/执行/验证/回退 |
| 失败模式路 | 后续扩展 | MVP 不做 |
| 推理预算（M4，支撑） | `dsh-llm` 的 `ConfigureCall` 设置 `reasoningEffort`/`maxTokens` | 独立实验开关，默认关闭 |
| 记忆提取（M5，支撑） | `memory_save_outcome` → 蒸馏为 MemoryItem | 固定提取法，先服务主假设 |

### 2.3 模块职责

| 模块 | 职责 | 关键依据/边界 |
|---|---|---|
| 捕获层 | 订阅会话事件并投影为轨迹事件；显式 `task_id` 关联；reasoning 文本可缺失，记录 `capture_status` | 主研究基于可观察操作/验证事件，不以私有 CoT 可见为前提 |
| 记忆库 | MemoryItem 增删查、双路索引、检索日志、离线评测接口 | 研究主线直接调用同一实现，减少工程版与实验版偏差 |
| 检索层 | 语义路与结构路独立召回 + 固定融合 + Top-k | 保留每路候选、分数、排序与注入记录（供消融） |
| 蒸馏层 | 任务终态 + 验证证据 → 策略/结构/失败教训条目 | 域内验证器证据作门控；失败不可归因时保留 unknown |
| 工具层 | 4 个面向模型的工具 | 记忆作为带来源的数据块提供，不升级为指令 |

### 2.4 关联机制（轨迹 ↔ 任务配对）

`instance_id` 只用于租户/用户隔离，不能充当任务 ID。`memory_begin_task` 生成 `task_id`；同一进程内，工具调用与后续会话事件都通过会话 id 关联到该任务。由 `task_id → session_id → 事件 seq` 建立确定性关系。

并发任务、重试和多 Agent 子任务在 M1 实测；若一个会话内出现多个并行任务，优先用显式 `task_id`，缺失时以时间窗与 prompt 嵌入作为低置信回退，并记录 `link_confidence` 与 `link_method`，低置信匹配不进入训练/研究数据。

---

## 3. 数据模型

### 3.1 核心实体

**AgentEpisode（Agent 任务）**——一项跨模型调用和工具交互的任务范围：

```
task_id, session_id, instance_id, started_at, ended_at,
agent_version, model/prompt/tool configuration, outcome
```

**TrajectoryEvent（轨迹事件）**——从 DSH 会话事件投影出的最小集合，事件顺序是首要观测对象：

```ts
interface TrajectoryEvent {
  eventId: string;       // 由 session id + seq + type 派生
  taskId: string;        // 显式关联，缺失时回退 sessionId
  seq: number;           // session 内顺序
  type: "model_message" | "tool_call" | "tool_result" | "verification" | "task_meta";
  reasoningText?: string;          // 模型接口提供时才存在
  reasoningCaptureStatus: "provided" | "not-provided" | "not-forwarded" | "parse-failed" | "captured";
  toolName?: string;
  toolArgsSummary?: string;        // 最小化保存，不含敏感数据
  outcome?: "success" | "fail" | "partial" | "unknown";
  usage?: { inputTokens: number; outputTokens: number; reasoningTokens?: number };
  ts: number;
}
```

原始推理文本、提示词和工具输出可能含敏感数据：默认最小化保存；支持本地优先、脱敏、保留期、导出与删除。`reasoningCaptureStatus` 区分"模型未提供/宿主未转发/解析失败/成功捕获"。

**TaskRecord（任务）**——任务语义记录：

```
task_id, task_desc, goal, task_family, split(train/dev/heldout),
retrieval_event_ids, outcome(success/fail/partial/unknown),
verifier_type, verifier_evidence, outcome_confidence,
linked_request_ids, agent/model/prompt/tool versions
```

**MemoryItem（记忆条目）**——质量评估后的蒸馏产物：

```ts
interface MemoryItem {
  memoryId: string;
  type: "strategy" | "structure" | "failure"; // 策略/结构/失败教训
  taskScope: string;            // task_family / 任务域
  preconditions: string[];      // 适用前提（显式列出，防误用）
  content: string;              // 蒸馏后的可复用内容（带来源、前提、验证方式）
  provenance: { taskId: string; eventIds: string[]; verifier: string };
  evidence: string;             // 验证证据摘要（可执行测试/环境状态/数学验证）
  confidence: number;           // [0,1]
  trustLevel: "verified" | "unverified" | "unknown";
  extractorVersion: string;
  createdAt: number;
  updatedAt: number;
  semanticText: string;         // 语义路索引文本（任务描述/目标）
  structureSignature: string[]; // 结构路索引（有限标签序列）
}
```

**关键边界**：结果正确 ≠ 推理忠实；`trustLevel` 只有在可执行测试/环境状态/数学验证器给出域内证据后才升为 `verified`；`type:"failure"` 仅在可归因时写入，否则保留 `unknown` 不强行提炼避坑卡。

**RetrievalEvent / ReuseOutcome**——记录候选记忆、召回与排序分数、最终注入内容/位置、是否被 Agent 使用、任务验证结果和预算。只累计"复用成功次数"不足以估计记忆贡献；离线评测先用随机/配对对照，线上可靠性仅在曝光和结果可追溯后估计，并按任务族及模型版本分层。

**关联关系**：AgentEpisode/Event ↔ TaskRecord 优先由显式 ID 关联；时间窗和嵌入仅作低置信回退，不可作为主要事实来源。

### 3.2 DSH 事件 → 轨迹事件映射

| DSH 会话事件 | 投影类型 |
|---|---|
| `assistant/message` / `assistant/attempt` | `model_message`（含可选 `reasoningText`） |
| `tool/call` | `tool_call` |
| `tool/result` | `tool_result` |
| `turn/start` / `turn/end` / `step/start` / `step/end` | `task_meta`（边界标记） |
| `goal/change` / `todo/write` / `working-directory/change` | `task_meta`（可选补充） |
| （本插件自定义）`reasoning-memory/outcome` | `verification` |

---

## 4. 记忆生命周期（闭环）

```
捕获(订阅会话事件 → 投影轨迹；reasoning 文本可缺失)
  → 关联(显式 task_id；任务结束由 memory_save_outcome 提交 outcome)
  → 评估(①按任务域验证终态与证据
          ②失败分类并保留未知/不确定
          ③蒸馏为可复用策略/结构/失败教训，附来源和适用前提)
  → 入库(任务语义/推理结构两路为 MVP；失败模式路后续扩展)
  → 检索(新任务 → 推理 RAG 混合检索)
  → 注入(分层注入，见 §8)
  → 更新(记录每次曝光及验证结果；有对照数据后再更新可靠性估计)
```

**三个内建的理论风险防御**：

- **结果与过程分别记录**：结果正确不能证明推理记录忠实或策略可迁移；过程评分模型也不能跨领域充当通用真值。以可执行测试、环境状态、数学验证器等领域证据作主要门控，PRM/LLM-as-judge 仅在对应领域校准并报告不确定度后作为辅助信号。
- **失败归因允许未知**：错误归因（把知识缺失记成策略错误）会造成负迁移。不能可靠归因时保留原始事件和未知标签，不强行提炼避坑卡。
- **记忆具备来源与信任边界**：不可信网页、仓库或工具输出可被用于污染长期记忆；新记忆记录来源、适用范围、验证证据和信任等级，检索内容按数据处理，不升级为指令。

---

## 5. 运行时：经验记忆如何参与 Agent 任务

目标运行时包含以下作用点。**MVP 先实现 ①、③、④；②须作为独立实验开关**：

| 时机 | 通道 | 作用的记忆 | 对当前任务的效果 |
|---|---|---|---|
| ① 任务开始 | `memory_retrieve` 显式检索（Agent 可见） | 经验证的策略/结构记忆 | 给当前任务提供可检查的经验；记忆带来源、适用前提和信任等级 |
| ② 每轮请求（后续实验） | 网关注入（可配置） | 摘要或负向经验 | 研究逐轮注入的收益、上下文成本和副作用；MVP 关闭 |
| ③ 执行中途 | `memory_retrieve` 按需检索 | 当前子问题相关记忆 | 记录触发原因、候选项和实际注入项，检验迭代检索相对首轮检索的增量 |
| ④ 收尾 | `memory_save_outcome` / `memory_end_task` | 验证证据和记忆回写 | 记录结果、验证器和置信度；曝光结果留给对照评测后更新记忆估计 |

隐藏式逐轮注入可能改变模型行为，却不让 Agent/用户清楚看到输入，且会与显式检索内容重复。只有在可审计的消融实验中启用，并记录实际注入内容、位置和 token 数。记忆作为带来源的数据块提供，不覆盖当前任务指令，不自动将未验证记忆提升为事实。

---

## 6. 主研究线：推理结构检索（M3）

### 6.1 研究假设与可证伪条件

研究假设是：结构特征在任务语义相似度之外提供可测的增量信号。**不能预设结构一定更有迁移价值**；需要用匹配样例和负控制验证。第一阶段聚焦编程/Agent 任务。若保留跨域结论，只选择一个窄的第二任务族并报告域间变化，不以多个宽泛基准的混合结果代替结构迁移证据。

### 6.2 检索表示与后续扩展

| 索引路 | 索引对象 | 解决什么 |
|---|---|---|
| 语义路 | 任务描述/目标的向量嵌入 | 必备基线 |
| 结构路 | 从可观察的 Agent 操作、工具调用、环境反馈和验证事件抽象有限状态/步骤图；模型提供的 reasoning 文本可作可选补充 | 检验结构相近、语义表面不同的任务是否有增量收益 |
| 失败模式路 | 经证据支持的失败类别；允许 `unknown`，不把 LLM 自述归因当真值 | 后续扩展，不作为 MVP 的必要研究贡献 |

结构表示须先定义有限标签集（分解、约束、搜索、执行、验证、回退）、标注说明和边关系；抽样由人工复核并报告标注一致性。自由 CoT 自动解析出的 DAG 只能视为预测标签，不能直接当作客观过程记录。

### 6.3 检索管线

```
查询构造（当前任务描述 + 可观测任务状态）
  → 语义路与结构路独立召回
  → 固定融合策略与排序日志
  → 可选多样性筛选
  → 分层组装
```

MVP 固定融合方式，保留每路候选、分数、排序与注入记录。只有完成随机/配对离线对照、确认奖励和曝光可识别后，才研究在线 bandit；单独观察"注入后成功"不足以判断记忆带来因果增益。

---

## 7. 检索机制（语义路 + 结构路 + 固定融合）

```
查询构造（当前任务描述 + 可观测任务状态）
  → 语义路独立召回（semanticText 相似度）
  → 结构路独立召回（structureSignature 相似度）
  → 固定融合 score = wSem * semScore + wStruct * structScore
  → 可选多样性筛选
  → Top-k 记忆摘要（带来源/前提/信任等级）返回给模型
```

- **语义路**：文本相似度。MVP 用确定性、可复现的 token 重叠 + 规范化子串（无需外部向量库，保证开源用户零部署）；预留 `embedding` 接口，后续可插 `sqlite-vec` 或远程嵌入。
- **结构路（主研究贡献）**：从轨迹抽象**有限标签集** `{decompose, constrain, search, execute, verify, rollback}`（分解/约束/搜索/执行/验证/回退）的动作序列，作为 `structureSignature`。召回用**顺序感知的标签序列相似度**（如带顺序惩罚的 bag-of-labels / 最长公共子序列比例）。
- **正反配比**：默认检索成功:失败为 `1:1`，由 `positiveNegativeRatio` 配置；一侧候选不足时按固定融合分数从另一侧补足并记录 `fallbackUsed`。`partial` 与 `unknown` 不进入正反池。
- **负控制**：结构路分数与语义路分数分列记录，支持"语义相似但结构不同"的配对检验。

---

## 8. 工具设计（面向模型）

全部用 `ctx.tools.register(defineTool({...}))` 注册，模型可见、可审计。

| 工具 | 作用 | 关键参数 / 返回 |
|---|---|---|
| `memory_begin_task` | 开始一个任务，生成 `task_id`，把后续事件关联到该任务 | `task_desc`、`goal`、`task_family` → `task_id` |
| `memory_retrieve` | 按需检索记忆（Agent 显式调用） | `query`、`k`、`route`(`auto/semantic/structure`) → Top-k 记忆摘要 |
| `memory_save_outcome` | 任务结束回写结果 + 验证证据，触发蒸馏 | `outcome`、`verifier`、`evidence`；成功时需 `reasoning_insight`，失败时仅在可归因时需 `failure_mode` → 记忆条目 id（或"未入库+原因"） |
| `memory_end_task` | 结束任务，触发该任务的离线/增量蒸馏与清理 | `task_id` → 摘要 |

### 8.1 分层注入协议

| 层 | 内容 | 通道 | 频次 |
|---|---|---|---|
| MVP | Top-k 记忆摘要及来源/前提/信任等级 | `memory_retrieve` 返回，Agent 可见 | 按需首轮检索 |
| 后续 A/B | 每轮摘要注入 | 网关可配置 | 单独实验，记录额外 token 和延迟 |
| 后续 A/B | 结构描述或记忆全文 | `memory_retrieve` 返回 | 子任务需要时按需检索 |

**防干扰设计**：注入内容带来源、适用前提、验证方式、信任等级和时间；用明确边界标记作为历史数据呈现。不要仅凭 reliability 数字暗示事实可靠。

---

## 9. 支撑模块：推理预算与验证（M4，暂不作为主贡献）

本模块研究可控的推理配置与预算分配，不宣称检测到"浅层思考"就等于测到了推理质量。不同 API 可能只提供 effort、token 上限或 think/no-think 开关；支持情况以适配器实测为准。

```
配置（适配器支持时设置 reasoning effort / thinking mode / 输出预算）
  → 记录（请求参数、reasoning token usage、上下文长度、工具事件、成本和延迟）
  → 评测（固定预算与自适应预算比较任务成功率、错误率、成本/成功任务）
  → 可选约束（预算耗尽时处理 incomplete；限制重试次数并记录额外成本）
```

**落点**：本插件默认不触碰模型请求。M4 作为独立实验开关接入 `dsh-llm` 的 `prepareCall(config, signal, configure?)`：

```ts
// 后续实验：注册一个 ConfigureCall，只设置适配器声明支持的推理配置
const effortPolicy: ConfigureCall = (controls, model) => {
  const first = model.reasoning?.efforts[0];
  return first === undefined ? controls : { ...controls, reasoningEffort: first.id };
};
```

Reasoning effort / thinking mode 是推理配置，不是可观测到的"深度保证"。思考 token 更多未必更好；对 DeepSeek-R1 的分析报告了推理时间的甜点区，额外计算可能降低表现。[DeepSeek-R1 Thoughtology](https://arxiv.org/abs/2504.07128)

PRM 只在校准过的领域辅助评估；代码任务优先用执行测试与环境证据，数学任务用可验证结果。PRM 不单独决定跨域记忆入库。

**指标体系**：

- **主指标**：任务成功率差及置信区间；按任务族/仓库/模板分组的迁移表现；
- **资源指标**：每个成功任务的总 token、推理 token（若 API 暴露）、调用次数、延迟和成本；
- **安全/质量指标**：失败类型、错误记忆命中率、记忆污染率、验证器误判率；
- 思考/回答比例、回溯次数和 PRM 分数仅作探索性相关指标，不作为"深度不退化"的验收真值。

---

## 10. 支撑模块：记忆提取（M5，暂不作为主贡献）

研究对象 = 生命周期中「评估 → 蒸馏 → 入库」环节。ReasoningBank 已研究从自评成功与失败经验中提炼策略记忆，故 M5 先服务于主假设：提供固定、可比较的记忆构造方法，避免与结构检索贡献混淆。第一轮只比较少数预先定义的提取条件：原始轨迹、朴素摘要、带外部验证证据的策略/结构记忆。

| 维度 | 第一阶段方案 | 后续问题 |
|---|---|---|
| 提取方法 | 固定同一提取模型/提示词；比较原始轨迹、摘要、带验证证据的结构化条目 | 成败对比提取能否带来主指标增益？ |
| 粒度 | 保留任务前提、操作结构、验证证据和来源 | 实例级/模式级/原理级何时适用？ |
| 时机 | MVP 任务完成后离线提取（或 `memory_end_task` 触发增量提取） | 在线粗提取与批量巩固的收益/延迟权衡？ |
| 选择性 | 先全量记录事件，限制研究评测中的提取成本并报告成本曲线 | 新颖性/难度/可验证性选择器是否优于全量？ |

暂缓把全部提取维度组合成全因子实验。先固定检索算法比较记忆构造方式，再固定记忆构造方式比较结构检索；避免同时改变"记忆写入"和"记忆读取"导致结论不可归因。

---

## 11. 评测体系

**关键设计**：评测同时覆盖单任务基线和任务流累积收益。记忆系统的核心增益需要任务流；但到 2026 年已有 MemoryAgentBench、AMA-Bench、Mem2ActBench 等长程记忆/Agent 使用评测，不能以"现有基准都独立单任务"为自建通用基准的依据。先复用现有评测，再补充聚焦推理结构迁移的窄任务流基准。

### 11.1 双阶段基准

| 阶段 | 域 | 基准 | 特殊构造 |
|---|---|---|---|
| 1 | Agent/编程 | 选一个可复现子集 + MemoryAgentBench/AMA-Bench/Mem2ActBench 中适用任务 | 先跑 MVP 与基线；自建部分只覆盖结构迁移所需任务流 |
| 2（条件启动） | 数学/逻辑 | 选一个窄任务族和可验证子集 | 仅在要主张跨域迁移且第一阶段结果稳定时加入结构相近异表面的配对变式 |

### 11.2 Baseline 与消融

- **Baseline**：无记忆 / 语义检索 / ReasoningBank 或其忠实复现（策略记忆）/ 结构检索；通用记忆系统只在任务设置与接口可公平对齐时纳入；
- **消融维度**：
  - 主线：语义路、结构路、结构表示来源、记忆提取条件逐项消融；
  - 支撑项：不同推理预算、首轮/迭代检索/逐轮注入分别消融；
  - 在线 bandit、三路融合和全套提取维度等扩展留到主线建立后。

### 11.3 防泄漏

按任务家族、仓库或生成模板切分；留出任务不能提前写入记忆；每个系统使用同一模型版本、提示词、工具、采样设置、预算和重试上限；任务流评测固定任务顺序并另做顺序扰动。用配对重复运行，报告置信区间；先做 pilot 估计方差，再确定正式样本规模。禁止从测试任务在线学习后再在同一测试流上报告首轮性能。

### 11.4 核心指标

任务成功率增益及置信区间、结构迁移增益、成本/成功任务、失败类型、错误记忆命中与 verifier 误判率。暂不采用"注入记忆后 PRM 分布不降"作为联合验收标准，因为 CoT 真实性和 PRM 跨域有效性尚未建立。

---

## 12. 团队分工（5 人）

| 角色 | 负责模块 | 主责里程碑 |
|---|---|---|
| P1 队长/架构 | 插件核心库、会话事件捕获、显式关联 ID、可复现记录 | M1/M2 集成与 M6 发布 |
| P2 工程 | 工具层（`memory_*` 四工具）、一个宿主联调、统一评测 harness | M1/M2 |
| P3 主研究 | 推理结构标签体系、结构检索和主假设实验 | M2/M3 |
| P4 评测/验证 | 域内任务验证器、结果归因、推理预算控制的支撑实验 | M2/M4 |
| P5 数据/记忆 | 任务流构造、标注一致性、基线记忆提取和数据治理 | M2/M5 |

**交叉点**：P4 的验证接口供记忆准入和主线评测共同使用；接口只返回任务域内证据、标签与置信度，不把单个 PRM 分数定义为通用过程真值。

**评测责任分摊**（防 5 人并行阻塞）：

- M2 先完成小型 pilot 和适用现有基准盘点，P5 领衔，P1/P2 支撑；
- harness 固定模型、工具、提示词、预算、任务顺序和结果记录；在 pilot 之后、正式实验前冻结版本；
- M6 只汇总预先登记的主假设实验和有限消融，不追加大规模新基准与全组合搜索。

---

## 13. 里程碑

```
M1 能力盘点与竖切片 → M2 pilot / baseline / harness → M3 主假设实验 → M6 论文与开源
                                                   ├→ M4 推理预算支撑消融（有余力时）
                                                   └→ M5 提取方法支撑消融（有余力时）
```

| 里程碑 | 内容 | 可演示产物 / go-no-go |
|---|---|---|
| M1 能力盘点与竖切片 | 在一个宿主 + 一个 DeepSeek 适配器上验证会话事件捕获、`task_id` 贯通、流式解析与 reasoning 可用状态 | 支持矩阵、样例任务事件；若关联或行为不可复现，先修复再扩功能 |
| M2 pilot 与评测协议 | 小型任务流、任务验证器、无记忆/语义检索基线、数据切分与泄漏检查；pilot 后冻结 harness 和主指标 | 端到端可复现 demo；确认主假设可测且至少有可用 verifier |
| M3 主研究实验 | 固定提取法，比较语义/结构检索与策略记忆基线；结构标签质检；报告效应量、置信区间和成本 | 结构检索增量报告；无增益时允许否定假设并重新定位 |
| M4 推理预算支撑实验 | 可用 effort/thinking 配置下比较固定/自适应预算，不引入不可观测"深度"标签 | 成功率—成本曲线；资源允许时完成 |
| M5 提取支撑实验 | 原始事件、朴素摘要、带验证证据的结构化记忆小规模比较 | 提取成本—下游增益；资源允许时完成 |
| M6 评测发布 | 复核主实验、发布插件包、文档和可视化 | 有边界清楚的论文素材 + v0.1 开源发布 |

---

## 14. 风险与对策

| 风险 | 对策 |
|---|---|
| reasoning/工具接口因模型与宿主而异 | M1 建立 capability matrix 和 adapter contract；一个组合验证后再扩展。不能假定 OpenAI 兼容即字段、状态和参数语义相同 |
| 流式响应与多类型事件解析复杂 | M1 就做流式解析；规范化为有序事件并保留原始 payload 引用，reasoning 字段可缺失 |
| 隐藏或缺失的 reasoning 输出 | reasoning 字段可选，记录缺失原因；主研究基于可观察操作/验证事件，不以私有 CoT 可见为前提 |
| 任务与请求错配 | 显式 task_id/session_id；并发、重试、子任务在 M1 验证；低置信自动匹配不进入研究语料 |
| PRM / LLM judge 误判 | 域内验证器优先，抽样人工审查；报告 verifier 误判率，不让 PRM 单独准入 |
| 记忆污染与提示注入 | 记忆来源/范围/信任级别；隔离数据与指令；删除、过期和审计能力；对恶意记忆单独红队评测 |
| 可靠性估计混淆 | 固定检索策略跑离线配对/随机对照；记录曝光与结果；确认贡献可识别后才上线动态调权 |
| 5 人并行与研究范围过载 | 先完成 M1/M2 gate，M3 是唯一必交研究主线，M4/M5 条件启动 |
| 提取成本失控（每条轨迹都跑 LLM 蒸馏） | 选择性提取（高价值判别前置）+ 小模型蒸馏兜底 + 成本-质量曲线给出操作点 |
| 多研究线叠加导致评测负荷过重 | pilot 后冻结主实验协议；先保证 M3 主线，再视资源完成 M4/M5 支撑实验 |

---

## 15. 技术基线（DSH）

- 语言：DSH 插件适配层 **TypeScript**；推理记忆核心算法 **Python 3.10+**。Python 文件随 DSH bundle 分发，由 TypeScript Host 入口通过可取消的 JSON stdin/stdout 子进程协议调用，不启动独立服务；本仓库根目录即插件包；
- 插件框架：Cordis（`@deepseek-ai/cordis`），配置 schema 用 `@deepseek-ai/schemastery`；
- 捕获：订阅 `dsh-session` 的 `session/event` 事件流（事件为 `{ seq, type, data }` 信封）；
- 模型：复用 `dsh-llm` / `dsh-llm-deepseek` 适配器；
- 存储：复用 `dsh-storage` + `dsh-storage-domain`，通过 `ctx.storageDomain` 持久化；可选 Qdrant 是重建型向量检索索引，不替换 DSH 事实源；
- 工具：`dsh-tools` 的 `defineTool`。

### 15.1 与本插件直接相关的现有包（复用清单）

| 包 | 复用点 |
|---|---|
| `dsh-session` | `session/event` 事件流、`KNOWN_SESSION_EVENT_TYPES`、session id 关联 |
| `dsh-llm` / `dsh-llm-deepseek` | 模型适配器、`reasoningEffort`、`usage`（reasoning token 若暴露） |
| `dsh-tools` | `defineTool` 工具注册 |
| `dsh-storage` / `dsh-storage-domain` / `dsh-storage-json` | MemoryItem 与检索记录持久化；通过类型化 `ctx.storageDomain` 访问，不直接操作 backend |
| `dsh-skill` | （可选）把"稳定策略记忆"作为 runtime skill 暴露给模型目录 |
| `dsh-session-query` / `dsh-tool-session-query` | 评测期读取历史会话（基线构造、离线对照） |
| `dsh-tool-todo` / `dsh-goal` | 任务边界与目标信号（`task_meta` 补充来源） |

### 15.2 插件包文件规划

```
reasoning-trajectory-memory/（本仓库根目录即 DSH 插件包）
├── package.json       # npm 包 + dsh.bundle.patch manifest
├── cordis.patch.yml   # 将插件行插入 DSH profile
├── README.md
├── tsconfig.json
├── src/
│   ├── index.ts       # Cordis 入口、session/event 捕获、5 个 memory_* 工具
│   ├── domain.ts      # DSH storage-domain schemas
│   ├── python.ts      # 可取消的 Python JSON bridge
│   ├── search-text.ts # 记忆/查询的词面与嵌入文本构造
│   ├── lexical-index.ts # BM25 倒排候选索引
│   ├── vector-index.ts  # 可选 Qdrant + embeddings REST adapter
│   └── enterprise-rules-rag.ts # 独立、只读、按 ruleSet 隔离的企业规则 RAG
└── python/
    ├── reasoning_memory.py # 稳定的 CLI worker 入口与兼容导出
    └── reasoning_memory_core/
        ├── constants.py         # 共享类型、词汇与上限
        ├── trajectory.py        # 工具轨迹抽象与结构签名
        ├── text_similarity.py   # 文本与序列相似度
        ├── reasoning_graph.py   # 图校验、自动构建与图匹配
        ├── distillation.py      # 成功策略/失败归因蒸馏
        ├── retrieval.py         # 多路融合、排序与正反配额
        ├── feedback.py          # 效用反馈计数
        └── service.py           # Python 操作分发
```

插件遵循 DSH bundle manifest（`dsh.bundle.patch`）并注册 `defineTool` 工具；Python worker 和 `reasoning_memory_core/` 模块随 npm 包分发，由 DSH Host 侧 TypeScript 入口调用。入口脚本保留稳定路径，算法按流程职责维护。它不是 FastAPI、MCP server 或独立应用。持久化使用 `ctx.storageDomain.open(spec)`，不直接访问底层 backend。

### 15.3 2026-10-28 混合检索实现补充

实现按用户提出的“检索效率与前沿论文思路”扩展：TypeScript 对任务情境、推理教训、前提、动作和推理图标签建本地 BM25 倒排索引；可选 Qdrant HNSW 从配置的 OpenAI-compatible embedding endpoint 获取向量。两路按 outcome 各取候选，排除活动 `taskId`，候选并集交给 Python；Python 结合推理语义、动作结构和逻辑图做加权 RRF，再执行正反比例配额。RRF 权重和候选评分记录进入检索审计。Qdrant 只保存向量、ID、outcome、taskId、内容/模型配置哈希等 payload，不保存完整记忆文本；`storageDomain` 仍然是事实源。

向量层默认关闭；启用后记忆语义文本和查询会被发送到配置的 embedding endpoint。启动索引回填在后台进行；回填时先走 BM25，DSH 记忆写入不等待向量 upsert。服务异常时降级到 BM25；BM25 没有候选时回退到全量 Python 排序。没有接入 cross-encoder 或时间检索通道，RRF/候选池权重仍需标注数据消融，不能将论文的质量结论外推为本插件结果。相关实现为 `src/search-text.ts`、`src/lexical-index.ts`、`src/vector-index.ts` 和 `python/reasoning_memory_core/retrieval.py`；本地测试位于 `.gitignore` 排除的 `test/`，不会进入 npm 包文件清单。

### 15.4 2026-10-28 企业规则 RAG 实现补充

增加独立于推理记忆的可选 enterprise rules 检索。管理员通过 profile 配置 `enterpriseRuleRagEnabled`、`enterpriseRuleSetId` 和独立 Qdrant collection；调用 `memory_retrieve` 时可按需请求 `include_enterprise_rules`，required 模式不可被调用参数关闭。插件只查询规则 collection，固定按 rule set、active 状态和有效时间过滤，并在客户端复核范围；规则原文仅在本次工具结果返回，不写入 reasoning memory 或 retrieval audit，审计仅存 rule IDs、状态和 required 标志。

规则 collection/embedding 由企业受控 ingestion pipeline 管理，插件无需写权限；可为 collection 配置 read-only Qdrant key 与独立 embedding key/model/URL/dimension。规则结果携带来源、版本和有效期，并作为独立 `enterpriseRules` 数组呈现。RAG 无命中不是“无适用规则”的证明；required 检索异常时结果提示先核验原始规则源。RAG 只做检索辅助，不强制工具权限，强制合规由 DSH 工具策略层或 enterprise policy engine 实现。规则文本仍可能包含间接 prompt injection，来源标记与模型提示不替代规则入库审批、最小权限和执行层控制。测试覆盖 scope/time filter 和不可信 payload 拒绝，但未接企业 ingestion 或真实 Qdrant。

捕获 `tool/call` 的 `name/callId` 与 `tool/result` 的 `message.source.callId/isError`，按 DSH session id 和 `task_id` 配对；不保存调用参数、工具输出、完整提示词或模型提供的 reasoning/CoT。只持久化成功策略、可归因失败教训和检索评分记录，不保存独立 TaskRecord。

成功经验需提交一句可迁移的 `reasoning_insight`；失败经验需提交有证据支持的 `failure_mode`，否则不入库。MVP 收到的 verifier/evidence 尚未由宿主独立验证，因此条目均为 `unverified`。正反检索默认成功:失败为 `1:1`，可配置；一侧候选不足时按分数补位并记录 `fallbackUsed`。

---

## 16. 开源优化集成策略与本地模型

### 16.1 集成原则

- **复用成熟、聚焦新增**：能对接的成熟能力（去重、文件索引、分层存储、审计写入）直接复用，不重写；本项目只实现"推理结构捕获 + 结构检索 + 固定融合"这块尚无插件覆盖的增量。
- **弱耦合、可替换**：与第三方插件通过适配接口对接，不依赖其内部实现；未安装某插件时，本插件仍能独立运行（降级为内建实现）。

### 16.2 分优先级集成

| 优先级 | 插件 | 集成方式 |
|---|---|---|
| P0（MVP 直接受益） | `dsh-tool-result-dedup` | 捕获层清洗：去重后的工具结果进入轨迹投影，降低噪声 |
| P0 | `dsh-project-memory` | 验证证据：把"读过的文件 + path:line"并入 `MemoryItem.evidence` 的环境状态来源 |
| P1（结构化存储） | `dsh-meow-memory` | 借鉴其 SQLite 分层存储；本插件的 MemoryItem 作为新增的 reasoning 层 |
| P1 | `dsh-memory-evolve` | 对接其项目决策/踩坑经验，作为策略/失败教训记忆的补充来源 |
| P2（语义路增强） | `graph-memory` | 其 SPO 三元组可作为语义路的一种结构化表示，替换/增强 MVP 的 token 重叠相似度 |
| P2（可信写入） | `dsh-mnemon` / `dsh-memento` / `dsh-self-improved` | 借鉴其可审计写入模式，本插件的 provenance/trustLevel 与之对齐 |

### 16.3 本地模型与 memory reasoning（后续研究方向）

若后续配置本地模型，可在不改变主假设的前提下扩展"memory reasoning"——**在检索到记忆后，由模型对记忆做组合与推理，而非仅检索后注入**：

- **本地嵌入模型**：结构路/语义路的 embedding 改用本地模型（如 bge 系列），实现开源零外部 API 的向量检索，替换 MVP 的 token 重叠相似度；
- **本地 LLM 做离线蒸馏**：M5 的"小模型蒸馏兜底"落地为本地模型，降低每条轨迹跑 LLM 的提取成本；
- **memory reasoning（更远的研究问题）**：把"检索 → 注入"升级为"检索 → 推理 → 注入"——模型先对 Top-k 记忆做去重、冲突消解、前提匹配，再产出针对当前任务的结构化提示。这属于主假设 H1 之外的第二阶段研究，仅在 H1 得到稳定结果后启动，避免与主线混淆。

**边界**：本地模型是可选增强，不是本项目成立的前提；MVP 不依赖本地模型即可复现。

---

## 17. 阅读清单与设计依据（截至 2026-10-26）

### 推理模型与 Agent 行动闭环

- [DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via Reinforcement Learning](https://arxiv.org/abs/2501.12948)：推理能力经后训练与推理时计算形成；反思、自我验证等行为不能视为由网关中的一个可插拔"think 模块"保证。
- [Qwen3 Technical Report](https://arxiv.org/abs/2505.09388)：同一模型家族支持 thinking / non-thinking 模式和推理预算控制，说明开关与预算是推理配置，不等同于过程质量验证。
- [ReTool: Reinforcement Learning for Strategic Tool Use in LLMs](https://proceedings.iclr.cc/paper_files/paper/2026/hash/4038c9208dfc22644c60ad39c24e5c53-Abstract-Conference.html)：研究模型推理与代码工具的多轮交织，并通过任务结果优化"何时/如何调用工具"。
- [Generalizable End-to-End Tool-Use RL with Synthetic CodeGym](https://proceedings.iclr.cc/paper_files/paper/2026/hash/1e4322fddd833f83c855660ac65e428d-Abstract-Conference.html)：把工具使用视为在可验证交互环境中学习的多轮 Agent 策略，支持将工具事件和环境结果作为一等数据。
- [Control-Token Injection Suppresses Chain-of-Thought and Defeats Reasoning-Based Oversight](https://arxiv.org/abs/2609.27542)：2026 年预印本在若干开放模型/工具 harness 上报告，chat template、非信任输入和工具解析器可共同影响 reasoning 通道与动作是否执行；应作为 parser/template 安全测试的动机，结论需按模型和 harness 独立复验。
- [OpenAI Reasoning Models Guide](https://developers.openai.com/api/docs/guides/reasoning)：示例说明 reasoning token 可不向 API 用户显示但仍计入上下文和输出预算；Responses 与 Chat Completions 的多轮推理状态处理也不同。此处仅作为供应商接口实例，不能推广到其他 API。
- [DeepSeek Thinking Mode API 文档](https://api-docs.deepseek.com/guides/thinking_mode/) 与 [Qwen/vLLM 推理解析文档](https://qwen.readthedocs.io/en/stable/deployment/vllm.html)：作为 M1 适配器能力盘点的官方依据，参数、工具回合上下文与 reasoning 字段需逐组合实测。

### 推理记录的忠实度与预算

- [Making Reasoning Matter: Measuring and Improving Faithfulness of Chain-of-Thought Reasoning](https://aclanthology.org/2024.findings-emnlp.882/)；[Measuring Chain of Thought Faithfulness by Unlearning Reasoning Steps](https://aclanthology.org/2025.emnlp-main.504/)：推理文本与模型决策之间的关系需用因果/忠实度方法评估，不把可读文本直接视为内部过程真值。
- [DeepSeek-R1 Thoughtology](https://arxiv.org/abs/2504.07128)：报告推理时间的甜点区及额外计算导致性能下降的现象，支持评估成功率—成本曲线而非单向拉高预算。
- [PRMBench](https://aclanthology.org/2025.acl-long.1230/)：过程奖励模型在细粒度错误检测上仍有明显弱点，PRM 应按领域校准并作为辅助证据。

### Agent 记忆与任务流评测

- [ReasoningBank (ICLR 2026)](https://iclr.cc/virtual/2026/poster/10007887)：成功/失败经验提炼、策略记忆、检索和回写是必须比较的近邻基线。
- [MemoryAgentBench (ICLR 2026)](https://mlanthology.org/iclr/2026/hu2026iclr-evaluating/)；[AMA-Bench (ICML 2026)](https://proceedings.mlr.press/v306/zhao26bs.html)；[Mem2ActBench (ACL 2026)](https://aclanthology.org/2026.acl-long.370.pdf)：用于盘点现有长程记忆、Agent 轨迹和记忆到工具执行的评测覆盖。
- [Hidden in Memory: Sleeper Memory Poisoning in LLM Agents](https://arxiv.org/abs/2605.15338)：持久化经验可能成为延迟攻击面，支持在提取、检索和写入路径设计来源、信任边界与红队测试。

---

## 附录：评审记录

- 2026-10-25 第 1 组（架构 + 数据模型 + 生命周期）确认，含补充"存储的推理模块对进行中任务的作用"（四作用点）；
- 2026-10-25 第 2 组（推理 RAG + 分层注入 + 思考保障三件套）确认；
- 2026-10-25 第 3 组（评测 + 分工 + 里程碑 + 风险）确认，含调整：记忆提取升格为第三条研究线（M5），评测发布移至 M6，评测责任分摊重排。
- 2026-10-26 初期计划复审：主研究收敛到结构检索增量；显式任务关联；模型提供的 reasoning 文本设为可选观测；更新评测、预算控制、记忆污染与基准范围。
- 2026-10-27 目标宿主变更：从"增强 Trae/Cline 等通用 Agent 工具（网关 + MCP Server + Python 核心库）"改为"DeepSeek Harness 原生插件（Cordis + TypeScript）"。原网关职责并入 `dsh-session` 事件捕获与 `dsh-llm` 适配器，原 MCP 职责并入插件工具层。此版本按 TypeScript 核心规划。
- 2026-10-27 生态调研与集成：盘点 8 个已有 DSH 记忆插件（`dsh-memory-evolve`、`graph-memory`、`dsh-meow-memory`、`dsh-project-memory`、`dsh-tool-result-dedup`、`dsh-mnemon`/`dsh-memento`/`dsh-self-improved`），确认其共同缺口为"未显式建模工具执行因果链/推理结构"，据此把本项目切入点收敛为 M3 结构检索；新增 §1.7（现有格局与定位）、§16（集成策略 + 本地模型 memory reasoning）。第三方插件 API 仍需集成前核对，本地模型为可选后续方向。
- 2026-10-28 MVP 实施补充（优先于 2026-10-27 TypeScript-only 基线）：本仓库本身作为 DSH Cordis bundle；插件/适配层、工具、配置和 storage-domain 接入使用 TypeScript；推理记忆蒸馏、结构签名、语义/结构相似度及正反配比使用随包分发的 Python 核心，通过可取消的 JSON stdin/stdout 子进程协议调用，不启动独立服务。成功策略必须附 `reasoning_insight`，失败记忆必须有可归因 `failure_mode`；只持久化蒸馏记忆和检索审计，不保存 TaskRecord 或原始 CoT。MVP 默认正反比例 1:1。
- 2026-10-28 研究迭代：将成功策略/失败教训单独保存为 `reasoning`，让推理教训成为检索主要语义信号；`auto` 分别使用情境、推理、动作结构权重 0.20/0.50/0.30，无结构时 0.30/0.70/0。加入当前任务排除，避免自检索；新增每任务每记忆仅一次的 helpful/harmful/neutral 聚合反馈，效用影响封顶在 ±20%，不改变正反配额或信任等级。增量字段用 schema 默认值兼容已有 v1 项目，不改变 DSH 插件及存储域版本。评估材料见 `MVP_EVALUATION.md`；分数权重仍需离线消融，不宣称已达到论文结果。
- 2026-10-28 混合检索：按 Hindsight 的多通道检索/RRF、H-Mem 的树图协同检索方向和 Qdrant 的过滤索引能力，新增 TypeScript BM25 与可选 Qdrant HNSW 候选，Python 做推理/结构/图信号 RRF；DSH domain 保持事实源、向量层为可重建派生索引。已验证 TypeScript build、Python 20/20 和 Node mock 测试 3/3（含后续 enterprise rule RAG）；真实 Qdrant、embedding endpoint、DSH profile 与基准尚未验证。
- 2026-10-28 企业规则 RAG：增加独立只读企业规则 collection、固定 ruleSet scope 和有效期过滤、按需/required 配置、来源/版本返回及 retrieval audit 状态；规则正文不写入经验记忆或审计记录。参考 Qdrant read-only keys 与 payload filtering 和 OWASP RAG 间接注入风险建议；尚未运行真实企业 KB 与 DSH profile 验证。
