from __future__ import annotations

"""解析活动 manifest 中的年报 PDF，并输出独立的 parsed/cleaned JSONL。

此入口只负责 PDF 解析和确定性清洗，不执行切块、Embedding、Qdrant 或 LLM。
"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cleaners.pdf_cleaner import clean_pdf_file
from parsers.pdf_parser import parse_pdf, write_jsonl, write_preview


ANNUAL_ROOT = ROOT / "data" / "annual_reports"
PROCESSED_ROOT = ROOT / "data" / "processed" / "annual_reports"
PARSED_DIR = PROCESSED_ROOT / "pdf_parsed"
CLEANED_DIR = PROCESSED_ROOT / "pdf_cleaned"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    keys = (
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
    return {key: record.get(key) for key in keys}


def _enrich_cleaned(path: Path, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    records = _read_jsonl(path)
    for record in records:
        record.update(metadata)
        record["source_format"] = "pdf"
        page_number = record.get("page_number")
        if record.get("record_type") == "table":
            record["source_location"] = (
                f"page={page_number};table={record.get('table_id', 'unknown-table')}"
            )
        else:
            record["source_location"] = f"page={page_number}"
    _write_jsonl(path, records)
    return records


def parse_active_manifest(
    manifest_path: Path = ANNUAL_ROOT / "manifest.jsonl",
    document_ids: list[str] | None = None,
) -> dict[str, Any]:
    manifest = _read_jsonl(manifest_path)
    if not manifest:
        raise ValueError(f"活动 manifest 为空：{manifest_path}")
    if any(record.get("source_format") != "pdf" for record in manifest):
        raise ValueError("活动 manifest 含非 PDF 输入，拒绝走年报 PDF 解析入口")
    if document_ids:
        wanted = set(document_ids)
        manifest = [record for record in manifest if str(record.get("document_id")) in wanted]
        if {str(record.get("document_id")) for record in manifest} != wanted:
            raise ValueError(f"活动 manifest 缺少指定文档：{sorted(wanted - {str(record.get('document_id')) for record in manifest})}")

    reports: list[dict[str, Any]] = []
    for record in manifest:
        document_id = str(record["document_id"])
        source_path = Path(str(record["raw_path"]))
        parsed_path = PARSED_DIR / f"{document_id}.jsonl"
        preview_path = PARSED_DIR / f"{document_id}.preview.txt"
        parse_report_path = PARSED_DIR / f"{document_id}.report.json"
        cleaned_path = CLEANED_DIR / f"{document_id}.cleaned.jsonl"
        clean_report_path = CLEANED_DIR / f"{document_id}.cleaned.report.json"

        result = parse_pdf(source_path)
        write_jsonl(result, parsed_path)
        write_preview(result, preview_path)
        pages = result.pages
        parse_report = {
            "stage": "parse",
            "document_id": document_id,
            "source_file": str(source_path),
            "source_format": "pdf",
            "page_count": result.page_count,
            "text_pages": sum(bool(page.text.strip()) for page in pages),
            "table_pages": sum(page.table_count > 0 for page in pages),
            "table_count": sum(page.table_count for page in pages),
            "image_or_ocr_pending_pages": sum(
                "ocr:pending" in page.parse_branches for page in pages
            ),
            "error_pages": sum(page.status == "error" for page in pages),
            "parsed_jsonl": str(parsed_path),
            "preview": str(preview_path),
        }
        parse_report["passed"] = (
            parse_report["page_count"] > 0
            and parse_report["text_pages"] > 0
            and parse_report["error_pages"] == 0
        )
        parse_report_path.write_text(
            json.dumps(parse_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        clean_report = clean_pdf_file(parsed_path, cleaned_path, clean_report_path)
        cleaned_records = _enrich_cleaned(cleaned_path, _metadata(record))
        reports.append(
            {
                "document_id": document_id,
                "source_file": str(source_path),
                "parsed": parse_report,
                "cleaned": {
                    "report": str(clean_report_path),
                    "record_count": len(cleaned_records),
                    "record_counts": clean_report.get("record_counts", {}),
                    "validation": clean_report.get("validation", {}),
                },
            }
        )

    aggregate = {
        "stage": "annual_report_pdf_parse_and_clean",
        "source_format": "pdf",
        "manifest": str(manifest_path),
        "document_count": len(manifest),
        "parsed_dir": str(PARSED_DIR),
        "cleaned_dir": str(CLEANED_DIR),
        "documents": reports,
        "boundaries": [
            "当前只完成 PDF 解析和确定性清洗。",
            "图像/OCR 记录保留为 image_pending，不调用 LLM 识图。",
            "尚未执行切块、Embedding、Qdrant、召回或生成。",
        ],
    }
    aggregate["passed"] = all(
        item["parsed"]["passed"] and item["cleaned"]["validation"].get("passed", False)
        for item in reports
    )
    output = PROCESSED_ROOT / "pdf_parse_and_clean.report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse and clean active annual-report PDFs.")
    parser.add_argument("--manifest", type=Path, default=ANNUAL_ROOT / "manifest.jsonl")
    parser.add_argument("--document-id", action="append", default=None)
    args = parser.parse_args()
    result = parse_active_manifest(args.manifest.resolve(), args.document_id)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
