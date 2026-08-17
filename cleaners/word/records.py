"""DOCX record cleaning and validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

try:
    from ..common import (
        CLEANING_VERSION,
        as_text,
        build_table_projection,
        clean_text,
        cloned_matrix,
        protected_tokens,
        source_digest,
        source_name,
    )
except ImportError:  # supports direct execution from the cleaners directory
    from common import (
        CLEANING_VERSION,
        as_text,
        build_table_projection,
        clean_text,
        cloned_matrix,
        protected_tokens,
        source_digest,
        source_name,
    )


def _is_section_title(style: Any) -> bool:
    value = as_text(style).strip().lower()
    return value.startswith("heading") or value in {"title", "subtitle"}


def _content_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record.get("record_type") in {"paragraph", "table"}]


def clean_word_records(
    records: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Clean paragraphs and project tables while retaining parse order."""

    content = _content_records(records)
    if not content:
        raise ValueError("Input JSONL does not contain DOCX paragraph or table records.")

    source_file = as_text(content[0].get("source_file"))
    doc_id = source_digest(source_file)
    output: list[dict[str, Any]] = []
    warnings: list[str] = []

    for record in content:
        record_type = record.get("record_type")
        common = {
            "doc_id": doc_id,
            "source_file": source_file,
            "source_name": source_name(source_file),
            "source_format": "docx",
            "source_record_index": record.get("record_index"),
            "sequence": record.get("sequence"),
            "page_number": record.get("page_number"),
            "parse_branch": record.get("parse_branch", "word:python-docx"),
        }

        if record_type == "paragraph":
            actions: list[str] = []
            raw_text = as_text(record.get("text"))
            cleaned = clean_text(raw_text, actions)
            if not cleaned:
                warnings.append(f"Empty paragraph was excluded: record_index={record.get('record_index')}")
                continue
            is_title = _is_section_title(record.get("style"))
            if is_title:
                actions.append("classify_section_title")
            output.append(
                {
                    **common,
                    "record_type": "text",
                    "content_role": "section_title" if is_title else "paragraph",
                    "style": record.get("style"),
                    "raw_text": raw_text,
                    "cleaned_text": cleaned,
                    "embedding_status": "included",
                    "cleaning": {
                        "version": CLEANING_VERSION,
                        "actions": sorted(set(actions)),
                        "removed_fragments": [],
                        "warnings": [],
                    },
                }
            )
            continue

        if record_type != "table":
            continue
        rows = cloned_matrix(record.get("rows"))
        table_number = int(record.get("table_number") or 0)
        table_id = f"word_t{table_number:02d}" if table_number else f"word_t{record.get('record_index', len(output)):02d}"
        header_row_count = 0 if len(rows) == 1 else 1
        table_text, labels, projection_warnings = build_table_projection(
            rows,
            table_id=table_id,
            location=f"sequence={record.get('sequence')}",
            header_row_count=header_row_count,
        )
        table_warnings = [*projection_warnings]
        if record.get("row_count") is not None and record.get("row_count") != len(rows):
            table_warnings.append("Parser row_count differs from raw rows; raw rows were retained.")
        output.append(
            {
                **common,
                "record_type": "table",
                "table_id": table_id,
                "table_number": record.get("table_number"),
                "row_count": record.get("row_count", len(rows)),
                "column_count": record.get("column_count", max((len(row) for row in rows), default=0)),
                "raw_matrix": deepcopy(rows),
                "rows": deepcopy(rows),
                "header_rows": deepcopy(rows[:header_row_count]),
                "header_row_count": header_row_count,
                "table_text": table_text,
                "table_headers": labels,
                "embedding_status": "included" if table_text else "excluded",
                "parser": record.get("parse_branch", "word:python-docx"),
                "cleaning": {
                    "version": CLEANING_VERSION,
                    "raw_matrix_changed": False,
                    "actions": ["build_table_text"],
                    "warnings": table_warnings,
                },
            }
        )
        warnings.extend(table_warnings)

    source_paragraphs = [record for record in content if record.get("record_type") == "paragraph"]
    source_tables = [record for record in content if record.get("record_type") == "table"]
    cleaned_text = [record for record in output if record.get("record_type") == "text"]
    cleaned_tables = [record for record in output if record.get("record_type") == "table"]
    raw_matrix_unchanged = all(
        source.get("rows") == cleaned.get("raw_matrix")
        for source, cleaned in zip(source_tables, cleaned_tables)
    ) and len(source_tables) == len(cleaned_tables)
    protected_preserved = all(
        protected_tokens(source.get("text")) == protected_tokens(cleaned.get("cleaned_text"))
        for source, cleaned in zip(source_paragraphs, cleaned_text)
    ) and len(source_paragraphs) == len(cleaned_text)
    table_projection_complete = all(
        all(
            not as_text(cell).strip() or as_text(cell).strip().lower() in cleaned.get("table_text", "").lower()
            for row in cleaned.get("raw_matrix", [])
            for cell in row
        )
        for cleaned in cleaned_tables
    )
    report = {
        "stage": "clean",
        "cleaning_version": CLEANING_VERSION,
        "source_file": source_file,
        "source_format": "docx",
        "doc_id": doc_id,
        "input_record_count": len(content),
        "output_record_count": len(output),
        "record_counts": {"text": len(cleaned_text), "table": len(cleaned_tables)},
        "metadata": metadata or {},
        "warnings": warnings,
        "validation": {
            "passed": raw_matrix_unchanged and protected_preserved and table_projection_complete,
            "raw_matrix_unchanged": raw_matrix_unchanged,
            "protected_text_tokens_preserved": protected_preserved,
            "table_projection_complete": table_projection_complete,
            "source_paragraph_count": len(source_paragraphs),
            "cleaned_text_count": len(cleaned_text),
            "source_table_count": len(source_tables),
            "cleaned_table_count": len(cleaned_tables),
        },
    }
    return output, report
