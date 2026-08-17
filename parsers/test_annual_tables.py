from __future__ import annotations

import unittest

try:
    from .pdf.table_context import explicit_statement_scope, table_source_metadata
except ImportError:  # supports discovery from the repository root
    from parsers.pdf.table_context import explicit_statement_scope, table_source_metadata


class AnnualTableContextTests(unittest.TestCase):
    def test_inherits_title_outside_table_horizontal_bounds(self) -> None:
        words = [
            {"text": "Standalone", "top": 22, "x0": 8},
            {"text": "Statement", "top": 22, "x0": 72},
            {"text": "of", "top": 22, "x0": 132},
            {"text": "Position", "top": 22, "x0": 150},
            {"text": "Year", "top": 54, "x0": 210},
            {"text": "ended", "top": 54, "x0": 245},
        ]

        metadata = table_source_metadata(
            words=words,
            bbox=(100.0, 70.0, 500.0, 700.0),
            page_text="Standalone Statement of Position\nYear ended",
        )

        self.assertEqual(metadata["table_title"], "Standalone Statement of Position")
        self.assertEqual(metadata["statement_scope"], "standalone")
        self.assertIn("Year ended", metadata["table_context"])

    def test_uses_page_leading_context_when_layout_title_is_unavailable(self) -> None:
        metadata = table_source_metadata(
            words=[],
            bbox=(50.0, 80.0, 500.0, 700.0),
            page_text="Consolidated Statements\nStatement of Position\nReporting period",
        )

        self.assertEqual(metadata["table_title"], "Statement of Position")
        self.assertEqual(metadata["statement_scope"], "consolidated")

    def test_conflicting_scope_labels_remain_unknown(self) -> None:
        self.assertEqual(
            explicit_statement_scope("Standalone and consolidated values"),
            "unknown",
        )


if __name__ == "__main__":
    unittest.main()
