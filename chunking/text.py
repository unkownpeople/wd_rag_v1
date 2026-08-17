from __future__ import annotations

"""正文切分：按标题、段落、句子和长度边界组织文本块。"""

import re
from typing import Any

from .config import ChunkConfig
from .identity import (
    as_text,
    make_chunk_id,
    page_value,
    source_record_ids,
    source_spans_payload,
    token_count_for_spans,
)
from .models import SourceSpan
from .boundaries import (
    estimate_tokens,
    paragraph_spans,
    sentence_spans,
    token_indices,
    token_spans,
)


HEADING_RE = re.compile(r"^(?:\d+(?:\.\d+)*|[A-Z])(?:[.)]|\s).{1,160}$")


def section_value(record: dict[str, Any], current_section: str) -> str:
    source_format = as_text(record.get("source_format")).lower()
    page = page_value(record)
    if source_format == "pdf" and page is not None:
        return f"page:{page}"
    return current_section or "document"


def is_heading(record: dict[str, Any], text: str) -> bool:
    role = as_text(record.get("content_role")).lower()
    style = as_text(record.get("style")).lower()
    return role in {"section_title", "title", "heading"} or style.startswith("heading") or bool(
        HEADING_RE.match(text)
    )


def _make_span(
    record_id: str,
    record_index: int,
    text: str,
    char_start: int,
    char_end: int,
    section_path: str,
    unit_type: str,
    paragraph_index: int,
    all_tokens: list[tuple[int, int]],
) -> SourceSpan:
    token_start, token_end = token_indices(all_tokens, char_start, char_end)
    return SourceSpan(
        record_id=record_id,
        record_index=record_index,
        text=text[char_start:char_end],
        char_start=char_start,
        char_end=char_end,
        token_start=token_start,
        token_end=token_end,
        section_path=section_path,
        unit_type=unit_type,
        paragraph_index=paragraph_index,
    )


def _make_text_chunk(
    spans: list[SourceSpan],
    records: dict[str, dict[str, Any]],
    parent_record_id: str,
    ordinal: int,
    section_path: str,
    boundary_reason: str,
    previous_chunk: dict[str, Any] | None,
    tokenizer,
) -> dict[str, Any]:
    """将正文来源范围组装成一个可持久化的块。"""

    chunk_text = "\n\n".join(span.text.strip() for span in spans if span.text.strip()).strip()
    source_page_values = [
        page_value(records[span.record_id])
        for span in spans
        if span.record_id in records and page_value(records[span.record_id]) is not None
    ]
    chunk_id = make_chunk_id(parent_record_id, "text", ordinal, chunk_text, spans)

    # 用来源 token 的交集计算重叠，避免把自然相邻文本误判为重叠。
    current_token_keys = {
        (span.record_id, token_index)
        for span in spans
        for token_index in range(span.token_start, span.token_end)
    }
    previous_token_keys = {
        (span["source_record_id"], token_index)
        for span in (previous_chunk or {}).get("source_spans", [])
        for token_index in range(span["token_start"], span["token_end"])
    }
    overlap_token_count = len(current_token_keys & previous_token_keys)
    first_record = records[spans[0].record_id]
    return {
        "chunk_id": chunk_id,
        "parent_record_id": parent_record_id,
        "source_record_ids": source_record_ids(spans),
        "source_format": as_text(first_record.get("source_format")),
        "page_start": min(source_page_values) if source_page_values else None,
        "page_end": max(source_page_values) if source_page_values else None,
        "section_path": section_path,
        "chunk_type": "text",
        "chunk_text": chunk_text,
        "token_count": estimate_tokens(chunk_text, tokenizer),
        "source_token_count": token_count_for_spans(spans),
        "overlap_from": previous_chunk.get("chunk_id") if overlap_token_count else None,
        "overlap_token_count": overlap_token_count,
        "boundary_reason": boundary_reason,
        "source_spans": source_spans_payload(spans),
    }


class TextAccumulator:
    """在同一文档/页面/标题范围内积累正文来源单元。"""

    def __init__(
        self,
        config: ChunkConfig,
        records: dict[str, dict[str, Any]],
        parent_id: str,
    ) -> None:
        self.config = config
        self.records = records
        self.parent_id = parent_id
        self.spans: list[SourceSpan] = []
        self.section_path = "document"
        self.chunks: list[dict[str, Any]] = []

    @property
    def token_count(self) -> int:
        return token_count_for_spans(self.spans)

    def set_section(self, section_path: str) -> None:
        if self.spans and self.section_path != section_path:
            self.flush("section_boundary")
        self.section_path = section_path

    def add(self, span: SourceSpan) -> None:
        span_tokens = span.token_end - span.token_start
        if self.spans:
            candidate_tokens = self.token_count + span_tokens
            # max_tokens 是硬上限；即使当前块还没达到 min_tokens，也不能越过它。
            if candidate_tokens > self.config.max_tokens:
                self.flush("max_tokens")
            # target_tokens 只有在当前块已达到 min_tokens 后才触发，避免大量短块。
            elif (
                candidate_tokens > self.config.target_tokens
                and self.token_count >= self.config.min_tokens
            ):
                self.flush("sentence" if span.unit_type == "sentence" else "paragraph")
        self.spans.append(span)

    def flush(self, reason: str) -> None:
        if not self.spans:
            return
        chunk = _make_text_chunk(
            self.spans,
            self.records,
            self.parent_id,
            len(self.chunks),
            self.section_path,
            reason,
            self.chunks[-1] if self.chunks else None,
            self.config.tokenizer,
        )
        if chunk["chunk_text"]:
            self.chunks.append(chunk)
        self.spans = []


