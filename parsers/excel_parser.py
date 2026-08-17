"""Backward-compatible facade for the modular Excel parser."""

try:
    from .excel import compare_excel_document, parse_excel
except ImportError:  # supports running parser scripts directly by file path
    from excel import compare_excel_document, parse_excel

__all__ = ["compare_excel_document", "parse_excel"]
