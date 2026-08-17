"""PDF cleaned-record assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from ..common import source_digest
except ImportError:  # supports direct execution from the cleaners directory
    from common import source_digest

from .constants import CLEANING_VERSION, _IMAGE_MARKERS
from .tables import build_table_text
from .text import as_text, boundary_fragments, clean_page_text
from .validation import validate_cleaned_output


def image_pending_records(page: dict[str, Any], doc_id: str) -> list[dict[str, Any]]:
    page_number = int(page.get("page_number", 0))
    source_file = as_text(page.get("source_file"))
    candidates: list[dict[str, Any]] = []
    for region in page.get("image_regions", []) or page.get("images", []) or []:
        if isinstance(region, dict):
            candidates.append(region)

    for excluded in page.get("excluded_candidates", []) or []:
        reason = as_text(excluded.get("reason"))
        if any(marker in reason.lower() for marker in _IMAGE_MARKERS):
            candidates.append({"bbox": excluded.get("bbox"), "reason": reason})

    if not candidates and page.get("status") == "needs_ocr":
        candidates.append({"bbox": None, "reason": "No text layer; OCR/image processing is deferred."})

    return [
        {
            "doc_id": doc_id,
            "source_file": source_file,
            "source_format": "pdf",
            "record_type": "image_pending",
            "source_page_record_index": page.get("record_index"),
            "page_number": page_number,
            "bbox": candidate.get("bbox"),
            "embedding_status": "excluded",
            "ocr_status": "pending",
            "reason": as_text(candidate.get("reason")) or "Image processing deferred.",
            "cleaning": {"version": CLEANING_VERSION, "warnings": []},
        }
        for candidate in candidates
    ]


def clean_pdf_records(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Clean parsed PDF pages into text, table, and deferred-image records."""

    if not pages:
        raise ValueError("Input JSONL does not contain PDF page records.")
    source_file = as_text(pages[0].get("source_file"))
    doc_id = source_digest(source_file)
    repeated_fragments = boundary_fragments(pages)
    output: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []

    for page in pages:
        page_number = int(page.get("page_number", 0))
        cleaned_text, actions, removed_fragments, removed_table_ids = clean_page_text(
            page, repeated_fragments
        )
        tables = [table for table in page.get("table_records", []) if isinstance(table, dict)]
        table_ids = [as_text(table.get("table_id")) or "unknown-table" for table in tables]
        text_warnings = [as_text(warning) for warning in page.get("warnings", []) if as_text(warning)]
        missing_table_ids = [table_id for table_id in table_ids if table_id not in removed_table_ids]
        if missing_table_ids:
            text_warnings.append("Table duplicate removal is incomplete: " + ", ".join(missing_table_ids))

        text_record: dict[str, Any] | None = None
        if cleaned_text:
            text_record = {
                "doc_id": doc_id,
                "source_file": as_text(page.get("source_file")),
                "source_name": Path(as_text(page.get("source_file"))).name,
                "source_format": "pdf",
                "record_type": "text",
                "source_page_record_index": page.get("record_index"),
                "page_number": page_number,
                "raw_text": as_text(page.get("text")),
                "cleaned_text": cleaned_text,
                # 表格副本未完全从页面正文删除时保留正文，避免因去重失败
                # 误删页面上下文；独立 table 记录同时保留 raw_matrix。
                "embedding_status": "included",
                "cleaning": {
                    "version": CLEANING_VERSION,
                    "actions": sorted(set(actions)),
                    "removed_fragments": removed_fragments,
                    "table_ids_removed_from_page_text": removed_table_ids,
                    "warnings": text_warnings,
                },
            }
            output.append(text_record)

        table_output_count = 0
        for table in tables:
            table_text, table_warnings = build_table_text(table)
            matrix = [[as_text(cell) for cell in row] for row in table.get("matrix", [])]
            table_record = {
                "doc_id": doc_id,
                "source_file": as_text(page.get("source_file")),
                "source_name": Path(as_text(page.get("source_file"))).name,
                "source_format": "pdf",
                "record_type": "table",
                "source_page_record_index": page.get("record_index"),
                "page_number": page_number,
                "table_id": as_text(table.get("table_id")) or "unknown-table",
                "bbox": table.get("bbox"),
                "raw_matrix": matrix,
                "header_rows": table.get("header_rows", []),
                "table_title": table.get("table_title"),
                "table_context": table.get("table_context"),
                "statement_scope": table.get("statement_scope"),
                "merged_cells": table.get("merged_cells", []),
                "table_text": table_text,
                "embedding_status": "included",
                "parser": table.get("extraction_method"),
                "cleaning": {
                    "version": CLEANING_VERSION,
                    "raw_matrix_changed": False,
                    "warnings": [as_text(warning) for warning in table.get("warnings", []) if as_text(warning)],
                    **({"projection_warnings": table_warnings} if table_warnings else {}),
                },
            }
            output.append(table_record)
            table_output_count += 1

        image_records = image_pending_records(page, doc_id)
        output.extend(image_records)
        if not text_record and not tables and not image_records:
            output.append(
                {
                    "doc_id": doc_id,
                    "source_file": as_text(page.get("source_file")),
                    "source_format": "pdf",
                    "record_type": "image_pending",
                    "source_page_record_index": page.get("record_index"),
                    "page_number": page_number,
                    "bbox": None,
                    "embedding_status": "excluded",
                    "ocr_status": "pending",
                    "reason": "Page produced no text or table; image/OCR processing is deferred.",
                    "cleaning": {"version": CLEANING_VERSION, "warnings": []},
                }
            )
            image_records = [{}]

        page_summaries.append(
            {
                "page_number": page_number,
                "text_records": 1 if text_record else 0,
                "table_records": table_output_count,
                "image_pending_records": len(image_records),
                "table_ids": table_ids,
                "table_ids_removed_from_page_text": removed_table_ids,
            }
        )

    report = {
        "stage": "clean",
        "cleaning_version": CLEANING_VERSION,
        "source_file": source_file,
        "doc_id": doc_id,
        "input_page_count": len(pages),
        "output_record_count": len(output),
        "record_counts": {
            "text": sum(record.get("record_type") == "text" for record in output),
            "table": sum(record.get("record_type") == "table" for record in output),
            "image_pending": sum(record.get("record_type") == "image_pending" for record in output),
        },
        "repeated_boundary_fragments": sorted(repeated_fragments),
        "pages": page_summaries,
        "warnings": [
            warning
            for record in output
            for warning in record.get("cleaning", {}).get("warnings", [])
            if warning
        ],
    }
    report["validation"] = validate_cleaned_output(pages, output)
    return output, report
