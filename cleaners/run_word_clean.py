from __future__ import annotations

"""Run the deterministic DOCX cleaning sample."""

import argparse
import json
from pathlib import Path

from .word_cleaner import clean_word_file


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "processed" / "parsed" / "rag_parse_branch_test.unified.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "cleaned" / "rag_parse_branch_test.cleaned.jsonl"
DEFAULT_REPORT = ROOT / "data" / "processed" / "cleaned" / "rag_parse_branch_test.cleaned.report.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean one parsed DOCX JSONL without LLM/OCR.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = clean_word_file(args.input, args.output, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["validation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
