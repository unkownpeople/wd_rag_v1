"""Backward-compatible facade for the modular DOCX parser."""

try:
    from .word import parse_word
except ImportError:  # supports running parser scripts directly by file path
    from word import parse_word

__all__ = ["parse_word"]
