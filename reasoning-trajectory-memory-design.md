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

### 1.8 迭代版本总览（v0.4.0 → v0.8.0）

主开发文档自 §18 起按迭代记录落地进度；下表为总览（对应设计细节见各节，完整规格与论文依据见 `docs/spec-0.X.0.md` 与 `docs/research-notes/0X-*.md`）：

| 版本 | 日期 | 主题 | 概率视角 | 四层映射新增 | 文档落点 |
|---|---|---|---|---|---|
| v0.4.0 | 2026-11-02 | 四层映射骨架落地（策略模板 + 激活动作化） | 经典关联（Beta 计数） | L1 模式分类 / L2 模板 / L3 元数据 / L4 激活计划 | §18、spec-0.4.0、01–04 |
| v0.5.0 | 2026-11-04 | 推理检索 RAG（文档检索） | 经典关联 | L1 模式过滤 / L2 `retrieve_document` / L3 文档域 / L4 步骤对齐检索 | §19、spec-0.5.0、05 |
| v0.6.0 | 2026-11-06 | 因果效用引擎（关联 → 因果） | 因果（do-演算）ATE | L1 混杂分层 / L2 处置 / L3 causalMeta / L4 do(retrieve)+反事实 | §20、spec-0.6.0、06 |
| **v0.7.0** | 2026-11-09 | **多概率保持引擎（单点坍缩 → 叠加保持）** | **量子（干涉 + 顺序）** | **L1 模式叠加 / L2 模板叠加+干涉 / L3 振幅+相位 / L4 延迟坍缩** | **§21、07** |
| **v0.8.0** | 2026-11-11 | **因果因子驱动的任务提炼（提炼侧因果信用分配）** | **因果（do-演算）uplift + 量子相位写入** | **L1 混杂分层 / L2 模板蒸馏处置 / L3 distillCausalMeta / L4 do(distill)+restored-state 反事实** | **§23、08** |

概率视角逐级推广：经典 ⊂ 因果 ⊂ 量子；每一级是前一级的推广（v0.6.0 = v0.7.0 的完全退相干极限），故逐版本保持零回归。v0.8.0 不引入新的概率层级，而是把因果（do(distill) 的 uplift）与量子（写时定相）**前移到提炼侧**（写路径），检索侧 v0.6.0/v0.7.0 逻辑不变。v0.7.0 的 `spec-0.7.0`（函数级实现）按计划后续开发。

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

> **迭代期分工映射（2026-11-09 补）**：M1–M6 为原始主线规划；自 v0.4.0 起工程按迭代推进（§18–§21），每迭代由 spec 明示角色——code-developer（实现，对应 P2）、deep-researcher（技术调研与文献/数字复核，对应 P3 主研究）、debugger（基线/对抗测试与零回归校验，对应 P4）、watcher（构建/测试监听）、lead（架构与文档，对应 P1）。v0.6.0 因果引擎与 v0.7.0 多概率引擎是 P3 主研究线在"概率视角"维度的深化（关联→因果→量子），其反事实/干涉/顺序效应评测归入 P4 的 `scripts/eval`。

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

## 18. 四层映射骨架落地（2026-11-02 迭代，v0.4.0）

### 18.1 迭代动机与学术来源

本迭代依据《面向 LLM 智能体的推理模块高效复用研究综述（终稿）》（30 篇文献，见 §18.3 映射表）落地其核心命题：把"任务处理思路逻辑"与"功能/领域激活"之间的映射作为一等公民。综述归纳的五条结构性空白中，本插件可以补上三条的插件侧对应物：

| 综述空白 | 本迭代落点 |
|---|---|
| 空白 1：检索结果落点单一（只能进 prompt，复用是建议性的） | `memory_plan_task` 产出**可执行的激活计划**：步骤级模块需求 + 主动检索点 + 推荐技能，把"建议"变成"计划动作"（仍为上下文级，插件无权重访问） |
| 空白 3：粒度失配（经验检索在任务级、激活干预在 token 级，缺"推理步骤"粒度） | 新增策略模板层 + `strategyTemplateId`/`reasoningLevel` 记忆字段 + `memory_retrieve` 的 `active_template_id`/`step_index`/`retrieval_intent` 步骤对齐参数 |
| 空白 4：检索与激活无联合决策 | `memory_plan_task` 是检索+激活的联合决策面；`memory_feedback` 扩展过程级信号（模板/步骤维度）驱动效用统计 |

空白 2（激活工程无持久化）在插件边界内对应为：激活**方案**（模板+模块需求）获得持久化与生命周期（挖掘→匹配→反馈→效用更新），但权重级激活本身不落地（见 §18.4）。空白 5（联合评测缺失）由 `scripts/eval` 离线 harness 起步（检索质量 × 模板匹配 × 消融，规范见 `docs/research-notes/04-eval-schema.md`）。

### 18.2 四层映射在插件中的落地

| 层 | 综述定义 | 插件构造 |
|---|---|---|
| L1 任务模式 τ | 任务表示学习/结构特征/关键词信号 | `classify_task_pattern()`：7 类确定性分类器（multi_hop_qa / code_synthesis / tool_orchestration / gui_operation / long_horizon_planning / numeric_search / general），四信号加权（task_family 查表 0.35 / 词法 0.30 / 结构签名 0.20 / 图节点分布 0.15），argmax + 边际置信度，general 兜底 |
| L2 策略模板 π | 策略模板/轨迹骨架（CoALA procedural memory 的结构化扩展） | `StrategyTemplate` 域记录：5 个内置种子（ToT/ToE 分解-回溯、Self-Refine、ReAct、Voyager 检索-复用、ToolLLM DFSDT）+ 确定性挖掘（SequenceMatcher 代表点聚类 θ_join=0.75/θ_form=0.55 + 多序列 LCS 骨干 + 成功占比门控）+ 调用者发布；`match(q,T) = (0.50 结构 + 0.30 词法 + 0.20 模式一致) × quality`，EvolveR 式 quality = (0.5+0.5×(succ+1)/(use+2))×(1−0.3×harmRatio) |
| L3 能力模块 m | 可寻址功能单元 + 元数据（适用域/触发条件/干预强度/历史效用） | 插件可寻址模块 = 推理记忆（success/failure）、策略模板、Agent Skill、DSH 工具、验证器；元数据 = 适用模式/前提/效用统计（Beta 平滑计数）；`moduleKind` 字段为未来宿主级模块（LoRA/SAE）预留接口占位 |
| L4 激活动作 a | 残差流叠加/特征干预/路由/工具注入/上下文注入 | 插件可执行的激活动作 = 上下文注入（检索记忆/模板）、技能加载（use_skill）、工具调用注入（invoke_tool）、验证（verify）、失败教训检索（rollback_check）；由激活计划的步骤级模块需求 d_t 显式驱动 |

**联合决策形式化**（综述 §2.1 的插件侧实例）：`memory_plan_task` 对任务 τ 与记忆状态 M_t 执行 π* = argmax match(τ, π)，展开为步骤级需求 d_t（模板步骤声明），并输出主动检索点（ProactAgent 式 proactive retrieval 的确定性近似）；`memory_retrieve` 以 `(active_template_id, step_index, retrieval_intent)` 把"何时检索、检索什么"对齐到推理步骤。过程级反馈（模板/记忆的 helpful/harmful/neutral）闭环回写效用统计——**这是低代价在线信号，不是校准的强化学习奖励**（ProactRL 的配对 continuation 过程奖励需训练基础设施，本插件不实现，见 §18.4）。

