"""Table reconstruction from explicit, visually verified PDF regions."""

from __future__ import annotations

from typing import Any

from .regions import PdfTableSpec
from .table_context import table_source_metadata


def _cell_words(
    words: list[dict[str, Any]],
    *,
    left: float,
    right: float,
    row_index: int,
    row_centers: list[float],
    region_top: float,
    region_bottom: float,
) -> str:
    """Rebuild one cell from words inside its registered rectangle.

    This is layout reconstruction, not semantic cleaning. A space is added
    only between separate PDF words in the same cell.
    """

    selected = [
        word
        for word in words
        if word["x0"] >= left - 0.5
        and word["x1"] <= right + 0.5
        and word["top"] >= region_top - 0.5
        and word["bottom"] <= region_bottom + 0.5
        and min(
            range(len(row_centers)),
            key=lambda index: abs(
                ((word["top"] + word["bottom"]) / 2) - row_centers[index]
            ),
        )
        == row_index
    ]
    selected.sort(key=lambda word: (word["top"], word["x0"]))
    return " ".join(str(word["text"]) for word in selected).strip()


def _cell_is_merged(spec: PdfTableSpec, row: int, column: int) -> bool:
    return any(
        merged.row_start <= row < merged.row_end
        and merged.column_start <= column < merged.column_end
        for merged in spec.merged_cells
    )


def extract_explicit_table(
    pdfplumber_page: Any,
    spec: PdfTableSpec,
    *,
    page_text: str = "",
) -> dict[str, Any]:
    """Extract only the cells inside one registered table region."""

    words = pdfplumber_page.extract_words(
        x_tolerance=1,
        y_tolerance=3,
        keep_blank_chars=False,
    )
    row_centers = [
        (top + bottom) / 2
        for top, bottom in zip(spec.row_edges, spec.row_edges[1:])
    ]
    matrix: list[list[str]] = []
    for row_index, (_top, _bottom) in enumerate(
        zip(spec.row_edges, spec.row_edges[1:])
    ):
        row: list[str] = []
        for column_index, (left, right) in enumerate(
            zip(spec.column_edges, spec.column_edges[1:])
        ):
            if _cell_is_merged(spec, row_index, column_index):
                row.append("")
                continue
            row.append(
                _cell_words(
                    words,
                    left=left,
                    right=right,
                    row_index=row_index,
                    row_centers=row_centers,
                    region_top=spec.bbox[1],
                    region_bottom=spec.bbox[3],
                )
            )
        matrix.append(row)

    # Keep only the top-left value of merged cells. Continuation cells remain
    # empty so rowspan/colspan information is not flattened during parsing.
    for merged in spec.merged_cells:
        matrix[merged.row_start][merged.column_start] = merged.value
        for row_index in range(merged.row_start, merged.row_end):
            for column_index in range(merged.column_start, merged.column_end):
                if (row_index, column_index) != (
                    merged.row_start,
                    merged.column_start,
                ):
                    matrix[row_index][column_index] = ""

    source_metadata = table_source_metadata(
        words=words,
        bbox=tuple(float(value) for value in spec.bbox),
        page_text=page_text or str(pdfplumber_page.extract_text() or ""),
    )
    return {
        "table_id": spec.table_id,
        "page_number": spec.page_number,
        "bbox": list(spec.bbox),
        "content_type": "table",
        "matrix": matrix,
        "header_rows": matrix[: spec.header_row_count],
        "headers": matrix[: spec.header_row_count],
        "rows": matrix[spec.header_row_count :],
        "row_count": len(matrix) - spec.header_row_count,
        "column_count": spec.column_count,
        "merged_cells": [merged.as_dict() for merged in spec.merged_cells],
        "extraction_method": "pdfplumber_explicit_region_words",
        "region_status": "accepted",
        "warnings": [],
        **source_metadata,
    }
