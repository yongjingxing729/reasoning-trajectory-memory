"""Shared limits and vocabularies for reasoning-memory operations."""

TAGS = ("decompose", "constrain", "search", "execute", "verify", "rollback")

TAG_TEXT = {
    "decompose": "任务分解",
    "constrain": "检查约束",
    "search": "搜索或读取证据",
    "execute": "执行操作",
    "verify": "验证结果",
    "rollback": "根据失败结果回退或修正",
}

EXTRACTOR_VERSION = "dsh-reasoning-memory/0.5.0"

MAX_CAPTURED_EVENTS = 2000

MAX_MEMORY_TEXT = 4000

MAX_TITLE_CHARS = 120

MAX_DESCRIPTION_CHARS = 300

MAX_CONTRAINDICATION_CHARS = 200

MAX_CONTRAINDICATIONS = 3

MAX_ITEMS_PER_OUTCOME = 3

MAX_SOURCE_MEMORY_IDS = 10

MAX_REASONING_TEXT_CHARS = 4000

# Above this similarity a distilled lesson is judged a near-verbatim copy of the
# task description and is rejected as too task-specific to transfer.
DEDUP_SIMILARITY = 0.9

EXTRACTION_METHODS = {"caller_insight", "llm_distillation", "llm_self_contrast"}

MAX_GRAPH_NODES = 24

MAX_GRAPH_EDGES = 48

MAX_GRAPH_NODE_TEXT = 400

GRAPH_NODE_KINDS = {"context", "premise", "inference", "action", "evidence", "conclusion", "failure"}

GRAPH_RELATIONS = {"supports", "requires", "leads_to", "precedes", "frames", "guides", "explains", "qualifies", "contradicts"}

GRAPH_SOURCES = {"agent_input", "task_input", "tool_trace", "verifier_input", "outcome_input", "derived"}

# ---------------------------------------------------------------------------
# v0.4.0 strategy-template layer. All values below are engineering initial
# constants following docs/research-notes/02-template-algorithms.md; they are
# deterministic by construction and expected to be recalibrated on the
# annotated evaluation set (docs/research-notes/04-eval-schema.md).
# ---------------------------------------------------------------------------

TASK_PATTERNS = (
    "multi_hop_qa",
    "code_synthesis",
    "tool_orchestration",
    "gui_operation",
    "long_horizon_planning",
    "numeric_search",
    "general",
)

MODULE_DEMAND_KINDS = (
    "retrieve_memory",
    "retrieve_template",
    "use_skill",
    "invoke_tool",
    "verify",
    "rollback_check",
    "retrieve_document",
)

# StrategyTemplate record caps (mirror the limits in the final v0.4.0 spec).
MAX_TEMPLATE_STEPS = 8

MAX_MODULE_DEMANDS_PER_STEP = 4

MAX_TEMPLATE_NAME_CHARS = 80

MAX_TEMPLATE_DESCRIPTION_CHARS = 400

MAX_TEMPLATES = 200

MAX_TEMPLATE_STEP_TITLE_CHARS = 200

MAX_TEMPLATE_INTENT_CHARS = 400

MAX_TEMPLATE_PRECONDITIONS = 8

MAX_TEMPLATE_PRECONDITION_CHARS = 200

MAX_TEMPLATE_SOURCE_MEMORY_IDS = 200

# Task-pattern classifier: signal weights sum to 1 over the signals that are
# actually present; S5 (historical retrieval hits) is deferred past v0.4.0.
PATTERN_SIGNAL_WEIGHTS = {
    "task_family": 0.35,
    "lexical": 0.30,
    "structure": 0.20,
    "graph": 0.15,
}

MIN_PATTERN_SCORE = 0.35

MIN_PATTERN_MARGIN = 0.10

# Deterministic workflow mining: online representative clustering over
# difflib.SequenceMatcher.ratio() similarities (02-template-algorithms.md 2.2).
CLUSTER_JOIN_SIMILARITY = 0.75

CLUSTER_FORM_SIMILARITY = 0.55

# Template match thresholds (strong / minimum injectable).
TEMPLATE_MATCH_STRONG = 0.75

TEMPLATE_MATCH_MIN = 0.55

