from __future__ import annotations

"""Offline comparison between cleaned source records and generated chunks."""

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from .boundaries import token_spans
from .config import ChunkConfig
from .identity import make_record_id


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ranges_length(ranges: list[tuple[int, int]]) -> int:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged)


def _source_text(record: dict[str, Any]) -> str:
    return str(record.get("cleaned_text") or "")


def _normalise_token_text(tokens: list[str]) -> str:
    """忽略块连接时新增的空白，只比较真实内容和 Token 数。"""

    return re.sub(r"\s+", "", "".join(tokens))


def _score_text_record(
    record: dict[str, Any],
    record_id: str,
    chunks: list[dict[str, Any]],
    config: ChunkConfig,
) -> dict[str, Any]:
    source = _source_text(record)
    source_tokens = token_spans(source, config.tokenizer)
    source_token_count = len(source_tokens)
    spans: list[tuple[int, int]] = []
    emitted_tokens = 0
    span_count = 0
    valid_span_count = 0
    internal_duplicate_tokens = 0
    for chunk in chunks:
        chunk_spans = [
            span for span in chunk.get("source_spans", []) if span.get("source_record_id") == record_id
        ]
        if not chunk_spans:
            continue
        chunk_token_keys: list[tuple[str, int]] = []
        for span in chunk_spans:
            span_count += 1
            start = int(span.get("char_start", -1))
            end = int(span.get("char_end", -1))
            token_start = int(span.get("token_start", -1))
            token_end = int(span.get("token_end", -1))
            if not (0 <= start <= end <= len(source) and 0 <= token_start <= token_end <= source_token_count):
                continue
            valid_span_count += 1
            spans.append((token_start, token_end))
            emitted_tokens += token_end - token_start
            chunk_token_keys.extend(
                (record_id, token_index)
                for token_index in range(token_start, token_end)
            )
        internal_duplicate_tokens += max(0, len(chunk_token_keys) - len(set(chunk_token_keys)))
    covered_tokens = _ranges_length(spans)
    char_ranges: list[tuple[int, int]] = []
    for chunk in chunks:
        for span in chunk.get("source_spans", []):
            if span.get("source_record_id") != record_id:
                continue
            char_ranges.append((int(span.get("char_start", 0)), int(span.get("char_end", 0))))
    covered_chars = _ranges_length(char_ranges)
    return {
        "record_id": record_id,
        "source_record_index": record.get("source_record_index", record.get("source_page_record_index")),
        "page_number": record.get("page_number"),
        "source_chars": len(source),
        "covered_chars": covered_chars,
        "lost_chars": max(0, len(source) - covered_chars),
        "char_coverage": round(covered_chars / len(source), 6) if source else 1.0,
        "source_tokens": source_token_count,
        "covered_tokens": covered_tokens,
        "lost_tokens": max(0, source_token_count - covered_tokens),
        "emitted_source_tokens": emitted_tokens,
        "duplicate_overlap_tokens": max(0, emitted_tokens - covered_tokens),
        "internal_duplicate_tokens": internal_duplicate_tokens,
        "cross_chunk_overlap_tokens": max(
            0,
            emitted_tokens - covered_tokens - internal_duplicate_tokens,
        ),
        "token_coverage": round(covered_tokens / source_token_count, 6) if source_token_count else 1.0,
        "source_span_validity": round(valid_span_count / span_count, 6) if span_count else 1.0,
    }


def _text_chunk_exactness(
    records: list[dict[str, Any]], chunks: list[dict[str, Any]], config: ChunkConfig
) -> float:
    source_by_id = {
        make_record_id(record, index): _source_text(record)
        for index, record in enumerate(records)
        if record.get("record_type") == "text"
    }
    exact = 0
    eligible = 0
    for chunk in chunks:
        expected_tokens: list[str] = []
        valid = True
        for span in chunk.get("source_spans", []):
            source = source_by_id.get(span.get("source_record_id"))
            if source is None:
                valid = False
                break
            source_tokens = token_spans(source, config.tokenizer)
            token_start = int(span.get("token_start", -1))
            token_end = int(span.get("token_end", -1))
            if not (0 <= token_start <= token_end <= len(source_tokens)):
                valid = False
                break
            expected_tokens.extend(source[token_start_:token_end_] for token_start_, token_end_ in source_tokens[token_start:token_end])
        if not valid or not expected_tokens:
            continue
        eligible += 1
        chunk_text = str(chunk.get("chunk_text") or "")
        # 评分必须和切块使用同一个 Tokenizer；不能回退到旧正则基线。
        actual_spans = token_spans(chunk_text, config.tokenizer)
        actual_tokens = [chunk_text[start:end] for start, end in actual_spans]
        if (
            len(actual_tokens) == len(expected_tokens)
            and _normalise_token_text(actual_tokens)
            == _normalise_token_text(expected_tokens)
        ):
            exact += 1
    return exact / eligible if eligible else 1.0


