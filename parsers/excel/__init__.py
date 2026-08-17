"""Modular Excel-compatible parser package."""

from .pipeline import parse_excel
from .validation import compare_excel_document

__all__ = ["compare_excel_document", "parse_excel"]