### 18.3 与综述 30 篇文献的映射

| 文献 | 采用/借鉴 | 不采用及理由 |
|---|---|---|
| Reflexion [1] | 失败→可检索修正知识；反馈信号闭环（本插件 `memory_feedback`） | 扁平反思池（ToE 证实有低于无经验基线风险）；反思文本持久化 |
| ExpeL [2] | 跨任务经验池 + 洞察/轨迹双粒度检索（reasoning/semanticText 双字段） | 无质量过滤的 insight 提炼（本插件用置信度门控 + 去特异化守卫） |
| ReAct [3] | 轨迹携带能力需求信号——`structureSignature` 与模板挖掘的数据来源 | thought 文本采集（隐私边界） |
| ECL [4] | 短期轨迹/长期任务级双层经验（工作记忆 vs 程序性知识划分） | 纯上下文注入无自演化（本插件加效用统计） |
| EvolveR [5] | 生命周期闭环（离线提炼→在线检索→效用维护）；(c_succ+1)/(c_use+2) 质量分原样复刻；原则库去重 | RL 策略进化（GRPO，无训练基础设施）；检索决策的策略化学习 |
| Tree-of-Experience [6] | 经验结构必须与推理层级对齐的方法论教训；可靠性由环境结果校准；保守录用（低质量经验管理不如无经验） | 分析视角×推理路径的完整树；手工层级设计（本插件以模板 coreSignature 为受限路径结构） |
| ProactAgent [7] | 检索的动作化思想（激活计划 + 主动检索点）；过程级信号；"总是检索是次优的" | ProactRL 配对 continuation 训练（无 RL 基础设施） |
| Self-Refine [8] | 生成—验证—修正作为内置模板（self-refine） | 跨任务复用机制（模板库承担） |
| AgentTrek [9] | 轨迹规模化合成思想 → eval 合成数据生成器 | 教程引导回放管线（超出插件边界） |
| ChatDev [10] / Voyager [11] | 多智能体实验床概念；技能库可执行化与跨任务迁移（skills 层既有） | 自动课程；Minecraft 环境 |
| MoT [12] | 置信度过滤作为质量门控（`insightConfidence` 写入门控） | 测试前预思考管线 |
| Distilling step-by-step [13] / STaR [14] | 推理过程比答案承载更多可迁移信息——"复用推理而非答案"的立论基础；自举闭环思想 → 模板挖掘 | 参数蒸馏（插件无权重访问） |
| LoRAHub [15] | 能力模块组合化思想：模板 = 模块序列的确定性组合，不训练任何参数 | CMA-ES 系数搜索（模块组合不需数值优化） |
| Generative Agents [16] | 反思即压缩（轨迹→模板提炼同构） | 手工加权检索评分 |
| MemGPT [17] | 记忆控制流下放给智能体（显式工具即此理念） | OS 分页语义 |
| HippoRAG [18] | 图结构单步多跳供给（reasoningGraph 既有） | KG 构建依赖 |
| A-MEM [19] | 记忆自我重组（模板效用/弃用标记的演化） | 动态链接改写 |
| MemWeaver [20] | 抽象模式 + 原始证据双通道检索（模板通道 + 记忆通道并存）；>95% 上下文压缩的结构化复用实证 | 三组件图/段落记忆实现 |
| CoALA [21] | 坐标系统：策略模板 = procedural memory 扩展；激活动作 = CoALA 未显式建模的 internal action 的插件侧近似；闭环学习 = learning processes | 完整认知架构复刻 |
| ActAdd [22] / CAA [23] | 方向提取的对比方法论（仅思想借鉴：模板挖掘用对比式签名聚类） | 权重干预（无权限） |
| Refusal direction [24] | **警示引用**：激活开关是双刃剑，同一机制可用于越狱——本插件坚持激活=上下文/工具级、可审计、可回滚 | 残差流干预 |
| Gemma Scope [25] / CoT 特征干预 [26] | 功能可被潜空间开关触发的证据（论证深度压榨的动机） | SAE 特征干预（无权限；跨模型可移植性未解） |
| DeepSeekMoE [27] | 通用↔专域分工思想 → 模板内通用步骤/专域步骤的模块需求区分 | 路由操纵 |
| RouterBench [28] / Gorilla [29] / ToolLLM [30] | 路由决策需领域信号 + 试错反馈（本插件模板元数据 + 效用统计的论据）；DFSDT 内置模板 | 多 LLM 路由；大规模 API 语料 |

### 18.4 越界声明（写实的边界）

- **路径 B（LoRA 组合）/ C（SAE 特征干预）/ E（RL 联合决策）不落地**：DSH 插件无模型权重与潜空间访问权限；[24] 的拒绝方向双刃剑警示（同一机制可用于越狱）与 [25][26] 的跨模型对齐难题使插件侧干预不具备安全与工程前提。`moduleKind` 字段仅作未来宿主级扩展的接口占位。
- **本迭代的"激活"是上下文/工具/技能注入的可执行化，不改变模型内部计算**：与综述 §2.4"深度=提升每一步执行的有效能力密度"的技术含义不同构；本插件主张的增益是"在正确步骤、以正确形式、给出正确经验/工具建议"，效应必须由评测验证（合成评测样本 ≠ 真实标注集结论）。
- **过程级反馈是低代价在线信号，不是 RL 奖励**：Beta 平滑计数影响排序的幅度受上限约束（与现有 feedback 乘数 0.8–1.2 同风格），不宣称校准的信用分配。

### 18.5 与既有设计原则的一致性

本迭代延续 MVP 的三条内建防御（结果/过程分离、失败归因允许未知、来源与信任边界）与确定性/可审计原则：分类器与匹配分全部可拆解（evidence/scores 输出）；挖掘全程无随机、幂等；fail 为主的簇不发射模板（保守录用，ToE 教训 [6]）；模板弃用只标记不删除（EvolveR 式剪枝的保守版）。所有阈值/权重为待标注集校准的初值，`scripts/eval` 的消融矩阵即校准工具。

---

## 19. 推理检索 RAG 落地（2026-11-04 迭代，v0.5.0）

### 19.1 定位与硬边界

在四层映射骨架（§18）上新增第三个检索面：**推理检索 RAG**——用户导入公司开发规范等文档，插件做推理感知的检索（不只是关键词/向量召回，而是带上任务模式、策略模板步骤意图与推理图上下文做过滤、确定性查询展开与轻量多跳）。它与既有两个检索面（经验记忆、企业规则 RAG）**互不侵入**，遵循两条硬规则：文档检索不进记忆 RRF 通道与正反配额（文档无 outcome/utility 语义）；索引期与检索期均不用 LLM（无摘要/实体抽取/假设文档/LLM 改写）。

### 19.2 四层映射对齐（新增组件落点）