# task_family substring table for classifier signal S1. Order matters: the
# first matching row wins.
TASK_FAMILY_PATTERNS = (
    ("humaneval", "code_synthesis"),
    ("mbpp", "code_synthesis"),
    ("code", "code_synthesis"),
    ("swe", "code_synthesis"),
    ("repo", "code_synthesis"),
    ("git", "code_synthesis"),
    ("debug", "code_synthesis"),
    ("refactor", "code_synthesis"),
    ("webarena", "gui_operation"),
    ("mind2web", "gui_operation"),
    ("browser", "gui_operation"),
    ("navigation", "gui_operation"),
    ("gui", "gui_operation"),
    ("ui", "gui_operation"),
    ("click", "gui_operation"),
    ("form", "gui_operation"),
    ("ecommerce", "gui_operation"),
    ("shopping", "gui_operation"),
    ("gsm8k", "numeric_search"),
    ("math", "numeric_search"),
    ("arithmetic", "numeric_search"),
    ("numeric", "numeric_search"),
    ("equation", "numeric_search"),
    ("finance", "numeric_search"),
    ("24game", "numeric_search"),
    ("hotpotqa", "multi_hop_qa"),
    ("multihop", "multi_hop_qa"),
    ("multi-hop", "multi_hop_qa"),
    ("qa", "multi_hop_qa"),
    ("question", "multi_hop_qa"),
    ("wikidata", "multi_hop_qa"),
    ("knowledge", "multi_hop_qa"),
    ("info", "multi_hop_qa"),
    ("alfworld", "long_horizon_planning"),
    ("sciworld", "long_horizon_planning"),
    ("plan", "long_horizon_planning"),
    ("long", "long_horizon_planning"),
    ("multi-step", "long_horizon_planning"),
    ("pipeline", "long_horizon_planning"),
    ("env", "long_horizon_planning"),
)

# Bilingual keyword vocabularies for classifier signal S2. Vocabularies are
# kept comparable in size so the per-class hit ratio (hits / len(vocab)) stays
# fair; all entries are engineering initial values.
PATTERN_KEYWORDS = {
    "multi_hop_qa": (
        "multi-hop", "multihop", "sub-question", "question answering",
        "multiple questions", "reasoning chain", "跨文档", "多跳", "子问题", "依次回答",
    ),
    "code_synthesis": (
        "code", "implement", "function", "bug", "fix", "refactor", "compile",
        "test case", "代码", "函数", "实现", "修复", "重构", "编译",
    ),
    "tool_orchestration": (
        "api", "integrate", "orchestrate", "pipeline", "workflow", "plugin",
        "automate", "接口", "集成", "编排", "工作流", "插件", "自动化",
    ),
    "gui_operation": (
        "click", "button", "webpage", "browser", "ui", "form", "navigate",
        "点击", "按钮", "网页", "浏览器", "界面", "表单", "导航",
    ),
    "long_horizon_planning": (
        "plan", "long-running", "multi-step", "milestone", "subtask", "roadmap",
        "计划", "长期", "多步", "里程碑", "子任务", "路线图",
    ),
    "numeric_search": (
        "math", "number", "calculate", "compute", "equation", "arithmetic",
        "24 game", "数学", "计算", "数字", "方程", "算术", "24点",
    ),
    "general": (),
}

# Step-tag to module-demand derivation table for builtin seeding, mining, and
# lossy signature derivation (02-template-algorithms.md 2.3). Required kinds
# come first; optional kinds follow.
MODULE_DEMAND_BY_TAG = {
    "decompose": (("retrieve_template",), ("retrieve_memory",)),
    "constrain": ((), ("retrieve_memory", "retrieve_document")),
    "search": (("retrieve_memory", "retrieve_document"), ("invoke_tool",)),
    "execute": (("invoke_tool",), ("use_skill",)),
    "verify": (("verify",), ()),
    "rollback": (("rollback_check",), ("verify",)),
}

# Canonical tag for each module-demand kind, used by publish_template_validate
# to derive a lossy structureSignature from caller-authored steps.
MODULE_DEMAND_TAG = {
    "retrieve_memory": "search",
    "retrieve_template": "decompose",
    "use_skill": "execute",
    "invoke_tool": "execute",
    "verify": "verify",
    "rollback_check": "rollback",
    "retrieve_document": "search",
}

# Deterministic intent text for generated module demands (retrieval query
# construction hints).
MODULE_DEMAND_INTENT = {
    "retrieve_memory": "检索与本步骤相关的成败经验，按正反配额取舍",
    "retrieve_template": "检索或切换到更细粒度的策略模板",
    "use_skill": "加载与本步骤匹配的已发布 Agent Skill",
    "invoke_tool": "调用执行本步骤所需的 DSH 工具",
    "verify": "用测试、环境或数值验证器核验本步骤结果",
    "rollback_check": "回退前先检索失败教训，确认归因后再改变路径",
    "retrieve_document": "检索用户导入的开发规范/文档，按步骤意图过滤",
}

