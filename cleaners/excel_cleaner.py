"""Backward-compatible facade for the modular Excel cleaner."""

try:
    from .excel import clean_excel_file, clean_excel_records
except ImportError:  # supports running cleaner scripts directly by file path
    from excel import clean_excel_file, clean_excel_records

__all__ = ["clean_excel_file", "clean_excel_records"]