| 层 | v0.4.0 现有 | v0.5.0 新增 |
|---|---|---|
| L1 任务模式 | `classify_task_pattern()` 7 类 | 文档/块元数据 `taskPatterns` 适用域；检索按当前模式过滤（软回退 + patternFallback 审计） |
| L2 策略模板 | `moduleDemands[].kind` 6 种 | 新增 kind `retrieve_document`（`MODULE_DEMAND_KINDS` + 词表/挖掘/校验三处联动）；search/constrain 步骤可声明"此步查文档" |
| L3 能力模块 | 记忆/模板/技能/工具/验证器 | **文档语料模块**：独立 domain `reasoning_memory_documents`（documents/chunks 两表）+ 独立 `DocumentLexicalIndex` + 可选独立向量 collection |
| L4 激活动作 | plan 激活计划；memory_retrieve 步骤对齐参数 | `memory_retrieve_document` 同构步骤对齐参数（active_template_id/step_index/retrieval_intent）+ 上下文注入（块带 headingPath/source/version）；plan 文本对含 retrieve_document 需求的步骤提示查文档 |

### 19.3 前沿论文依据（8 篇，数字经 arXiv 复核）

| 论文 | 借鉴 | 不采用（越界） |
|---|---|---|
| Self-RAG (2310.11511) | 按需检索思想 → 模板步骤 `retrieve_document` 声明式近似 | reflection tokens（需训练） |
| RAPTOR (2401.18059) | 多层表示 → 标题层级路径 + 块链接 | LLM 递归摘要（QuALITY +20% 增益依赖摘要层） |
| GraphRAG (2404.16130) | 局部检索失败于全局问题 → 限定步骤级局部证据 | 实体图 + 社区摘要（1M token 全局总结越界） |
| HippoRAG (2405.14831) | 图扩散单步替代迭代（up to +20%、10-30x cheaper）→ 有界 BFS | LLM 实体抽取 + PPR |
| Step-Back (2310.06117) | 抽象化改写 → 确定性词表泛化 | LLM 生成抽象 |
| HyDE (2212.10496) | 查询侧扩充 → 确定性查询展开 | 假设文档生成 |
| Adaptive-RAG (2403.14403) | 复杂度路由 → 模块需求声明 + intent 微调 | 训练分类器 |
| IRCoT (2212.10509) | 多跳检索价值（up to +21pts）→ 图扩散多跳；步进检索留给 agent 侧 | 检索与 CoT 交错生成 |

### 19.4 与既有设计的延续性

延续 MVP 三条内建防御与确定性/可审计原则：文档块是外部内容，返回带 docName/source/version 并提示不执行嵌入指令（与企业规则 RAG 同款安全语义）；超限导入拒绝而非静默截断；分块/多跳/融合全程无随机、纯标准库、分数可拆解；独立 domain + 独立索引保证记忆/企业规则零回归。完整设计见 `docs/spec-0.5.0.md` 与 `docs/research-notes/05-reasoning-rag.md`。

---

## 20. 因果效用引擎落地（2026-11-06 迭代，v0.6.0）

### 20.1 因果诊断

v0.5.0 的效用反馈是**关联性**的：`0.8 + 0.4×(helpful+1)/(helpful+harmful+2)` 估计 P(成功|用过记忆)，不回答"使用记忆是否**导致**成功"。混杂（任务模式同时影响"检索了什么"与"是否成功"）与选择性偏差（好记忆被用在简单任务上）都会失真——ToE 报告的"朴素经验管理常低于无经验基线"正是此类失真的实例。本迭代把反馈升级为**因果信用分配**：估计处理效应 E[Y|do(m)]−E[Y|do(¬m)]，以有界、保守、可审计的方式进入检索排序。

### 20.2 核心机制

- **反事实信用分配（一等原语）**：`memory_record_counterfactual` 记录配对续写双臂（同前缀有/无检索的结局对比，COMA 式反事实基线；综述 §6.5 / ProactAgent [7]）。
- **观察性因果估计**：按任务模式（L1）分层的 ATE/CATE（后门/分层调整，Laplace 平滑，每臂 ≥5 层门槛 + Wald CI）；IPTW 仅作稳健性对照（差异 >0.15 审计 iptwDisagreement，不喂排序）。
- **辛普森悖论防护**：粗效应符号与分层效应符号相反且双侧显著 → `simpsonFlip` 告警，排序一律采用分层估计。
- **保守集成**：n_eff ≥20 才启用因果分量；收缩 ATE×n_eff/(n_eff+20)；乘数 `1+clip(ATE_shrunk, ±0.20)` 与 Beta 乘数同区间 [0.8,1.2]——**因果引擎永不比现状更激进**；样本不足自动回退 Beta 效用（零回归可测）；负效应只压低不删除。

### 20.3 四层映射对齐

| 层 | 因果落点 |
|---|---|
| L1 任务模式 | **混杂分层主键**：stratification/backdoor 的 stratum s；CATE 逐层输出 |
| L2 策略模板 | **处置分配 do(template)**：模板选择即策略处置；模板效用同款升级 |
| L3 能力模块 | causalMeta additive 元数据（ATE/CATE/样本量/辛普森标记）；Beta 计数保留为回退先验 |
| L4 激活动作 | **干预 do(retrieve)**：treatment 定义；配对续写 = 显式反事实干预对照 |

### 20.4 识别假设与越界（诚实边界）

识别假设 5 条（可忽略性/SUTVA/处置良定义/结局良定义/verdict=代理标签）如实写入文档：仅控制在已观测混杂下成立；自报 verdict 是代理标签（Egami DSL 2306.04746 的教训），故因果结局用环境 outcome 而非 verdict。越界 7 条：在线因果发现、LLM 生成反事实/效应判断（Kıcıman 2305.00050："LLMs ignore the actual data"）、梯度优化（Retroformer）、DML 双重稳健、文本嵌入学混杂、因果分进配额、插件代行配对续写。完整公式与论文依据见 `docs/spec-0.6.0.md` 与 `docs/research-notes/06-causal-ai.md`。

---

## 21. 多概率保持引擎落地（2026-11-09 迭代，v0.7.0）

### 21.1 诊断：从"单点坍缩"到"多概率保持"（量子概率视角）

**现象（用户命题，需受控测量）**：开源中观察到"Top-1 到 Top-3 的准确率下降"——即**把前 3 候选注入 Agent 上下文时，下游任务准确率反而不及只注入 Top-1**。这里必须区分两种"准确率"：**覆盖精度** Acc@k（正确项是否落在前 k）随 k 单调不减，不是本命题所指；本命题指的是**承诺/注入精度**——把更多候选放进上下文后模型的最终输出质量反而下降。经典"可加、顺序对称"的概率模型无法解释"候选更多反而更差"，唯一出路是**候选间存在干涉 / 上下文污染 / 顺序效应**——这正是量子概率的领域。

**可证伪的操作化定义（H-Q1）**：固定模型/工具/提示词/预算与 held-out 任务集，比较 (a) 只注入 top-1 候选 vs (b) 注入 top-k 候选（保留振幅/相位/顺序元数据）的下游成功率。若 (b) 显著低于 (a)（候选更多反而更差），且该差异随候选顺序置换而显著变化，则经典可加-顺序对称模型被违反，构成"多概率保持 + 干涉建模"介入的必要条件；反之（(b) ≥ (a) 且顺序无关）则 H-Q1 被否证、本引擎无增量。评测由 `scripts/eval` 的坍缩损失 / 顺序效应 / 干涉三组受控测量执行（07 研究 §6）。

