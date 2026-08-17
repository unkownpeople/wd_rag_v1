from __future__ import annotations

"""切分流程编排：将清洗记录分派到正文或表格逻辑。"""

from typing import Any

from .boundaries import token_spans
from .config import ChunkConfig
from .identity import as_text, canonical_digest, make_record_id, page_value
from .models import SourceSpan
from .tables import make_table_chunks
from .text import TextAccumulator, append_text_units, is_heading, record_text_units, section_value


_SOURCE_METADATA_KEYS = (
    "document_id",
    "company_id",
    "company_name",
    "ticker",
    "cik",
    "fiscal_year",
    "report_type",
    "language",
    "currency",
    "unit_scale",
    "source_url",
    "source_provider",
    "source_kind",
    "source_file",
    "source_location",
    "source_sha256",
    "sha256",
    "period_end",
    "table_title",
    "table_context",
    "statement_scope",
)


def _copy_source_metadata(
    chunk: dict[str, Any], records: dict[str, dict[str, Any]]
) -> None:
    """把来源记录的可过滤字段带到每个正文/表格块。"""

    source = next(
        (records.get(record_id) for record_id in chunk.get("source_record_ids", [])),
        None,
    )
    if source is None:
        return
    for key in _SOURCE_METADATA_KEYS:
        if chunk.get(key) not in (None, ""):
            continue
        value = source.get(key)
        if value not in (None, ""):
            chunk[key] = value


def chunk_records(
    records: list[dict[str, Any]], config: ChunkConfig | None = None
) -> list[dict[str, Any]]:
    """对一份清洗 JSONL 的记录执行一次正文/表格切分。"""

    config = config or ChunkConfig()
    record_map = {
        make_record_id(record, index): record for index, record in enumerate(records)
    }
    parent_id = canonical_digest([record.get("doc_id") for record in records])[:20]
    accumulator = TextAccumulator(config, record_map, parent_id)
    chunks: list[dict[str, Any]] = []
    current_source = ""
    current_page: int | None = None
    current_section = "document"

    def drain_text_chunks() -> None:
        chunks.extend(accumulator.chunks)
        accumulator.chunks = []

    for index, record in enumerate(records):
        record_type = record.get("record_type")
        if record_type == "table":
            accumulator.flush("record_boundary")
            drain_text_chunks()
            matrix = [list(row) for row in record.get("raw_matrix", [])]
            if matrix:
                record_id = make_record_id(record, index)
                chunks.extend(
                    make_table_chunks(
                        record,
                        record_id,
                        index,
                        matrix,
                        config,
                        len(chunks),
                    )
                )
            continue

        if record_type != "text" or not as_text(record.get("cleaned_text")).strip():
            accumulator.flush("record_boundary")
            drain_text_chunks()
            continue

        record_id = make_record_id(record, index)
        record_map[record_id] = record
        source_format = as_text(record.get("source_format")).lower()
        page = page_value(record)
        source_file = as_text(record.get("source_file"))

        # 文档、PDF 页面和表格都是硬边界，不让上下文意外跨越。
        if current_source and source_file != current_source:
            accumulator.flush("source_boundary")
            drain_text_chunks()
            current_section = "document"
        if source_format == "pdf" and current_page is not None and page != current_page:
            accumulator.flush("page_boundary")
            drain_text_chunks()
        current_source = source_file
        current_page = page

        text = as_text(record.get("cleaned_text"))
        if source_format != "pdf" and is_heading(record, text):
            accumulator.flush("section_boundary")
            current_section = text
            heading_span = _make_heading_span(
                record_id, index, text, current_section, config.tokenizer
            )
            accumulator.set_section(current_section)
            accumulator.add(heading_span)
            continue

        section_path = section_value(record, current_section)
        accumulator.set_section(section_path)
        append_text_units(
            accumulator,
            record_text_units(record, record_id, index, section_path, config),
        )

    accumulator.flush("document_end")
    drain_text_chunks()
    for ordinal, chunk in enumerate(chunks):
        chunk["chunk_index"] = ordinal
        chunk["parent_record_id"] = chunk.get("parent_record_id") or chunk["source_record_ids"][0]
        _copy_source_metadata(chunk, record_map)
    return chunks


def _make_heading_span(
    record_id: str,
    record_index: int,
    text: str,
    section_path: str,
    tokenizer,
):
    """标题也是来源内容，不能只写入 metadata 后丢弃。"""

    token_count = len(token_spans(text, tokenizer))

    return SourceSpan(
        record_id=record_id,
        record_index=record_index,
        text=text,
        char_start=0,
        char_end=len(text),
        token_start=0,
        token_end=token_count,
        section_path=section_path,
        unit_type="heading",
        paragraph_index=0,
    )
