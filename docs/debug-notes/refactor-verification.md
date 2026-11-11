# 解耦重构独立验证报告（refactor verification）

- 验证对象：`src/index.ts`（3,599 行单体）→ 组合根 + 层模块 + 7 组 `tools/*`；Python 测试单体拆分；Python `templates.py` 拆分 + 门面。
- 基线：`HEAD = 6c26f8b`（`git show HEAD:<path>` 为唯一 pre-refactor 证据）。
- 环境：Windows 11、Node v24.12.0、Python 3.13.9（DPsh 文件沙箱 workspace-write）。
- 快照稳定性：验证开始与结束时 `src/**` 全部 35 个文件的 SHA-256 完全一致；`git status` 在验证期间只有 Lead 清理 scratch 脚本（`refactor-*.mjs` / `refactor-analysis.json`）这一变化。结论针对上述最终快照。
- 只读约束：本次验证未修改任何源码；本文件是唯一写入。`artifacts/`、`.npm-cache-tmp/`、临时解包目录均已清理。

## 结论总览

| # | 检查 | 结论 |
|---|---|---|
| 1 | 干净构建 `tsc -p tsconfig.json` + dist 产物 | **PASS** |
| 2 | 架构守卫 `node scripts/verify-structure.mjs` | **PASS**（并做了变异测试，守卫非空转） |
| 3 | Python 套件 187 tests | **PASS** |
| 4 | Node 套件 47 pass / 0 fail（`--test-isolation=none`） | **PASS**（沙箱限制已说明） |
| 5 | `npm pack` → 解包 → verify-bundle → 解包产物启动冒烟 | **PASS**（需仓库内解包布局，见 §5） |
| 6a | 22 个 `memory_*` 工具名逐一保留、无重复 | **PASS** |
| 6b | 冻结文件（domain/bridge 等）`git diff HEAD` 为空 | **PASS**（18 个既有 src 文件全部零改动） |
| 6c | `templates.py` 门面 API 面与 HEAD 等价 | **PASS**（62 个名字、签名、常量值全一致） |
| 6d | 测试未被削弱 | **PASS**（187 个 test 方法 AST 逐一对齐 HEAD） |
| 7 | 新模块代码质量 / hidden behavior change | **PASS，2 个 LOW 发现 + 4 条 INFO** |
| 8 | 变更范围 | **PASS**（仅预期文件；早期 scratch 脚本已被 Lead 清理） |

未发现会导致行为回归的缺陷；唯一真实行为差异是**工具注册顺序**（LOW，见 F1）。

## 1. 构建

```
Remove-Item -Recurse dist; node node_modules/typescript/bin/tsc -p tsconfig.json
→ TSC_EXIT=0
```

`dist/` 重新生成 35 个 `.js`（+ `.d.ts`），含要求的 `dist/index.js`、`dist/runtime.js`、`dist/storage-layer.js`、`dist/index-layer.js`、`dist/capture.js`、`dist/skill-export.js` 及 `dist/tools/{admin,documents,memory-consolidation,memory-lifecycle,memory-retrieval,skills,templates}.js`。`dist/index.js` 为 1,206 byte（守卫上限 8,192），入口 34 行（上限 60）。`tsconfig.json` 未改动且 `include: src/**/*.ts` 已覆盖新目录。

## 2. 架构守卫

```
node scripts/verify-structure.mjs
→ architecture verification OK: 35 TS files, 7 tool groups, entry 34 lines   (exit 0)
```

守卫不是空转——在临时副本上做变异测试（不改仓库）：

| 变异 | 结果 |
|---|---|
| `tools/admin.ts` 增加 `import { createRuntime } from '../runtime.js'` | FAIL（捕获 value import） |
| `index.ts` 增加 `defineTool` token | FAIL |
| 新增 951 行 `src/big.ts` | FAIL（900 行上限） |
| 从 `index.ts` 删除 `registerAdminTools(ctx, runtime)` 调用 | FAIL |
| 把 `src` 完整复制后原样运行 | OK（证明变异失败非复制噪声） |

## 3. Python 套件

```
python -m unittest discover -s test -v   → Ran 187 tests ... OK   (exit 0)
npm run test:python (scripts/run-python-tests.mjs, spawnSync stdio:inherit) → Ran 187 tests ... OK (exit 0)
```

