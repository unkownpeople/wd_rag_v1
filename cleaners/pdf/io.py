"""PDF cleaning JSONL input/output boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .records import clean_pdf_records


def read_jsonl(input_file: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(input_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL line {line_number} is not an object.")
        if value.get("record_type") == "document_meta":
            continue
        records.append(value)
    return records


def clean_pdf_file(input_file: Path, output_file: Path, report_file: Path) -> dict[str, Any]:
    input_file = input_file.resolve()
    output_file = output_file.resolve()
    report_file = report_file.resolve()
    if not input_file.is_file():
        raise FileNotFoundError(f"Parsed JSONL does not exist: {input_file}")
    cleaned_records, report = clean_pdf_records(read_jsonl(input_file))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in cleaned_records),
        encoding="utf-8",
        newline="\n",
    )
    report_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report
