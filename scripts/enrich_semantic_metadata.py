from __future__ import annotations

"""把确定性报表语义元数据写回切块文件，并生成阶段报告。"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from embeddings.semantic_metadata import enrich_chunk


REQUIRED_FIELDS = (
    "statement_family",
    "statement_scope",
    "period_end",
    "unit",
    "table_title_raw",
    "table_group_id",
)


def enrich_directory(chunk_dir: Path, output: Path | None = None) -> dict[str, Any]:
    chunk_dir = chunk_dir.resolve()
    files: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    total = 0
    for path in sorted(chunk_dir.glob("*.chunks.jsonl")):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for line_number, row in enumerate(rows, 1):
            enrich_chunk(row)
            absent = [field for field in REQUIRED_FIELDS if field not in row]
            if absent:
                missing.append({"file": str(path), "line": line_number, "fields": absent})
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
            newline="\n",
        )
        total += len(rows)
        files.append({"file": str(path), "chunk_count": len(rows)})

    report = {
        "stage": "annual_report_semantic_metadata_enrichment",
        "chunk_dir": str(chunk_dir),
        "file_count": len(files),
        "chunk_count": total,
        "required_fields": list(REQUIRED_FIELDS),
        "files": files,
        "missing": missing,
        "passed": bool(files) and not missing,
    }
    output_path = (output or (chunk_dir.parent / f"{chunk_dir.name}.semantic_metadata.report.json")).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich annual-report chunks with semantic metadata.")
    parser.add_argument("--chunk-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    return 0 if enrich_directory(args.chunk_dir, args.output)["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
