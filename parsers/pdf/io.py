"""File writers for raw PDF parse results."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import PdfParseResult


def write_jsonl(result: PdfParseResult, output_file: Path) -> None:
    """Write one raw page record per JSONL line."""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="\n") as file:
        for page in result.pages:
            file.write(json.dumps(asdict(page), ensure_ascii=False) + "\n")


def write_preview(result: PdfParseResult, output_file: Path) -> None:
    """Write a human-readable parse-only preview without cleaning."""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="\n") as file:
        file.write(f"source_file: {result.source_file}\n")
        file.write(f"page_count: {result.page_count}\n")
        file.write("stage: parse_only\n\n")
        for page in result.pages:
            file.write(
                f"=== page {page.page_number} | status={page.status} | "
                f"branches={','.join(page.parse_branches)} | "
                f"tables={page.table_count} ===\n"
            )
            file.write(page.text)
            if page.text and not page.text.endswith("\n"):
                file.write("\n")
            for table in page.table_records:
                file.write(
                    f"[table {table['table_id']} bbox={table['bbox']} "
                    f"method={table['extraction_method']}]\n"
                )
                for row in table["matrix"]:
                    file.write("\t".join(row) + "\n")
                file.write(
                    f"[table_shape] rows={table['row_count']} "
                    f"columns={table['column_count']} "
                    f"header_rows={len(table['header_rows'])}\n"
                )
            if page.warnings:
                file.write("[warnings]\n")
                for warning in page.warnings:
                    file.write(f"- {warning}\n")
            if page.error:
                file.write(f"[error] {page.error}\n")
            file.write("\n")
