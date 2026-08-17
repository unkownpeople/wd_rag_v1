"""Acceptance checks for parsed PDF tables."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any


def _page_records(result: Any) -> list[dict[str, Any]]:
    if hasattr(result, "pages"):
        return [asdict(page) for page in result.pages]
    if hasattr(result, "records"):
        return list(result.records)
    raise TypeError("Expected PdfParseResult or ParsedDocument-like result")


def _value_at(matrix: list[list[str]], row: int, column: int) -> str | None:
    if row >= len(matrix) or column >= len(matrix[row]):
        return None
    return matrix[row][column]


def _compare_matrix(
    expected: list[list[str]], actual: list[list[str]], table_id: str
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    if len(expected) != len(actual):
        differences.append(
            {
                "path": f"tables[{table_id}].matrix.row_count",
                "expected": len(expected),
                "actual": len(actual),
            }
        )
    row_count = max(len(expected), len(actual))
    for row in range(row_count):
        expected_row = expected[row] if row < len(expected) else []
        actual_row = actual[row] if row < len(actual) else []
        if len(expected_row) != len(actual_row):
            differences.append(
                {
                    "path": f"tables[{table_id}].matrix[{row}].column_count",
                    "expected": len(expected_row),
                    "actual": len(actual_row),
                }
            )
        column_count = max(len(expected_row), len(actual_row))
        for column in range(column_count):
            expected_value = _value_at(expected, row, column)
            actual_value = _value_at(actual, row, column)
            if expected_value != actual_value:
                differences.append(
                    {
                        "path": f"tables[{table_id}].matrix[{row}][{column}]",
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )
    return differences


def compare_pdf_tables(result: Any, reference_file: Path) -> dict[str, Any]:
    """Compare every accepted table cell with the verified reference file."""

    reference = json.loads(reference_file.read_text(encoding="utf-8"))
    pages = _page_records(result)
    actual_tables = {
        table["table_id"]: table
        for page in pages
        for table in page.get("table_records", [])
    }
    expected_tables = {
        table["table_id"]: table for table in reference.get("tables", [])
    }
    table_checks: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    for table_id, expected in expected_tables.items():
        actual = actual_tables.get(table_id)
        table_differences: list[dict[str, Any]] = []
        if actual is None:
            table_differences.append({"path": "table", "expected": expected, "actual": None})
        else:
            if expected.get("page_number") != actual.get("page_number"):
                table_differences.append(
                    {
                        "path": "page_number",
                        "expected": expected.get("page_number"),
                        "actual": actual.get("page_number"),
                    }
                )
            if expected.get("bbox") != actual.get("bbox"):
                table_differences.append(
                    {
                        "path": "bbox",
                        "expected": expected.get("bbox"),
                        "actual": actual.get("bbox"),
                    }
                )
            table_differences.extend(
                _compare_matrix(expected["matrix"], actual["matrix"], table_id)
            )
            if expected.get("merged_cells", []) != actual.get("merged_cells", []):
                table_differences.append(
                    {
                        "path": "merged_cells",
                        "expected": expected.get("merged_cells", []),
                        "actual": actual.get("merged_cells", []),
                    }
                )
        check = {
            "table_id": table_id,
            "page_number": expected.get("page_number"),
            "matched": not table_differences,
            "expected_cell_count": sum(len(row) for row in expected["matrix"]),
            "actual_cell_count": sum(len(row) for row in actual["matrix"]) if actual else 0,
            "differences": table_differences,
        }
        table_checks.append(check)
        differences.extend(table_differences)

    unexpected_table_ids = sorted(set(actual_tables) - set(expected_tables))
    if unexpected_table_ids:
        differences.append(
            {
                "path": "tables.unexpected_ids",
                "expected": [],
                "actual": unexpected_table_ids,
            }
        )

    exclusion_checks: list[dict[str, Any]] = []
    for page_number_text, reason in reference.get("excluded_pages", {}).items():
        page_number = int(page_number_text)
        page = next((page for page in pages if page.get("page_number") == page_number), None)
        accepted_count = len(page.get("table_records", [])) if page else 0
        candidate_count = page.get("table_candidate_count", 0) if page else 0
        passed = accepted_count == 0
        exclusion_checks.append(
            {
                "page_number": page_number,
                "candidate_count": candidate_count,
                "accepted_table_count": accepted_count,
                "reason": reason,
                "passed": passed,
            }
        )
        if not passed:
            differences.append(
                {
                    "path": f"excluded_pages[{page_number}].accepted_table_count",
                    "expected": 0,
                    "actual": accepted_count,
                }
            )

    expected_source = str(reference.get("source", ""))
    actual_source = Path(str(getattr(result, "source_file", ""))).name
    source_matched = actual_source == expected_source
    if not source_matched:
        differences.append(
            {"path": "source", "expected": expected_source, "actual": actual_source}
        )

    return {
        "source": actual_source,
        "reference": str(reference_file.resolve()),
        "visually_verified_reference": bool(reference.get("visually_verified")),
        "expected_table_count": len(expected_tables),
        "actual_table_count": len(actual_tables),
        "source_matched": source_matched,
        "table_checks": table_checks,
        "exclusion_checks": exclusion_checks,
        "acceptance": {
            "real_table_regions_explicit": len(actual_tables) == len(expected_tables),
            "non_table_candidates_excluded": all(check["passed"] for check in exclusion_checks),
            "table_structure_restored": all(check["matched"] for check in table_checks),
            "itemwise_source_comparison": not differences,
        },
        "matched": not differences,
        "differences": differences,
    }
