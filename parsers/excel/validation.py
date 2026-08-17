"""Source-to-parse comparisons for Excel-compatible documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .reader import read_delimited_rows, read_xlsx_rows
from .table import table_projection


def source_tables(input_file: Path) -> list[tuple[str, list[list[Any]], str]]:
    suffix = input_file.suffix.lower()
    if suffix == ".csv":
        rows, _ = read_delimited_rows(input_file, ",")
        return [(input_file.stem, rows, "csv")]
    if suffix == ".tsv":
        rows, _ = read_delimited_rows(input_file, "\t")
        return [(input_file.stem, rows, "csv")]
    if suffix == ".xlsx":
        _, sheet_rows = read_xlsx_rows(input_file)
        return [(sheet_name, rows, "openpyxl") for sheet_name, rows in sheet_rows]
    raise ValueError(f"Cannot compare unsupported Excel source: {input_file}")


def table_differences(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    differences: list[str] = []
    if expected.get("headers") != actual.get("headers"):
        differences.append("raw_content.headers")
    if expected.get("rows") != actual.get("rows"):
        differences.append("raw_content.rows")
    return differences


def compare_excel_document(result: Any, input_file: Path) -> dict[str, Any]:
    """Compare parsed table records with a fresh source-file read."""

    input_file = input_file.resolve()
    parsed_tables = [
        record for record in result.records if record.get("content_type") == "table"
    ]
    expected_tables = source_tables(input_file)
    comparisons: list[dict[str, Any]] = []

    for index in range(max(len(parsed_tables), len(expected_tables))):
        parsed = parsed_tables[index] if index < len(parsed_tables) else None
        expected = expected_tables[index] if index < len(expected_tables) else None
        if parsed is None or expected is None:
            comparisons.append(
                {
                    "table_index": index,
                    "matched": False,
                    "differences": ["table_count"],
                    "parsed": parsed,
                    "source": expected,
                }
            )
            continue

        sheet_name, source_rows, source_parser = expected
        source_content, source_range, source_warnings = table_projection(source_rows)
        parsed_content = parsed.get("raw_content", {})
        checks = {
            "source_type": parsed.get("source_type") == "excel",
            "source_name": parsed.get("source") == input_file.name,
            "sheet": parsed.get("location", {}).get("sheet") == sheet_name,
            "range": parsed.get("location", {}).get("range") == source_range,
            "headers": parsed_content.get("headers") == source_content.get("headers"),
            "rows": parsed_content.get("rows") == source_content.get("rows"),
            "parser": parsed.get("parser") == source_parser,
        }
        differences = [name for name, matched in checks.items() if not matched]
        differences.extend(table_differences(source_content, parsed_content))
        comparisons.append(
            {
                "table_index": index,
                "matched": not differences,
                "location": {"sheet": sheet_name, "range": source_range},
                "checks": checks,
                "differences": sorted(set(differences)),
                "source_raw_content": source_content,
                "parsed_raw_content": parsed_content,
                "source_warnings": source_warnings,
            }
        )

    return {
        "source_type": "excel",
        "source": input_file.name,
        "parser": sorted({record.get("parser", "") for record in parsed_tables}),
        "table_count": len(comparisons),
        "matched": all(item["matched"] for item in comparisons),
        "tables": comparisons,
        "warnings": list(result.warnings),
    }
