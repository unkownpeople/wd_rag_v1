"""Excel table normalization and parse-record creation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from ..models import ParsedDocument
except ImportError:  # supports direct execution from the parsers directory
    from models import ParsedDocument

from .reader import cell_value, column_name


def table_projection(rows: list[list[Any]]) -> tuple[dict[str, Any], str | None, list[str]]:
    warnings: list[str] = []
    column_count = max((len(row) for row in rows), default=0)
    if any(len(row) != column_count for row in rows):
        warnings.append("Rows had unequal lengths; missing trailing cells were kept as null.")

    rectangular_rows = [
        [cell_value(value) for value in row] + [None] * (column_count - len(row))
        for row in rows
    ]
    headers = rectangular_rows[0] if rectangular_rows else []
    body_rows = rectangular_rows[1:] if rectangular_rows else []
    last_row = len(rectangular_rows)
    last_column = column_name(column_count)
    cell_range = f"A1:{last_column}{last_row}" if last_row and last_column else None
    return {"headers": headers, "rows": body_rows}, cell_range, warnings


def table_record(
    *,
    source_file: Path,
    sheet_name: str,
    rows: list[list[Any]],
    parser_name: str,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    content, cell_range, projection_warnings = table_projection(rows)
    combined_warnings = list(warnings or [])
    combined_warnings.extend(projection_warnings)
    return {
        "source_type": "excel",
        "source": source_file.name,
        "location": {"sheet": sheet_name, "range": cell_range},
        "content_type": "table",
        "raw_content": content,
        "parser": parser_name,
        "warnings": combined_warnings,
    }


def document_from_delimited(
    input_file: Path, delimiter: str
) -> ParsedDocument:
    from .reader import read_delimited_rows

    rows, warnings = read_delimited_rows(input_file, delimiter)
    return ParsedDocument(
        source_file=str(input_file),
        source_format=input_file.suffix.lower().lstrip("."),
        records=[
            table_record(
                source_file=input_file,
                sheet_name=input_file.stem,
                rows=rows,
                parser_name="csv",
                warnings=warnings,
            )
        ],
        metadata={"sheet_names": [input_file.stem]},
    )


def document_from_xlsx(input_file: Path) -> ParsedDocument:
    from .reader import read_xlsx_rows

    sheet_names, sheet_rows = read_xlsx_rows(input_file)
    records = [
        table_record(
            source_file=input_file,
            sheet_name=sheet_name,
            rows=rows,
            parser_name="openpyxl",
        )
        for sheet_name, rows in sheet_rows
    ]
    return ParsedDocument(
        source_file=str(input_file),
        source_format="xlsx",
        records=records,
        metadata={"sheet_names": sheet_names},
    )
