"""Backward-compatible facade for the modular DOCX cleaner."""

try:
    from .word import clean_word_file, clean_word_records
except ImportError:  # supports running cleaner scripts directly by file path
    from word import clean_word_file, clean_word_records

__all__ = ["clean_word_file", "clean_word_records"]
