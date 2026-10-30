"""CLI compatibility entrypoint for the DSH Cordis adapter.

The actual reasoning-memory implementation lives in focused modules under
``reasoning_memory_core``. This file remains the stable Python worker path used
by TypeScript and re-exports the former module-level API for local callers.

Two transports are supported:

- **stdin/stdout** (default): read one JSON request from stdin, write one JSON
  response to stdout. The standard Cordis bridge protocol.
- **file I/O** (``--file-io <in> <out>``): read the request from ``<in>`` and
  write the response to ``<out>``. Used by the TypeScript adapter as a
  fallback when the host blocks pipe-based child-process stdio (EPERM).
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


def _process(request: dict) -> str:
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    result = handle_request(request)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def _run_file_io(argv: list[str]) -> int:
    flag = argv.index("--file-io")
    if flag + 2 >= len(argv):
        raise ValueError("--file-io requires an input path and an output path")
    input_path, output_path = argv[flag + 1], argv[flag + 2]
    with open(input_path, "r", encoding="utf-8") as source:
        request = json.load(source)
    response = _process(request)
    with open(output_path, "w", encoding="utf-8") as target:
        target.write(response)
    return 0


def main() -> int:
    try:
        if "--file-io" in sys.argv:
            return _run_file_io(list(sys.argv))
        request = json.load(sys.stdin)
        sys.stdout.write(_process(request))
        return 0
    except Exception as error:  # Report a transport-safe failure to stderr.
        sys.stderr.write(f"reasoning-memory core error: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
