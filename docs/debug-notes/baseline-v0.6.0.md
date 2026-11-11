# v0.6.0 字节基线（零回归 gate）

- 日期：2026-11-11
- 执行人：code-developer（MVP 实现）
- 仓库：`D:\w2132\zhongzhuan\reasoning-trajectory-memory`（dsh-reasoning-memory v0.6.0）
- 目的：冻结 `_retrieve` / `plan_task` 全请求的 `json.dumps(result, sort_keys=True, ensure_ascii=False)` sha256，作为 v0.7.0（量子）/ v0.8.0（因果提炼）零回归的字节基线。没有本基线，任何量子/因果提炼代码不得合入。

## 1. 环境

| 项目 | 值 |
| --- | --- |
| OS | Windows（DSH 沙箱会话，workspace-write 模式） |
| Node.js | v24.12.0 |
| `python` | 3.13.9（`python -m unittest` / `scripts/eval` 均用同一解释器） |
| git | `main` @ `fff2306`（v0.6.0 最后一笔：docs: causal utility engine spec…） |

## 2. 分步结果

### 2.1 `npm run prepare`（tsc）— 通过（退出码 0）

### 2.2 `npm run test:python` — 通过：**151/151**（`python -m unittest discover -s test -v`）

含 `CausalUtilityTests`（v0.6.0 合成已知结构：分层 ATE/CATE/辛普森/IPTW/配对/收缩/确定性/零回归）与 `CausalBoundaryTests` 全部绿。

### 2.3 `npm run test:node` — 运行器层 EPERM（环境边界，非代码缺陷）

`node --test test/*.test.mjs` 为每个测试文件 spawn 子进程并采用管道 stdio 收集输出，本沙箱拒绝该 spawn（`EPERM -4048`）。**测试代码从未开始执行**；用最小探针复现（与 `docs/debug-notes/baseline.md` 记录的 v0.3.0 同款根因一致）：

```
node -e "spawn('python',['-c','print(1)'],{stdio:['pipe','pipe','pipe']})"
→ Error: spawn EPERM { errno: -4048, code: 'EPERM', syscall: 'spawn' }（同步抛出）
```

**进程内等价验证（`--test-isolation=none`）— 通过：44/44**：

```
node --test --test-isolation=none "test/*.test.mjs"  →  tests 44 / pass 44 / fail 0
```

其中 `causal.test.mjs` 真实走通了 `src/python.ts` 的 **EPERM → file-io 降级路径**（pipe spawn 同步抛 EPERM → `startFileTransport()` → `stdio: 'ignore'` spawn 成功）。本沙箱下验收 `npm test` 的 Node 侧以 `--test-isolation=none` 为等价命令；无此限制的环境（CI / 普通终端）中 `npm test` 正常全绿。

## 3. 冻结基线 sha256

口径：`sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8"))`。fixture 构造见 §4；零回归测试须用同一 fixture 复算并比对。开关默认关闭时，v0.7.0 / v0.8.0 的 `_retrieve` / `plan_task` 输出须与下列 sha256 逐字节一致（新字段默认值不得出现在关闭态输出中；`superposition` 字段完全缺席）。

| 场景 | sha256 |
| --- | --- |
| `retrieve.weighted.mixed` | `23fd07c3123fa20af20ea40ccf66ea1ca40e46fa22f67f0d944443f23c646fc4` |
| `retrieve.rrf.sparse_dense` | `d36e093aa5cd4bddb77548434440c7941234eeab36b01aa3eb6f5e4a3eaedf0c` |
| `retrieve.causal.applied` | `3ebd70b85d726202fbbe6b6b47dec8bd8f3fcc3e3e7c8a5a815696c50691c8a6` |
| `plan_task.builtin.code` | `bbeb4489fb3dee188ce1ae584eb90dafce43648b7c9db4d3d1b1f7398fa7da05` |

## 4. fixture 构造（可复现）

