# 01 · DSH 契约核实：storage-domain / dsh-tools / 现有插件模式

> 核实范围：本仓库 `node_modules` 内实际安装的 `@deepseek-ai/dsh-storage-domain`、`@deepseek-ai/dsh-storage`、`@deepseek-ai/dsh-tools` 的类型声明与编译产物（`lib/`），以及本插件 `src/index.ts`、`src/domain.ts`、`src/skill-domain.ts`。
> 结论一律附证据（文件路径:行号）。行号以本仓库 `node_modules` 中 0.0.1-rc.1 版本产物为准。

---

## (a) version=1 domain 新增 `templates` 表：旧介质能否继续打开？

**结论：可以。** 新增一张表（不改 version、不改既有表 schema）不会破坏旧介质；兼容性关卡是 **version 戳**，不是表集合。校验发生在 **domain 层、打开时的持久化读取边界（open → loadAll）**；不存在"表结构哈希"。

### 证据链

1. **`defineDomain` 只校验名称/版本/global-null，不产生任何哈希**
   `node_modules/@deepseek-ai/dsh-storage-domain/lib/index.js:61-67`：
   - domain/table 名须匹配 `UNIT_NAME_RE`；
   - `version` 必须是非负整数；
   - `global` schema 不得接受 `null`（`null` 是介质"从未写入"哨兵）。
   - **没有** 对 tables 集合、schema 内容做哈希或固化。`defineDomain` 原样返回 spec。

2. **描述符投影只把表名列表传给后端**
   `node_modules/@deepseek-ai/dsh-storage-domain/lib/index.js:73-80`（`descriptorOf`）：
   ```js
   { name, version, tables: Object.keys(spec.tables), hasGlobal }
   ```
   `tables` 仅作为静态形状描述传入 `KvFacet.open`。

3. **后端 KvFacet 契约只定义了两种拒绝原因，均与表集合无关**
   `node_modules/@deepseek-ai/dsh-storage/lib/types/backend.d.ts:26-39`：
   > "A version already stamped on the medium that differs from `descriptor.version` rejects with `version-mismatch`; a medium that cannot be parsed as this unit rejects with `malformed-medium`."

   完整的后端错误码集见 `node_modules/@deepseek-ai/dsh-storage/lib/types/error.d.ts:6`：
   `'backend-not-found' | 'form-not-mounted' | 'duplicate-backend' | 'duplicate-mount' | 'version-mismatch' | 'malformed-medium' | 'closed'`
   —— **不存在任何"表集合不一致"错误码**。

4. **Domain 层打开时逐表回填，缺失表 = 空表**
   `node_modules/@deepseek-ai/dsh-storage-domain/lib/index.js:350-356`（`DomainFacility.open`）：
   ```js
   for (const [table, tableSpec] of Object.entries(spec.tables)) {
     const records = new Map();
     for (const [key, raw] of Object.entries(snapshot.tables[table] ?? {})) ...
   }
   ```
   - spec 里新声明的 `templates` 在旧介质上不存在 → `snapshot.tables['templates'] ?? {}` → **空 Map，正常打开**。
   - 介质上存在、spec 未声明的表 → **被忽略**（domain 层只遍历 spec.tables，见 `lib/index.js:352`），不可见也不校验。

5. **校验层 = domain 层，时机 = 打开时的持久化读取边界（每条记录 zod parse）**
   `node_modules/@deepseek-ai/dsh-storage-domain/lib/index.js:352-355` + `:394-407`（`parseRecord`）：每条已存记录用 `tableSpec.valueSchema.parse(raw)` 解析，失败抛 `DomainError('invalid-record')`，`detail = { table, key }`（`lib/index.js:14-29`）。
   **写入路径不再校验**：`KvTableImpl.put/update` 直接 `unit.putRecord`（`lib/index.js:246-275`）；global 同理，类型声明明说 "validation happens at the durable read boundary"（`node_modules/@deepseek-ai/dsh-storage-domain/lib/types/domain.d.ts:22-29`）。
   → 也就是说：**旧数据能否打开，只取决于旧记录能否通过（未修改的）zod schema**。旧表 schema 不动，新增表不影响旧记录。

6. **版本戳语义**
   `node_modules/@deepseek-ai/dsh-storage-domain/lib/types/spec.d.ts:34`：
   > "Domain format version; a medium stamped with a different version rejects at open."

   `node_modules/@deepseek-ai/dsh-storage/lib/types/backend.d.ts:44-45`：version "stamped on the medium at first materialization"。
   → 版本号是唯一的介质级兼容门。**保持 `version: 1` 不变 = 旧介质必开**；若升 version 才触发 `version-mismatch`。

