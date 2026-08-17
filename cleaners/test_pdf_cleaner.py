from __future__ import annotations

import unittest

try:
    from .pdf_cleaner import clean_pdf_records
except ImportError:  # supports unittest discovery with cleaners as the start directory
    from cleaners.pdf_cleaner import clean_pdf_records


class PdfCleanerTests(unittest.TestCase):
    def test_keeps_raw_matrix_and_projects_table_rows(self) -> None:
        pages = [
            {
                "source_file": "fixture.pdf",
                "page_number": 1,
                "record_index": 0,
                "text": "Table 1\nModel Score\nA 10%\n\nThe answer is 10%.\n1",
                "table_records": [
                    {
                        "table_id": "p01_t01",
                        "page_number": 1,
                        "bbox": [0, 0, 100, 100],
                        "matrix": [["Model", "Score"], ["A", "10%"]],
                        "header_rows": [["Model", "Score"]],
                        "merged_cells": [],
                        "warnings": [],
                    }
                ],
                "warnings": [],
            }
        ]

        records, report = clean_pdf_records(pages)

        table = next(record for record in records if record["record_type"] == "table")
        text = next(record for record in records if record["record_type"] == "text")
        self.assertEqual(table["raw_matrix"], [["Model", "Score"], ["A", "10%"]])
        self.assertIn("Score=10%", table["table_text"])
        self.assertNotIn("Model Score A 10%", text["cleaned_text"])
        self.assertEqual(report["validation"]["raw_matrix_unchanged"], True)

    def test_page_number_is_removed_but_protected_number_remains(self) -> None:
        pages = [
            {
                "source_file": "fixture.pdf",
                "page_number": 2,
                "record_index": 0,
                "text": "The model scored 42.5% in 2024.\n2",
                "table_records": [],
                "warnings": [],
            }
        ]

        records, report = clean_pdf_records(pages)

        text = records[0]
        self.assertEqual(text["cleaned_text"], "The model scored 42.5% in 2024.")
        self.assertEqual(report["validation"]["protected_text_tokens_preserved"], True)

    def test_figure_labels_are_deferred_and_merged_group_is_projected(self) -> None:
        pages = [
            {
                "source_file": "fixture.pdf",
                "page_number": 2,
                "record_index": 1,
                "text": "Diagram label 14\nFigure 1: Caption. Body has 2024 data.\n2",
                "excluded_candidates": [
                    {"reason": "Figure 1 is an architecture diagram."}
                ],
                "table_records": [],
            },
            {
                "source_file": "fixture.pdf",
                "page_number": 3,
                "record_index": 2,
                "text": "Table 1\nModel Closed Book A B Open Book C D\n3",
                "table_records": [
                    {
                        "table_id": "p03_t01",
                        "page_number": 3,
                        "matrix": [
                            ["Model", ""],
                            ["Closed Book", "A"],
                            ["", "B"],
                            ["Open Book", "C"],
                            ["", "D"],
                        ],
                        "header_rows": [["Model", ""]],
                        "merged_cells": [
                            {"row_start": 0, "row_end": 1, "column_start": 0, "column_end": 2},
                            {"row_start": 1, "row_end": 3, "column_start": 0, "column_end": 1},
                            {"row_start": 3, "row_end": 4, "column_start": 0, "column_end": 1},
                        ],
                    }
                ],
            }
        ]

        records, report = clean_pdf_records(pages)

        text = next(record for record in records if record["record_type"] == "text")
        table = next(record for record in records if record["record_type"] == "table")
        image = next(record for record in records if record["record_type"] == "image_pending")
        self.assertNotIn("Diagram label", text["cleaned_text"])
        self.assertIn("remove_image_region_text_without_bbox", text["cleaning"]["actions"])
        self.assertIn("Model group=Open Book", table["table_text"])
        self.assertEqual(image["embedding_status"], "excluded")
        self.assertTrue(report["validation"]["passed"])


if __name__ == "__main__":
    unittest.main()
