"""PDF document orchestration: open the file and parse pages."""

from __future__ import annotations

from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from .metadata import extract_metadata
from .models import PdfPageRecord, PdfParseResult
from .page_parser import parse_page
from .regions import table_specs_by_page


def parse_pdf(input_pdf: Path) -> PdfParseResult:
    """Extract text and registered tables while preserving page boundaries."""

    input_pdf = input_pdf.resolve()
    if not input_pdf.is_file():
        raise FileNotFoundError(f"PDF file does not exist: {input_pdf}")

    reader = PdfReader(str(input_pdf))
    pages = []
    specs_by_page = table_specs_by_page(input_pdf)

    with pdfplumber.open(str(input_pdf)) as plumber_pdf:
        for index, pypdf_page in enumerate(reader.pages):
            try:
                plumber_page = plumber_pdf.pages[index]
                page_record = parse_page(
                    source_file=str(input_pdf),
                    page_number=index + 1,
                    pypdf_page=pypdf_page,
                    pdfplumber_page=plumber_page,
                    page_table_specs=specs_by_page.get(index + 1, ()),
                    source_pdf=input_pdf,
                )
            except Exception as exc:  # preserve a page-level failure in output
                page_record = PdfPageRecord(
                    source_file=str(input_pdf),
                    page_number=index + 1,
                    parse_branches=["error"],
                    status="error",
                    error=str(exc),
                )
            pages.append(page_record)

    return PdfParseResult(
        source_file=str(input_pdf),
        page_count=len(reader.pages),
        metadata=extract_metadata(reader),
        pages=pages,
    )