def _split_long_sentence(
    span: SourceSpan,
    config: ChunkConfig,
    full_token_spans: dict[str, list[tuple[int, int]]],
) -> list[list[SourceSpan]]:
    """单句超过最大长度时，使用 token 窗口兜底。"""

    tokens = full_token_spans[span.record_id]
    start = span.token_start
    end = span.token_end
    parts: list[list[SourceSpan]] = []
    while start < end:
        part_end = min(start + config.max_tokens, end)
        # token_spans 的 token 起点可能落在句子前的空白上；不能让这个
        # 空白把窗口的相对切片索引变成负数，否则首个窗口会变成空块，
        # 随后整段正文被评分为丢失。
        char_start = max(span.char_start, tokens[start][0])
        char_end = tokens[part_end - 1][1]
        part_text = span.text[char_start - span.char_start : char_end - span.char_start]
        parts.append(
            [
                SourceSpan(
                    record_id=span.record_id,
                    record_index=span.record_index,
                    text=part_text,
                    char_start=char_start,
                    char_end=char_end,
                    token_start=start,
                    token_end=part_end,
                    section_path=span.section_path,
                    unit_type="token_window",
                    paragraph_index=span.paragraph_index,
                )
            ]
        )
        if part_end == end:
            break
        start = max(part_end - config.token_overlap, start + 1)
    return parts


def _split_long_paragraph(
    sentences: list[SourceSpan],
    config: ChunkConfig,
    full_token_spans: dict[str, list[tuple[int, int]]],
) -> list[tuple[list[SourceSpan], str]]:
    """长段落优先按句累积，并在被拆开的边界保留上一句。"""

    if not sentences:
        return []
    if any(span.token_end - span.token_start > config.max_tokens for span in sentences):
        parts: list[tuple[list[SourceSpan], str]] = []
        for sentence in sentences:
            if sentence.token_end - sentence.token_start <= config.max_tokens:
                parts.append(([sentence], "sentence"))
            else:
                parts.extend(
                    (window, "token_window")
                    for window in _split_long_sentence(sentence, config, full_token_spans)
                )
        return parts

    parts: list[tuple[list[SourceSpan], str]] = []
    current: list[SourceSpan] = []
    for sentence in sentences:
        sentence_tokens = sentence.token_end - sentence.token_start
        current_tokens = token_count_for_spans(current)
        if current and current_tokens + sentence_tokens > config.target_tokens:
            parts.append((current, "sentence"))
            overlap = [current[-1]] if config.sentence_overlap else []
            if overlap and token_count_for_spans(overlap) + sentence_tokens <= config.max_tokens:
                current = overlap
            else:
                current = []
        current.append(sentence)
    if current:
        parts.append((current, "paragraph_end"))
    return parts


def record_text_units(
    record: dict[str, Any],
    record_id: str,
    record_index: int,
    section_path: str,
    config: ChunkConfig,
) -> list[tuple[list[SourceSpan], str]]:
    """把一条清洗正文记录拆成待累积的来源单元。"""

    text = as_text(record.get("cleaned_text"))
    all_tokens = token_spans(text, config.tokenizer)
    units: list[tuple[list[SourceSpan], str]] = []
    for paragraph_index, (paragraph_start, paragraph_end) in enumerate(paragraph_spans(text)):
        sentences = [
            _make_span(
                record_id,
                record_index,
                text,
                sentence_start,
                sentence_end,
                section_path,
                "sentence",
                paragraph_index,
                all_tokens,
            )
            for sentence_start, sentence_end in sentence_spans(text, paragraph_start, paragraph_end)
        ]
        if not sentences:
            continue
        if token_count_for_spans(sentences) <= config.target_tokens:
            units.append((sentences, "paragraph"))
        else:
            units.extend(_split_long_paragraph(sentences, config, {record_id: all_tokens}))
    return units


def append_text_units(
    accumulator: TextAccumulator,
    units: list[tuple[list[SourceSpan], str]],
) -> None:
    """把正文单元送入累积器，并处理长句兜底边界。"""

    for spans, reason in units:
        # 长段落拆出的相邻单元会共享上一句。若当前累积块还未因长度
        # 边界落块，先落当前块，再把共享句放入下一块，形成跨块重叠；
        # 这样同一来源 Token 不会在同一块内重复。
        existing_keys = {
            (existing.record_id, token_index)
            for existing in accumulator.spans
            for token_index in range(existing.token_start, existing.token_end)
        }
        duplicate_spans = [
            span
            for span in spans
            if {
                (span.record_id, token_index)
                for token_index in range(span.token_start, span.token_end)
            }.issubset(existing_keys)
        ]
        if duplicate_spans and len(duplicate_spans) < len(spans):
            accumulator.flush(reason)
        elif duplicate_spans and len(duplicate_spans) == len(spans):
            continue
        for span in spans:
            accumulator.add(span)
        if (
            reason in {"sentence", "paragraph_end"}
            and accumulator.token_count >= accumulator.config.target_tokens
            and accumulator.token_count >= accumulator.config.min_tokens
        ):
            accumulator.flush(reason)