**归因（本迭代的立场）**：LLM 本质是**概率模型**（在候选上输出分布），但 Agent 的"壳子"——工具编排、argmax 决策、单条注入——把它**坍缩为单一输出**。这一步坍缩丢弃了分布中承载的信息，且不符合现实世界"任务本身多值、可叠加、顺序相关"的逻辑。这与量子力学里"叠加态在测量时坍缩为单一本征态"同构：测量（坍缩）不可逆地丢失叠加信息。

**三级概率推进（诚实定位）**：

| 迭代 | 概率视角 | 回答的问题 |
|---|---|---|
| v0.5.0 | 经典（Kolmogorov）关联 | 相关 vs 不相关 |
| v0.6.0 | 因果（do-演算）干预 | 相关 ≠ 因果（混杂/辛普森） |
| **v0.7.0** | **量子（广义概率论）** | **组合效应为何 ≠ 边际和；过早坍缩丢了什么；顺序为何是处置变量** |

三者**逐级推广而非替代**：经典 ⊂ 因果 ⊂ 量子。v0.6.0 因果引擎是 v0.7.0 的**「完全退相干极限」**——当干涉项为 0、相位被抹平，量子引擎严格退化为因果引擎行为，从结构上保证零回归。

### 21.2 核心机制（三原语 + 退相干桥接）

- **叠加态表示（一等数据表示）**：候选（任务模式/模板/记忆）不再被 argmax 坍缩为单点，而是保持叠加态 `|ψ⟩ = Σ_i φ_i|i⟩`，振幅 `φ_i = r_i·e^{iθ_i}`；模长 `r_i` 来自既有检索/匹配/效用分归一化，相位 `θ_i ∈ {0, π}` 来自因果效应方向（§21.4）。概率由玻恩定则提取 `P(i)=|φ_i|²`。**MVP 只用实数振幅（相位 {0,π}），纯标准库可算。**
- **干涉项（组合效应的一等载体）**：把候选 i、j **相干叠加**（振幅相加而非概率相加）时，组合振幅平方 `|φ_i + φ_j|² = r_i² + r_j² + 2·r_i·r_j·cos(θ_i−θ_j)`。经典（非相干）融合只加概率 `r_i² + r_j²`（RRF 通道融合正是如此），量子叠加多出的交叉项 `2·r_i·r_j·cos(θ_i−θ_j)` 即**干涉项**：同相（θ_i=θ_j）→ 建设性（组合强于边际和）；反相（θ_i=0, θ_j=π）→ 破坏性（组合弱于边际和）。这一交叉项正是 v0.6.0 边际 ATE 够不到的**交互效应（effect modification）**，也解释了 ToE"朴素经验管理低于无经验基线"的破坏性干涉可能。**符号约定（裁决 Q6）**："建设性/破坏性"指"组合振幅平方相对边际和的偏离方向"，**不指"更接近/远离成功"**；接近成功与否由相位本身承载（θ=0 正向、θ=π 负向）。双负（θ_i=θ_j=π）相干 → 交叉项为正，正确解读是"两个负振幅相干后组合更负、**偏离成功更远**"，而非"更有用"。
- **延迟坍缩与显式测量**：检索—融合—排序后"取 top-k"是一次隐式坍缩。本引擎把它变成显式、可审计、可再测量的操作——把坍缩推迟到最后一个负责任的时刻，证据增量时用**部分测量（Lüders 更新）**保留未观测子空间的相干。
- **退相干桥接（零回归的机制化）**：`ρ → (1−rate)·ρ + rate·diag(ρ)`，退相干速率由有效样本量驱动（`rate = k/(n_eff+k)`，复用 v0.6.0 收缩先验）。样本不足 → 相位/干涉被抹平 → 退化为经典-因果路径。**完全退相干 = v0.6.0 行为**。**开关语义（裁决 Q1）**：自动退相干是量子路径的固有组成部分，仅在 `quantumDecisionEnabled=true` 时求值；开关关闭时**完全跳过量子路径、不做任何退相干计算**，故输出与 v0.6.0 逐字节一致。配置 `decoherenceRate`（默认 0）是叠加在自动速率之上的**手动偏置**（0 = 不加额外退相干），与自动速率取和后 clip 到 [0,1]。

### 21.3 四层映射的量子化落地

| 层 | v0.6.0 现状 | v0.7.0 量子落点 |
|---|---|---|
| L1 任务模式 | argmax → 单类 + 置信度/边际 | **叠加态**：输出 7 类概率（振幅）向量而非单一类；`confidence/margin` 升级为振幅；仅在需单一模式的动作点"测量" |
| L2 策略模板 | argmax match → 单模板注入 | **模板叠加 + 干涉**：top-k 模板以振幅并存；结构重叠模板产生干涉项 |
| L3 能力模块 | Beta 效用 + causalMeta（ATE 幅度） | **振幅（模长）+ 相位**：模长 = 效用幅度、相位 = ATE 符号；密度矩阵非对角元 = 模块间相干 |
| L4 激活动作 | 排序 → 注入 top-k（隐式坍缩） | **延迟坍缩 + 显式测量 + 再测量**：注入带振幅/相位的叠加分布；坍缩推迟到最终承诺点 |

### 21.4 与因果效用引擎（v0.6.0）的交融

相位把两条线**焊在一起**：v0.6.0 的 ATE 符号 → v0.7.0 的相位（正效应 θ=0、负效应 θ=π），因果方向决定干涉是建设还是破坏。**该映射是编码约定（可替换）**：相位 θ∈{0,π} 承载"该单元对成功振幅的贡献方向"，而 ATE 符号是去混杂后该贡献方向的可观测代理；相位也可取更细粒度或由其他可观测因果量导出，但 MVP 锁定 {0,π} 以保确定性、可审计、零回归。一句话分工：**v0.6.0 回答"使用 m 是否导致成功"（边际因果）；v0.7.0 回答"把多条候选放在一起时，组合效应为何不等于边际和，以及过早坍缩丢掉了什么"（干涉 + 顺序）。** 相位与干涉的识别假设沿用 06（可忽略性/SUTVA/处置与结局良定义/verdict=代理标签），并新增 5 条（§21.6）。

### 21.5 数据模型（additive，零回归）

