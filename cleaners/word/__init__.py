"""Modular DOCX cleaning package."""

from .io import clean_word_file
from .records import clean_word_records

__all__ = ["clean_word_file", "clean_word_records"]
