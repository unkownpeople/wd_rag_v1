from __future__ import annotations

"""Unified parse-only dispatcher for PDF, Word, and Excel-compatible files."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

try:
    from .excel_parser import parse_excel
    from .models import ParsedDocument
    from .pdf_parser import parse_pdf
    from .word_parser import parse_word
except ImportError:  # supports direct execution from the parsers directory
    from excel_parser import parse_excel
    from models import ParsedDocument
    from pdf_parser import parse_pdf
    from word_parser import parse_word


def _parse_pdf_document(input_file: Path) -> ParsedDocument:
    result = parse_pdf(input_file)
    records: list[dict[str, Any]] = []
    for page in result.pages:
        record = asdict(page)
        record.update(
            {
                "source_format": "pdf",
                "record_type": "page",
                "record_index": len(records),
            }
        )
        records.append(record)
    return ParsedDocument(
        source_file=result.source_file,
        source_format="pdf",
        records=records,
        metadata={
            **result.metadata,
            "accepted_table_count": sum(page.table_count for page in result.pages),
            "whole_page_candidate_count": sum(
                page.table_candidate_count for page in result.pages
            ),
        },
        warnings=[
            "PDF tables are accepted only from visually verified explicit regions; whole-page candidates are diagnostic only."
        ],
    )


def parse_document(input_file: Path) -> ParsedDocument:
    """Dispatch to exactly one format branch by file extension."""

    input_file = input_file.resolve()
    suffix = input_file.suffix.lower()
    parsers: dict[str, Callable[[Path], ParsedDocument]] = {
        ".pdf": _parse_pdf_document,
        ".docx": parse_word,
        ".csv": parse_excel,
        ".tsv": parse_excel,
        ".xlsx": parse_excel,
    }
    parser = parsers.get(suffix)
    if parser is None:
        supported = ", ".join(sorted(parsers))
        raise ValueError(f"Unsupported document format {suffix!r}; supported: {supported}")
    return parser(input_file)


def write_document_jsonl(result: ParsedDocument, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="\n") as file:
        file.write(
            json.dumps(
                {
                    "record_type": "document_meta",
                    "source_file": result.source_file,
                    "source_format": result.source_format,
                    "record_count": len(result.records),
                    "metadata": result.metadata,
                    "warnings": result.warnings,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        for record in result.records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_document_preview(result: ParsedDocument, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="\n") as file:
        file.write(f"source_file: {result.source_file}\n")
        file.write(f"source_format: {result.source_format}\n")
        file.write("stage: parse_only\n")
        file.write(f"record_count: {len(result.records)}\n\n")
        for index, record in enumerate(result.records):
            record_type = record.get("record_type", record.get("content_type", "record"))
            branch = record.get("parse_branch", record.get("parser", ""))
            file.write(f"=== {record_type} {index} | branch={branch} ===\n")
            if record.get("content_type") == "table":
                raw_content = record.get("raw_content", {})
                file.write("headers: " + "\t".join(str(cell) for cell in raw_content.get("headers", [])) + "\n")
                for row in raw_content.get("rows", []):
                    file.write("\t".join(str(cell) for cell in row) + "\n")
            elif record_type == "table":
                for row in record.get("rows", []):
                    file.write("\t".join(str(cell) for cell in row) + "\n")
            else:
                file.write(str(record.get("text", "")) + "\n")
            file.write("\n")
        if result.warnings:
            file.write("[warnings]\n")
            for warning in result.warnings:
                file.write(f"- {warning}\n")