- 新独立 domain `reasoning_memory_quantum`（version 1，与 `reasoning_memory_causal` 并列）：存叠加态/坍缩审计记录（候选振幅与相位、密度矩阵摘要、`coherence`、`collapseReason`、`coherenceBefore/After`、`estimatorVersion`；FIFO 上限）。
- 候选审计 additive 字段：`amplitude: number default 0`、`phase: number default 0`、`interferenceDelta: number default 0`、`coherence: number default 0`、`collapseReason: string default ''`（全部带默认值，不破坏旧介质）。
- Python 新模块 `qprobability.py`（纯 stdlib、实数域、确定性）：`build_superposition` / `interference_pair` / `luders_update` / `coherence` / `decoherence`（设计级要点见 `docs/research-notes/07-quantum-probability.md` §3，函数签名与常量后续 `spec-0.7.0` 定稿）。
- 配置（additive，默认关闭）：`quantumDecisionEnabled`（默认 false）、`maxSuperpositionSize`（默认 3）、`phaseFromCausal`（默认 true）、`decoherenceRate`（默认 0）。
- **多概率消费格式（一等契约）**：`quantumDecisionEnabled=true` 时，`memory_retrieve`/`memory_plan_task` 除传统 `top-k` 摘要外附带 `superposition[]` 字段——每条候选 `{id, amplitude r, phase θ, p=|φ|², interferenceDelta, coherence}`，并**保留候选顺序语义（顺序即处置变量）**。下游模型在最后一个承诺点之前按该分布推理，而非在检索时被迫坍缩；坍缩点由插件审计（`collapseReason`/`coherenceBefore/After`）。**契约裁决（Q3/Q4）**：开关关闭时 `superposition` 字段**完全缺席**（`"superposition" not in result`，非空数组），保证与 v0.6.0 输出字典结构逐字节一致；`interferenceDelta` 审计**同时存原始值 `interferenceDeltaRaw` 与封顶后值 `interferenceDelta`**（原始值供"每个估计量可拆解"，封顶值进最终 multiplier，二者分离）。

### 21.6 诚实边界与越界声明

识别假设 5 条（`07-quantum-probability.md` §0 原样）：多概率保持是**决策框架假设非模型本体假设**；相位来源 = 可观测因果方向；干涉以环境 outcome 为锚（**合成 gold 只验证机制算术、不验证真实联合因果**——真实集靠配对/组合实验逐步积累，诚实标注，见 test-plan §7 Q2）；坍缩是不可逆信息丢失边界；确定性优先。

越界（不做什么）：

1. **不涉及量子计算/量子硬件**：无 qubit、无量子电路、MVP 不用复数（实数振幅即可）；"量子"仅指概率论形式语言。
2. **不声称模型内部是量子系统**：叠加态是插件决策框架的数据表示，不观测、不干预模型内部。
3. **不做量子因果结构发现/量子因果模型**（Costa & Shrapnel / Allen et al. 已复核 NJP 18:063032 / PRX 7:031021，仍仅作背景指针、不落地）；
4. **不采样做线上 Self-Consistency**（采样 = 显式随机，仅离线消融，固定 seed）；
5. **不把干涉/相位进 RRF 通道权重与正反配额**（沿用 v0.6.0 硬边界：只作用于最终 multiplier、封顶 ±20%、负效应只压低不删除）；
6. **不生成"量子/坍缩"的解释性文本给模型**（审计输出 = 数值表，与 06"不生成反事实叙述"同款纪律）。
7. **不承诺下游遵守注入顺序**（裁决 Q5）：插件保证 `superposition[]` 输出有序并审计顺序，但不保证下游 Agent 按给定顺序推理——"顺序作为处置变量"的端到端可审计性仅插件侧成立。

完整公式与文献依据见 `docs/research-notes/07-quantum-probability.md`；对抗性测试清单见 `docs/test-plan-0.7.0.md`（六组 32 用例；其 §7 设计疑点 Q1–Q6 已于本节裁决落笔）；具体函数/实现细节后续 `spec-0.7.0` 开发（本设计文档为最终确定的主开发文档）。

### 21.7 术语速查（量子概率在本插件中的工程含义）

下表固定量子概率术语在**本插件决策框架**中的含义（与物理含义划清界限，见 §21.6 越界 1/2）：

| 术语 | 工程含义 | 落点 |
|---|---|---|
| 叠加态 \|ψ⟩ | 多候选并存、不坍缩为单点的决策表示 | `memory_retrieve`/`memory_plan_task` 输出 |
| 振幅 φ = r·e^{iθ} | 模长 r = 效用/相关幅度；相位 θ = 因果方向（{0,π}） | L3 模块、候选审计 |
| 玻恩定则 P(i)=\|φ_i\|² | 由振幅提取"被采用"的概率（归一化） | 叠加态归一化/审计 |
| 干涉项 `2r_i r_j cos(θ_i−θ_j)` | 相干叠加相对概率相加的**偏离**；建设/破坏指"组合强于/弱于边际和"，成功方向由相位承载 | L2 模板对 / L4 注入对 |
| 测量/坍缩 | 把叠加态固定为单一输出的不可逆操作 | L4 "取 top-k" / 最终承诺点 |
| 部分测量（Lüders） | 证据增量时更新态、保留未观测相干 | 步骤级再检索 |
| 密度矩阵 ρ | 态的一等表示；非对角元 = 相干 | `reasoning_memory_quantum` |
| 退相干 | 干涉/相位被抹平 → 退化为经典-因果路径 | 样本不足自动回退 |

---

## 22. 前沿思路来源与后续方向（2026-11-09 检索）

### 22.1 定位

在落地 v0.7.0 时同步检索 2025–2026 前沿工作，作为**思路来源**——不新增运行时依赖、不立即落地，用途有三：① 佐证"量子叠加 + 延迟坍缩"不是孤例，而是正在成形的研究脉络；② 为 §21 的相位识别、顺序效应、上下文依赖提供更锋利的表述；③ 标注 v0.8.0 候选方向。三篇均经 `web_fetch` 复核 arXiv 摘要页（2026-11-09，HTTP 200）。

### 22.2 三篇已核实的前沿论文

| 论文 | 主题 | 与本项目的直接关联 |
|---|---|---|
| Quantum Abduction: A New Paradigm for Reasoning under Uncertainty（arXiv:2509.16958，Pareschi 2025） | 假设以叠加态并存、建设/破坏性干涉、仅在证据相干时坍缩，反对"消元式搜索"的过早剪枝 | **v0.7.0 的近亲**：suspension = 延迟坍缩；interference = 干涉项；"dynamic synthesis over premature elimination" = 反对 argmax 单点坍缩 |
| From Reasoning to Agentic: Credit Assignment in RL for LLMs（arXiv:2604.09459，Zhang 2026，69 篇综述） | 稀疏结局奖励下的信用分配；"text-only histories can leave even the sign of credit unidentified"；restored-state 对照识别协议特定因果对比；CA-ID Card | **v0.6.0/v0.7.0 的相位地基**：ATE 符号 = 相位，而符号本身在纯文本历史下可能不可识别 → 必须反事实对 + 收缩 |
| Contextual Relevance and Adaptive Sampling for LLM-Based Document Reranking（arXiv:2511.01208，Huang et al. 2025） | 相关度 = 对候选集合与呈现顺序的上下文边缘化；位置偏差 + 批次组成偏差 | **顺序即处置变量 + 干涉项**的检索侧独立证据；"uncertainty-aware sampling" 对应离线 Self-Consistency 消融 |

### 22.3 对本设计的直接启示（交融）

