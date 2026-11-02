"""Seeded synthetic episode dataset for the offline evaluation harness.

Produces JSONL episodes following docs/research-notes/04-eval-schema.md
(schemaVersion 1). The data is SYNTHETIC: families, vocabularies, lessons,
and gold labels are generated from fixed pools with a fixed seed so every run
reproduces the same episodes. Results on this data do not transfer to a real
annotated set; the sample report must say so.

The generator intentionally builds structure-similar / semantic-different
negative-control pairs inside each family: two episodes share the same
abstractEvents and gold template but use disjoint task vocabulary, so only the
structure/template channels can connect them.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from reasoning_memory_core.constants import MODULE_DEMAND_BY_TAG, MODULE_DEMAND_INTENT, TAG_TEXT, TASK_PATTERNS, TAGS  # noqa: E402

SCHEMA_VERSION = 1

CATEGORIES = ("workflow", "gotcha", "premise_awareness", "state_recall", "generic")
TRANSFERABILITY = ("high", "medium", "low")
EVIDENCE_QUALITY = ("strong", "weak", "unattributable")
POTENTIAL_HARM = ("none", "contextual", "general")
SPLITS = ("dev", "test")

# One spec per task family. Groups keep a whole family group inside one split
# (leak rule 2); the split column is per family, so every family of a group
# must share it.
FAMILY_SPECS = (
    {
        "family": "code_modification",
        "group": "code",
        "split": "dev",
        "pattern": "code_synthesis",
        "templateId": "tpl-code-refactor",
        "signature": ("decompose", "search", "execute", "verify"),
        "queries": (
            "重构 utils.py 的解析函数并跑单测",
            "如何稳妥地给既有 Python 仓库做重构",
            "重构数据访问层并保证测试通过",
            "给旧仓库的解析逻辑做安全重构",
        ),
        "lessons": (
            "先跑单测定位失败点，再最小化改动实现，最后重跑验证",
            "重构时保持行为不变，用测试套件作为停止条件",
        ),
        "gotchaLesson": "仓库没有测试基础设施时不要套用测试先行策略",
    },
    {
        "family": "code_bugfix",
        "group": "code",
        "split": "dev",
        "pattern": "code_synthesis",
        "templateId": "tpl-code-refactor",
        "signature": ("search", "execute", "verify", "execute"),
        "queries": (
            "修复解析函数的一个边界崩溃",
            "如何定位并修复既有代码库里的一个 bug",
            "修复序列化模块的空指针问题",
            "给解析模块修一个偶发崩溃",
        ),
        "lessons": (
            "先用最小复现定位崩溃点，再修改并回归验证",
            "修复 bug 时先写失败测试锁定问题，再改实现",
        ),
        "gotchaLesson": "没有复现用例时不要猜测崩溃原因",
    },
    {
        "family": "web_navigation",
        "group": "web",
        "split": "test",
        "pattern": "gui_operation",
        "templateId": "tpl-web-nav",
        "signature": ("search", "execute", "verify", "search"),
        "queries": (
            "在网页表单里提交一份订单",
            "如何在一个购物网站上完成一次下单",
            "在后台页面填写并保存一条配置",
            "在一个电商页面完成结账流程",
        ),
        "lessons": (
            "先定位表单字段再逐项填写，提交后核对结果页面",
            "页面操作后先核验状态变化，再继续下一步",
        ),
        "gotchaLesson": "页面结构变动后不要沿用旧的选择器",
    },
    {
        "family": "web_form_fill",
        "group": "web",
        "split": "test",
        "pattern": "gui_operation",
        "templateId": "tpl-web-nav",
        "signature": ("search", "execute", "verify"),
        "queries": (
            "填写一份用户注册表单",
            "如何在注册页面完成表单提交",
            "填写一份多字段的联系人表单",
            "在一个表单页面上完成注册流程",
        ),
        "lessons": (
            "逐项核对表单字段与占位提示，提交后检查成功提示",
            "提交表单前先校验必填字段，避免二次返工",
        ),
        "gotchaLesson": "表单校验规则变更后不要沿用旧的字段假设",
    },
    {
        "family": "info_retrieval",
        "group": "info",
        "split": "dev",
        "pattern": "multi_hop_qa",
        "templateId": "tpl-info-multihop",
        "signature": ("search", "search", "execute", "verify"),
        "queries": (
            "跨文档回答一个需要两段证据的问题",
            "如何检索并组合多份资料回答一个问题",
            "从两份知识库材料里拼出答案",
            "结合多篇资料回答一个复合问题",
        ),
        "lessons": (
            "把问题拆成子问题逐段检索，再合并证据回答",
            "检索结果先核对来源再组合，避免拼接错误事实",
        ),
        "gotchaLesson": "单篇资料不完整时不要过早下结论",
    },
    {
        "family": "env_state_tracking",
        "group": "env",
        "split": "test",
        "pattern": "long_horizon_planning",
        "templateId": "tpl-env-plan",
        "signature": ("decompose", "search", "execute", "verify"),
        "queries": (
            "在环境里分阶段搭建并验证一个服务",
            "如何分步搭建环境并逐步验证状态",
            "按里程碑部署一组服务并核对状态",
            "规划一次多阶段的环境搭建任务",
        ),
        "lessons": (
            "先分解里程碑，每阶段完成后核对环境状态再继续",
            "环境变更后立即验证状态，失败时回退上一步",
        ),
        "gotchaLesson": "环境状态未核实时不要继续下一步操作",
    },
)

# Each family carries its own negative-control pair texts: the two texts are
# mutually disjoint (no shared surface words) while the pair episodes share
# the same abstractEvents and gold template, so only structure/template
# channels can connect them. Texts are also disjoint across families so the
# cross-split duplicate check (leak rule 3) stays clean.
FAMILY_PAIR_TEXTS = {
    "code_modification": ("解析函数并跑单测的重构任务", "序列化模块并做回归修复"),
    "code_bugfix": ("定位边界崩溃并写失败用例", "检查内存分配并做指针校验"),
    "web_navigation": ("填写订单表单并核对结果", "点击导航菜单并检查跳转"),
    "web_form_fill": ("填写注册表单并核对字段", "提交联系表单并检查提示"),
    "info_retrieval": ("合并两份资料回答问题", "比对知识库条目找出答案"),
    "env_state_tracking": ("分阶段部署并核对状态", "搭建服务并逐步验证"),
}


def build_template_record(template_id: str, pattern: str, signature: tuple[str, ...]) -> dict[str, Any]:
    steps = []
    for index, tag in enumerate(signature, start=1):
        required, optional = MODULE_DEMAND_BY_TAG.get(tag, ((), ()))
        demands = [
            {"kind": kind, "intent": MODULE_DEMAND_INTENT.get(kind, "")}
            for kind in (*required, *optional)
        ]
        steps.append({"index": index, "title": TAG_TEXT.get(tag, tag), "moduleDemands": demands})
    tag_text = " → ".join(TAG_TEXT.get(tag, tag) for tag in signature)
    return {
        "templateId": template_id,
        "name": f"memory_tpl_eval-{template_id.removeprefix('tpl-')}",
        "description": f"Use when a task follows this observed step pattern: {tag_text}.",
        "taskPatterns": [pattern],
        "steps": steps,
        "structureSignature": list(signature),
        "preconditions": ["gold labels are synthetic and only valid for harness evaluation"],
        "provenance": {"author": "caller", "sourceMemoryIds": [], "sourceTaskIds": []},
        "utility": {"helpful": 0, "harmful": 0, "neutral": 0},
        "version": 1,
        "status": "active",
        "createdAt": 1,
        "updatedAt": 1,
    }


class EpisodeGenerator:
    """Deterministic synthetic episode generator (pure stdlib, no network)."""

    def __init__(self, seed: int = 42, episodes_per_family: int = 8):
        if episodes_per_family < 6:
            raise ValueError("episodes_per_family must be at least 6 (pair + gotcha + queries need earlier writes)")
        self.seed = seed
        self.episodes_per_family = episodes_per_family
        self.random = random.Random(seed)

    def generate(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        episodes: list[dict[str, Any]] = []
        order = 0
        templates: list[dict[str, Any]] = []
        seen_template_ids: set[str] = set()
        for spec_index, spec in enumerate(FAMILY_SPECS):
            template_id = spec["templateId"]
            if template_id not in seen_template_ids:
                seen_template_ids.add(template_id)
                templates.append(build_template_record(
                    template_id, spec["pattern"], spec["signature"],
                ))
            family = spec["family"]
            signature = list(spec["signature"])
            pair_first = self.episodes_per_family - 2
            pair_second = self.episodes_per_family - 1
            for index in range(self.episodes_per_family):
                order += 1
                episode_id = f"ep-{family}-{index:02d}"
                outcome = "fail" if index == self.episodes_per_family // 2 else "success"
                task_family_hint = family if index in (0, 2, 4) else family
                if index == pair_first:
                    task_desc = self._pair_text(spec, 0, index)
                    goal = ""
                elif index == pair_second:
                    task_desc = self._pair_text(spec, 1, index)
                    goal = ""
                else:
                    task_desc = spec["queries"][index % len(spec["queries"])]
                    goal = "让结果通过验证" if index % 2 == 0 else ""
                graph = self._family_graph(spec, signature, index)
                if outcome == "success":
                    lesson = spec["lessons"][index % len(spec["lessons"])]
                else:
                    lesson = spec["gotchaLesson"]
                memory_id = f"mem-{episode_id}-1"
                write = {
                    "memoryId": memory_id,
                    "type": "strategy" if outcome == "success" else "failure",
                    "outcome": outcome,
                    "taskScope": family,
                    "preconditions": ["存在可核验的结果"] if outcome == "success" else ["前提未被满足"],
                    "title": lesson[:24],
                    "description": "When the preconditions match this lesson applies" if outcome == "success" else "When the precondition conflict appears this lesson applies",
                    "content": lesson,
                    "contraindications": [] if outcome == "success" else ["前提冲突时本教训不适用"],
                    "reasoning": lesson,
                    "reasoningGraph": graph,
                    "structureSignature": signature,
                    "ann": {
                        "transferability": "high" if outcome == "success" else "medium",
                        "evidenceQuality": "strong" if outcome == "success" else "unattributable",
                        "potentialHarm": "none" if outcome == "success" else "contextual",
                        "deSpecified": True,
                        "goldTemplateId": template_id,
                        "annotators": ["a1", "a2"],
                        "agreement": "agree",
                    },
                }
                queries: list[dict[str, Any]] = []
                expected: list[dict[str, Any]] = []
                gotcha_index = self.episodes_per_family // 2
                if index == pair_second:
                    # Structure-similar / semantic-different negative
                    # control: the pair writes share a signature and
                    # template but disjoint surface words.
                    expected.append({
                        "memoryId": f"mem-ep-{family}-{pair_first:02d}-1",
                        "relevance": 3,
                        "misleading": False,
                        "basis": "结构相同、表面语义不同的负控制对",
                        "category": "workflow",
                    })
                elif index >= 1:
                    # The generic previous write is a transferable strategy;
                    # the fail-gotcha episode never serves as that reference.
                    previous_index = index - 1
                    if previous_index == gotcha_index:
                        previous_index -= 1
                    expected.append({
                        "memoryId": f"mem-ep-{family}-{previous_index:02d}-1",
                        "relevance": 3,
                        "misleading": False,
                        "basis": "同族同模板的可迁移策略",
                        "category": "workflow",
                    })
                if index > gotcha_index:
                    expected.append({
                        "memoryId": f"mem-ep-{family}-{gotcha_index:02d}-1",
                        "relevance": 0,
                        "misleading": True,
                        "basis": "看似相关但前提冲突的失败教训",
                        "category": "gotcha",
                    })
                queries.append({
                    "queryId": f"{episode_id}-q1",
                    "turn": 1,
                    "text": task_desc,
                    "k": 4,
                    "route": "auto",
                    "reasoningGraphJson": json.dumps(graph, ensure_ascii=False) if graph else None,
                    "expected": expected,
                    "goldTemplate": {
                        "templateId": template_id,
                        "structureSignature": signature,
                        "moduleRequirements": ["retrieve_memory", "invoke_tool", "verify"],
                        "justification": f"{family} 的公共骨架",
                    },
                    "ann": {"allowSelfRetrieval": False, "intent": "transfer"},
                })
                episodes.append({
                    "schemaVersion": SCHEMA_VERSION,
                    "episode": {
                        "episodeId": episode_id,
                        "order": order,
                        "split": spec["split"],
                        "taskFamily": family,
                        "taskFamilyGroup": spec["group"],
                        "taskPattern": spec["pattern"],
                        "task": {
                            "taskDesc": task_desc,
                            "goal": goal,
                            "abstractEvents": signature,
                            "reasoningGraph": graph,
                        },
                        "outcome": outcome,
                        "verifier": "合成验证器" if outcome == "success" else "合成失败记录",
                        "evidence": "合成数据生成的确定性证据" if outcome == "success" else "合成数据生成的确定性失败记录",
                        "memoryWrites": [write],
                        "queries": queries,
                        "ann": {"containsSecretAnswer": False, "notes": f"synthetic {family}"},
                    },
                })
        episodes.sort(key=lambda episode: episode["episode"]["order"])
        return episodes, templates

    def _pair_text(self, spec: dict[str, Any], vocab_index: int, index: int) -> str:
        return FAMILY_PAIR_TEXTS[spec["family"]][vocab_index]

    def _family_graph(self, spec: dict[str, Any], signature: list[str], index: int) -> dict[str, Any]:
        nodes = [
            {"id": "context", "kind": "context", "text": f"{spec['family']} 的任务情境", "source": "task_input"},
            {"id": "lesson", "kind": "inference", "text": "先核验前提再执行", "source": "agent_input"},
        ]
        edges = [{"sourceId": "context", "targetId": "lesson", "relation": "frames", "source": "derived"}]
        action_ids = []
        for step, tag in enumerate(signature[:4], start=1):
            action_id = f"action_{step}"
            action_ids.append(action_id)
            nodes.append({"id": action_id, "kind": "action", "text": TAG_TEXT.get(tag, tag), "source": "tool_trace"})
        if action_ids:
            edges.append({"sourceId": "lesson", "targetId": action_ids[0], "relation": "guides", "source": "derived"})
            for left, right in zip(action_ids, action_ids[1:]):
                edges.append({"sourceId": left, "targetId": right, "relation": "precedes", "source": "tool_trace"})
        nodes.append({"id": "outcome", "kind": "conclusion", "text": "任务结果：success", "source": "outcome_input"})
        edges.append({"sourceId": "lesson", "targetId": "outcome", "relation": "supports", "source": "agent_input"})
        return {"version": 1, "nodes": nodes, "edges": edges}


def write_jsonl(path: str | Path, episodes: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for episode in episodes:
            handle.write(json.dumps(episode, ensure_ascii=False) + "\n")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            lines.append({"__line__": number, "__raw__": stripped, "value": json.loads(stripped)})
    return lines


if __name__ == "__main__":
    episodes, templates = EpisodeGenerator().generate()
    write_jsonl(ROOT / "scripts" / "eval" / "data" / "synthetic-dev.jsonl", episodes)
    print(f"wrote {len(episodes)} episodes and {len(templates)} gold templates")
