from __future__ import annotations

"""从 SEC EDGAR 收集固定的 3 家公司、3 个财政年度 10-K 主文档。

该脚本只写入项目 F 盘目录，不使用全局缓存。SEC 的 10-K HTML 是官方申报
文件，后续由年报 HTML 解析器提取正文和表格。
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "annual_reports"
DEFAULT_YEARS = (2022, 2023, 2024)
SEC_USER_AGENT = "RagAnnualReports/1.0 local-project"

COMPANIES: tuple[dict[str, str], ...] = (
    {"company_id": "apple", "company_name": "Apple Inc.", "ticker": "AAPL", "cik": "0000320193"},
    {"company_id": "microsoft", "company_name": "Microsoft Corporation", "ticker": "MSFT", "cik": "0000789019"},
    {"company_id": "coca_cola", "company_name": "The Coca-Cola Company", "ticker": "KO", "cik": "0000021344"},
)


def _request(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept-Encoding": "identity",
            "Accept": "text/html,application/json,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _submissions(cik: str) -> dict[str, Any]:
    payload = _request(f"https://data.sec.gov/submissions/CIK{cik}.json")
    return json.loads(payload)


def _select_filings(submissions: dict[str, Any], years: set[int]) -> dict[int, dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent", {})
    selected: dict[int, dict[str, Any]] = {}
    for index, form in enumerate(recent.get("form", [])):
        if form != "10-K":
            continue
        report_date = str(recent.get("reportDate", [""])[index])
        if len(report_date) < 4 or not report_date[:4].isdigit():
            continue
        fiscal_year = int(report_date[:4])
        if fiscal_year not in years or fiscal_year in selected:
            continue
        selected[fiscal_year] = {
            "filing_date": recent["filingDate"][index],
            "report_date": report_date,
            "accession": recent["accessionNumber"][index],
            "primary_document": recent["primaryDocument"][index],
            "form": form,
        }
    return selected


def collect(
    output_dir: Path = DEFAULT_OUTPUT,
    years: tuple[int, ...] = DEFAULT_YEARS,
    *,
    force: bool = False,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    year_set = set(years)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for company in COMPANIES:
        try:
            filings = _select_filings(_submissions(company["cik"]), year_set)
        except Exception as exc:
            errors.append({"company_id": company["company_id"], "stage": "submissions", "error": str(exc)})
            continue

        for fiscal_year in sorted(years):
            filing = filings.get(fiscal_year)
            document_id = f"{company['company_id']}_{fiscal_year}_10k"
            if filing is None:
                errors.append({"document_id": document_id, "stage": "select_filing", "error": "未找到目标财政年度 10-K"})
                continue

            accession_path = filing["accession"].replace("-", "")
            source_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/"
                f"{accession_path}/{filing['primary_document']}"
            )
            target_dir = raw_dir / company["company_id"] / str(fiscal_year)
            target_path = target_dir / f"{document_id}.html"
            status = "downloaded"
            try:
                if target_path.is_file() and not force:
                    content = target_path.read_bytes()
                    status = "reused"
                else:
                    content = _request(source_url)
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target_path.write_bytes(content)
                lowered = content[:20000].lower()
                if len(content) < 1000 or b"<html" not in lowered:
                    raise ValueError("SEC 返回内容不是有效 HTML 年报主文档")
                if b"your request rate threshold" in lowered or b"request originates" in lowered:
                    raise ValueError("SEC 返回限流/拒绝页面，不写入为年报")
                record = {
                    **company,
                    "document_id": document_id,
                    "fiscal_year": fiscal_year,
                    "report_type": "annual_report",
                    "language": "en",
                    "currency": "USD",
                    "unit_scale": "as_reported",
                    "filing_date": filing["filing_date"],
                    "report_date": filing["report_date"],
                    "form": filing["form"],
                    "accession": filing["accession"],
                    "primary_document": filing["primary_document"],
                    "source_url": source_url,
                    "raw_path": str(target_path),
                    "download_status": status,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "size_bytes": len(content),
                    "sha256": _sha256(content),
                }
                records.append(record)
            except Exception as exc:
                errors.append({"document_id": document_id, "stage": "download", "url": source_url, "error": str(exc)})
            time.sleep(0.25)

    records.sort(key=lambda item: (item["company_id"], item["fiscal_year"]))
    manifest_path = output_dir / "manifest.jsonl"
    manifest_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )
    report = {
        "stage": "annual_report_collection",
        "output_dir": str(output_dir),
        "requested_companies": len(COMPANIES),
        "requested_years": list(years),
        "expected_count": len(COMPANIES) * len(years),
        "record_count": len(records),
        "downloaded_count": sum(record["download_status"] == "downloaded" for record in records),
        "reused_count": sum(record["download_status"] == "reused" for record in records),
        "error_count": len(errors),
        "errors": errors,
        "manifest": str(manifest_path),
        "passed": len(records) == len(COMPANIES) * len(years) and not errors,
    }
    (output_dir / "collection.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect official SEC 10-K HTML files into the F-drive project.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true", help="重新下载已存在的 HTML 文件")
    args = parser.parse_args()
    report = collect(args.output_dir, force=args.force)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