1. **延迟坍缩是共识**：Quantum Abduction 的 "sustain multiple explanatory lines in suspension … collapse only when coherence with evidence is reached" 与 §21.2"把坍缩推迟到最后一个负责任的时刻"逐句对应——本插件的工程近似（实数振幅、确定性）可视为其"量子认知 + NLP 嵌入"路线的最小确定性版。
2. **相位符号可能不可识别（关键诚实话）**：Credit Assignment 综述指出"text-only histories can leave even the sign of credit unidentified"——§21.4 的相位 θ∈{0,π} 依赖 ATE 符号，而该符号在纯文本历史下可能本身不可识别。这**强化**了 §21.6 的收缩 + 退相干回退：相位符号只在 restored-state 反事实对 / 分层达标时启用，否则抹平为 0（退相干），与综述的"restored-state comparisons identify a protocol-specific causal contrast"一致。其 **CA-ID Card**（每条声明链到 estimand + evidence provenance + falsification test）可作为候选审计字段蓝本。
3. **上下文相关效用**：Contextual Relevance 的 "relevance marginalized over the distribution of … candidate set composition and presentation order" 与 §21"顺序即处置变量 + 干涉项"是同一思想的两个侧面——一个在检索侧、一个在激活侧。其 "uncertainty-aware sampling" 可作为 §21.5 离线 Self-Consistency 式消融的对照方法。

### 22.4 v0.8.0 候选方向（诚实标注，不承诺）

1. **相位细粒度化**：由 {0,π} 扩展到连续相位或复振幅（Quantum Abduction 用 NLP 嵌入构造振幅/相位），仅在反事实对充足时启用，仍受收缩与封顶约束。
2. **上下文相关效用（contextual relevance）**：把 L3 效用从"边际"扩展为"对候选集合/顺序的条件期望"，与干涉项统一在同一形式化下。
3. **CA-ID Card 式审计**：候选审计新增 `estimand` / `evidenceProvenance` / `falsificationTest`，把每条因果/相位声明链到可证伪性。

### 22.5 复核记录

| arXiv | 题名 | 复核状态 |
|---|---|---|
| 2509.16958 | Quantum Abduction（Pareschi 2025） | ✅ web_fetch 摘要页（2026-11-09） |
| 2604.09459 | Credit Assignment in RL for LLMs（Zhang 2026） | ✅ web_fetch 摘要页（2026-11-09） |
| 2511.01208 | Contextual Relevance and Adaptive Sampling（Huang et al. 2025） | ✅ web_fetch 摘要页（2026-11-09） |

---

## 23. 因果因子驱动的任务提炼（2026-11-11 迭代，v0.8.0）

### 23.1 诊断：从"关联性提炼"到"因果提炼"，以及"继承效用"污染

v0.4.0 起，任务提炼（`memory_save_outcome` → 蒸馏 → MemoryItem）一直由**关联性信号**驱动：

- helpful/harmful Beta 计数（`0.8 + 0.4×(helpful+1)/(helpful+harmful+2)`）回答"该记忆与成功**相关**吗"；
- `insightConfidence` 是调用者/LLM 蒸馏时的**自评**（spec-0.4.0 §3.5 门控，写入策略而非校准质量判定）。

两者都不回答"提炼这条记忆是否**导致**下游更好"。更尖锐的是**继承效用（inherited utility）污染**（MGPO 教训，08 研究 §1）：一条候选条目的**表观效用**（写它之后库的性能）可能**继承自库中已有信息**，而不是它自身的贡献——

```
apparent(m)    = E[Y | 库 = M ∪ {m}]   # 表观效用：写 m 后的库性能
inherited(m)   = E[Y | 库 = M]         # 继承效用：不写 m、仅靠已有库 M\m
incremental(m) = apparent(m) − inherited(m)   # 增量贡献 = 奇妙因子
```

典型污染：m 与已有条目 m' 近似重复，成功本来就会发生，写 m 不带来任何增量（`incremental(m) ≈ 0`），却把 `apparent(m)` 的高值误记为 m 的功劳，导致记忆库膨胀（MGPO：平均记忆长度相对优化前降低近 80% 的冗余即此类）却不提升效果。

v0.6.0 已在**检索侧**用 do(retrieve) 的 ATE 做因果信用分配；本迭代把它**前移到提炼侧**——用 do(distill) 的增量因果贡献作为提炼决策的一等信号。一句话：**先因果地决定记住什么，再因果地决定检索什么**（§22 的 Credit Assignment 综述教训 2604.09459 在提炼侧的直接落地；§22.4 候选方向中的"CA-ID Card 式审计"并入本节 §23.5 元数据）。

### 23.2 奇妙因子形式化：增量因果贡献 uplift(m)

**定义（提炼侧处理效应）**：

```
uplift(m) = E[Y | do(distill m)] − E[Y | do(¬distill m)]
          = incremental(m) = apparent(m) − inherited(m)
```

- `do(distill m)`：把候选条目 m 写入记忆库（库 M → M∪{m}）的干预；
- `do(¬distill m)`：不写 m（库保持 M）的反事实；
- `Y`：下游任务流结局（任务成功率 / 步骤级结局，与 v0.6.0 同源；**不用 verdict 当结局**，verdict 是代理标签）。

**增量贡献分离（反事实要点）**：`uplift(m)` 不是"写 m 之后库有多好"（表观），而是"写 m 相对不写 m 多带来多少"（增量）。后者要求固定除"是否写 m"以外的一切——尤其固定库中**其余条目**，这正是 MGPO 把"继承自已存信息的效用"隔离出来的反事实操作。

**配对 rollout 估计（TQG-Exp 估计思想，08 研究 §2）**：

```
uplift_hat(m) = mean_{paired}( Y_{with m} − Y_{without m} )
```

同一下游任务（或同任务流前缀），库状态只差"是否含 m"，前缀/模型/工具/库中其余条目全部固定；"without m"臂即 restored-state 式反事实（把库恢复到写 m 之前再跑）。这是 do(distill) 最接近 L3 反事实层的 gold 证据（COMA 反事实基线的提炼侧对应）。

**观察性分层估计（线上低成本近似）**：按 L1 任务模式分层，每层 s 做 2×2 表（Laplace 平滑），`uplift_s = ŷ_{1s} − ŷ_{0s}`，`uplift_stratified = Σ_s (n_s/N)·uplift_s`；辛普森防护同 v0.6.0（粗表观符号 vs 分层符号异号 → 告警、采用分层）。

**收缩/封顶（保守核心）**：`uplift_shrunk = uplift_stratified × n_eff/(n_eff+20)`（先验收缩）；封顶 `clip(uplift_shrunk, ±0.20)`；`n_eff ≥ 20` 且每臂 ≥5 才启用因果分量，否则回退（门控回退到关联路径、权重回退到 insightConfidence、相位不写 = 0）。

### 23.3 三落点

| 落点 | 机制 | 诚实边界 |
|---|---|---|
| ① 提炼门控 | `distillGateEnabled=true` 且 `uplift_shrunk < distillUpliftThreshold`（默认 0）→ 拒绝入库，`reason='below_distill_uplift_gate'`——**学会不记住**（近重复/无增量条目不写） | 门控只拒绝新写入，不删除已有条目（"只升不删"纪律的提炼侧对应）；因果增量估计是计数近似，不是校准质量判定 |
| ② 提炼权重 | 蒸馏条目 `confidence`/效用 = `uplift_shrunk` 的有界映射；`insightConfidence` **降级为辅助信号**（仅作候选 tie-break，不再单独决定入库或置信度） | insightConfidence 自评即便准确率 80–90% 仍有偏（Egami DSL 2306.04746 教训），故降级；`uplift_shrunk` 收缩 + 封顶，永不比关联路径更激进 |
| ③ 相位写入 | `distillPhase = (uplift_shrunk ≥ 0 ? 0 : π)`，蒸馏时写入 MemoryItem——写时定相，检索侧 v0.7.0 直接消费（不再等到检索时由 ATE 符号推相位） | 相位映射沿用 v0.7.0 约定：θ=0 正贡献（"成功放大器"）、θ=π 负贡献（"负增量条目"，见 §23.7 术语注）；符号仅在反事实对/分层达标时可信，否则收缩为 0 |

