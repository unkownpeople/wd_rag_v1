"""Backward-compatible public facade for the modular PDF parser.

New implementation code lives under :mod:`parsers.pdf`. Existing scripts may
continue importing ``parsers.pdf_parser`` without changing their entry point.
"""

try:
    from .pdf import (
        PdfPageRecord,
        PdfParseResult,
        compare_pdf_tables,
        parse_pdf,
        write_jsonl,
        write_preview,
    )
except ImportError:  # supports running parser scripts directly by file path
    from pdf import (
        PdfPageRecord,
        PdfParseResult,
        compare_pdf_tables,
        parse_pdf,
        write_jsonl,
        write_preview,
    )

__all__ = [
    "PdfPageRecord",
    "PdfParseResult",
    "compare_pdf_tables",
    "parse_pdf",
    "write_jsonl",
    "write_preview",
]
