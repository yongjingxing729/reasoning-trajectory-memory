"""JSON bridge transports (stdin/stdout and --file-io) for the Python core."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

class JsonBridgeTests(unittest.TestCase):
    def test_cli_accepts_and_returns_json(self) -> None:
        script = ROOT / "python" / "reasoning_memory.py"
        request = {
            "operation": "retrieve",
            "query": "schema validation",
            "memories": [],
            "events": [],
            "k": 2,
            "route": "semantic",
        }
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        response = json.loads(completed.stdout)
        self.assertEqual(response["results"], [])
        self.assertEqual(response["fusionWeights"], {
            "context": 0.3,
            "reasoning": 0.7,
            "structure": 0.0,
            "graph": 0.0,
            "vector": 0.0,
            "lexical": 0.0,
        })
        self.assertEqual(response["fusionMode"], "weighted")

    def test_cli_file_io_mode_reads_and_writes_files(self) -> None:
        script = ROOT / "python" / "reasoning_memory.py"
        directory = ROOT / "test" / ".tmp-file-io-test"
        directory.mkdir(exist_ok=True)
        try:
            input_path = directory / "request.json"
            output_path = directory / "response.json"
            input_path.write_text(json.dumps({
                "operation": "retrieve",
                "query": "schema validation",
                "memories": [],
                "events": [],
                "k": 2,
                "route": "semantic",
            }), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script), "--file-io", str(input_path), str(output_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            response = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(response["fusionMode"], "weighted")
            self.assertEqual(response["results"], [])
        finally:
            for file in ("request.json", "response.json"):
                (directory / file).unlink(missing_ok=True)
            directory.rmdir()

    def test_cli_file_io_mode_reports_errors_to_the_error_file(self) -> None:
        script = ROOT / "python" / "reasoning_memory.py"
        directory = ROOT / "test" / ".tmp-file-io-test"
        directory.mkdir(exist_ok=True)
        try:
            input_path = directory / "request.json"
            output_path = directory / "response.json"
            error_path = directory / "error.txt"
            input_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script), "--file-io", str(input_path), str(output_path), str(error_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("core error", error_path.read_text(encoding="utf-8"))
            self.assertFalse(output_path.exists())
        finally:
            for file in ("request.json", "response.json", "error.txt"):
                (directory / file).unlink(missing_ok=True)
            directory.rmdir()

    def test_cli_pipe_transport_is_utf8_safe_for_chinese_content(self) -> None:
        # Regression for the en-US Windows CI failure: text-mode stdout used the
        # console codepage (cp1252), which cannot encode the Chinese content the
        # core returns. The protocol must be UTF-8 bytes regardless of locale.
        script = ROOT / "python" / "reasoning_memory.py"
        request = {
            "operation": "distill",
            "taskId": "task-zh",
            "sourceSessionId": "session-zh",
            "taskFamily": "general",
            "semanticText": "发布前校验模式",
            "events": tool_events(),
            "outcome": "success",
            "verifier": "unit-suite",
            "evidence": "校验后测试通过",
            "reasoningInsight": "发布生成产物前先校验源模式",
            "failureMode": "",
        }
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        response = json.loads(completed.stdout.decode("utf-8"))
        self.assertTrue(response["stored"])
        self.assertEqual(response["memories"][0]["reasoning"], "发布生成产物前先校验源模式")


if __name__ == "__main__":
    unittest.main()
