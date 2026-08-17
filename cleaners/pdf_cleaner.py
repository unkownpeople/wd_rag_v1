"""Backward-compatible facade for the modular PDF cleaner."""

try:
    from .pdf import clean_pdf_file, clean_pdf_records, validate_cleaned_output
    from .pdf.constants import CLEANING_VERSION
except ImportError:  # supports running cleaner scripts directly by file path
    from pdf import clean_pdf_file, clean_pdf_records, validate_cleaned_output
    from pdf.constants import CLEANING_VERSION

__all__ = ["CLEANING_VERSION", "clean_pdf_file", "clean_pdf_records", "validate_cleaned_output"]
