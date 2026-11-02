# 测试基线报告（T-debug-1 · 阶段一）

- 日期：2026-11-02
- 执行人：debugger（调试与质量负责人）
- 仓库：`D:\w2132\zhongzhuan\reasoning-trajectory-memory`（dsh-reasoning-memory v0.3.0）
- 命令：`npm test`（等价于 `npm run prepare && npm run test:python && npm run test:node`）

## 1. 环境

| 项目 | 值 | 备注 |
| --- | --- | --- |
| OS | Windows（DSH 沙箱会话，workspace-write 模式） | 本会话运行于受限环境 |
| Node.js | v24.12.0 | `package.json` engines 要求 `>=22`，满足 |
| `python`（PATH 首个） | 3.13.9 | `D:\Users\w2132\conda_file\python.exe` |
| `py` 启动器 | 3.12.4 | 测试启动器在 Windows 上优先用 `python`，故实际解释器为 3.13.9 |
| 其它 PATH 中的 python | AppGallery / WindowsApps / anaconda3 | 存在多个解释器，选择顺序由 PATH 决定 |
| tsc | 通过 `npm run prepare` | `dist/` 于 15:20:46 重新生成，26 个产物正常（13 个模块 × .js/.d.ts） |

脚本 `scripts/run-python-tests.mjs` 在 Windows 按 `python → py → python3` 顺序探测，且用 `spawnSync(..., { stdio: 'inherit' })` 运行——这一点在本沙箱中恰好可执行（见 §3）。

## 2. 分步结果

### 2.1 `npm run prepare`（tsc -p tsconfig.json）— **通过**

无输出、退出码 0。`dist/` 下 26 个 `.js`/`.d.ts` 文件全部重新生成（api-config、builtin-store、distillation-llm、domain、embedding-client、enterprise-rules-rag、index、lexical-index、python、search-text、skill-domain、skill-format、vector-index）。

### 2.2 `npm run test:python` — **通过：33/33**（0.473s）

| 测试类 | 用例数 | 结果 |
| --- | --- | --- |
| DistillationTests | 15 | ok |
| RetrievalTests | 14 | ok |
| JsonBridgeTests | 4 | ok |
| **合计** | **33** | **OK** |

环境噪声（非失败）：PowerShell 将 unittest 的 stderr 逐行输出标记为 `NativeCommandError`（因为命令以 `2>&1 | Out-String` 捕获输出），纯属显示噪音，不影响退出码与判定。

### 2.3 `npm run test:node` — **在运行器层面失败：5 个测试文件 0 执行**

```
✖ test\api-config.test.mjs      Error: spawn EPERM (errno -4048, syscall: spawn)
✖ test\builtin-store.test.mjs   Error: spawn EPERM
✖ test\plugin-smoke.test.mjs    Error: spawn EPERM
✖ test\retrieval_index.test.mjs Error: spawn EPERM
✖ test\skill-format.test.mjs    Error: spawn EPERM
tests 5 / pass 0 / fail 5
```

**根因（环境限制，非仓库缺陷）**：`node --test` 为每个测试文件启动子进程，并用管道 stdio 收集输出。本 DSH 会话的沙箱禁止“以管道 stdio 捕获其它程序输出”的 spawn（文档明确的边界：只有 `stdio: 'inherit'` / `'ignore'` 的 spawn 可用）。测试代码本身从未开始执行。

**验证**：用最小探针复现——

```
node -e "spawn('python',['-c','print(1)'],{stdio:['pipe','pipe','pipe']})"
→ Error: spawn EPERM { errno: -4048, code: 'EPERM', syscall: 'spawn' }（同步抛出）
```

### 2.4 补充验证：逐文件直接运行（进程内 node:test）— **通过：32/32**

由于 `npm run test:smoke` 本身就是 `node test/plugin-smoke.test.mjs` 这种直接运行方式（进程内执行 node:test，不经过 runner 的管道 spawn），逐文件直接运行是合法的等价位补充证据：

| 文件 | 用例数 | 结果 | 备注 |
| --- | --- | --- | --- |
| retrieval_index.test.mjs | 5 | ok | BM25/Qdrant/RAG/解析器 |
| builtin-store.test.mjs | 8 | ok | PGlite 同步/检索/过滤/持久化/关闭 |
| api-config.test.mjs | 7 | ok | 本地 HTTP server 往返正常 |
| skill-format.test.mjs | 11 | ok | |
| plugin-smoke.test.mjs | 1 | ok | **完整生命周期 + 真实 Python worker + PGlite** |
| **合计** | **32** | **ok** | |

特别说明：plugin-smoke 在本沙箱中真实走通了 `src/python.ts` 的 **EPERM → file-io 降级路径**（pipe spawn 同步抛 EPERM → `startFileTransport()` → `stdio: 'ignore'` spawn 成功），等于对该降级路径做了一次真实环境验证。

## 3. 结论

1. **代码与测试套件本身是绿的**：Python 33/33 通过；Node 32/32 在进程内运行全部通过。无回归。
2. `npm test` 在本沙箱退出码 1 的**唯一原因**是 `node --test` 运行器用管道 stdio spawn 子进程被沙箱拒绝（EPERM -4048）。这是本会话的运行环境边界，已用探针确认；在无此限制的环境（CI ubuntu/windows runner、普通终端）中 `npm test` 应正常全绿。**建议 CI/CD 继续使用 `npm test`**；在受限调试会话中改用逐文件 `node test/*.test.mjs` 直跑（与本仓库 `test:smoke` 脚本同款方式）。
3. 环境待办（影响真实部署，与代码无关）：
   - Windows 上 Python 解释器选择完全由 PATH 决定（本机有 4 个 python），且 `run-python-tests.mjs` 与插件默认 `pythonExecutable: 'python'` 均**不检查版本下限**（脚本注释声称需要 3.10+，代码仅探测可执行）。若 PATH 首个是 3.8/3.9，运行时会因 `dict[str, Any]` / `X | None` 语法崩溃。
   - 插件配置默认 `pythonExecutable` 在 Windows 为 `'python'`；若用户机器只有 `py` 启动器没有 PATH 中的 python，桥会以 ENOENT 失败（错误信息可见，但体验差）。

## 4. 交付物

- 本文件：`docs/debug-notes/baseline.md`
- 风险审查清单：`docs/debug-notes/risk-review.md`（只读审查，未改任何源码）