# Template-channel fusion weights (final spec 3.4). The RRF row applies while
# an active template is bound; the weighted row applies to the weighted
# fallback. Channels without any ranked candidate are excluded from RRF
# normalization, as before.
TEMPLATE_RRF_WEIGHTS = {
    "lexical": 0.25,
    "reasoning": 0.10,
    "vector": 0.30,
    "structure": 0.10,
    "graph": 0.10,
    "template": 0.15,
}

TEMPLATE_WEIGHTED_WEIGHTS = {
    "context": 0.15,
    "reasoning": 0.40,
    "structure": 0.20,
    "graph": 0.10,
    "template": 0.15,
}

# Weighted-mode micro adjustments by step retrieval intent (final spec 3.4).
# Positive and negative deltas are clamped so every weight stays in [0, 1].
RETRIEVAL_INTENT_WEIGHT_DELTAS = {
    "rollback_check": {"structure": 0.05, "template": 0.05, "context": -0.05, "reasoning": -0.05},
    "verify": {"graph": 0.05},
}

# Five idempotently seeded builtin templates (final spec 3.2). Steps are
# generated from each signature through MODULE_DEMAND_BY_TAG at seed time.
# Names carry the memory_tpl_ namespace prefix with a kebab-case body.
BUILTIN_TEMPLATES = (
    {
        "templateId": "memory_tpl_builtin_tot",
        "name": "memory_tpl_tot-decompose-backtrack",
        "description": "Use when a task needs decomposition into subgoals with verification and explicit backtracking on failed checks.",
        "structureSignature": ("decompose", "constrain", "search", "execute", "verify", "rollback"),
        "taskPatterns": ("long_horizon_planning", "numeric_search"),
    },
    {
        "templateId": "memory_tpl_builtin_self_refine",
        "name": "memory_tpl_self-refine",
        "description": "Use when a task benefits from generate, reflect, and revise cycles until verification passes.",
        "structureSignature": ("execute", "verify", "rollback", "execute", "verify"),
        "taskPatterns": ("code_synthesis", "general"),
    },
    {
        "templateId": "memory_tpl_builtin_react",
        "name": "memory_tpl_react-act-observe",
        "description": "Use when a task requires alternating evidence gathering, action, and observation until the goal is verified.",
        "structureSignature": ("search", "execute", "verify", "search", "execute"),
        "taskPatterns": ("multi_hop_qa", "tool_orchestration"),
    },
    {
        "templateId": "memory_tpl_builtin_voyager",
        "name": "memory_tpl_voyager-retrieve-reuse",
        "description": "Use when a task should first retrieve reusable components or precedents, then compose and verify them.",
        "structureSignature": ("search", "decompose", "execute", "verify"),
        "taskPatterns": ("tool_orchestration", "gui_operation"),
    },
    {
        "templateId": "memory_tpl_builtin_toolllm",
        "name": "memory_tpl_toolllm-dfsdt",
        "description": "Use when a task involves trial and error over tools with backtracking and distillation of what worked.",
        "structureSignature": ("search", "execute", "verify", "rollback", "execute", "verify"),
        "taskPatterns": ("tool_orchestration", "code_synthesis"),
    },
)

# ---------------------------------------------------------------------------
# v0.5.0 reasoning-aware document retrieval (final spec 0.5.0). Documents are
# an independent retrieval surface: they never enter the memory RRF channels
# or the positive/negative quota. All values are engineering initial values.
# ---------------------------------------------------------------------------

MAX_DOCS = 200

MAX_DOC_CONTENT_CHARS = 200_000

MAX_CHUNKS_PER_DOC = 2000

MAX_CHUNK_TEXT_CHARS = 2000

MAX_DOC_NAME_CHARS = 80

MAX_DOC_TASK_PATTERNS = 7

MAX_CHUNK_REFS = 20

CHUNK_TARGET_CHARS = 1200

CHUNK_MIN_CHARS = 300

DOC_QUERY_DEFAULT_K = 4

DOC_QUERY_MAX_K = 10

DOC_MAX_HOPS = 2

DOC_HOP_DECAY = 0.6

DOC_EXPAND_CAP = 16

# Document-domain mini-RRF channel weights (final spec 3.2). RRF is used only
# when both lexical and vector channels have ranked candidates; otherwise the
# same weights drive a normalized weighted sum over the active channels.
DOC_RRF_WEIGHTS = {
    "lexical": 0.45,
    "vector": 0.35,
    "pattern": 0.10,
    "link": 0.10,
}

# Step-intent micro adjustments for the document-domain fusion.
DOC_INTENT_WEIGHT_DELTAS = {
    "constrain": {"pattern": 0.05},
}

# Default load cap for memory_load_document (chunks per call).
DOC_LOAD_DEFAULT_LIMIT = 50

DOC_LOAD_MAX_LIMIT = 200

DOC_LOAD_FULL_READ_THRESHOLD = 200
