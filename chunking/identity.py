from __future__ import annotations

"""切分结果的稳定标识和公共元数据转换。"""

import hashlib
import json
from typing import Any, Iterable

from .models import SourceSpan


def as_text(value: Any) -> str:
    return "" if value is None else str(value)


def canonical_digest(value: Any) -> str:
    """对 JSON 值生成稳定摘要，确保重复运行产生相同 ID。"""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def make_record_id(record: dict[str, Any], record_index: int) -> str:
    """根据清洗记录身份生成稳定的来源记录 ID。"""

    identity = {
        "doc_id": record.get("doc_id"),
        "record_type": record.get("record_type"),
        "source_page_record_index": record.get("source_page_record_index"),
        "source_record_index": record.get("source_record_index"),
        "sequence": record.get("sequence"),
        "table_id": record.get("table_id"),
        "record_index": record_index,
    }
    return "record_" + canonical_digest(identity)[:20]


def source_spans_payload(spans: Iterable[SourceSpan]) -> list[dict[str, Any]]:
    """将内部来源范围转换为 JSONL 可序列化结构。"""

    return [
        {
            "source_record_id": span.record_id,
            "record_index": span.record_index,
            "char_start": span.char_start,
            "char_end": span.char_end,
            "token_start": span.token_start,
            "token_end": span.token_end,
            "unit_type": span.unit_type,
            "paragraph_index": span.paragraph_index,
        }
        for span in spans
    ]


def source_record_ids(spans: Iterable[SourceSpan]) -> list[str]:
    return list(dict.fromkeys(span.record_id for span in spans))


def page_value(record: dict[str, Any]) -> int | None:
    value = record.get("page_number")
    return int(value) if value is not None else None


def token_count_for_spans(spans: Iterable[SourceSpan]) -> int:
    return sum(max(0, span.token_end - span.token_start) for span in spans)


def make_chunk_id(
    parent_record_id: str,
    chunk_type: str,
    ordinal: int,
    chunk_text: str,
    source_spans: Iterable[SourceSpan],
) -> str:
    payload = {
        "parent_record_id": parent_record_id,
        "chunk_type": chunk_type,
        "ordinal": ordinal,
        "chunk_text": chunk_text,
        "source_spans": source_spans_payload(source_spans),
    }
    return "chunk_" + canonical_digest(payload)[:24]