7. **本仓库自身的实证**
   - `src/domain.ts:42`："Additive defaults keep the existing v1 storage records readable." —— 项目已在 v1 domain 内用 zod `.default()` 加过字段（`:43-52`），`src/domain.ts:110`："Defaults admit legacy v1 audits written with { semantic, structure }." —— 证明同 version 下"新增字段（additive schema）"路径已被实践。
   - `src/skill-domain.ts:4-7`：skills 之所以放在**独立 domain**（`reasoning_memory_skills`, version 1），注释写明是为了"existing reasoning-memory domain (v1) and its data stay untouched"——这是**设计选择（隔离回滚面）**，不是契约要求。给 `reasoning_memory` v1 直接加 `templates` 表在契约上同样安全。

### 落地建议（针对 `templates` 表）

- 方案 A（推荐先做）：在 `reasoningMemoryDomain`（`src/domain.ts:133-141`）加
  `templates: domainTable<string, TemplateRecord>(templateSchema)`，version 保持 1。
- 方案 B（与 skills 一致）：新建 `reasoning_memory_templates` domain, version 1。适合模板层需要独立生命周期/独立回收的场景。
- 两条铁律（来自契约）：
  1. **一旦有数据写入，表记录 schema 只能 additive**（新字段一律 zod `.default()`），否则下次打开 `invalid-record` 会让整个 domain 打不开（`open` 失败时 unit 会被关闭并重抛，`lib/index.js:365-372`）。
  2. 表名必须匹配 `UNIT_NAME_RE = /^[a-z][a-z0-9_]*$/`（`node_modules/@deepseek-ai/dsh-storage/lib/index.js:80`；storage-domain 侧同款校验 `lib/index.js:62-64`）。
- 注意点：本仓库 node_modules 只装了两个契约包；**具体 JSON 介质后端实现随 Harness 打包在 `app.asar` 内**（`C:\Users\w2132\AppData\Local\Programs\DeepSeek Harness\resources\app.asar`，未解包的 JS 无法直接引用行号）。以上结论依据的是打包进 node_modules 的**规范契约文本**（`backend.d.ts` 是 "normative contract text ... the shared conformance suite in `tests/contract.ts` checks every rule"，`backend.d.ts:76`）。契约只允许 version-mismatch/malformed-medium 两种拒绝，表集合变化在契约层面不可能拒绝。

---

## (b) `defineTool`：schema 库、可选参数/默认值、result payload 限制

**结论：参数与输出 schema 用的既不是 zod 也不是 JSON Schema 原文，而是 dsh-tools 自带的统一 JSON-value DSL（`ParameterSchemaSpec` / `ValueSchemaSpec`），编译到强约束 JSON Schema 子集后校验。`default` 是不生效的注解；可选参数 = 不加 `required: true`；result 必须是可无损 JSON 序列化的值并通过 `output.schema` 校验，原生工具无文档化字节上限。**

### 1. 用什么库声明

- **不是 zod**。`defineTool` 在 `node_modules/@deepseek-ai/dsh-tools/lib/index.js:836-882`；DSL 编译器在 `:534-793`。
- 类型面：`node_modules/@deepseek-ai/dsh-tools/lib/types/schema.d.ts:71-88`：
  - `ValueSchemaSpec` = `type: 'string'|'number'|'integer'|'boolean'|'null'|'array'|'object'|'json'` 或 `oneOf`（`:72`）；标量支持 `enum`/`const`（`:20-47`）。
  - `ParameterSchemaSpec` = 隐式开放对象，每个属性 `ValueSchemaSpec & { required?: true }`（`:74-84`）。
- 三个库的分工（勿混用）：
  | 用途 | 库 | 证据 |
  |---|---|---|
  | 插件 `Config` | **schemastery** | `src/index.ts:6,102-144`；dsh-storage-domain 同样（其 `lib/index.js:301-304`） |
  | storage domain 记录 schema | **zod** | `src/domain.ts:2,35-75`；`dsh-storage-domain/lib/index.js:36-39` 注释 "Record schemas are zod" |
  | `defineTool` 参数/输出 | **dsh-tools DSL** | 本节全部证据 |