**与 insightConfidence 门控的共存**：v0.4.0 的自评门控（`minInsightConfidence`）作为**自评下限**先执行，v0.8.0 的 uplift 门控作为**因果下限**后执行；uplift 样本不足时权重回退 `insightConfidence`（保持 v0.4.0–v0.7.0 行为不变）。

### 23.4 四层映射对齐

| 层 | v0.6.0（检索侧） | v0.8.0（提炼侧） |
|---|---|---|
| L1 任务模式 | 混杂分层主键（检索 ATE 的 stratum） | **混杂分层主键（提炼 uplift 的 stratum）**：同一套 L1 分类器/分层变量，处理效应换到 do(distill) |
| L2 策略模板 | 处置 do(template)（模板选择） | **模板蒸馏处置 do(distill template)**：提炼出的策略模板的增量贡献同理估计；模板 utility 升级为提炼侧 uplift |
| L3 能力模块 | causalMeta additive（ATE/CATE/样本量） | **distillCausalMeta additive**（uplift/inherited/样本量/相位/CA-ID Card 式字段）；insightConfidence 降级为辅助 |
| L4 激活动作 | do(retrieve)+配对续写 | **do(distill)+restored-state 反事实**：写动作=干预；反事实=库回到写 m 前再跑（配对 rollout） |

### 23.5 数据模型（additive，零回归）

- **新独立 domain `reasoning_memory_distill_causal`（version 1，与 `reasoning_memory_causal` 并列）**：蒸馏因果事件（配对 rollout 双臂 / 观察性事件 / uplift 估计审计），每 memoryId FIFO 上限（初值 200，同 v0.6.0）。
- **MemoryItem additive 字段（全部带默认值，不破坏旧介质）**：

  | 字段 | 类型 | 默认 | 语义 |
  |---|---|---|---|
  | `distillUplift` | number | `0` | 收缩后的增量因果贡献（clip ±0.20） |
  | `distillInherited` | number | `0` | 继承效用估计（`apparent − uplift`） |
  | `distillSamples` | int | `0` | 有效样本量 n_eff |
  | `distillPhase` | number | `0` | θ∈{0,π}，写时定相 |
  | `distillGate` | enum `'passed'\|'rejected'\|'na'` | `'na'` | 门控结果；na=未启用 |
  | `distillEstimand` | string | `''` | CA-ID Card 式：estimand 描述 |
  | `distillEvidenceProvenance` | string | `''` | CA-ID Card 式：证据来源 |
  | `distillFalsificationTest` | string | `''` | CA-ID Card 式：可证伪性 |

- **配置（additive，默认关闭/中性）**：`distillGateEnabled`（false）、`distillUpliftThreshold`（0）、`phaseFromDistillUplift`（true）、`distillShrinkPrior`（20）、`distillMinArmSamples`（5）。
- **Python 新模块 `distill_causal.py`（纯 stdlib、确定性）**：`estimate_distill_uplift` / `gate_distill` / `phase_from_uplift` / `inherited_utility`（设计级要点见 08 研究 §5，函数签名后续 `spec-0.8.0` 定稿）。

### 23.6 识别假设与越界（诚实边界）

**识别假设**（沿 v0.6.0 5 条 + 提炼侧新增 2 条）：

1. 可忽略性（仅控制在已观测混杂下成立；未观测混杂则 uplift 有偏）；
2. SUTVA（库条目间无干扰；配对 rollout 通过固定其余条目来隔离——这要求可执行 restored-state，否则继承效用无法识别）；
3. 处置良定义（do(distill m)=写 m；do(¬distill m)=不写 m）；
4. 结局良定义（Y=环境/验证器终态）；
5. verdict = 代理标签（收缩 + gold 校验）；
6. **增量因果符号在纯文本历史下可能不可识别**（Credit Assignment 2604.09459："text-only histories can leave even the sign of credit unidentified"）→ uplift 符号只在对齐的反事实对 / 分层达标时启用，否则收缩为 0、不写相位、退回关联路径；
7. **restored-state 反事实对是 gold 的唯一来源**；观察性分层估计是低成本近似，不声称等价。

**越界（不做什么）**：

1. 不落地 MGPO 的 memory-rewrite 梯度优化 / 检索器微调（无训练基础设施）；
2. 不落地 TQG-Exp 的 Trajectory Quality Loss 训练（TQL 需训练；MVP 只借"配对 rollout 估计经验贡献"的估计思想）；
3. 不委托 LLM 判断因果增量（Kıcıman 2305.00050 / Jin 2306.05836 教训）——全部确定性计数；
4. 不把 uplift 进 RRF 权重与正反配额（沿用 v0.6.0 硬边界：只作用于提炼门控/权重/相位，检索侧 multiplier 位置不变）；
5. **线上不做 restored-state rollout**（识别成本高；配对 rollout 只在离线评测/标注集做，线上用观察性分层 + 收缩）；
6. 不删除低 uplift 已有条目（只拒绝新写入，不改 outcome 池归属）。

### 23.7 与 v0.6.0 / v0.7.0 的交融（含术语注）

- **v0.6.0 = 检索侧因果（do(retrieve) 的 ATE 幅度）**；**v0.8.0 = 提炼侧因果（do(distill) 的 uplift 幅度）**。两者是同一套分层/收缩/封顶机制作用在**不同处置**上，互不替代：v0.8.0 只在其上游（写路径）加一道门控 + 写时定相，v0.6.0 检索侧 ATE 与 multiplier 逻辑**逐字节不变**。
- **v0.7.0 = 检索侧相位（θ 由 ATE 符号推导）**；**v0.8.0 = 写时定相（θ 由 uplift 符号在蒸馏时即写入）**。检索侧仍消费同一 θ 字段，只是相位来源从"检索时算 ATE 符号"前移到"蒸馏时算 uplift 符号"——`phaseFromDistillUplift=true` 时优先用写时相位，`=false` 或样本不足时回退 v0.7.0 检索侧推导，保证零回归。
- **术语注（诚实标注）**：本节 θ=0 称"成功放大器"（正增量贡献）、θ=π 称"负增量条目"（应被系统避让/压低的条目），沿用 v0.7.0 的 **θ=π = 负贡献** 约定。"负增量条目"指其表观效用继承自已存信息、自身无正增量乃至有害的条目，**不是**"帮助规避失败的正面教训"——后者（好的失败教训）增量为正、应落 θ=0。以此命名规避"失败避让器"与日常语义（"帮人避失败的好教训"）的冲突，避免检索侧误解相位符号。