按文件分布（合计 187）：test_adversarial 5、test_causal 20、test_distillation 21、test_distill_causal 14、test_documents 31、test_eval_schema 12、test_json_bridge 4、test_quantum 22、test_retrieval 14、test_templates 44。`_support.py` 不匹配默认 discovery 模式 `test*.py`，不会被误收集。

## 4. Node 套件

```
node --test --test-isolation=none test/*.test.mjs
→ tests 47 / pass 47 / fail 0   (exit 0)
```

沙箱限制确认：默认隔离模式 `node --test test/*.test.mjs` 在本机 11 个文件全部因 per-file 子进程 spawn 被拒而失败（EPERM，`✖` × 11，pass 0），属环境限制而非重构缺陷；CI（ubuntu/windows，非沙箱）运行默认模式。关于“是否掩盖问题”：11 个测试文件中唯一的进程级全局改动是 `test/api-config.test.mjs:64,101` 临时设置/删除 `TEST_EMBED_KEY`（已清理），没有 `chdir`、没有跨文件共享单例；所有用例自建临时目录/实例，合并进程运行未发现相互依赖迹象，但本机无法独立复跑默认模式（残余不确定性见 §9）。

## 5. 打包/部署契约

本机默认 npm cache 在 workspace 之外被沙箱拒绝（EPERM），改用仓库内 cache：

```
npm pack --cache .npm-cache-tmp --pack-destination artifacts
→ dsh-reasoning-memory-0.8.0.tgz: 96 files, 169.5 kB package, 744.2 kB unpacked (exit 0)
tar -xzf artifacts/dsh-reasoning-memory-0.8.0.tgz -C artifacts/bundle
node scripts/verify-bundle.mjs artifacts/bundle/package
→ bundle verification OK (exit 0)
PLUGIN_BUNDLE_ENTRY=artifacts/bundle/package/dist/index.js node test/plugin-smoke.test.mjs
→ ✔ the built bundle boots in a mock DSH host and runs the full memory lifecycle
  tests 1 / pass 1 / fail 0 (exit 0)
```

tarball 同时包含：7 个 `dist/tools/*.js`、全部 28 个其它 `dist/*.js`、21 个 `python/reasoning_memory_core/*.py`（含 6 个 `template_*.py`）与 `python/reasoning_memory.py`——与 `scripts/verify-bundle.mjs:36-66` 的清单和实际 `src/` 文件集合完全对应（35 = 28 + 7），无遗漏模块。
`dist/tools/*.js` 中也确认存在全部 22 个工具名。

注意点（非缺陷）：把 tarball 解包到**仓库外**（如 `$env:TEMP`）再启动会 `ERR_MODULE_NOT_FOUND: 'yaml'`——裸依赖需由宿主 profile 的 `node_modules` 解析；`cd.yml:75-79` 的 CD 正是“解包到 checkout 内”再做冒烟，本报告按该 canonical 布局验证通过。另：`artifacts/` 实际**不在** `.gitignore`（只有 `dist/`、`node_modules/` 等），本地跑 pack 后需手动清理，已清理。

## 6. 行为等价性（独立于测试套件）

### 6a 工具面：PASS

- HEAD 单体中的 22 个 `name: 'memory_*'`（`git show HEAD:src/index.ts`）与新 `src/tools/*` 的集合**完全相同**，两边各 22 个、无重复、无新增。
- 22 个 `ctx.tools.register(defineTool(...))` 全部只出现在 `src/tools/*`（分布 5/2/1/4/5/4/1）；`src/` 其它文件 0 处注册。
- 新 `src/index.ts:28-34` 恰好各调用一次 7 个 `register*Tools`；构建产物 `dist/tools/*.js` 中 22 个名字齐全。

### 6b 冻结文件：PASS

`git diff HEAD --` 对以下文件均**为空**：`src/domain.ts`、`document-domain.ts`、`causal-domain.ts`、`quantum-domain.ts`、`distill-causal-domain.ts`、`skill-domain.ts`、`builtin-store.ts`、`python.ts`。额外核对了其余既有模块（`api-config.ts`、`distillation-llm.ts`、`document-index.ts`、`document-vector.ts`、`embedding-client.ts`、`enterprise-rules-rag.ts`、`lexical-index.ts`、`search-text.ts`、`skill-format.ts`、`vector-index.ts`）也全部零改动——storage-domain v1 additive 兼容（6 个 domain 均 `version: 1`、schema 未动）与 Python JSON 桥均未被触碰。

### 6c Python `templates.py` 门面：PASS

