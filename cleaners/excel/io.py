"""Excel cleaning JSONL input/output boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .records import clean_excel_records


def read_jsonl(input_file: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with input_file.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_number}: {input_file}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL line {line_number} is not an object: {input_file}")
            records.append(value)
    return records


def clean_excel_file(input_file: Path, output_file: Path, report_file: Path) -> dict[str, Any]:
    records = read_jsonl(input_file.resolve())
    meta = next((record for record in records if record.get("record_type") == "document_meta"), {})
    cleaned, report = clean_excel_records(records, metadata=meta.get("metadata", {}))
    output_file = output_file.resolve()
    report_file = report_file.resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="\n") as file:
        for record in cleaned:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
