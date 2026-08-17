"""Parse one PDF page into the raw page model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import PdfPageRecord
from .regions import PdfTableSpec, non_table_note
from .annual_tables import extract_annual_tables
from .table_extractor import extract_explicit_table


def parse_page(
    *,
    source_file: str,
    page_number: int,
    pypdf_page: Any,
    pdfplumber_page: Any,
    page_table_specs: tuple[PdfTableSpec, ...],
    source_pdf: Path,
) -> PdfPageRecord:
    """Extract text and registered tables from one page only."""

    record = PdfPageRecord(source_file=source_file, page_number=page_number)

    try:
        record.text = pypdf_page.extract_text() or ""
        record.text_char_count = len(record.text)
        if record.text.strip():
            record.parse_branches.append("text:pypdf")
    except Exception as exc:  # keep one broken page from hiding other pages
        record.warnings.append(f"pypdf text extraction failed: {exc}")

    try:
        # Whole-page detection is diagnostic only. The explicit region registry
        # remains authoritative for accepted table content.
        is_annual_report = "annual_reports" in {part.lower() for part in source_pdf.parts}
        generic_candidates = [] if is_annual_report else (pdfplumber_page.extract_tables() or [])
        record.table_candidate_count = len(generic_candidates)
    except Exception as exc:  # table detection is optional per page
        record.warnings.append(f"pdfplumber candidate detection failed: {exc}")

    try:
        for spec in page_table_specs:
            table_record = extract_explicit_table(
                pdfplumber_page,
                spec,
                page_text=record.text,
            )
            record.table_records.append(table_record)
            record.table_regions.append(
                {
                    "table_id": spec.table_id,
                    "bbox": list(spec.bbox),
                    "page_number": spec.page_number,
                    "status": "accepted",
                }
            )
        if not page_table_specs and "annual_reports" in {part.lower() for part in source_pdf.parts}:
            record.table_records.extend(
                extract_annual_tables(
                    pdfplumber_page,
                    page_number,
                    source_pdf.stem,
                    page_text=record.text,
                )
            )
            record.table_candidate_count = len(record.table_records)
        record.tables = [table["matrix"] for table in record.table_records]
        record.table_count = len(record.table_records)
        if record.table_records:
            record.parse_branches.append("table:pdfplumber:explicit_region")
    except Exception as exc:  # table extraction is optional per page
        record.warnings.append(f"explicit PDF table extraction failed: {exc}")

    if record.table_candidate_count and record.table_count == 0:
        reason = non_table_note(source_pdf, page_number)
        record.excluded_candidates.append(
            {
                "candidate_count": record.table_candidate_count,
                "accepted_count": 0,
                "reason": reason
                or "No visually verified table region is registered for this page.",
            }
        )
        record.parse_branches.append("table:candidates_excluded")
        record.warnings.append(
            "Whole-page pdfplumber candidates were excluded because no explicit table region is registered."
        )
    elif record.table_candidate_count != record.table_count:
        record.excluded_candidates.append(
            {
                "candidate_count": record.table_candidate_count,
                "accepted_count": record.table_count,
                "reason": "Whole-page candidates are diagnostic only; the explicit region registry is authoritative.",
            }
        )
        record.warnings.append(
            "Whole-page candidate count differs from accepted explicit regions; rejected candidates are not emitted as tables."
        )

    if not record.parse_branches:
        record.parse_branches.append("ocr:pending")
        record.status = "needs_ocr"
        record.warnings.append(
            "No text layer or table was extracted; OCR is intentionally not run in this stage."
        )

    return record