把 HEAD 版 `templates.py` 放进临时包副本（其余模块用工作区版本），分别 `import reasoning_memory_core.templates` 对比：

- 非 dunder 名字集合：62 vs 62，**完全相同**（含 `BUILTIN_TEMPLATES`、各 `MAX_*`、`TASK_PATTERNS`、`_clip` 等私有名，以及 `re/time/uuid/Counter/SequenceMatcher/Any` 等历史导入名）。
- 逐名比较签名、类、常量值（dict/list/str/数字/regex pattern+flags）：**0 差异**。
- 行级 provenance：HEAD 版 854 条非注释非空行**全部 0 条缺失**地出现在 7 个新文件中；新增行仅为 import/模块 docstring。HEAD 12 条 `#` 注释全部保留。
- 顶层函数：HEAD 20 个 → 新 20 个同名（拆分到 `template_{build,classify,match,mining,plan,publish}.py`），无丢失/改名。
- 依赖方向：`template_*.py` 不 import 门面，无环；`service.py`/`doc_rag.py`/`reasoning_memory.py` 仍 `from .templates import ...`（与 HEAD 相同）。

### 6d 测试未削弱：PASS

用 `ast` 对比 HEAD `test/test_reasoning_memory.py` 与拆分后 10 个文件（+`_support.py`）：

- 187 个 test 方法（在 22 个 `unittest.TestCase` 类中）：名字集合完全相同，**逐方法 AST dump（函数体）完全相同**，0 个改动、0 个重命名、0 个 skip 新增。
- 共享夹具 `ROOT` / `make_memory` / `tool_events` 与 HEAD 版本 AST 完全相同（`test/_support.py`）。
- HEAD 模块级 setup（`ROOT = Path(__file__).resolve().parents[1]`、`sys.path.insert(0, str(ROOT / "python"))`、`import reasoning_memory as core`、公共 import）全部保留；10 个拆分文件都保留了 `if __name__ == "__main__": unittest.main()`，直接运行单文件仍可用。
- 全仓库 Python 测试无 `mock.patch`/monkeypatch 用法，不存在“把失败测试打补丁绕过”的迹象。

## 7. 代码审查：发现（按严重度）

### F1 — LOW（唯一真实行为差异）：工具注册顺序相对 HEAD 改变

- 实际（`src/index.ts:28-34` + 各 `tools/*` 内注册顺序）：
  `begin_task` → `feedback` → `save_outcome` → `record_counterfactual` → `end_task` → `retrieve` → `query` → `consolidate` → `plan_task` → `list_templates` → `load_template` → `publish_template` → `import_document` → `list_documents` → `load_document` → `retrieve_document` → `delete_document` → `api_status` → `publish_skill` → `list_skills` → `load_skill` → `load_skill_resource`。
- HEAD 原顺序：`begin_task` → `retrieve` → `feedback` → `save_outcome` → `record_counterfactual` → `consolidate` → `query` → 模板 4 → 文档 5 → `api_status` → 技能 4 → `end_task`。
- 差异：`retrieve`/`query` 被移到 lifecycle 组之后；`end_task` 从“最后注册”变为 lifecycle 组内第 5 个；组内 `feedback` 相对 `retrieve` 前移。
- 影响面核查：22 个名字无重复（重复注册会覆盖，未见）；Cordis `ToolRegistry.register` 用 `layer.tools.insert(name, definition)`，`view()` 用 `new Map(entries)` 保留插入顺序（`node_modules/@deepseek-ai/dsh-tools/lib/index.js:2710,2786`），因此注册顺序会进入 registry 视图；但 Code-Mode 的 SDK 渲染按字典序排序（同文件 `1598-1608`、`2294-2306`），且没有测试或代码路径依赖顺序。实际影响应限于“非 Code-Mode 工具清单的呈现顺序 / prompt 缓存命中”，不是功能错误。
- 期望 vs 实际：若要求“移动大部分 verbatim 且顺序不变”，此处不符；建议恢复 HEAD 顺序，或在评审中明确接受并记录。最小复现：比较 HEAD `src/index.ts` 中 22 个 `name:` 的出现顺序与新 7 个 `tools/*.ts` 的拼接顺序。

### F2 — LOW（文档不准确）：`docs/ARCHITECTURE.md:57` 声称 `tsc` 能暴露环依赖

