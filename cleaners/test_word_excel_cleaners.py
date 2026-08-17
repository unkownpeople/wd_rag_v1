from __future__ import annotations

import copy
import unittest

try:
    from .excel_cleaner import clean_excel_records
    from .word_cleaner import clean_word_records
except ImportError:  # supports unittest discovery with cleaners as the start directory
    from cleaners.excel_cleaner import clean_excel_records
    from cleaners.word_cleaner import clean_word_records


class WordCleanerTests(unittest.TestCase):
    def test_single_row_kpi_table_is_searchable_and_preserved(self) -> None:
        records = [{
            "record_type": "table",
            "source_file": "F:\\workspace\\kpi.docx",
            "record_index": 0,
            "sequence": 0,
            "table_number": 1,
            "rows": [["FY2024 REVENUE Rs.240,893 Cr", "NET PROFIT Rs.46,099 Cr"]],
            "row_count": 1,
            "column_count": 2,
        }]

        cleaned, report = clean_word_records(records)

        self.assertTrue(report["validation"]["passed"])
        self.assertEqual(cleaned[0]["raw_matrix"], records[0]["rows"])
        self.assertEqual(cleaned[0]["header_row_count"], 0)
        self.assertIn("FY2024 REVENUE", cleaned[0]["table_text"])

    def test_keeps_paragraph_facts_and_projects_table(self) -> None:
        records = [
            {
                "record_type": "paragraph",
                "source_file": "F:\\workspace\\sample.docx",
                "record_index": 0,
                "sequence": 0,
                "style": "Heading 1",
                "text": "  1. 标题  ",
            },
            {
                "record_type": "paragraph",
                "source_file": "F:\\workspace\\sample.docx",
                "record_index": 1,
                "sequence": 1,
                "style": "Normal",
                "text": "金额为 -12.50%，引用 [7]。",
            },
            {
                "record_type": "table",
                "source_file": "F:\\workspace\\sample.docx",
                "record_index": 2,
                "sequence": 2,
                "table_number": 1,
                "rows": [["字段", "值"], ["金额", "-12.50%"], ["公式", "=SUM(A1:A2)"]],
                "row_count": 3,
                "column_count": 2,
                "parse_branch": "word:python-docx",
            },
        ]
        source = copy.deepcopy(records)
        cleaned, report = clean_word_records(records)
        self.assertEqual(records, source)
        self.assertTrue(report["validation"]["passed"])
        self.assertEqual(cleaned[0]["content_role"], "section_title")
        table = next(record for record in cleaned if record["record_type"] == "table")
        self.assertEqual(table["raw_matrix"], source[2]["rows"])
        self.assertIn("字段=金额", table["table_text"])
        self.assertIn("值=-12.50%", table["table_text"])
        self.assertIn("值==SUM(A1:A2)", table["table_text"])


class ExcelCleanerTests(unittest.TestCase):
    def test_keeps_raw_content_and_projection_only_cleans_copy(self) -> None:
        records = [
            {"record_type": "document_meta", "source_file": "F:\\workspace\\sales.xlsx", "source_format": "xlsx"},
            {
                "source_type": "excel",
                "source": "sales.xlsx",
                "location": {"sheet": "Sheet1", "range": "A1:D3"},
                "content_type": "table",
                "raw_content": {
                    "headers": [" 产品 ", "数量", "金额", "公式"],
                    "rows": [["A", 10, 1000, "=B2*C2"], ["B", None, 0, ""]],
                },
                "parser": "openpyxl",
                "warnings": [],
            },
        ]
        source = copy.deepcopy(records)
        cleaned, report = clean_excel_records(records)
        self.assertEqual(records, source)
        self.assertTrue(report["validation"]["passed"])
        table = cleaned[0]
        self.assertEqual(table["raw_content"], source[1]["raw_content"])
        self.assertIn("产品=A", table["table_text"])
        self.assertIn("数量=空值", table["table_text"])
        self.assertIn("公式==B2*C2", table["table_text"])


if __name__ == "__main__":
    unittest.main()
