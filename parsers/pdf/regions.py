"""Compatibility access to the visually verified PDF region registry."""

try:
    from ..pdf_table_regions import PdfTableSpec, non_table_note, table_specs_by_page
except ImportError:  # supports running the parser facade directly
    from pdf_table_regions import PdfTableSpec, non_table_note, table_specs_by_page

__all__ = ["PdfTableSpec", "non_table_note", "table_specs_by_page"]