原文：“校验方式：相对 import 只应指向同层或下层；`tsc -p tsconfig.json` 会暴露环依赖与越层引用。” 实际 `tsc` 不校验架构方向，也接受循环 import；当前代码里就存在 1 个 type-only 环：`src/index-layer.ts:17` `import type { RuntimeState } from './runtime.js'` 与 `src/runtime.ts:11` value-import `index-layer` 互相引用。该环被 `import type` 擦除、运行时无环（全图 0 个 value cycle），tsc 通过、无功能影响；但文档该句会误导后续维护者，建议改为“由 `scripts/verify-structure.mjs` 守护；`tsc` 只做类型检查”。

### F3 — INFO（守卫覆盖缺口，非当前违规）

`scripts/verify-structure.mjs` 的“工具禁止互相 import”只匹配 `from '../tools/...`（第 60 行），同目录兄弟 import（如 `from './admin.js'`）可绕过；“工具不得 value-import 层”只匹配 `import { ... } from '../<layer>.js'`（第 62 行），`import * as x from '../runtime.js'` / default import 可绕过；也不检测“同一 `register*Tools` 被调用两次”。当前代码 0 违规（独立 import 图扫描确认：tools→tools 0 条、层→tools 0 条、tools value-import runtime 0 条、全图仅上述 1 个 type-only 环）。建议顺手收紧正则/增加 callsite 计数，属加固而非回归。

### F4 — INFO：启动期副作用顺序微调

`runtime.ts:36-42` 的 `checkPythonVersion` 现在在 `createStorageLayer`（打开 6 个 domain）**之前**执行；HEAD 在 domain 打开之后（HEAD L1272）。两者都只 `ctx.logger.error` 不阻塞，最终状态一致，只有日志相对顺序/“storage 打开抛错时是否已记录 python 错误”这一边缘差异。`storage.close()`（6 个 domain）先于 `indexes.close()`（apiConfig.stop + builtinStore.close），与 HEAD disposer 顺序（HEAD L1576-1587）一致。

### F5 — INFO：Python 门面的 monkeypatch 语义在原理上变化（无消费者依赖）

HEAD 中 `plan_task`/`mine_templates` 通过**同一模块**全局名解析 `classify_task_pattern` 等；拆分后它们从 `template_plan.py`/`template_mining.py` 各自的模块全局解析，所以外部 `patch('...templates.classify_task_pattern')` 不再影响这些调用（`templates.py:23-27` 的注释却声称“测试套件会把名字 patch 到该模块上”）。实测全测试套件无任何 monkeypatch，`service.py`/`doc_rag.py`/`reasoning_memory.py` 一直是 import 时绑定函数对象，行为不变；仅对“仓库外 monkeypatch 门面名”的用法有理论影响。

### F6 — INFO：注释/文案

- HEAD 的 harmful-feedback 窗口注释含 “(final spec 5)”，在 `src/tools/memory-lifecycle.ts:25-27` 被改写为同义表述（规则保留、spec 引用丢失）。
- HEAD 的 filesystem export 注释被改写进 `src/skill-export.ts:1-3` 头注释（规则保留）。
- 注释行多重集对比：HEAD 85 条 TS 注释中仅上述 6 行因换行/改写不在新模块中，其余 79 条逐字保留；Python 侧 12 条 `#` 注释全部保留。**未发现承载规则被删除。**

## 8. 变更范围

最终 `git status --short` 与 `git diff --stat`：仅以下预期变更，无越界文件。

- 修改：`.github/workflows/{ci,cd}.yml`（+verify:structure 步骤）、`README.md`、`reasoning-trajectory-memory-design.md`、`package.json`（0.8.0、verify:structure、test 链）、`package-lock.json`（0.3.0→0.8.0）、`scripts/verify-bundle.mjs`（扩清单+入口体积上限）、`python/reasoning_memory_core/templates.py`（门面）、`src/index.ts`（3599→34 行）。
- 删除：`test/test_reasoning_memory.py`（被 10 个 `test_*.py` + `_support.py` 取代）。
- 新增：`src/` 的 11 个新文件 + `src/tools/` 7 个文件、`python/` 6 个 `template_*.py`、`test/` 11 个文件、`scripts/verify-structure.mjs`、`docs/ARCHITECTURE.md`。
- 验证中途存在过的 `refactor-analysis.json` / `scripts/refactor-*.mjs` 已由 Lead 在最终快照前删除；最终快照无 scratch 残留。
- 版本号 0.8.0 与 lock、design doc 一致；三个 YAML（含 `cordis.patch.yml`）用 `yaml` 包解析通过；`tsconfig.json`、`cordis.patch.yml` 未改。

