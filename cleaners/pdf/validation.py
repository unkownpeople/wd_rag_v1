"""PDF cleaning traceability and protection checks."""

from __future__ import annotations

from typing import Any

from .constants import (
    _KNOWN_PDF_JOIN_REPAIRS,
    _LIGATURES,
    _PAGE_NUMBER_RE,
    _PROTECTED_TOKEN_RE,
)
from .tables import remove_table_duplicates
from .text import (
    as_text,
    boundary_fragments,
    join_lines,
    normalise_line,
    normalise_lines,
    remove_pending_image_text,
)


def protected_tokens(text: str) -> list[str]:
    return [token.lower() for token in _PROTECTED_TOKEN_RE.findall(text.translate(_LIGATURES))]


def validate_cleaned_output(
    source_pages: list[dict[str, Any]], cleaned_records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Check page coverage, table preservation, and protected-token stability."""

    source_pages_by_number = {int(page.get("page_number", 0)): page for page in source_pages}
    repeated_fragments = boundary_fragments(source_pages)
    cleaned_pages = {int(record.get("page_number", 0)) for record in cleaned_records}
    expected_pages = set(source_pages_by_number)
    page_coverage = cleaned_pages == expected_pages

    source_tables = {
        as_text(table.get("table_id")) or "unknown-table": table
        for page in source_pages
        for table in page.get("table_records", [])
        if isinstance(table, dict)
    }
    cleaned_tables = {
        as_text(record.get("table_id")): record
        for record in cleaned_records
        if record.get("record_type") == "table"
    }
    raw_matrix_unchanged = True
    table_projection_complete = True
    for table_id, source_table in source_tables.items():
        cleaned = cleaned_tables.get(table_id)
        if cleaned is None:
            raw_matrix_unchanged = False
            table_projection_complete = False
            continue
        source_matrix = [[as_text(cell) for cell in row] for row in source_table.get("matrix", [])]
        if cleaned.get("raw_matrix") != source_matrix or cleaned.get("cleaning", {}).get("raw_matrix_changed"):
            raw_matrix_unchanged = False
        projection = as_text(cleaned.get("table_text")).translate(_LIGATURES).lower()
        for row in source_matrix:
            for cell in row:
                value = normalise_line(cell).translate(_LIGATURES).lower()
                if value and value not in projection:
                    table_projection_complete = False

    text_token_checks: list[bool] = []
    unresolved_table_pages: list[int] = []
    known_pdf_word_joins_absent = True
    for record in cleaned_records:
        if record.get("record_type") != "text":
            continue
        page_number = int(record.get("page_number", 0))
        source_page = source_pages_by_number[page_number]
        cleaned_text = as_text(record.get("cleaned_text"))
        source_lines = normalise_lines(as_text(record.get("raw_text")))
        non_empty_count = sum(bool(line) for line in source_lines)
        non_empty_index = 0
        retained_lines: list[str] = []
        for line in source_lines:
            if not line:
                retained_lines.append(line)
                continue
            non_empty_index += 1
            at_boundary = non_empty_index <= 3 or non_empty_index > max(3, non_empty_count - 3)
            if at_boundary and line in repeated_fragments:
                continue
            if at_boundary and line == str(page_number) and _PAGE_NUMBER_RE.fullmatch(line):
                continue
            retained_lines.append(line)
        expected_text = join_lines(retained_lines, [])
        expected_text, _ = remove_table_duplicates(
            expected_text,
            [table for table in source_page.get("table_records", []) if isinstance(table, dict)],
            [],
            [],
        )
        expected_text = remove_pending_image_text(source_page, expected_text, [], [])
        text_token_checks.append(protected_tokens(expected_text) == protected_tokens(cleaned_text))
        if any(pattern.search(cleaned_text) for pattern, _ in _KNOWN_PDF_JOIN_REPAIRS):
            known_pdf_word_joins_absent = False
        if record.get("embedding_status") == "excluded":
            unresolved_table_pages.append(page_number)

    first_page_number = min(source_pages_by_number)
    first_page = source_pages_by_number[first_page_number]
    if first_page.get("table_records"):
        first_page_title_preserved = True
    else:
        expected_first_page_lines = normalise_lines(as_text(first_page.get("text", "")))
        non_empty_count = sum(bool(line) for line in expected_first_page_lines)
        non_empty_index = 0
        retained_first_page_lines: list[str] = []
        for line in expected_first_page_lines:
            if not line:
                retained_first_page_lines.append(line)
                continue
            non_empty_index += 1
            at_boundary = non_empty_index <= 3 or non_empty_index > max(3, non_empty_count - 3)
            if at_boundary and line == str(first_page_number) and _PAGE_NUMBER_RE.fullmatch(line):
                continue
            retained_first_page_lines.append(line)
        expected_first_page_text = join_lines(retained_first_page_lines, [])
        expected_first_page_text, _ = remove_table_duplicates(
            expected_first_page_text,
            [table for table in first_page.get("table_records", []) if isinstance(table, dict)],
            [],
            [],
        )
        expected_first_page_text = remove_pending_image_text(first_page, expected_first_page_text, [], [])
        first_page_lines = [line for line in normalise_lines(expected_first_page_text) if line][:2]
        cleaned_text_all = " ".join(
            as_text(record.get("cleaned_text"))
            for record in cleaned_records
            if record.get("record_type") == "text"
            and int(record.get("page_number", 0)) == first_page_number
        )
        first_page_title_preserved = all(line in cleaned_text_all for line in first_page_lines)

    removed_table_ids_by_page = {
        int(record.get("page_number", 0)): set(
            record.get("cleaning", {}).get("table_ids_removed_from_page_text", [])
        )
        for record in cleaned_records
        if record.get("record_type") == "text"
    }
    all_table_ids_removed = True
    for page_number, source_page in source_pages_by_number.items():
        expected_table_ids = {
            as_text(table.get("table_id")) or "unknown-table"
            for table in source_page.get("table_records", [])
            if isinstance(table, dict)
        }
        if expected_table_ids and expected_table_ids != removed_table_ids_by_page.get(page_number, set()):
            all_table_ids_removed = False
            break

    return {
        "passed": all(
            [
                page_coverage,
                raw_matrix_unchanged,
                table_projection_complete,
                all(text_token_checks) if text_token_checks else True,
                known_pdf_word_joins_absent,
                first_page_title_preserved,
            ]
        ),
        "page_coverage": page_coverage,
        "raw_matrix_unchanged": raw_matrix_unchanged,
        "table_projection_complete": table_projection_complete,
        "protected_text_tokens_preserved": all(text_token_checks) if text_token_checks else True,
        "table_duplicates_removed_from_page_text": all_table_ids_removed,
        "known_pdf_word_joins_repaired": known_pdf_word_joins_absent,
        "first_page_title_preserved": first_page_title_preserved,
        "unresolved_table_pages": sorted(set(unresolved_table_pages)),
        "source_page_count": len(source_pages),
        "cleaned_page_count": len(cleaned_pages),
        "source_table_count": len(source_tables),
        "cleaned_table_count": len(cleaned_tables),
    }