def _score_tables(
    records: list[dict[str, Any]], chunks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    table_scores: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if record.get("record_type") != "table":
            continue
        record_id = make_record_id(record, index)
        matrix = record.get("raw_matrix") or []
        related = [chunk for chunk in chunks if record_id in chunk.get("source_record_ids", [])]
        header_count = int(related[0].get("header_row_count", 1)) if related else 1
        body_rows = max(0, len(matrix) - header_count)
        covered_rows = {
            int(row_index)
            for chunk in related
            for row_index in chunk.get("row_indices", [])
            if int(row_index) >= header_count
        }
        raw_ok = all(_digest(chunk.get("raw_matrix")) == _digest(matrix) for chunk in related)
        headers_ok = bool(related) and all(chunk.get("header_row_count", 0) >= header_count for chunk in related)
        table_scores.append(
            {
                "record_id": record_id,
                "table_id": record.get("table_id"),
                "source_body_rows": body_rows,
                "covered_body_rows": len(covered_rows),
                "lost_body_rows": max(0, body_rows - len(covered_rows)),
                "row_coverage": round(len(covered_rows) / body_rows, 6) if body_rows else 1.0,
                "chunk_count": len(related),
                "raw_matrix_preserved": raw_ok,
                "header_preserved": headers_ok,
            }
        )
    total_rows = sum(item["source_body_rows"] for item in table_scores)
    covered_rows = sum(item["covered_body_rows"] for item in table_scores)
    table_summary = {
        "table_count": len(table_scores),
        "table_chunk_count": sum(item["chunk_count"] for item in table_scores),
        "body_rows": total_rows,
        "covered_body_rows": covered_rows,
        "lost_body_rows": max(0, total_rows - covered_rows),
        "row_coverage": round(covered_rows / total_rows, 6) if total_rows else 1.0,
        "raw_matrix_preserved": all(item["raw_matrix_preserved"] for item in table_scores),
        "header_preserved": all(item["header_preserved"] for item in table_scores),
    }
    return table_scores, table_summary


def _weighted_average(values: list[tuple[float, int]]) -> float:
    total_weight = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / total_weight if total_weight else 1.0


def score_chunks(
    records: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    config: ChunkConfig | None = None,
) -> dict[str, Any]:
    """Return an intrinsic score; this does not measure retrieval or answer quality."""

    config = config or ChunkConfig()
    text_chunks = [chunk for chunk in chunks if chunk.get("chunk_type") == "text"]
    text_records = []
    for index, record in enumerate(records):
        if record.get("record_type") != "text" or not _source_text(record).strip():
            continue
        text_records.append(
            _score_text_record(record, make_record_id(record, index), text_chunks, config)
        )
    table_records, table_summary = _score_tables(records, chunks)

    content_values = [
        (item["token_coverage"], item["source_tokens"])
        for item in text_records
    ] + [
        (item["row_coverage"], item["source_body_rows"])
        for item in table_records
    ]
    content_preservation = _weighted_average(content_values)
    text_exactness = _text_chunk_exactness(records, text_chunks, config)
    exactness = _weighted_average(
        [(text_exactness, sum(item["source_tokens"] for item in text_records))]
        + [(1.0 if item["raw_matrix_preserved"] else 0.0, max(1, item["source_body_rows"])) for item in table_records]
    )
    boundary_values = []
    size_values = []
    metadata_values = []
    for chunk in chunks:
        boundary_values.append(1.0 if chunk.get("boundary_reason") in {"sentence", "paragraph", "paragraph_end", "document_end", "table_group", "record_boundary", "page_boundary", "section_boundary"} else 0.5)
        token_count = int(chunk.get("token_count") or 0)
        if chunk.get("chunk_type", "").startswith("table"):
            size_values.append(1.0)
        elif token_count < config.min_tokens:
            size_values.append(0.5 if chunk.get("boundary_reason") in {"document_end", "paragraph_end", "section_boundary"} else 0.0)
        else:
            size_values.append(1.0 if token_count <= config.max_tokens else 0.0)
        required = ["chunk_id", "parent_record_id", "source_record_ids", "source_format", "section_path", "chunk_type", "chunk_text", "token_count", "source_token_count", "source_spans"]
        if chunk.get("chunk_type", "").startswith("table"):
            required = [key for key in required if key != "source_spans"]
        present = sum(key in chunk and chunk.get(key) not in (None, "") for key in required)
        if chunk.get("chunk_type", "").startswith("table"):
            present += sum(key in chunk for key in ["table_id", "raw_matrix", "row_indices", "header_row_count"])
            metadata_values.append(present / (len(required) + 4))
        else:
            metadata_values.append(present / len(required))
    boundary_score = sum(boundary_values) / len(boundary_values) if boundary_values else 1.0
    size_score = sum(size_values) / len(size_values) if size_values else 1.0
    metadata_score = sum(metadata_values) / len(metadata_values) if metadata_values else 1.0
    overall = 100 * (
        0.40 * content_preservation
        + 0.25 * exactness
        + 0.15 * boundary_score
        + 0.10 * size_score
        + 0.10 * metadata_score
    )
    skipped_images = sum(record.get("record_type") == "image_pending" for record in records)
    internal_duplicates = sum(item["internal_duplicate_tokens"] for item in text_records)
    warnings = [
        "image_pending 记录未进入切块，等待 OCR/图像处理。" if skipped_images else None,
        "该评分不是固定评测集上的 Recall@K/MRR。",
    ]
    if internal_duplicates:
        warnings.append(f"发现 {internal_duplicates} 个块内重复 Token，需要检查重叠边界。")
    return {
        "score_type": "offline_intrinsic_chunking_score",
        "score_note": "仅比较清洗源与切块的覆盖、完整性、边界和元数据；不包含 Embedding、向量召回或问答质量。",
        "overall_score": round(overall, 2),
        "grade": "A" if overall >= 90 else "B" if overall >= 80 else "C" if overall >= 70 else "D",
        "chunk_count": len(chunks),
        "source_record_count": len(records),
        "text_record_count": len(text_records),
        "image_pending_excluded": skipped_images,
        "text": {
            "record_count": len(text_records),
            "source_tokens": sum(item["source_tokens"] for item in text_records),
            "covered_tokens": sum(item["covered_tokens"] for item in text_records),
            "lost_tokens": sum(item["lost_tokens"] for item in text_records),
            "duplicate_overlap_tokens": sum(item["duplicate_overlap_tokens"] for item in text_records),
            "internal_duplicate_tokens": internal_duplicates,
            "cross_chunk_overlap_tokens": sum(
                item["cross_chunk_overlap_tokens"] for item in text_records
            ),
            "token_coverage": round(_weighted_average([(item["token_coverage"], item["source_tokens"]) for item in text_records]), 6) if text_records else 1.0,
            "exact_chunk_ratio": round(text_exactness, 6),
            "records": text_records,
        },
        "tables": {**table_summary, "records": table_records},
        "quality_dimensions": {
            "content_preservation": round(content_preservation * 100, 2),
            "content_exactness": round(exactness * 100, 2),
            "boundary_quality": round(boundary_score * 100, 2),
            "size_quality": round(size_score * 100, 2),
            "metadata_completeness": round(metadata_score * 100, 2),
        },
        "comparison": {
            "source_records": len(records),
            "generated_chunks": len(chunks),
            "text_source_tokens": sum(item["source_tokens"] for item in text_records),
            "text_covered_tokens": sum(item["covered_tokens"] for item in text_records),
            "text_lost_tokens": sum(item["lost_tokens"] for item in text_records),
            "table_source_body_rows": table_summary["body_rows"],
            "table_covered_body_rows": table_summary["covered_body_rows"],
            "table_lost_body_rows": table_summary["lost_body_rows"],
            "duplicate_overlap_tokens": sum(item["duplicate_overlap_tokens"] for item in text_records),
            "internal_duplicate_tokens": internal_duplicates,
            "cross_chunk_overlap_tokens": sum(
                item["cross_chunk_overlap_tokens"] for item in text_records
            ),
        },
        "warnings": [warning for warning in warnings if warning],
    }
