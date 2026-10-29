"""Shared limits and vocabularies for reasoning-memory operations."""

TAGS = {"decompose", "constrain", "search", "execute", "verify", "rollback"}

TAG_TEXT = {
    "decompose": "任务分解",
    "constrain": "检查约束",
    "search": "搜索或读取证据",
    "execute": "执行操作",
    "verify": "验证结果",
    "rollback": "根据失败结果回退或修正",
}

EXTRACTOR_VERSION = "dsh-reasoning-memory/0.3.0"

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
