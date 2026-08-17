from __future__ import annotations

"""Run the deterministic Excel-compatible table cleaning sample."""

import argparse
import json
from pathlib import Path

from .excel_cleaner import clean_excel_file


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "processed" / "parsed" / "finqa_table_01.unified.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "cleaned" / "finqa_table_01.cleaned.jsonl"
DEFAULT_REPORT = ROOT / "data" / "processed" / "cleaned" / "finqa_table_01.cleaned.report.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean one parsed CSV/TSV/XLSX JSONL without LLM.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = clean_excel_file(args.input, args.output, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["validation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