- DSL 编译产物受"强制 JSON Schema 子集"约束（`dsh-tools/lib/index.js:32-47`）：约束关键字仅 `type/oneOf/properties/required/additionalProperties/items/enum/const`，注解仅 `description/title/default/examples`；`oneOf` 必须恰好命中一支（`:416-419`）；对象节点必须显式声明 `additionalProperties: true|false`（`:697`，README.md:95）。

### 2. 可选参数与默认值

- **可选**：省略 `required: true`。`ParameterPropertySpec` 定义（`schema.d.ts:74-76`）；类型推断 `InferArgs` 把无 `required` 的键标为可选（`schema.d.ts:104-108`）。隐式参数根是**开放**的（多余键不拒绝，README.md:95 "The implicit parameter root is open"）。
- **默认值不生效**：
  - `default` 是 "Non-validating default annotation; it must be lossless JSON data"（`schema.d.ts:14-15`）。
  - README.md:95 原文："**Defaults are not applied**; open objects without `properties` and arrays without `items` receive only a container type check."
  - 本插件手工落默认的范式：`src/index.ts:987-988` `Math.max(1, Math.min(20, Math.floor(args.k ?? 4)))`、`(args.route ?? 'auto')`。
  - `examples` 同样仅注解（`schema.d.ts:16-17`）。
- 校验时机：registry 在执行前校验模型参数，违规抛 `ToolArgsError`（code `INVALID_ARGS`），走正常错误结果路径（`lib/index.js:862-865`；README.md:95）。`validateArgs` 公开导出（`lib/index.js:826-828`）。

### 3. result payload 限制

- **值域**：成功值为 `JsonValue`（lossless JSON：无 `undefined`/BigInt/环/稀疏数组/-0/异形对象）。README.md:48："Success is `{ isError:false, value:JsonValue, ... }`"；:95："the registry snapshots and validates the returned lossless JSON before presentation"。
- **schema 约束**：`output.schema` 经 `valueSchemaSpecToJsonSchema` 编译并过 `assertSupportedJsonSchema`（`lib/index.js:846, 790-794`）；返回值必须符合该 schema。对象根必须显式 `additionalProperties`（见上）。`type:'json'` 是作者专用"任意无损 JSON"节点（`schema.d.ts:63-66`）。
- **字节上限**：**原生工具（native mode）没有文档化的单次 result 字节上限**。仅 Code Mode `run_code` 有：worker `maxOutputBytes` 默认 **64 MiB**（只约束外层 logs/result/failure 组合负载，README.md:123）；且 "Code Mode intermediate values are execution-local and unbounded by bytes"（README.md:195）。会话语义层的 spill 策略（`tools/code-dispatch-log`）可把超大的 `tool/code-dispatch` content 换成 preview+locator（README.md:195）。
- **其他定义期限制**：`timeoutMs` 必须为正有限数，仅元数据、registry 不强制执行（`lib/index.js:844`；README.md:99 "declarative only"）；缺失或不受支持的 output 声明、非正/非有限 `timeoutMs` 在注册期即失败（README.md:20）。
- 执行签名：`execute(args: InferArgs<S>, exec: ToolRunContext): Promise<InferValue<O>>`（`schema.d.ts:208`）；`exec.signal` 只读、协作式取消（README.md:33-35）。类型推断精确到 16 层容器，之后回退 `JsonValue`（README.md:93，`schema.d.ts:124`）。

---

## (c) `src/index.ts` 现有工具注册与 storageDomain 用法

**结论：`inject: ['tools','storageDomain']`；`apply` 里 `await ctx.storageDomain.open(domainSpec)` 拿句柄 → `domain.table(name)` 拿表 → 同步 `get/entries/size`、`await put/delete`（`update` 未使用）；所有写前先 zod `parse`；12 个工具全部 `ctx.tools.register(defineTool({...}))`。**

### 注册与生命周期

- 服务注入：`src/index.ts:53-54`
  ```ts
  export const name = 'reasoning-memory'
  export const inject = ['tools', 'storageDomain']
  ```
  `storageDomain` 服务由 domain 插件 `provide("storageDomain", facility)`（`dsh-storage-domain/lib/index.js:425`），facility 上暴露 `.open(spec)`（`:341-373`）。
- 打开 domain：`src/index.ts:594-599`
  ```ts
  const domain = await ctx.storageDomain.open(reasoningMemoryDomain)
  const memories = domain.table('memories')
  const retrievals = domain.table('retrievals')
  const skillsDomain = await ctx.storageDomain.open(reasoningMemorySkillsDomain)
  const skills = skillsDomain.table('skills')
  ```
