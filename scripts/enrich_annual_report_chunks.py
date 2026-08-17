from __future__ import annotations

"""把 PDF 年报来源元数据补回切块，供 Qdrant 过滤和引用使用。"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chunking.identity import make_record_id


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _cleaned_path(chunk_dir: Path, chunk_path: Path) -> Path:
    cleaned_dir = chunk_dir.parent / chunk_dir.name.replace("_chunks", "_cleaned")
    return cleaned_dir / chunk_path.name.replace(".chunks.jsonl", ".cleaned.jsonl")


def enrich(chunk_dir: Path) -> dict[str, Any]:
    chunk_dir = chunk_dir.resolve()
    required = (
        "company_id",
        "company_name",
        "ticker",
        "cik",
        "document_id",
        "fiscal_year",
        "report_type",
        "language",
        "currency",
        "unit_scale",
        "source_url",
        "source_provider",
        "sha256",
    )
    files: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    total = 0
    for chunk_path in sorted(chunk_dir.glob("*.chunks.jsonl")):
        cleaned_path = _cleaned_path(chunk_dir, chunk_path)
        if not cleaned_path.is_file():
            missing.append({"file": str(chunk_path), "reason": "cleaned_file_missing"})
            continue
        cleaned = _read_jsonl(cleaned_path)
        source_by_id = {make_record_id(record, index): record for index, record in enumerate(cleaned)}
        chunks = _read_jsonl(chunk_path)
        file_missing: list[dict[str, Any]] = []
        for chunk in chunks:
            source = next(
                (source_by_id.get(source_id) for source_id in chunk.get("source_record_ids", [])),
                None,
            )
            if source is None:
                file_missing.append({"chunk_id": chunk.get("chunk_id"), "reason": "source_record_missing"})
                continue
            for key in required:
                chunk[key] = source.get(key)
            if not chunk.get("source_location"):
                chunk["source_location"] = source.get("source_location")
            total += 1
        chunk_path.write_text(
            "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks),
            encoding="utf-8",
            newline="\n",
        )
        missing.extend(file_missing)
        files.append({"file": str(chunk_path), "chunk_count": len(chunks), "missing_count": len(file_missing)})

    report = {
        "stage": "annual_report_chunk_metadata_enrichment",
        "chunk_dir": str(chunk_dir),
        "file_count": len(files),
        "chunk_count": total,
        "files": files,
        "missing": missing,
        "passed": bool(files) and not missing,
    }
    output = chunk_dir / "metadata.enrichment.report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich annual-report chunks with source metadata.")
    parser.add_argument(
        "--chunk-dir",
        type=Path,
        default=ROOT / "data" / "processed" / "annual_reports" / "pdf_chunks",
    )
    args = parser.parse_args()
    result = enrich(args.chunk_dir)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
