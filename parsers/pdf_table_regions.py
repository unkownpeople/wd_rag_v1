from __future__ import annotations

"""Visually verified table regions for the bound PDF fixture.

PDF tables in this project are accepted only when their page, bounding box,
row bands, and column bands are explicitly registered.  Whole-page
``pdfplumber.extract_tables`` output is diagnostic telemetry only; it is never
promoted to a table record because figures and ordinary images can look like
tables to a geometry-only detector.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MergedCellSpec:
    """A visually merged cell using half-open row/column indexes."""

    row_start: int
    row_end: int
    column_start: int
    column_end: int
    value: str

    def as_dict(self) -> dict[str, object]:
        return {
            "row_start": self.row_start,
            "row_end": self.row_end,
            "column_start": self.column_start,
            "column_end": self.column_end,
            "value": self.value,
        }


@dataclass(frozen=True)
class PdfTableSpec:
    """Coordinates and layout rules for one real table in a PDF page."""

    table_id: str
    page_number: int
    bbox: tuple[float, float, float, float]
    column_edges: tuple[float, ...]
    row_edges: tuple[float, ...]
    header_row_count: int
    merged_cells: tuple[MergedCellSpec, ...] = ()

    @property
    def column_count(self) -> int:
        return len(self.column_edges) - 1

    @property
    def row_count(self) -> int:
        return len(self.row_edges) - 1

    def as_dict(self) -> dict[str, object]:
        return {
            "table_id": self.table_id,
            "page_number": self.page_number,
            "bbox": list(self.bbox),
            "column_edges": list(self.column_edges),
            "row_edges": list(self.row_edges),
            "header_row_count": self.header_row_count,
            "merged_cells": [cell.as_dict() for cell in self.merged_cells],
        }


def _specs_for_rag_original() -> tuple[PdfTableSpec, ...]:
    # Coordinates use pdfplumber's page coordinate system: x0, top, x1,
    # bottom, with the origin at the top-left of a 612 x 792 point page.
    return (
        PdfTableSpec(
            table_id="p06_t01",
            page_number=6,
            bbox=(110.1, 128.2, 299.9, 213.0),
            column_edges=(110.1, 139.0, 194.0, 216.0, 255.0, 278.0, 299.9),
            row_edges=(
                128.2,
                142.5,
                154.9,
                165.9,
                176.9,
                189.4,
                201.4,
                213.0,
            ),
            header_row_count=1,
            merged_cells=(
                MergedCellSpec(0, 1, 0, 2, "Model"),
                MergedCellSpec(1, 3, 0, 1, "Closed Book"),
                MergedCellSpec(3, 5, 0, 1, "Open Book"),
            ),
        ),
        PdfTableSpec(
            table_id="p06_t02",
            page_number=6,
            bbox=(312.1, 135.9, 501.9, 213.0),
            column_edges=(
                312.1,
                353.0,
                375.0,
                399.0,
                425.0,
                450.0,
                475.0,
                501.9,
            ),
            row_edges=(135.9, 148.0, 160.0, 174.5, 188.9, 201.0, 213.0),
            header_row_count=2,
            merged_cells=(
                MergedCellSpec(0, 1, 1, 3, "Jeopardy"),
                MergedCellSpec(0, 1, 3, 5, "MSMARCO"),
                MergedCellSpec(1, 2, 5, 7, "Label Acc."),
            ),
        ),
        PdfTableSpec(
            table_id="p08_t04",
            page_number=8,
            bbox=(127.2, 106.2, 282.9, 176.7),
            column_edges=(127.2, 188.0, 233.0, 282.9),
            row_edges=(106.2, 121.5, 134.0, 144.0, 154.0, 164.0, 176.7),
            header_row_count=1,
        ),
        PdfTableSpec(
            table_id="p07_t03",
            page_number=7,
            bbox=(111.0, 218.0, 505.0, 349.0),
            column_edges=(111.0, 142.0, 189.0, 216.5, 505.0),
            row_edges=(
                218.0,
                231.5,
                242.5,
                251.0,
                260.0,
                269.5,
                278.5,
                287.5,
                298.5,
                307.5,
                317.5,
                327.8,
                337.8,
                349.0,
            ),
            header_row_count=1,
            merged_cells=(
                MergedCellSpec(1, 5, 0, 1, "MS-MARCO"),
                MergedCellSpec(1, 4, 1, 2, "define middle ear"),
                MergedCellSpec(4, 7, 1, 2, "what currency needed in scotland"),
                MergedCellSpec(7, 12, 0, 1, "Jeopardy Question Generation"),
                MergedCellSpec(7, 10, 1, 2, "Washington"),
                MergedCellSpec(10, 13, 1, 2, "The Divine Comedy"),
            ),
        ),
        PdfTableSpec(
            table_id="p08_t05",
            page_number=8,
            bbox=(310.5, 111.2, 503.4, 171.7),
            column_edges=(310.5, 378.0, 439.0, 503.4),
            row_edges=(111.2, 126.5, 139.0, 149.0, 159.0, 171.7),
            header_row_count=1,
        ),
        PdfTableSpec(
            table_id="p08_t06",
            page_number=8,
            bbox=(108.0, 214.4, 515.6, 315.3),
            column_edges=(
                108.0,
                202.0,
                232.0,
                263.0,
                290.0,
                318.0,
                347.0,
                387.0,
                417.0,
                446.0,
                481.0,
                515.6,
            ),
            row_edges=(
                214.4,
                227.7,
                239.7,
                252.8,
                264.9,
                277.9,
                290.0,
                303.1,
                315.3,
            ),
            header_row_count=2,
            merged_cells=(
                MergedCellSpec(0, 1, 5, 7, "Jeopardy-QGen"),
                MergedCellSpec(0, 1, 7, 9, "MSMarco"),
                MergedCellSpec(1, 2, 2, 4, "Exact Match"),
                MergedCellSpec(1, 2, 9, 11, "Label Accuracy"),
            ),
        ),
        PdfTableSpec(
            table_id="p19_t07",
            page_number=19,
            bbox=(179.3, 98.5, 430.5, 192.5),
            column_edges=(179.3, 295.0, 333.0, 393.0, 430.5),
            row_edges=(
                98.5,
                108.5,
                123.0,
                133.0,
                143.0,
                153.0,
                163.0,
                173.0,
                183.0,
                192.5,
            ),
            header_row_count=1,
        ),
    )


TABLE_SPECS_BY_FILENAME: dict[str, tuple[PdfTableSpec, ...]] = {
    "rag_original_2005.11401.pdf": _specs_for_rag_original(),
}


NON_TABLE_PAGE_NOTES: dict[str, dict[int, str]] = {
    "rag_original_2005.11401.pdf": {
        2: "Figure 1 is an architecture diagram; the whole-page table candidate is excluded.",
    }
}


def table_specs_for_pdf(input_pdf: Path) -> tuple[PdfTableSpec, ...]:
    """Return only regions verified for the exact bound PDF filename."""

    return TABLE_SPECS_BY_FILENAME.get(input_pdf.name.lower(), ())


def table_specs_by_page(input_pdf: Path) -> dict[int, tuple[PdfTableSpec, ...]]:
    return {
        page_number: tuple(specs)
        for page_number, specs in _group_by_page(table_specs_for_pdf(input_pdf)).items()
    }


def _group_by_page(
    specs: tuple[PdfTableSpec, ...],
) -> dict[int, list[PdfTableSpec]]:
    grouped: dict[int, list[PdfTableSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.page_number, []).append(spec)
    return grouped


def non_table_note(input_pdf: Path, page_number: int) -> str | None:
    return NON_TABLE_PAGE_NOTES.get(input_pdf.name.lower(), {}).get(page_number)
