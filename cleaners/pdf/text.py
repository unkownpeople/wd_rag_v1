"""PDF page-text cleaning rules."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from .constants import (
    _IMAGE_MARKERS,
    _KEEP_HYPHENATED_COMPOUNDS,
    _LIGATURES,
    _KNOWN_PDF_JOIN_REPAIRS,
    _PAGE_NUMBER_RE,
)


def as_text(value: Any) -> str:
    return "" if value is None else str(value)


def normalise_line(line: str) -> str:
    line = line.replace("\u00a0", " ").replace("\u2007", " ").replace("\u202f", " ")
    line = line.translate(_LIGATURES)
    return re.sub(r"[ \t]+", " ", line).strip()


def normalise_lines(raw_text: str) -> list[str]:
    return [
        normalise_line(line)
        for line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]


def boundary_fragments(pages: Iterable[dict[str, Any]]) -> set[str]:
    pages = list(pages)
    first_page_number = min((int(page.get("page_number", 0)) for page in pages), default=0)
    locations: dict[str, set[tuple[int, str]]] = defaultdict(set)
    for page in pages:
        page_number = int(page.get("page_number", 0))
        if page_number == first_page_number:
            continue
        non_empty = [line for line in normalise_lines(as_text(page.get("text", ""))) if line]
        for line in non_empty[:3]:
            if len(line) >= 3 and not _PAGE_NUMBER_RE.fullmatch(line):
                locations[line].add((page_number, "top"))
        for line in non_empty[-3:]:
            if len(line) >= 3 and not _PAGE_NUMBER_RE.fullmatch(line):
                locations[line].add((page_number, "bottom"))

    return {
        fragment
        for fragment, entries in locations.items()
        if len({page_number for page_number, _ in entries}) >= 2
    }


def join_lines(lines: list[str], actions: list[str]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue

        if current:
            previous = current[-1]
            if previous.endswith("-") and line[:1].islower() and not previous.endswith("--"):
                prefix_match = re.search(r"([A-Za-z]+)-$", previous)
                suffix_match = re.match(r"([a-z]+)", line)
                compound = (
                    f"{prefix_match.group(1)}-{suffix_match.group(1)}".lower()
                    if prefix_match and suffix_match
                    else ""
                )
                if compound in _KEEP_HYPHENATED_COMPOUNDS:
                    current[-1] = previous + line
                    actions.append("preserve_hyphenated_compound")
                else:
                    current[-1] = previous[:-1] + line
                    actions.append("join_hyphenated_line")
            else:
                current.append(line)
        else:
            current.append(line)

    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def remove_pending_image_text(
    page: dict[str, Any],
    text: str,
    actions: list[str],
    removed_fragments: list[dict[str, str]],
) -> str:
    """Drop diagram labels when the parser only exposes a figure note."""

    reasons = [
        as_text(candidate.get("reason"))
        for candidate in page.get("excluded_candidates", []) or []
        if isinstance(candidate, dict)
    ]
    if not any(any(marker in reason.lower() for marker in _IMAGE_MARKERS) for reason in reasons):
        return text
    match = re.search(r"\bFigure\s+\d+\s*:", text, flags=re.IGNORECASE)
    if match is None or not text[: match.start()].strip():
        return text
    prefix = text[: match.start()].strip()
    actions.append("remove_image_region_text_without_bbox")
    removed_fragments.append({"reason": "image_region_text_without_bbox", "text": prefix})
    return text[match.start() :].strip()


def repair_pdf_word_joins(
    text: str,
    actions: list[str],
    repaired_fragments: list[dict[str, str]],
) -> str:
    for pattern, replacement in _KNOWN_PDF_JOIN_REPAIRS:
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        for match in matches:
            repaired_fragments.append(
                {"reason": "pdf_word_join", "text": match.group(0), "replacement": replacement}
            )
        text = pattern.sub(replacement, text)
        actions.append("repair_pdf_word_join")
    return text


def clean_page_text(
    page: dict[str, Any],
    repeated_fragments: set[str],
) -> tuple[str, list[str], list[dict[str, str]], list[str]]:
    """Clean one page and return text, actions, removals, and removed table IDs."""

    from .tables import remove_table_duplicates

    page_number = int(page.get("page_number", 0))
    raw_text = as_text(page.get("text", ""))
    lines = normalise_lines(raw_text)
    non_empty_count = sum(bool(line) for line in lines)
    non_empty_index = 0
    actions: list[str] = []
    removed_fragments: list[dict[str, str]] = []
    retained: list[str] = []

    for line in lines:
        if not line:
            retained.append("")
            continue
        non_empty_index += 1
        at_boundary = non_empty_index <= 3 or non_empty_index > max(3, non_empty_count - 3)
        if at_boundary and line in repeated_fragments:
            actions.append("remove_repeated_header_footer")
            removed_fragments.append({"reason": "repeated_header_footer", "text": line})
            continue
        if at_boundary and line == str(page_number) and _PAGE_NUMBER_RE.fullmatch(line):
            actions.append("remove_page_number_line")
            removed_fragments.append({"reason": "page_number", "text": line})
            continue
        retained.append(line)

    if any(normalise_line(line) != line.strip() for line in raw_text.splitlines()):
        actions.append("normalize_whitespace")
    if raw_text.replace("\r\n", "\n").replace("\r", "\n") != raw_text:
        actions.append("normalize_line_endings")
    if raw_text.translate(_LIGATURES) != raw_text:
        actions.append("normalize_pdf_ligatures")

    text = join_lines(retained, actions)
    tables = [table for table in page.get("table_records", []) if isinstance(table, dict)]
    text, removed_table_ids = remove_table_duplicates(text, tables, actions, removed_fragments)
    text = remove_pending_image_text(page, text, actions, removed_fragments)
    text = repair_pdf_word_joins(text, actions, removed_fragments)
    return text, actions, removed_fragments, removed_table_ids