- 关闭：`ctx.effect` 清理器里 `domain.close()` / `skillsDomain.close()`（`src/index.ts:816-823`）。
- domain spec：`src/domain.ts:133-141`（`name:'reasoning_memory', version:1, tables:{memories,retrievals}`，记录 schema 为 zod）；`src/skill-domain.ts:34-41`（独立 v1 domain）。

### 读写模式（现有代码中的全部形态）

- 读（同步，内存态）：`memories.get(id)`（`:1254,1269`）、`[...memories.entries()]`（`:600,1202,1459,1787`）、`memories.size`（`:990,1456`）、`skills.entries()`（`:1750,1787,1814`）。
- 写（异步，先持久化再更新内存，链上串行）：
  - `await memories.put(item.memoryId, item)`（`:1452,1586`，feedback 路径 `:1272`）
  - `await retrievals.put(retrievalId, audit)`（`:1196`）
  - `await retrievals.delete(key)`（`:1205`，溢出回收）
  - `await skills.put(record.skillId, record)`（`:1767`）
  - `update()` 未使用；如需要读-改-写原子性可用 `table.update(key, fn)`（`dsh-storage-domain/lib/types/domain.d.ts:70-77`）。
- **写入前插件侧 zod 校验**（与 domain 层"读取边界校验"双保险）：
  - `retrievalRecordSchema.parse({...})`（`:1177-1195`）
  - `memoryItemSchema.parse(item)`（`:1450,1584`）、`memoryItemSchema.parse({...item, feedback, updatedAt})`（`:1271`）
  - `skillSchema.parse({...})`（`:1754-1766`）
- 溢出回收范式：按 `createdAt` 排序删最旧（memories `:1456-1468`，retrievals `:1200-1206`）。
- 事件面：写入经 `domain/changed` 事件广播（`dsh-storage-domain/lib/index.js:203-209`），包内 invariant 会校验事件与内存态一致（`dsh-storage-domain/lib/invariant.js:19-36`）——插件若监听该事件做二级索引同步是受支持的（本插件目前不用事件，而是直接写后同步 `lexicalIndex`/`builtinStore`/`vectorIndex`，见 `:1453-1454` 等）。

### 工具注册清单（`ctx.tools.register(defineTool({...}))`）

| 行号 | 工具 | 备注 |
|---|---|---|
| `src/index.ts:924` | `memory_begin_task` | 参数含可选 `goal`/`task_family`（`:929-930`） |
| `src/index.ts:973` | `memory_retrieve` | 可选 `k`（默认 4、上限 20，`:978,987`） |
| `src/index.ts:1233` | `memory_feedback` | |
| `src/index.ts:1286` | `memory_save_outcome` | |
| `src/index.ts:1491` | `memory_consolidate` | |
| `src/index.ts:1623` | `memory_query` | |
| `src/index.ts:1682` | `memory_api_status` | |
| `src/index.ts:1712` | `memory_publish_skill` | |
| `src/index.ts:1781` | `memory_list_skills` | `parameters: {}` 亦合法（`:1784`） |
| `src/index.ts:1806` | `memory_load_skill` | |
| `src/index.ts:1838` | `memory_load_skill_resource` | |
| `src/index.ts:1861` | `memory_end_task` | |

所有输出均为 `output: { schema: <as const 对象>, render: renderJson }`，`renderJson` 在 `:572-574`（JSON.stringify 2 空格）。输出 schema 对象在本插件内以 `as const` 直接写 DSL 字面量（如 `MEMORY_VIEW_SCHEMA` `:258-298`），这是本仓库的既定风格。

### 模板层（综述路径 A）落地时的直接启示

1. 新表 `templates` 可安全加进 v1 domain（见 (a)）；记录 schema 用 zod 且字段只能 additive。
2. 新工具（如 `memory_find_template`）用 `defineTool` + DSL；可选参数不加 `required`，默认值在 execute 内 `?? ` 兜底（DSL 的 `default` 注解不生效）。
3. 模板检索输出走 `output.schema`（对象根、`additionalProperties: false`、逐字段 `required: true` 是本仓库风格）。
4. 写模板时先 `templateSchema.parse(...)` 再 `await templates.put(...)`；读取全量走 `[...templates.entries()]`（内存态同步读，无需 async）。
