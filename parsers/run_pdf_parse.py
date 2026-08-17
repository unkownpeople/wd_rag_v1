from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .pdf_parser import (
        PdfParseResult,
        compare_pdf_tables,
        parse_pdf,
        write_jsonl,
        write_preview,
    )
except ImportError:  # supports running this file directly by its absolute path
    from pdf_parser import PdfParseResult, compare_pdf_tables, parse_pdf, write_jsonl, write_preview


ROOT = Path(__file__).resolve().parents[1]

# This is the code-level binding for the first PDF parsing run.
INPUT_PDF = ROOT / "data" / "pdf" / "rag_original_2005.11401.pdf"
OUTPUT_DIR = ROOT / "data" / "processed" / "parsed"
OUTPUT_JSONL = OUTPUT_DIR / "rag_original_2005.11401.jsonl"
OUTPUT_PREVIEW = OUTPUT_DIR / "rag_original_2005.11401.preview.txt"
TABLE_REFERENCE = OUTPUT_DIR / "rag_original_2005.11401.table_reference.json"
OUTPUT_TABLE_COMPARE = OUTPUT_DIR / "rag_original_2005.11401.table.compare.json"


def _summary(result: PdfParseResult) -> dict[str, object]:
    pages = result.pages
    return {
        "source_file": result.source_file,
        "page_count": result.page_count,
        "text_pages": sum("text:pypdf" in page.parse_branches for page in pages),
        "table_pages": sum(page.table_count > 0 for page in pages),
        "accepted_tables": sum(page.table_count for page in pages),
        "whole_page_candidate_count": sum(
            page.table_candidate_count for page in pages
        ),
        "excluded_candidate_pages": sum(
            bool(page.excluded_candidates) for page in pages
        ),
        "ocr_pending_pages": sum("ocr:pending" in page.parse_branches for page in pages),
        "error_pages": sum(page.status == "error" for page in pages),
        "output_jsonl": str(OUTPUT_JSONL),
        "output_preview": str(OUTPUT_PREVIEW),
        "table_compare": str(OUTPUT_TABLE_COMPARE),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse one bound PDF for inspection.")
    parser.add_argument(
        "--input",
        type=Path,
        default=INPUT_PDF,
        help=f"PDF path; default: {INPUT_PDF}",
    )
    args = parser.parse_args()

    result = parse_pdf(args.input)
    write_jsonl(result, OUTPUT_JSONL)
    write_preview(result, OUTPUT_PREVIEW)
    comparison = compare_pdf_tables(result, TABLE_REFERENCE)
    OUTPUT_TABLE_COMPARE.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("PDF parsing completed")
    print(
        json.dumps(
            {
                **_summary(result),
                "table_structure_acceptance": comparison["matched"],
                "table_differences": len(comparison["differences"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n--- first two parsed pages ---")
    for page in result.pages[:2]:
        print(
            f"\n[page {page.page_number}] "
            f"branches={','.join(page.parse_branches)} "
            f"chars={page.text_char_count} tables={page.table_count}"
        )
        preview = page.text.strip().replace("\n", " ")
        print(preview[:600] or "<no text layer>")


if __name__ == "__main__":
    main()
