"""Data models for raw PDF parsing output."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PdfPageRecord:
    """Raw extraction result for one PDF page.

    This model belongs to parsing only. Cleaning creates separate records and
    never mutates the values stored here.
    """

    source_file: str
    page_number: int
    parse_branches: list[str] = field(default_factory=list)
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    table_records: list[dict[str, Any]] = field(default_factory=list)
    table_regions: list[dict[str, Any]] = field(default_factory=list)
    table_candidate_count: int = 0
    excluded_candidates: list[dict[str, Any]] = field(default_factory=list)
    text_char_count: int = 0
    table_count: int = 0
    status: str = "ok"
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class PdfParseResult:
    """Complete raw extraction result for one PDF."""

    source_file: str
    page_count: int
    metadata: dict[str, str] = field(default_factory=dict)
    pages: list[PdfPageRecord] = field(default_factory=list)
