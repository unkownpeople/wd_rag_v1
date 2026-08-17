"""Excel table projection and value-preservation validation."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from ..common import (
        CLEANING_VERSION,
        as_text,
        build_table_projection,
        matrix_from_excel_content,
        normalise_line,
        protected_tokens,
        source_digest,
        source_name,
    )
except ImportError:  # supports direct execution from the cleaners directory
    from common import (
        CLEANING_VERSION,
        as_text,
        build_table_projection,
        matrix_from_excel_content,
        normalise_line,
        protected_tokens,
        source_digest,
        source_name,
    )


def _content_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record.get("content_type") == "table"]


def _raw_content(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("raw_content")
    if not isinstance(value, dict):
        raise ValueError("Excel table record is missing raw_content object.")
    return {
        "headers": deepcopy(value.get("headers") or []),
        "rows": deepcopy(value.get("rows") or []),
    }


def clean_excel_records(
    records: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project Excel tables without changing their raw content."""

    content = _content_records(records)
    if not content:
        raise ValueError("Input JSONL does not contain Excel table records.")

    meta = next((record for record in records if record.get("record_type") == "document_meta"), {})
    source_file = as_text(meta.get("source_file")) or as_text(content[0].get("source_file"))
    source_format = as_text(meta.get("source_format")) or Path(source_file).suffix.lower().lstrip(".")
    doc_id = source_digest(source_file)
    output: list[dict[str, Any]] = []
    warnings: list[str] = []

    for table_index, record in enumerate(content, start=1):
        raw_content = _raw_content(record)
        matrix = matrix_from_excel_content(raw_content)
        location = record.get("location") if isinstance(record.get("location"), dict) else {}
        sheet_name = as_text(location.get("sheet")) or f"sheet_{table_index}"
        table_id = f"excel_t{table_index:02d}"
        table_text, labels, projection_warnings = build_table_projection(
            matrix,
            table_id=table_id,
            location=f"sheet={sheet_name};range={as_text(location.get('range')) or 'unknown'}",
            header_row_count=1,
        )
        table_warnings = [as_text(warning) for warning in record.get("warnings", []) if as_text(warning)]
        table_warnings.extend(projection_warnings)
        output.append(
            {
                "doc_id": doc_id,
                "source_file": source_file,
                "source_name": source_name(source_file) or as_text(record.get("source")),
                "source_format": source_format,
                "source_type": "excel",
                "record_type": "table",
                "source_record_index": record.get("record_index", table_index - 1),
                "table_id": table_id,
                "location": deepcopy(location),
                "content_type": "table",
                "raw_content": deepcopy(raw_content),
                "raw_matrix": deepcopy(matrix),
                "table_headers": labels,
                "table_text": table_text,
                "parser": record.get("parser"),
                "embedding_status": "included" if table_text else "excluded",
                "cleaning": {
                    "version": CLEANING_VERSION,
                    "raw_content_changed": False,
                    "raw_matrix_changed": False,
                    "actions": ["build_table_text"],
                    "warnings": table_warnings,
                },
            }
        )
        warnings.extend(table_warnings)

    raw_content_unchanged = all(
        original.get("raw_content") == cleaned.get("raw_content")
        for original, cleaned in zip(content, output)
    ) and len(content) == len(output)
    table_projection_complete = all(
        all(
            not normalise_line(cell) or normalise_line(cell).lower() in cleaned.get("table_text", "").lower()
            for row in cleaned.get("raw_matrix", [])
            for cell in row
        )
        for cleaned in output
    )
    protected_preserved = True
    for cleaned in output:
        source_tokens = Counter(
            token
            for row in cleaned.get("raw_matrix", [])
            for cell in row
            for token in protected_tokens(cell)
        )
        projection_tokens = Counter(protected_tokens(cleaned.get("table_text", "")))
        if any(projection_tokens[token] < count for token, count in source_tokens.items()):
            protected_preserved = False
            break
    report = {
        "stage": "clean",
        "cleaning_version": CLEANING_VERSION,
        "source_file": source_file,
        "source_format": source_format,
        "doc_id": doc_id,
        "input_table_count": len(content),
        "output_record_count": len(output),
        "record_counts": {"table": len(output)},
        "metadata": metadata or meta.get("metadata", {}),
        "warnings": warnings,
        "validation": {
            "passed": raw_content_unchanged and table_projection_complete and protected_preserved,
            "raw_content_unchanged": raw_content_unchanged,
            "raw_matrix_unchanged": raw_content_unchanged,
            "table_projection_complete": table_projection_complete,
            "protected_values_preserved": protected_preserved,
            "source_table_count": len(content),
            "cleaned_table_count": len(output),
        },
    }
    return output, report
