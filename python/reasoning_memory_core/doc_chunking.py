"""document chunking for the reasoning-memory document retrieval surface.

Deterministic, stdlib-only chunking of user-imported documents (final spec
0.5.0 section 3.1). Markdown headings give the chunk structure; plain text
falls back to blank-line sections. No overlapping windows: chunks are linked
through prev/next (document order), parent (heading hierarchy), and refs
(deterministically extracted markdown anchors and section references).

Everything here is a pure function: no IO, no randomness, no LLM. Degenerate
inputs (empty content, oversized paragraphs, malformed headings) degrade
deterministically and are reported in the audit dict.
"""

from __future__ import annotations

import re
from typing import Any

from .constants import (
    CHUNK_MIN_CHARS,
    CHUNK_TARGET_CHARS,
    MAX_CHUNK_REFS,
    MAX_CHUNK_TEXT_CHARS,
    MAX_CHUNKS_PER_DOC,
    MAX_DOC_CONTENT_CHARS,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?;；.])")
_SECTION_REF_RE = re.compile(r"(?:见|参见|依据)\s*§?\s*(\d+(?:\.\d+)*)")
_CHAPTER_REF_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百]+)\s*[章节]")
_CHAPTER_HEADING_RE = re.compile(r"^第\s*([0-9一二三四五六七八九十百]+)\s*章")
_HEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)")
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _normalize_heading(text: str) -> str:
    return re.sub(r"[\s#。，、:：.]+", "", text).lower()


