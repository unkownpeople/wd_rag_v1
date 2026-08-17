from __future__ import annotations

import copy
import unittest

from .chunker import ChunkConfig, chunk_records, make_record_id
from .score import score_chunks


class ChunkerTests(unittest.TestCase):
    def test_text_source_is_fully_covered_and_short_paragraphs_merge(self) -> None:
        records = [
            {
                "doc_id": "doc-1",
                "source_file": "F:\\data\\fixture.docx",
                "source_format": "docx",
                "record_type": "text",
                "source_record_index": 0,
                "content_role": "section_title",
                "cleaned_text": "1. 介绍",
            },
            {
                "doc_id": "doc-1",
                "source_file": "F:\\data\\fixture.docx",
                "source_format": "docx",
                "record_type": "text",
                "source_record_index": 1,
                "cleaned_text": "这是第一段。",
            },
            {
                "doc_id": "doc-1",
                "source_file": "F:\\data\\fixture.docx",
                "source_format": "docx",
                "record_type": "text",
                "source_record_index": 2,
                "cleaned_text": "这是第二段，和前一段属于同一主题。",
            },
        ]
        chunks = chunk_records(records, ChunkConfig(target_tokens=50, min_tokens=2, max_tokens=80))
        self.assertEqual(len(chunks), 1)
        self.assertIn("1. 介绍", chunks[0]["chunk_text"])
        self.assertIn("第二段", chunks[0]["chunk_text"])
        self.assertEqual(score_chunks(records, chunks)["comparison"]["text_lost_tokens"], 0)

    def test_long_paragraph_only_overlaps_at_sentence_boundary(self) -> None:
        text = "".join(f"第{i}句包含一些用于切块测试的内容。" for i in range(20))
        records = [
            {
                "doc_id": "doc-2",
                "source_file": "F:\\data\\fixture.pdf",
                "source_format": "pdf",
                "record_type": "text",
                "source_page_record_index": 0,
                "page_number": 1,
                "cleaned_text": text,
            }
        ]
        chunks = chunk_records(records, ChunkConfig(target_tokens=34, min_tokens=2, max_tokens=40))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(any(chunk["overlap_token_count"] > 0 for chunk in chunks[1:]))
        self.assertEqual(score_chunks(records, chunks)["comparison"]["text_lost_tokens"], 0)
        for chunk in chunks:
            source_keys = [
                (span["source_record_id"], token_index)
                for span in chunk.get("source_spans", [])
                for token_index in range(span["token_start"], span["token_end"])
            ]
            self.assertEqual(len(source_keys), len(set(source_keys)))

    def test_long_sentence_with_leading_whitespace_keeps_all_tokens(self) -> None:
        text = "Name of the Project " + " ".join(f"district{i}" for i in range(700))
        records = [{
            "doc_id": "doc-long-pdf-row",
            "source_file": "F:\\data\\tcs.pdf",
            "source_format": "pdf",
            "record_type": "text",
            "source_page_record_index": 99,
            "page_number": 100,
            "cleaned_text": text,
        }]

        config = ChunkConfig(target_tokens=40, max_tokens=80, min_tokens=2, token_overlap=10)
        chunks = chunk_records(records, config)
        score = score_chunks(records, chunks, config)

        self.assertGreater(len(chunks), 1)
        self.assertEqual(score["comparison"]["text_lost_tokens"], 0)
        self.assertEqual(score["comparison"]["internal_duplicate_tokens"], 0)

    def test_min_tokens_delays_target_boundary(self) -> None:
        records = [
            {
                "doc_id": "doc-min",
                "source_file": "F:\\data\\fixture.docx",
                "source_format": "docx",
                "record_type": "text",
                "source_record_index": 0,
                "cleaned_text": "第一句很短。\n\n第二句也很短但是足够长。",
            }
        ]
        config = ChunkConfig(target_tokens=5, min_tokens=8, max_tokens=20)
        chunks = chunk_records(records, config)
        self.assertEqual(len(chunks), 1)
        self.assertGreater(chunks[0]["token_count"], config.target_tokens)

    def test_large_table_groups_by_input_and_keeps_raw_matrix(self) -> None:
        matrix = [
            ["Task", "Input", "Model", "Generation"],
            ["Task A", "question A", "BART", "answer 1"],
            ["", "", "RAG-T", "answer 2"],
            ["", "", "RAG-S", "answer 3"],
            ["Task B", "question B", "BART", "answer 4"],
            ["", "", "RAG-T", "answer 5"],
            ["", "", "RAG-S", "answer 6"],
        ]
        records = [
            {
                "doc_id": "doc-3",
                "source_file": "F:\\data\\table.csv",
                "source_format": "csv",
                "record_type": "table",
                "source_record_index": 0,
                "table_id": "table-3",
                "raw_matrix": matrix,
                "table_headers": ["Task", "Input", "Model", "Generation"],
                "location": {"sheet": "Sheet1", "range": "A1:D7"},
            }
        ]
        original = copy.deepcopy(matrix)
        chunks = chunk_records(records, ChunkConfig(small_table_body_rows=3))
        self.assertEqual(len(chunks), 2)
        self.assertEqual([chunk["row_indices"] for chunk in chunks], [[1, 2, 3], [4, 5, 6]])
        self.assertTrue(all(chunk["raw_matrix"] == original for chunk in chunks))
        self.assertIn("Input=question A", chunks[0]["search_text"])
        self.assertIn("Input=question A", chunks[0]["chunk_text"])
        self.assertIn("Input=question B", chunks[1]["search_text"])
        self.assertEqual(chunks[0]["row_matrix"][2][1], "")
        self.assertEqual(chunks[1]["row_matrix"][2][1], "")
        self.assertEqual(score_chunks(records, chunks)["tables"]["lost_body_rows"], 0)

    def test_image_pending_is_not_embedded_as_a_chunk(self) -> None:
        records = [
            {
                "doc_id": "doc-4",
                "source_file": "F:\\data\\fixture.pdf",
                "source_format": "pdf",
                "record_type": "image_pending",
                "page_number": 2,
            }
        ]
        self.assertEqual(chunk_records(records), [])
        self.assertEqual(score_chunks(records, [])['image_pending_excluded'], 1)

    def test_geography_table_keeps_parent_rows_and_period_years(self) -> None:
        matrix = [
            ["", "Year ended March 31, 2023", "Year ended March 31, 2022 (` crore)"],
            ["Americas", "", ""],
            ["North America", "1,13,208", "90,630"],
            ["India", "10,941", "9,547"],
        ]
        records = [{
            "doc_id": "tcs-2023",
            "source_file": "F:\\data\\tcs.pdf",
            "source_format": "pdf",
            "record_type": "table",
            "page_number": 296,
            "table_id": "geo",
            "table_title": "Revenue disaggregation by geography is as follows",
            "table_context": "Standalone Financial Statements (` crore)",
            "statement_scope": "standalone",
            "raw_matrix": matrix,
            "header_row_count": 1,
        }]

        chunks = chunk_records(records)
        india = next(chunk for chunk in chunks if "India" in chunk["row_labels"])

        self.assertEqual(india["period_years"], [2022, 2023])
        self.assertEqual(india["measure_name"], "revenue")
        self.assertEqual(india["statement_scope"], "standalone")
        self.assertIn("9,547", india["search_text"])
        north_america = next(chunk for chunk in chunks if "North America" in chunk["row_labels"])
        self.assertEqual(north_america["parent_row_labels"], ["Americas"])

    def test_chunks_keep_filterable_source_metadata(self) -> None:
        records = [
            {
                "doc_id": "doc-meta",
                "source_file": "F:\\data\\annual.pdf",
                "source_format": "pdf",
                "record_type": "text",
                "source_page_record_index": 0,
                "page_number": 1,
                "cleaned_text": "Revenue and total assets are disclosed here.",
                "company_id": "apple",
                "document_id": "apple_2024_10k",
                "fiscal_year": 2024,
                "source_url": "https://example.test/apple.pdf",
                "sha256": "abc123",
            }
        ]

        chunks = chunk_records(records)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["company_id"], "apple")
        self.assertEqual(chunks[0]["document_id"], "apple_2024_10k")
        self.assertEqual(chunks[0]["fiscal_year"], 2024)
        self.assertEqual(chunks[0]["source_url"], "https://example.test/apple.pdf")
        self.assertEqual(chunks[0]["sha256"], "abc123")


if __name__ == "__main__":
    unittest.main()