## 9. 残余风险 / 未证实项

1. **默认 Node 隔离模式无法在本沙箱复跑**（EPERM）；CI 的真实双平台结果未在本地验证。已用 `--test-isolation=none` 跑全量 47 用例，并检查无跨文件全局状态依赖。
2. **F1 注册顺序**：若宿主对非 Code-Mode 工具清单顺序敏感（prompt 缓存/顺序断言），则是唯一潜在可观察差异；无测试覆盖该顺序。
3. Python 门面 monkeypatch 语义（F5）与守卫正则缺口（F3）属理论风险，当前代码/测试/CI 均无触发点。
4. 本报告以最终快照为准（src 哈希稳定）；若之后源码再变，需重跑 §1–§5 的判定命令。
5. 行级 provenance / 逐工具 LCS 对比在归一化时忽略了缩进、`export/const` 前缀与 `rt.`/`state.` 前缀；语义等价已通过逐站点核对 5 个可变标志补齐：HEAD 中每个标志出现的行数（含声明/初始化行）与新版（runtime 初始化 + 使用点）逐一相同——`vectorIndexDegraded` 11、`vectorIndexSynchronized` 3、`builtinStoreStatus` 15、`enterpriseRuleRagInitError` 4、`documentVectorDegraded` 7；初始值完全一致，且工具层全部通过 `rt.state.*` 实时读写（无解构快照）。

## 10. 验证后修复记录（Lead，验证快照之后）

验证报告锁定快照后，Lead 按 F1–F6 做了如下处理，并重跑了全部检查：

- **F1 注册顺序**：确认**显式接受**并记录（`docs/ARCHITECTURE.md` §2 新增说明：注册顺序按生命周期分组是刻意设计，工具以名称为查找键，Code-Mode SDK 渲染按名称排序，因此该顺序不构成外部契约）。未回退逐项顺序，避免为一个非契约差异把工具注册重新拆散。
- **F2 文档不准确**：已修正 `docs/ARCHITECTURE.md` 中"tsc 暴露环依赖"的表述——tsc 只负责类型与模块解析，分层/互导/文件规模规则由 `verify:structure` 静态守护；并注明唯一存在的 type-only 环（`index-layer.ts` ↔ `runtime.ts` 的 `RuntimeState`）在运行时被擦除、全图无值层环。
- **F3 守卫缺口**：`scripts/verify-structure.mjs` 已收紧——同目录 `./` 兄弟导入、namespace/default 值导入层、`register*Tools` 调用重复计数全部纳入检查；并做了变异测试（临时新增 `src/tools/bad-guard-probe.ts` 的 `./admin.js` 兄弟导入被正确判 FAIL，删除后恢复 OK）。
- **F4 启动顺序**：已把 Python 版本门移到 `createStorageLayer` 之后、模板播种之前（`storage.ensureTemplatesSeeded()` 由 runtime 在门之后触发），恢复 HEAD 的 open → gate → seed 顺序；`storage.close() → indexes.close()` 的销毁顺序不变。
- **F6 注释**：harmful-feedback 注释已补回 "(final spec 5)" 引用。
- **F5（门面 monkeypatch 语义）**：保留为已记录的理论风险，仓库内无消费者依赖。
- 额外：`.gitignore` 增加 `/artifacts/`（CD 产物目录此前未忽略）。

修复后重跑（同一工作副本）：

- `tsc -p tsconfig.json` → exit 0；`node scripts/verify-structure.mjs` → OK（35 TS files, 7 tool groups, entry 34 lines）。
- `node --test --test-isolation=none test/*.test.mjs` → tests 47 / pass 47 / fail 0。
- `python -m unittest discover -s test` → Ran 187 tests ... OK。
- verify:structure 变异测试通过（见 F3）。
- 解包冒烟：把 `dist/` + `python/` + `package.json` 复制到仓库内 `.bundle-stage/package`（与 CD 的"在 checkout 内解包"布局一致）后 `PLUGIN_BUNDLE_ENTRY=.../dist/index.js node test/plugin-smoke.test.mjs` → 1 pass / 0 fail；`node scripts/verify-bundle.mjs` → OK。（仓库外临时目录解包会因裸依赖解析不到 `node_modules` 而 `ERR_MODULE_NOT_FOUND`，属布局限制，与重构无关。）
- 说明：`node --test` 默认隔离模式在该沙箱仍因子进程 EPERM 不可用；CI 双平台按默认模式运行。
