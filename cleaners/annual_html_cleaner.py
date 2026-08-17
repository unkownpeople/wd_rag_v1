from __future__ import annotations

"""SEC 10-K HTML 的清洗与原始表格保真检查。"""

import json
from pathlib import Path
from typing import Any

from .common import build_table_projection, clean_text, normalise_line


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def clean_annual_html_file(input_file: Path, output_file: Path, report_file: Path) -> dict[str, Any]:
    rows = _read_jsonl(input_file)
    meta = next((row for row in rows if row.get("record_type") == "document_meta"), {})
    source_rows = [row for row in rows if row.get("record_type") != "document_meta"]
    cleaned: list[dict[str, Any]] = []
    text_count = 0
    table_count = 0
    raw_matrix_unchanged = True
    table_ids: set[str] = set()
    warnings: list[str] = []

    for row in source_rows:
        record = dict(row)
        if row.get("record_type") == "text":
            raw_text = str(row.get("raw_text") or "")
            actions: list[str] = []
            cleaned_text = clean_text(raw_text, actions)
            if cleaned_text:
                record["cleaned_text"] = cleaned_text
                record["embedding_status"] = "included"
                record["cleaning"] = {"version": "clean-v1-html", "actions": sorted(set(actions)), "warnings": []}
                cleaned.append(record)
                text_count += 1
            continue

        if row.get("record_type") == "table":
            table_id = str(row.get("table_id") or "")
            matrix = [[normalise_line(cell) for cell in row_values] for row_values in row.get("raw_matrix", [])]
            if not table_id or table_id in table_ids or not matrix:
                raw_matrix_unchanged = False
                warnings.append(f"表格 ID 缺失、重复或矩阵为空：{table_id}")
                continue
            table_ids.add(table_id)
            projection, labels, projection_warnings = build_table_projection(
                matrix,
                table_id=table_id,
                location=str(row.get("source_location") or ""),
                header_row_count=1,
            )
            record["raw_matrix"] = matrix
            record["header_rows"] = [matrix[0]]
            record["table_headers"] = labels
            record["table_text"] = projection
            record["embedding_status"] = "included"
            record["cleaning"] = {
                "version": "clean-v1-html",
                "raw_matrix_changed": False,
                "projection_warnings": projection_warnings,
                "warnings": [],
            }
            cleaned.append(record)
            table_count += 1

    required_metadata = ("company_id", "fiscal_year", "document_id", "source_url", "sha256")
    metadata_complete = all(all(str(row.get(key) or "") for key in required_metadata) for row in cleaned)
    report = {
        "stage": "clean",
        "cleaning_version": "clean-v1-html",
        "source_file": meta.get("source_file") or (source_rows[0].get("source_file") if source_rows else ""),
        "source_format": "html",
        "input_record_count": len(source_rows),
        "output_record_count": len(cleaned),
        "record_counts": {"text": text_count, "table": table_count},
        "metadata_complete": metadata_complete,
        "raw_matrix_unchanged": raw_matrix_unchanged,
        "warnings": warnings,
    }
    report["validation"] = {
        "passed": bool(cleaned) and text_count > 0 and table_count > 0 and metadata_complete and raw_matrix_unchanged,
        "non_empty_output": bool(cleaned),
        "text_records_present": text_count > 0,
        "table_records_present": table_count > 0,
        "metadata_complete": metadata_complete,
        "raw_matrix_unchanged": raw_matrix_unchanged,
        "unique_table_ids": len(table_ids) == table_count,
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cleaned), encoding="utf-8", newline="\n")
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return report
