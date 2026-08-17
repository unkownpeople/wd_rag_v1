from __future__ import annotations

"""下载并切换到 3 份 Apple 年报 PDF 活动数据源。

SEC 备案主文档仍作为 HTML 备份保留；活动 manifest 使用公开年报 PDF，
以便后续走 PDF 解析器并保留页码、表格和图像待处理信息。
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "annual_reports"
PDF_URL_TEMPLATE = (
    "https://www.annualreports.com/HostedData/AnnualReportArchive/a/"
    "NASDAQ_AAPL_{year}.pdf"
)
TARGET_COMPANY = "apple"
TARGET_YEARS = (2022, 2023, 2024)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RagAnnualReports/1.0 local-project",
            "Accept": "application/pdf,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def collect(output_dir: Path = DEFAULT_OUTPUT, *, force: bool = False) -> dict[str, object]:
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "manifest.jsonl"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"原 HTML manifest 不存在：{manifest_path}")

    old_records = _read_jsonl(manifest_path)
    selected = {
        int(record["fiscal_year"]): record
        for record in old_records
        if record.get("company_id") == TARGET_COMPANY
        and int(record.get("fiscal_year", 0)) in TARGET_YEARS
    }
    missing = [year for year in TARGET_YEARS if year not in selected]
    if missing:
        raise ValueError(f"HTML manifest 缺少 Apple 年度记录：{missing}")

    html_manifest = output_dir / "manifest.html.jsonl"
    html_report = output_dir / "collection.html.report.json"
    if not html_manifest.exists():
        shutil.copy2(manifest_path, html_manifest)
    collection_report = output_dir / "collection.report.json"
    if collection_report.is_file() and not html_report.exists():
        shutil.copy2(collection_report, html_report)

    records: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for year in TARGET_YEARS:
        original = selected[year]
        url = PDF_URL_TEMPLATE.format(year=year)
        target_dir = output_dir / "raw" / TARGET_COMPANY / str(year)
        target_path = target_dir / f"apple_{year}_10k.pdf"
        try:
            if target_path.is_file() and not force:
                content = target_path.read_bytes()
                status = "reused"
            else:
                content = _download(url)
                target_dir.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(content)
                status = "downloaded"
            if len(content) < 1000 or not content.startswith(b"%PDF-"):
                raise ValueError("下载内容不是有效 PDF")
            records.append(
                {
                    **original,
                    "source_format": "pdf",
                    "source_provider": "annualreports.com",
                    "source_url": url,
                    "sec_html_source_url": original.get("source_url"),
                    "html_backup_path": original.get("raw_path"),
                    "raw_path": str(target_path),
                    "download_status": status,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "size_bytes": len(content),
                    "sha256": _sha256(content),
                }
            )
        except Exception as exc:
            errors.append({"document_id": str(original["document_id"]), "url": url, "error": str(exc)})

    records.sort(key=lambda item: int(item["fiscal_year"]))
    manifest_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )
    report: dict[str, object] = {
        "stage": "annual_report_pdf_collection",
        "output_dir": str(output_dir),
        "source_format": "pdf",
        "source_provider": "annualreports.com",
        "requested_company": TARGET_COMPANY,
        "requested_years": list(TARGET_YEARS),
        "expected_count": len(TARGET_YEARS),
        "record_count": len(records),
        "downloaded_count": sum(record["download_status"] == "downloaded" for record in records),
        "reused_count": sum(record["download_status"] == "reused" for record in records),
        "error_count": len(errors),
        "errors": errors,
        "manifest": str(manifest_path),
        "html_backup_manifest": str(html_manifest),
        "passed": len(records) == len(TARGET_YEARS) and not errors,
    }
    collection_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Download active Apple annual-report PDFs to the F-drive project.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    report = collect(args.output_dir, force=args.force)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