```python
import hashlib, json, sys
sys.path.insert(0, r"D:\w2132\zhongzhuan\reasoning-trajectory-memory\python")
import reasoning_memory as core

def make_memory(memory_id, outcome, *, context="validate schema before publishing",
                reasoning="validate schema before publishing", task_id="prior-task", feedback=None):
    return {
        "memoryId": memory_id, "outcome": outcome,
        "semanticText": context, "reasoning": reasoning,
        "reasoningGraph": {"version": 1, "nodes": [], "edges": []},
        "content": "", "feedback": feedback or {"helpful": 0, "harmful": 0, "neutral": 0},
        "structureSignature": ["search", "execute", "verify"],
        "provenance": {"taskId": task_id},
    }

def tool_events(success=True, tool="bash"):
    return [
        {"seq": 1, "type": "tool_call", "callId": "c1", "toolName": tool},
        {"seq": 2, "type": "tool_result", "callId": "c1", "toolName": tool, "success": success},
    ]

def sha(result):
    return hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
```

### 4.1 `retrieve.weighted.mixed`

```python
memories = [
    make_memory("p1", "success", context="validate schema before publishing", reasoning="validate schema before publishing"),
    make_memory("p2", "success", context="an unrelated browser workflow", reasoning="prefer a quiet interface and avoid needless decoration"),
    make_memory("n1", "fail", context="deploy without verifying", reasoning="deploy without verifying the schema"),
    make_memory("n2", "fail", context="skip migration validation", reasoning="skip migration validation"),
]
sha(core._retrieve({
    "operation": "retrieve", "query": "validate schema before publishing", "events": [],
    "memories": memories, "k": 4, "route": "auto", "positiveNegativeRatio": 1.0,
    "excludeTaskId": "active-task",
}))
```

### 4.2 `retrieve.rrf.sparse_dense`

```python
rrf_memories = [
    {**make_memory("p1", "success", reasoning="validate database schema"), "lexicalScore": 1.0, "vectorScore": 0.82},
    {**make_memory("p2", "success", reasoning="check schema before deploy"), "lexicalScore": 0.55, "vectorScore": 0.91},
    {**make_memory("n1", "fail", reasoning="deploy without verifying schema"), "lexicalScore": 0.8, "vectorScore": 0.77},
    {**make_memory("n2", "fail", reasoning="skip migration validation"), "lexicalScore": 0.3, "vectorScore": 0.69},
]
sha(core._retrieve({
    "operation": "retrieve", "query": "validate schema before publishing", "events": [],
    "memories": rrf_memories, "k": 4, "route": "auto", "positiveNegativeRatio": 1.0,
    "excludeTaskId": "active-task",
}))
```

### 4.3 `retrieve.causal.applied`

```python
causal_memories = [{
    "memoryId": "m1", "outcome": "success",
    "semanticText": "重构解析函数", "reasoning": "先定位失败点再修改",
    "structureSignature": [], "reasoningGraph": {"version": 1, "nodes": [], "edges": []},
    "feedback": {"helpful": 4, "harmful": 0, "neutral": 0},
    "provenance": {"taskId": "prior-1"}, "lexicalScore": 0.0, "vectorScore": 0.0,
    "strategyTemplateId": "", "status": "active",
}]
def cev(treatment, outcome, n):
    return [{"memoryId": "m1", "kind": "memory", "taskId": "t", "taskPattern": "code_synthesis",
             "treatment": treatment, "outcome": outcome, "stepIndex": 0, "retrievalIntent": "",
             "modelVersion": "", "pairId": "", "createdAt": 0} for _ in range(n)]
causal_events = (cev("used", "success", 90) + cev("used", "fail", 10)
                 + cev("retrieved_unused", "success", 10) + cev("retrieved_unused", "fail", 90))
sha(core._retrieve({
    "operation": "retrieve", "query": "重构", "k": 2, "route": "semantic",
    "memories": causal_memories, "causalEventsByMemory": {"m1": causal_events},
}))
```

### 4.4 `plan_task.builtin.code`

```python
templates = core.builtin_template_records(now_ms=1700000000000)
sha(core.plan_task(
    task_desc="重构解析函数并跑测试", goal="通过全部单元测试", task_family="code",
    events=tool_events(), has_graph=False, graph_node_kinds=[], templates=templates,
    max_templates=2, template_match_min=0.55,
))
```

## 5. 结论

1. Python 151/151 通过；Node 44/44（进程内）通过。v0.6.0 全绿。
2. 四项冻结 sha256 已落盘，作为零回归门。v0.7.0 / v0.8.0 合入前必须保证：`quantumDecisionEnabled=false` 且 `distillGateEnabled=false` 时，上述四场景 sha256 不变。
