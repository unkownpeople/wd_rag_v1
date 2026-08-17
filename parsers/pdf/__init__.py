"""Modular PDF parsing implementation.

The package is split by responsibility so the public parser facade does not
also contain table extraction, file output, and acceptance logic.
"""

from .io import write_jsonl, write_preview
from .models import PdfPageRecord, PdfParseResult
from .pipeline import parse_pdf
from .validation import compare_pdf_tables

__all__ = [
    "PdfPageRecord",
    "PdfParseResult",
    "compare_pdf_tables",
    "parse_pdf",
    "write_jsonl",
    "write_preview",
]
