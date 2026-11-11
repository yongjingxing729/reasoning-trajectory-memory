"""strategy templates for the DSH reasoning-memory core.

Deterministic, stdlib-only implementation of the v0.4.0 four-layer mapping
skeleton (final spec 3.1-3.3): task-pattern classification, strategy-template
matching, deterministic workflow mining, template utility statistics, the
read-only plan_task entry point, and caller-template draft validation.

Every score is decomposed into named components so the evaluation harness can
audit and ablate each signal. No function here performs IO or touches the DSH
storage domain; persistence is decided by the TypeScript layer.

This module is the stable public facade for the template layer. The
implementation lives in the cohesive template_* sibling modules, and every
name that was previously importable from this module is re-exported here
unchanged.
"""

from __future__ import annotations

# The pre-refactor module namespace is a de-facto internal contract: other core
# modules and the test suite patch names onto this module. Keep the historical
# re-exports below stable even though the implementation now lives in the
# template_* siblings, so `dir(templates)` and `from .templates import ...`
# keep behaving exactly as before the split.
import re
import time
import uuid
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from .constants import (
    BUILTIN_TEMPLATES,
    CLUSTER_FORM_SIMILARITY,
    CLUSTER_JOIN_SIMILARITY,
    MAX_MODULE_DEMANDS_PER_STEP,
    MAX_SUPERPOSITION_SIZE,
    MAX_TEMPLATE_DESCRIPTION_CHARS,
    MAX_TEMPLATE_INTENT_CHARS,
    MAX_TEMPLATE_NAME_CHARS,
    MAX_TEMPLATE_PRECONDITION_CHARS,
    MAX_TEMPLATE_PRECONDITIONS,
    MAX_TEMPLATE_SOURCE_MEMORY_IDS,
    MAX_TEMPLATE_STEP_TITLE_CHARS,
    MAX_TEMPLATE_STEPS,
    MIN_PATTERN_MARGIN,
    MIN_PATTERN_SCORE,
    MODULE_DEMAND_BY_TAG,
    MODULE_DEMAND_INTENT,
    MODULE_DEMAND_KINDS,
    MODULE_DEMAND_TAG,
    PATTERN_KEYWORDS,
    PATTERN_SIGNAL_WEIGHTS,
    TAGS,
    TAG_TEXT,
    TASK_FAMILY_PATTERNS,
    TASK_PATTERNS,
    TEMPLATE_MATCH_MIN,
    TEMPLATE_MATCH_STRONG,
)
from .qprobability import build_superposition
from .template_build import (
    _active_retrieval_points,
    _module_demands_for_tag,
    _prior_utility,
    build_template_steps,
    builtin_template_records,
    derive_signature_from_demands,
)
from .template_classify import (
    _argmax,
    _clip,
    _family_hit,
    _graph_scores,
    _keyword_hits,
    _structure_scores,
    classify_task_pattern,
)
from .template_match import (
    _multi_sequence_lcs,
    _normalize_signature,
    template_match_score,
)
from .template_mining import mine_templates
from .template_plan import plan_task
from .template_publish import (
    _EM_DASH,
    _KABAB_NAME,
    _TRIGGER_TOKENS,
    apply_template_feedback,
    publish_template_validate,
)
from .text_similarity import _clean_text, semantic_similarity, sequence_similarity
from .trajectory import structure_signature
