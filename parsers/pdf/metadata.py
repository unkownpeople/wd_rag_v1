"""PDF metadata extraction kept separate from page parsing."""

from __future__ import annotations

from pypdf import PdfReader


def extract_metadata(reader: PdfReader) -> dict[str, str]:
    """Return JSON-safe document metadata without changing page content."""

    raw_metadata = reader.metadata or {}
    return {
        str(key).lstrip("/"): str(value)
        for key, value in raw_metadata.items()
        if value is not None
    }
