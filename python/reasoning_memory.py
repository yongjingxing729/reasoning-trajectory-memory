"""CLI compatibility entrypoint for the DSH Cordis adapter.

The actual reasoning-memory implementation lives in focused modules under
``reasoning_memory_core``. This file remains the stable Python worker path used
by TypeScript and re-exports the former module-level API for local callers.
"""

from __future__ import annotations

import json
import sys

from reasoning_memory_core import handle_request
from reasoning_memory_core.constants import *  # noqa: F403
from reasoning_memory_core.distillation import _make_memory
from reasoning_memory_core.feedback import _record_feedback
from reasoning_memory_core.reasoning_graph import (
    _fallback_reasoning_graph,
    _parse_reasoning_graph,
    graph_similarity,
)
from reasoning_memory_core.retrieval import _retrieve
from reasoning_memory_core.text_similarity import (
    _clean_text,
    _reasoning_text,
    _token_set,
    semantic_similarity,
    sequence_similarity,
)
from reasoning_memory_core.trajectory import classify_tool, structure_signature


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        result = handle_request(request)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as error:  # Report a transport-safe failure to stderr.
        sys.stderr.write(f"reasoning-memory core error: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