def _cn_to_int(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if all(char in _CN_NUMBERS for char in value):
        return _CN_NUMBERS[value]
    return None


def _split_long_paragraph(paragraph: str, target: int) -> list[str]:
    """Split one paragraph at sentence boundaries into <= target pieces."""
    if len(paragraph) <= target:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_BOUNDARY_RE.split(paragraph):
        while len(sentence) > MAX_CHUNK_TEXT_CHARS:
            pieces.append(sentence[:MAX_CHUNK_TEXT_CHARS])
            sentence = sentence[MAX_CHUNK_TEXT_CHARS:]
        candidate = current + sentence
        if current and len(candidate) > target:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    if not pieces:
        pieces = [paragraph[:MAX_CHUNK_TEXT_CHARS]]
    return pieces


def _extract_refs(text: str) -> tuple[list[str], int]:
    """Deterministic reference extraction; returns (resolved targets, unresolved count).

    Markdown links contribute their target when it is an anchor (#...); local
    paths without an anchor stay unresolved. Section references (见/参见/依据
    1.2, 第 3 章) contribute normalized numeric section keys. Resolution to
    chunk ids happens in a second pass against the heading index.
    """
    refs: list[str] = []
    unresolved = 0
    for match in _MARKDOWN_LINK_RE.finditer(text):
        target = match.group(1).strip()
        if target.startswith("#") and len(target) > 1:
            refs.append(("anchor", _normalize_heading(target[1:])))
        else:
            unresolved += 1
    for match in _SECTION_REF_RE.finditer(text):
        refs.append(("section", match.group(1)))
    for match in _CHAPTER_REF_RE.finditer(text):
        number = _cn_to_int(match.group(1))
        if number is not None:
            refs.append(("chapter", str(number)))
        else:
            unresolved += 1
    return refs, unresolved


def chunk_document(
    *,
    doc_id: str,
    content: str,
    chunk_target: int = CHUNK_TARGET_CHARS,
    chunk_min: int = CHUNK_MIN_CHARS,
) -> dict[str, Any]:
    """Chunk one document into linked chunk records.

    Returns {"chunks": [...], "charCount": int, "chunkCount": int, "audit": {...}}.
    chunk ids are derived deterministically as f"{doc_id}#{seq}".
    """
    if not isinstance(content, str) or not content.strip():
        return {
            "chunks": [], "charCount": 0, "chunkCount": 0,
            "audit": {"truncated": False, "headingsDetected": False, "unresolvedRefs": 0,
                      "oversizedParagraphs": 0, "contentTruncated": False},
        }
    target = max(100, int(chunk_target))
    minimum = max(1, min(int(chunk_min), target))
    original_length = len(content)
    content = content[:MAX_DOC_CONTENT_CHARS]
    content_truncated = original_length > MAX_DOC_CONTENT_CHARS

    lines = content.split("\n")
    headings_detected = False
    # Raw sections: each heading of any level starts a new section; sections
    # hold paragraphs that are sentence-split when oversized. Heading-only
    # sections are kept: they anchor the parent links of their children.
    sections: list[dict[str, Any]] = []
    current_heading: tuple[int, str] | None = None
    current_paragraphs: list[str] = []
    oversized = 0

    def flush_section() -> None:
        nonlocal current_heading, current_paragraphs, oversized
        if current_heading is None and not current_paragraphs:
            return
        text = "\n".join(current_paragraphs).strip()
        pieces: list[str] = []
        for paragraph in current_paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if len(paragraph) > target:
                oversized += 1
                pieces.extend(_split_long_paragraph(paragraph, target))
            else:
                pieces.append(paragraph)
        sections.append({
            "heading": current_heading[1] if current_heading else None,
            "headingLevel": current_heading[0] if current_heading else 0,
            "pieces": pieces,
            "rawText": text,
        })
        current_heading = None
        current_paragraphs = []

    for line in lines:
        heading = _HEADING_RE.match(line.strip())
        if heading:
            headings_detected = True
            flush_section()
            current_heading = (len(heading.group(1)), heading.group(2).strip())
            continue
        if not line.strip():
            if current_paragraphs:
                flush_section()
            continue
        current_paragraphs.append(line.rstrip())
    flush_section()

    # Assemble sections into chunks: heading boundaries delimit chunks. Tiny
    # pieces produced by the sentence split merge into the previous piece of
    # the SAME section only; heading boundaries are never collapsed, so the
    # heading structure survives even for very short sections.
    chunks: list[dict[str, Any]] = []
    for section in sections:
        heading = section["heading"]
        pieces = list(section["pieces"])
        merged_pieces: list[str] = []
        for piece in pieces:
            if merged_pieces and len(piece) < minimum and len(merged_pieces[-1]) + 1 + len(piece) <= MAX_CHUNK_TEXT_CHARS:
                merged_pieces[-1] = f"{merged_pieces[-1]}\n{piece}"
            else:
                merged_pieces.append(piece)
        if heading or not headings_detected:
            # A heading always starts a new chunk; for plain text (no
            # headings anywhere) every blank-line section is its own chunk.
            chunks.append({
                "heading": heading,
                "headingLevel": section["headingLevel"],
                "text": "",
            })
        elif not chunks:
            chunks.append({"heading": "", "headingLevel": 0, "text": ""})
        for piece in merged_pieces:
            if not chunks:
                chunks.append({"heading": "", "headingLevel": 0, "text": ""})
            current = chunks[-1]
            if current["text"] and len(current["text"]) + 1 + len(piece) > MAX_CHUNK_TEXT_CHARS:
                chunks.append({"heading": "", "headingLevel": 0, "text": ""})
                current = chunks[-1]
            current["text"] = f"{current['text']}\n{piece}" if current["text"] else piece

    if len(chunks) > MAX_CHUNKS_PER_DOC:
        chunks = chunks[:MAX_CHUNKS_PER_DOC]
        truncated = True
    else:
        truncated = False

    # Heading paths per chunk follow the markdown heading stack.
    heading_stack: list[tuple[int, str]] = []
    for chunk in chunks:
        if chunk["headingLevel"] > 0:
            while heading_stack and heading_stack[-1][0] >= chunk["headingLevel"]:
                heading_stack.pop()
            heading_stack.append((chunk["headingLevel"], chunk["heading"]))
        chunk["headingPath"] = [title for _, title in heading_stack]

    # Parent links point at the nearest ancestor heading chunk.
    level_chunk: dict[int, str] = {}
    records: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_id = f"{doc_id}#{index}"
        parent = ""
        if chunk["headingLevel"] > 0:
            for ancestor_level in range(chunk["headingLevel"] - 1, 0, -1):
                if ancestor_level in level_chunk:
                    parent = level_chunk[ancestor_level]
                    break
            level_chunk = {level: cid for level, cid in level_chunk.items() if level < chunk["headingLevel"]}
            level_chunk[chunk["headingLevel"]] = chunk_id
        elif level_chunk:
            # A continuation chunk (oversized split under a heading) hangs off
            # the deepest current heading chunk.
            parent = level_chunk[max(level_chunk)]
        records.append({
            "chunkId": chunk_id,
            "docId": doc_id,
            "seq": index,
            "headingPath": chunk["headingPath"],
            "text": chunk["text"][:MAX_CHUNK_TEXT_CHARS],
            "prevChunkId": "",
            "nextChunkId": "",
            "parentChunkId": parent,
            "refs": [],
        })
    for index, record in enumerate(records):
        if index > 0:
            record["prevChunkId"] = records[index - 1]["chunkId"]
        if index + 1 < len(records):
            record["nextChunkId"] = records[index + 1]["chunkId"]

    # Second pass: resolve refs against the heading index.
    heading_index: dict[str, str] = {}
    section_index: dict[str, str] = {}
    for record in records:
        for path_part in record["headingPath"]:
            normalized = _normalize_heading(path_part)
            if normalized and normalized not in heading_index:
                heading_index[normalized] = record["chunkId"]
            number = _HEADING_NUMBER_RE.match(path_part)
            if number and number.group(1) not in section_index:
                section_index[number.group(1)] = record["chunkId"]
            # Chinese-numeral chapter headings ("第三章 详述") also register
            # their numeric key so 见第三章/第 3 章 references resolve.
            chapter = _CHAPTER_HEADING_RE.match(path_part)
            if chapter:
                chapter_number = _cn_to_int(chapter.group(1))
                if chapter_number is not None and str(chapter_number) not in section_index:
                    section_index[str(chapter_number)] = record["chunkId"]
    unresolved_total = 0
    for record in records:
        raw_refs, unresolved = _extract_refs(record["text"])
        unresolved_total += unresolved
        resolved: list[str] = []
        for kind, key in raw_refs:
            if kind in ("anchor", "section"):
                target = heading_index.get(key) or section_index.get(key)
            else:
                target = section_index.get(key)
                if target is None:
                    for number, chunk_id in section_index.items():
                        if number.startswith(key + "."):
                            target = chunk_id
                            break
            if target and target != record["chunkId"] and target not in resolved:
                resolved.append(target)
        record["refs"] = resolved[:MAX_CHUNK_REFS]

    return {
        "chunks": records,
        "charCount": len(content),
        "chunkCount": len(records),
        "audit": {
            "truncated": truncated,
            "headingsDetected": headings_detected,
            "unresolvedRefs": unresolved_total,
            "oversizedParagraphs": oversized,
            "contentTruncated": content_truncated,
        },
    }
