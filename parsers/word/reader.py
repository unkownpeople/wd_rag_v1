"""DOCX reader: document order, paragraphs, tables, and metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

try:
    from ..models import ParsedDocument
except ImportError:  # supports direct execution from the parsers directory
    from models import ParsedDocument


def _iter_block_items(document: DocumentObject) -> Iterator[Paragraph | Table]:
    """Yield body paragraphs and tables in their original order."""

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _core_metadata(document: DocumentObject) -> dict[str, str]:
    properties = document.core_properties
    values = {
        "title": properties.title,
        "subject": properties.subject,
        "author": properties.author,
        "last_modified_by": properties.last_modified_by,
    }
    return {key: value for key, value in values.items() if value}


def _cell_text(cell: Any) -> str:
    return str(cell.text)


def parse_word(input_docx: Path) -> ParsedDocument:
    """Parse one DOCX without cleaning, chunking, or embedding its content."""

    input_docx = input_docx.resolve()
    if not input_docx.is_file():
        raise FileNotFoundError(f"Word file does not exist: {input_docx}")
    if input_docx.suffix.lower() != ".docx":
        raise ValueError(f"Word branch expects .docx: {input_docx}")

    document = Document(str(input_docx))
    records: list[dict[str, Any]] = []
    sequence = 0
    table_number = 0

    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            text = block.text
            if not text.strip():
                continue
            records.append(
                {
                    "source_file": str(input_docx),
                    "source_format": "docx",
                    "record_type": "paragraph",
                    "record_index": len(records),
                    "sequence": sequence,
                    "style": block.style.name if block.style else None,
                    "page_number": None,
                    "parse_branch": "word:python-docx",
                    "text": text,
                }
            )
        else:
            table_number += 1
            rows = [[_cell_text(cell) for cell in row.cells] for row in block.rows]
            records.append(
                {
                    "source_file": str(input_docx),
                    "source_format": "docx",
                    "record_type": "table",
                    "record_index": len(records),
                    "sequence": sequence,
                    "table_number": table_number,
                    "page_number": None,
                    "parse_branch": "word:python-docx",
                    "row_count": len(rows),
                    "column_count": max((len(row) for row in rows), default=0),
                    "rows": rows,
                    "text": "\n".join("\t".join(row) for row in rows),
                }
            )
        sequence += 1

    return ParsedDocument(
        source_file=str(input_docx),
        source_format="docx",
        records=records,
        metadata=_core_metadata(document),
        warnings=[
            "DOCX page numbers are not inferred during parsing; keep paragraph/table sequence for this stage."
        ],
    )
