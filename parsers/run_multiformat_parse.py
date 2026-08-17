from __future__ import annotations

import json
from pathlib import Path

try:
    from .document_parser import parse_document, write_document_jsonl, write_document_preview
    from .excel_parser import compare_excel_document
    from .pdf_parser import compare_pdf_tables
except ImportError:  # supports running this file directly by its absolute path
    from document_parser import parse_document, write_document_jsonl, write_document_preview
    from excel_parser import compare_excel_document
    from pdf_parser import compare_pdf_tables


ROOT = Path(__file__).resolve().parents[1]

# The first multi-format test set. The Excel input is an existing downloaded CSV
# table preview; CSV is directly readable by Excel and exercises the Excel branch.
INPUT_FILES = [
    ROOT / "data" / "pdf" / "rag_original_2005.11401.pdf",
    ROOT / "data" / "word" / "rag_parse_branch_test.docx",
    ROOT / "data" / "tables_preview" / "finqa_table_01.csv",
]
OUTPUT_DIR = ROOT / "data" / "processed" / "parsed"
PDF_TABLE_REFERENCE = OUTPUT_DIR / "rag_original_2005.11401.table_reference.json"


def main() -> None:
    for input_file in INPUT_FILES:
        if not input_file.is_file():
            raise FileNotFoundError(f"Bound test input does not exist: {input_file}")
        result = parse_document(input_file)
        stem = f"{input_file.stem}.unified"
        output_jsonl = OUTPUT_DIR / f"{stem}.jsonl"
        output_preview = OUTPUT_DIR / f"{stem}.preview.txt"
        write_document_jsonl(result, output_jsonl)
        write_document_preview(result, output_preview)
        comparison_file = None
        comparison_matched = None
        if result.source_format in {"csv", "tsv", "xlsx"}:
            comparison = compare_excel_document(result, input_file)
            comparison_file = OUTPUT_DIR / f"{stem}.compare.json"
            comparison_file.write_text(
                json.dumps(comparison, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            comparison_matched = comparison["matched"]
        elif result.source_format == "pdf":
            comparison = compare_pdf_tables(result, PDF_TABLE_REFERENCE)
            comparison_file = OUTPUT_DIR / f"{stem}.table.compare.json"
            comparison_file.write_text(
                json.dumps(comparison, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            comparison_matched = comparison["matched"]
        print(
            json.dumps(
                {
                    "source_format": result.source_format,
                    "source_file": result.source_file,
                    "records": len(result.records),
                    "warnings": len(result.warnings),
                    "comparison_matched": comparison_matched,
                    "output_jsonl": str(output_jsonl),
                    "output_preview": str(output_preview),
                    "comparison_file": str(comparison_file) if comparison_file else None,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
