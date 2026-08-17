"""Excel-compatible parse dispatcher."""

from __future__ import annotations

from pathlib import Path

try:
    from ..models import ParsedDocument
except ImportError:  # supports direct execution from the parsers directory
    from models import ParsedDocument

from .table import document_from_delimited, document_from_xlsx


def parse_excel(input_file: Path) -> ParsedDocument:
    """Parse CSV, TSV, or XLSX into raw table records."""

    input_file = input_file.resolve()
    if not input_file.is_file():
        raise FileNotFoundError(f"Excel file does not exist: {input_file}")

    suffix = input_file.suffix.lower()
    if suffix == ".csv":
        return document_from_delimited(input_file, ",")
    if suffix == ".tsv":
        return document_from_delimited(input_file, "\t")
    if suffix == ".xlsx":
        return document_from_xlsx(input_file)
    raise ValueError(f"Excel branch supports .csv, .tsv, and .xlsx: {input_file}")