### 23.8 测试要点

- **合成已知因果结构**：注入真 uplift；注入"继承效用污染"（apparent 高但 incremental≈0 的近重复条目）→ 估计器分离出增量贡献、门控拒绝入库（"学会不记住"）。
- **门控边界**：`distillUpliftThreshold` 上下的拒绝/通过、收缩单调性、`n_eff<20`/每臂<5 回退关联路径。
- **相位映射**：符号→{0,π} 正确；`phaseFromDistillUplift=false` 回退 v0.7.0 检索侧推导。
- **零回归**：`distillGateEnabled=false` 且新字段全默认 → 与 v0.7.0 输出逐字节一致。
- **确定性**：双跑全等；辛普森同款检出。
- **additive 兼容**：旧 MemoryItem（无新字段）读回/检索/反馈不抛错（Node mock 覆盖）。

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
- 2026-11-02 四层映射落地（v0.4.0）：依据《推理模块高效复用》综述（30 篇文献）新增策略模板库、7 类任务模式分类、激活计划（`memory_plan_task`）、模板挖掘/发布、检索 template 通道与步骤对齐参数、洞察置信度写入门控、保守弃用标记与 `scripts/eval` 离线评测 harness。路径 A（上下文级）落地，路径 B/C/E（LoRA/SAE/RL 权重级）为文档化越界项（见 §18.4）。DSH 契约经双人核实：v1 domain 新增 templates 表不破坏旧介质（无表结构哈希、缺失表回填空 Map、逐记录读取边界校验），templates 表 schema 铁律为 additive。模板通道 RRF 权重（template 0.15 主臂 / structure 0.05+template 0.20 备选臂）与分类器/挖掘阈值均为待标注集校准的初值。
- 2026-11-04 推理检索 RAG（v0.5.0）：新增第三个检索面——用户导入开发规范文档，做四层映射对齐的推理感知检索（L1 模式软过滤、L2 新增模块需求 kind `retrieve_document`、L3 独立 domain `reasoning_memory_documents` + 独立索引、L4 步骤对齐文档检索）。依据 8 篇前沿推理 RAG 论文（Self-RAG/RAPTOR/GraphRAG/HippoRAG/Step-Back/HyDE/Adaptive-RAG/IRCoT，数字经 arXiv 复核），严格只做 stdlib 确定性近似（标题层级分块 + 块链接图 + 有界 BFS 多跳 + 确定性查询展开），LLM 摘要/实体抽取/假设文档/训练路由/全局总结全部越界（见 §19）。硬边界：文档检索不进记忆 RRF 通道与正反配额；企业规则 RAG 与记忆检索零回归。
- 2026-11-06 因果效用引擎（v0.6.0）：把关联性 Beta 效用反馈升级为因果信用分配——按任务模式分层（后门/分层调整）的 ATE/CATE + IPTW 稳健对照 + 配对续写反事实记录原语（`memory_record_counterfactual`）+ 辛普森悖论检测告警；因果乘数 `1+clip(ATE×n_eff/(n_eff+20), ±0.20)` 与 Beta 乘数同区间、永不更激进，样本不足自动回退 Beta（零回归可测）。独立 domain `reasoning_memory_causal` 存因果事件（FIFO 上限）。识别假设 5 条与越界 7 条写入 §20.4：仅控制在已观测混杂下成立；verdict 是代理标签、因果结局用环境 outcome；估计全部确定性计数完成、不委托 LLM。依据 8 篇 arXiv 复核论文（COMA/ProactAgent/Egami×2/Schölkopf/Kıcıman/Jin/Karimi/Retroformer）与 Pearl 因果层级经典，详见 `docs/research-notes/06-causal-ai.md`。
- 2026-11-09 多概率保持引擎（v0.7.0）：受量子力学概率与因果 AI 启发，把"Agent 壳子把概率模型坍缩为单一输出"作为待解决问题——开源中 Top-1→Top-3 准确率下降被归因于过早坍缩丢失叠加信息。新增四层映射的量子化落地：L1 任务模式输出叠加态（7 类振幅向量）而非单类、L2 模板叠加 + 干涉、L3 能力模块振幅（模长=效用幅度）+ 相位（=因果 ATE 方向）、L4 延迟坍缩 + 显式测量 + 再测量（Lüders 更新）。核心机制三原语（叠加态/干涉项/延迟坍缩）+ 退相干桥接：`ρ→(1−rate)ρ+rate·diag(ρ)`，速率由有效样本量驱动，**完全退相干极限 = v0.6.0 因果引擎行为**，结构上保证 `quantumDecisionEnabled=false` 时零回归。与因果引擎的交融：ATE 符号 → 相位（正=0/负=π），因果方向决定干涉建设/破坏；干涉项 = 边际 ATE 够不到的交互效应（effect modification），也解释 ToE"朴素经验管理低于无经验基线"。诚实边界：不涉量子计算/硬件、不称模型内部为量子、相位只取 {0,π} 实数、确定性优先、干涉只作用于最终 multiplier 封顶 ±20%。新独立 domain `reasoning_memory_quantum`（additive）存叠加/坍缩审计。依据量子认知（Busemeyer/Pothos 期刊书籍）+ Self-Consistency（2203.11171）+ ToT（2305.10601）+ LLM 顺序效应（2308.11483），详见 `docs/research-notes/07-quantum-probability.md`；具体函数实现后续 `spec-0.7.0` 开发（本设计文档为最终主开发文档）。同步检索 3 篇前沿论文作为思路来源（Quantum Abduction 2509.16958 / Credit Assignment 2604.09459 / Contextual Relevance 2511.01208，见 §22）。
- 2026-11-11 因果因子驱动的任务提炼（v0.8.0）：把 v0.6.0 的检索侧因果信用分配**前移到提炼侧**——用增量因果贡献 `uplift(m)=E[Y|do(distill m)]−E[Y|do(¬distill m)]` 作为任务提炼（`memory_save_outcome`→蒸馏→MemoryItem）的一等信号，把"表观效用"从"继承自已存信息的效用"里分离（MGPO 教训）。三落点：①提炼门控（uplift 低于阈值拒绝入库，"学会不记住"）；②提炼权重（蒸馏 confidence/效用=uplift，insightConfidence 降级为辅助）；③相位写入（uplift 符号→相位 θ∈{0,π}，蒸馏时即定"成功放大器/失败避让器"，检索侧 v0.7.0 直接消费）。新独立 domain `reasoning_memory_distill_causal` + MemoryItem additive 字段（distillUplift/distillInherited/distillSamples/distillPhase/distillGate/CA-ID Card 三字段，全部带默认值）+ 配置默认关闭，零回归可测。识别假设 7 条与越界 6 条写入 §23.6：增量因果符号在纯文本历史下可能不可识别（Credit Assignment 2604.09459）→ 收缩 + restored-state 反事实对为 gold、线上不做 rollout；不委托 LLM 判因果。依据 MGPO 2609.37930 / TQG-Exp 2610.14877 / Credit Assignment 2604.09459（web_fetch 2026-11-11 已复核）+ MemTrial 2610.11732 / COFFEQA 2610.12837（待复核），详见 §23 与 `docs/research-notes/08-causal-distillation.md`。
