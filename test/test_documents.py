"""Document chunking, RAG, document service and document adversarial cases."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import *  # noqa: F401,F403

class DocumentChunkingTests(unittest.TestCase):
    def test_markdown_headings_build_paths_and_parent_links(self) -> None:
        result = core.chunk_document(doc_id="doc-a", content=(
            "# 开发规范\n\n## 2.1 代码风格\n函数命名使用 snake_case。\n"
            "## 2.2 测试要求\n每个模块必须有单测。\n"
        ))
        self.assertEqual(result["chunkCount"], 3)
        self.assertTrue(result["audit"]["headingsDetected"])
        self.assertEqual(result["chunks"][1]["headingPath"], ["开发规范", "2.1 代码风格"])
        self.assertEqual(result["chunks"][1]["parentChunkId"], "doc-a#1")
        self.assertEqual(result["chunks"][0]["nextChunkId"], "doc-a#2")
        self.assertEqual(result["chunks"][1]["prevChunkId"], "doc-a#1")

    def test_section_and_anchor_refs_resolve_to_chunk_ids(self) -> None:
        result = core.chunk_document(doc_id="doc-b", content=(
            "# 规范\n\n## 2.1 命名\n函数用 snake_case，见 §2.2。\n"
            "## 2.2 测试\n依据 2.1 先写测试。\n"
        ))
        naming = next(chunk for chunk in result["chunks"] if chunk["headingPath"][-1] == "2.1 命名")
        testing = next(chunk for chunk in result["chunks"] if chunk["headingPath"][-1] == "2.2 测试")
        self.assertIn(testing["chunkId"], naming["refs"])
        self.assertIn(naming["chunkId"], testing["refs"])

    def test_unresolved_references_are_counted_not_linked(self) -> None:
        result = core.chunk_document(doc_id="doc-c", content=(
            "# 规范\n\n## 说明\n见 §9.9 与 [本地文档](local-guide.md)。\n"
        ))
        chunk = result["chunks"][1]
        self.assertEqual(chunk["refs"], [])
        self.assertGreater(result["audit"]["unresolvedRefs"], 0)

    def test_plain_text_uses_blank_line_sections(self) -> None:
        result = core.chunk_document(doc_id="doc-d", content="第一段。\n\n第二段。\n\n第三段。")
        self.assertEqual(result["chunkCount"], 3)
        self.assertTrue(all(chunk["headingPath"] == [] for chunk in result["chunks"]))

    def test_oversized_paragraph_splits_at_sentence_boundaries(self) -> None:
        paragraph = "这是一句很长的测试句子。" * 300
        result = core.chunk_document(doc_id="doc-e", content=f"# 章节\n{paragraph}")
        self.assertGreater(result["chunkCount"], 1)
        self.assertGreater(result["audit"]["oversizedParagraphs"], 0)

    def test_tiny_fragments_merge_within_a_section_only(self) -> None:
        result = core.chunk_document(doc_id="doc-f", content=(
            "## 甲\n一句话。另一句话。\n## 乙\n另一句话。又一句话。\n"
        ))
        # Heading boundaries survive even though the texts are short.
        self.assertEqual(result["chunkCount"], 2)
        self.assertEqual(result["chunks"][0]["headingPath"][-1], "甲")
        self.assertEqual(result["chunks"][1]["headingPath"][-1], "乙")

    def test_empty_document_yields_no_chunks(self) -> None:
        result = core.chunk_document(doc_id="doc-g", content="   \n")
        self.assertEqual(result["chunks"], [])
        self.assertEqual(result["chunkCount"], 0)

    def test_oversized_content_is_truncated_and_audited(self) -> None:
        result = core.chunk_document(doc_id="doc-h", content="长内容" * 120_000)
        self.assertTrue(result["audit"]["contentTruncated"])

    def test_chunk_cap_truncates_and_audits(self) -> None:
        content = ("短块\n\n" * 9500)
        result = core.chunk_document(doc_id="doc-i", content=content, chunk_target=100, chunk_min=1)
        self.assertTrue(result["audit"]["truncated"])
        self.assertEqual(result["chunkCount"], 2000)

    def test_chunk_ids_are_deterministic(self) -> None:
        content = "# 甲\n内容。\n\n## 乙\n更多内容。\n"
        first = core.chunk_document(doc_id="doc-j", content=content)
        second = core.chunk_document(doc_id="doc-j", content=content)
        self.assertEqual(first["chunks"], second["chunks"])


class DocumentRagTests(unittest.TestCase):
    def setUp(self) -> None:
        chunked = core.chunk_document(doc_id="doc-code", content=(
            "# 开发规范\n\n## 2.1 命名\n函数使用 snake_case。\n"
            "## 2.2 测试\n提交前必须跑单测，见 §2.1。\n"
        ))
        self.chunks = chunked["chunks"]
        self.documents = [
            {"docId": "doc-code", "name": "memory_doc_dev-guide", "source": "company",
             "version": "1", "taskPatterns": ["code_synthesis"]},
            {"docId": "doc-web", "name": "memory_doc_web-guide", "source": "company",
             "version": "1", "taskPatterns": ["gui_operation"]},
            {"docId": "doc-general", "name": "memory_doc_general", "source": "",
             "version": "1", "taskPatterns": ["general"]},
        ]
        self.chunks.append({
            "chunkId": "doc-web#1", "docId": "doc-web", "seq": 1, "headingPath": ["页面"],
            "text": "点击按钮后检查页面状态", "prevChunkId": "", "nextChunkId": "",
            "parentChunkId": "", "refs": [],
        })
        self.chunks.append({
            "chunkId": "doc-general#1", "docId": "doc-general", "seq": 1, "headingPath": ["通用"],
            "text": "通用文档内容", "prevChunkId": "", "nextChunkId": "",
            "parentChunkId": "", "refs": [],
        })

    def test_pattern_filter_selects_matching_documents_only(self) -> None:
        result = core.retrieve_document(
            query="重构代码库的函数命名", task_pattern="code_synthesis",
            chunks=self.chunks, documents=self.documents, k=4,
            lexical_scores={"doc-code#2": 0.9},
        )
        self.assertEqual(result["pattern"], "code_synthesis")
        self.assertFalse(result["patternFallback"])
        doc_ids = {row["docId"] for row in result["results"]}
        self.assertIn("doc-code", doc_ids)
        self.assertNotIn("doc-web", doc_ids)

    def test_general_documents_pass_every_filter(self) -> None:
        result = core.retrieve_document(
            query="点击按钮", task_pattern="gui_operation",
            chunks=self.chunks, documents=self.documents, k=4,
            lexical_scores={"doc-general#1": 0.8},
        )
        self.assertFalse(result["patternFallback"])
        self.assertEqual(result["results"][0]["docId"], "doc-general")

    def test_pattern_fallback_when_no_document_matches(self) -> None:
        result = core.retrieve_document(
            query="计算数学方程", task_pattern="numeric_search",
            chunks=[chunk for chunk in self.chunks if chunk["docId"] == "doc-code"],
            documents=[doc for doc in self.documents if doc["docId"] == "doc-code"],
            k=4, lexical_scores={"doc-code#1": 0.9},
        )
        self.assertTrue(result["patternFallback"])
        self.assertEqual(result["results"], [])

    def test_expansion_includes_family_keys_and_template_intent(self) -> None:
        template = {
            "templateId": "memory_tpl_x",
            "steps": [
                {"index": 2, "moduleDemands": [
                    {"kind": "retrieve_document", "intent": "检索命名规范章节"},
                ]},
            ],
        }
        result = core.retrieve_document(
            query="重构代码库", task_pattern="code_synthesis",
            chunks=self.chunks, documents=self.documents, k=2,
            active_template=template, step_index=2,
        )
        self.assertIn("检索命名规范章节", result["expansion"]["intentTexts"])
        self.assertIn("humaneval", result["expansion"]["stepBackTerms"])

    def test_vector_and_lexical_rankings_trigger_rrf(self) -> None:
        result = core.retrieve_document(
            query="重构代码库", task_pattern="code_synthesis",
            chunks=self.chunks, documents=self.documents, k=4,
            lexical_scores={"doc-code#2": 0.9, "doc-code#3": 0.5},
            vector_scores={"doc-code#3": 0.8},
        )
        self.assertEqual(result["fusionMode"], "rrf")
        self.assertTrue(all(row["score"] <= 1.0 for row in result["results"]))

    def test_weighted_fusion_when_vector_absent(self) -> None:
        result = core.retrieve_document(
            query="重构代码库", task_pattern="code_synthesis",
            chunks=self.chunks, documents=self.documents, k=4,
            lexical_scores={"doc-code#2": 0.9},
        )
        self.assertEqual(result["fusionMode"], "weighted")

    def test_multi_hop_expansion_is_bounded_and_cycle_safe(self) -> None:
        hop_chunks = [
            {"chunkId": "d1#1", "docId": "d1", "seq": 1, "headingPath": ["根"], "text": "根",
             "prevChunkId": "", "nextChunkId": "", "parentChunkId": "", "refs": ["d1#2"]},
            {"chunkId": "d1#2", "docId": "d1", "seq": 2, "headingPath": ["子"], "text": "子",
             "prevChunkId": "d1#1", "nextChunkId": "", "parentChunkId": "d1#1", "refs": ["d1#3"]},
            {"chunkId": "d1#3", "docId": "d1", "seq": 3, "headingPath": ["孙"], "text": "孙",
             "prevChunkId": "d1#2", "nextChunkId": "", "parentChunkId": "d1#2", "refs": ["d1#1"]},
        ]
        result = core.retrieve_document(
            query="根", task_pattern="general", chunks=hop_chunks,
            documents={"d1": {"name": "memory_doc_d1", "source": "", "version": "1", "taskPatterns": ["general"]}},
            k=1, max_hops=2, lexical_scores={"d1#1": 0.9},
        )
        self.assertEqual(result["audit"]["visitedChunks"], 3)
        self.assertEqual(result["audit"]["seeds"], 1)

    def test_hop_zero_disables_expansion(self) -> None:
        hop_chunks = [
            {"chunkId": "d1#1", "docId": "d1", "seq": 1, "headingPath": ["根"], "text": "根",
             "prevChunkId": "", "nextChunkId": "d1#2", "parentChunkId": "", "refs": []},
            {"chunkId": "d1#2", "docId": "d1", "seq": 2, "headingPath": ["子"], "text": "子",
             "prevChunkId": "d1#1", "nextChunkId": "", "parentChunkId": "", "refs": []},
        ]
        result = core.retrieve_document(
            query="根", task_pattern="general", chunks=hop_chunks,
            documents={"d1": {"name": "memory_doc_d1", "source": "", "version": "1", "taskPatterns": ["general"]}},
            k=1, max_hops=0, lexical_scores={"d1#1": 0.9},
        )
        self.assertEqual(result["audit"]["visitedChunks"], 1)

    def test_expansion_respects_the_visited_cap(self) -> None:
        chain = []
        for index in range(1, 40):
            chain.append({
                "chunkId": f"c#1#{index}", "docId": "c", "seq": index, "headingPath": [f"块{index}"],
                "text": f"块{index}", "prevChunkId": f"c#1#{index - 1}" if index > 1 else "",
                "nextChunkId": f"c#1#{index + 1}" if index < 39 else "",
                "parentChunkId": "", "refs": [],
            })
        result = core.retrieve_document(
            query="块1", task_pattern="general", chunks=chain,
            documents={"c": {"name": "memory_doc_c", "source": "", "version": "1", "taskPatterns": ["general"]}},
            k=1, max_hops=2, lexical_scores={"c#1#1": 0.9},
        )
        self.assertLessEqual(result["audit"]["visitedChunks"], 16)

    def test_empty_corpus_returns_a_reason(self) -> None:
        result = core.retrieve_document(
            query="任意", task_pattern="general", chunks=[], documents=[],
        )
        self.assertEqual(result["results"], [])
        self.assertIn("no document chunk", result["reason"])


class DocumentServiceTests(unittest.TestCase):
    def test_import_document_operation_chunks(self) -> None:
        result = core.handle_request({
            "operation": "import_document", "docId": "svc-doc", "name": "memory_doc_svc",
            "content": "# S\n内容一。\n\n## T\n内容二。\n",
        })
        self.assertEqual(result["chunkCount"], 2)
        self.assertEqual(result["chunks"][0]["chunkId"], "svc-doc#1")

    def test_retrieve_document_operation(self) -> None:
        result = core.handle_request({
            "operation": "retrieve_document", "query": "重构代码库", "k": 2,
            "taskPattern": "code_synthesis",
            "chunks": [{
                "chunkId": "d#1", "docId": "d", "seq": 1, "headingPath": ["规范"],
                "text": "函数命名使用 snake_case", "prevChunkId": "", "nextChunkId": "",
                "parentChunkId": "", "refs": [],
            }],
            "documents": [{
                "docId": "d", "name": "memory_doc_d", "source": "", "version": "1",
                "taskPatterns": ["code_synthesis"],
            }],
            "lexicalScores": {"d#1": 0.9},
        })
        self.assertEqual(result["results"][0]["chunkId"], "d#1")
        self.assertEqual(result["results"][0]["docName"], "memory_doc_d")

    def test_delete_document_plan_lists_chunk_ids(self) -> None:
        chunks = [
            {"chunkId": "d#1", "docId": "d", "seq": 1, "headingPath": [], "text": "a",
             "prevChunkId": "", "nextChunkId": "", "parentChunkId": "", "refs": []},
            {"chunkId": "d#2", "docId": "d", "seq": 2, "headingPath": [], "text": "b",
             "prevChunkId": "", "nextChunkId": "", "parentChunkId": "", "refs": []},
        ]
        result = core.handle_request({"operation": "delete_document_plan", "docId": "d", "chunks": chunks})
        self.assertEqual(result["chunkIds"], ["d#1", "d#2"])


class DocumentAdversarialTests(unittest.TestCase):
    """T-rag-debug adversarial regressions for the v0.5.0 document surface."""

    def _chunk(self, chunk_id: str, **overrides: object) -> dict:
        chunk: dict = {
            "chunkId": chunk_id, "docId": "doc", "seq": int(chunk_id.split("#")[1]),
            "headingPath": [], "text": chunk_id,
            "prevChunkId": "", "nextChunkId": "", "parentChunkId": "", "refs": [],
        }
        chunk.update(overrides)
        return chunk

    def _docs(self, patterns: list[str]) -> dict:
        return {"doc": {
            "docId": "doc", "name": "memory_doc_x", "source": "", "version": "1",
            "taskPatterns": patterns,
        }}

    def _retrieve(self, chunks: list[dict], **overrides: object) -> dict:
        request: dict = {
            "operation": "retrieve_document",
            "query": "unrelated query words",
            "chunks": chunks,
            "documents": [self._docs(["general"])["doc"]],
            "taskPattern": "general",
            "k": 4,
        }
        request.update(overrides)
        return core.handle_request(request)

    # --- R1: zero text signal must yield empty results, not 1.0 rows ---------
    def test_zero_text_signal_returns_empty_not_full_score(self) -> None:
        chunks = [self._chunk("doc#1"), self._chunk("doc#2")]
        result = self._retrieve(chunks, lexicalScores={"doc#1": 0.0, "doc#2": 0.0})
        self.assertEqual(result["results"], [])
        self.assertEqual(result["audit"]["seeds"], 0)
        self.assertIn("text", result["reason"])

    def test_zero_text_signal_with_empty_score_maps(self) -> None:
        chunks = [self._chunk("doc#1")]
        result = self._retrieve(chunks, lexicalScores={})
        self.assertEqual(result["results"], [])
        self.assertIn("text", result["reason"])

    def test_positive_lexical_signal_still_retrieves(self) -> None:
        chunks = [self._chunk("doc#1")]
        result = self._retrieve(chunks, lexicalScores={"doc#1": 0.9})
        self.assertEqual(len(result["results"]), 1)
        self.assertGreater(result["results"][0]["score"], 0.0)

    # --- R2 (adjudicated): channel-carried hop decay -------------------------
    def test_weighted_mode_seed_beats_hop_neighbor_on_equal_scores(self) -> None:
        chunks = [
            self._chunk("doc#1", nextChunkId="doc#2"),
            self._chunk("doc#2", prevChunkId="doc#1"),
        ]
        result = self._retrieve(
            chunks,
            lexicalScores={"doc#1": 0.9, "doc#2": 0.9},
            k=1,
        )
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["chunkId"], "doc#1")
        self.assertEqual(result["results"][0]["hop"], 0)
        self.assertEqual(result["results"][0]["breakdown"]["link"], 1.0)
        self.assertEqual(result["audit"]["visitedChunks"], 2)

    def test_rrf_mode_hop_decay_is_carried_by_the_link_channel(self) -> None:
        # RRF allows a vector-strong hop-1 neighbor to outrank the seed; the
        # 0.6^hop decay stays visible in the auditable link breakdown. A third
        # chunk occupies the second seed slot so doc#2 is only reachable
        # through the link walk.
        chunks = [
            self._chunk("doc#1", nextChunkId="doc#2"),
            self._chunk("doc#2", prevChunkId="doc#1"),
            self._chunk("doc#3"),
        ]
        result = self._retrieve(
            chunks,
            lexicalScores={"doc#1": 0.9, "doc#2": 0.3, "doc#3": 0.9},
            vectorScores={"doc#1": 0.2, "doc#2": 0.9, "doc#3": 0.15},
            k=2,
        )
        self.assertEqual(result["fusionMode"], "rrf")
        self.assertEqual(len(result["results"]), 2)
        hop_row = next(row for row in result["results"] if row["chunkId"] == "doc#2")
        seed_row = next(row for row in result["results"] if row["chunkId"] == "doc#1")
        self.assertEqual(hop_row["hop"], 1)
        self.assertEqual(hop_row["viaChunkId"], "doc#1")
        self.assertEqual(seed_row["hop"], 0)
        self.assertAlmostEqual(hop_row["breakdown"]["link"], 0.6)
        self.assertEqual(seed_row["breakdown"]["link"], 1.0)

    def test_hop_walk_is_cycle_safe_and_capped(self) -> None:
        chunks = [
            self._chunk("doc#1", nextChunkId="doc#2"),
            self._chunk("doc#2", prevChunkId="doc#1", nextChunkId="doc#1"),
        ]
        result = self._retrieve(chunks, lexicalScores={"doc#1": 0.9}, k=1, maxHops=2)
        self.assertEqual(result["audit"]["visitedChunks"], 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["chunkId"], "doc#1")
        self.assertEqual(result["results"][0]["hop"], 0)

    # --- R3: Chinese-numeral chapter references resolve ----------------------
    def test_chinese_chapter_heading_resolves_chapter_reference(self) -> None:
        result = core.chunk_document(doc_id="doc-cn", content=(
            "# 第一章 概述\n见第三章的做法。\n# 第三章 详述\n内容如下。"
        ))
        target = next(chunk for chunk in result["chunks"] if chunk["headingPath"][-1] == "第三章 详述")
        source = next(chunk for chunk in result["chunks"] if "见第三章" in chunk["text"])
        self.assertIn(target["chunkId"], source["refs"])
        self.assertEqual(result["audit"]["unresolvedRefs"], 0)

    # --- R4: truncation flag off-by-one ---------------------------------------
    def test_content_truncation_flag_is_strict(self) -> None:
        exact = core.chunk_document(doc_id="d1", content="x" * 200_000)
        self.assertFalse(exact["audit"]["contentTruncated"])
        over = core.chunk_document(doc_id="d2", content="x" * 200_001)
        self.assertTrue(over["audit"]["contentTruncated"])
        self.assertEqual(over["charCount"], 200_000)


if __name__ == "__main__":
    unittest.main()
