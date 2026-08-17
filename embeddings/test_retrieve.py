from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from pathlib import Path

from .contracts import QueryRequest
from .retrieve import BM25Index, QdrantRetriever, assemble_context
from .vectorize import DEFAULT_CHUNK_DIR, _payload
from .semantic_metadata import enrich_chunk


class BM25IndexTests(unittest.TestCase):
    def test_vector_payload_keeps_table_fragment_and_source_metadata(self) -> None:
        chunk = {
            "chunk_id": "chunk-table",
            "company_id": "apple",
            "document_id": "apple_2024_10k",
            "fiscal_year": 2024,
            "source_provider": "AnnualReports.com",
            "table_header": [["Item", "2024"]],
            "header_row_count": 1,
            "row_indices": [1],
            "row_matrix": [["Item", "2024"], ["Total assets", "364980"]],
            "raw_matrix": [["Item", "2024"], ["Total assets", "364980"]],
            "search_text": "Item=Total assets; 2024=364980",
        }

        payload = _payload(chunk)

        self.assertEqual(payload["company_id"], "apple")
        self.assertEqual(payload["source_provider"], "AnnualReports.com")
        self.assertEqual(payload["table_header"], [["Item", "2024"]])
        self.assertEqual(payload["row_indices"], [1])
        self.assertEqual(payload["row_matrix"][1][0], "Total assets")
        self.assertIn("Total assets", payload["search_text"])
        self.assertNotIn("raw_matrix", payload)

    def test_bm25_registry_deduplicates_full_table_matrix(self) -> None:
        matrix = [["Item", "2024"], ["Revenue", "100"], ["Operating income", "20"]]
        index = BM25Index(
            [
                {
                    "chunk_id": "row-1",
                    "chunk_type": "table_group",
                    "table_group_id": "statement-1",
                    "raw_matrix": matrix,
                    "search_text": "Revenue 100",
                },
                {
                    "chunk_id": "row-2",
                    "chunk_type": "table_group",
                    "table_group_id": "statement-1",
                    "raw_matrix": matrix,
                    "search_text": "Operating income 20",
                },
            ]
        )

        self.assertEqual(index.table_matrices, {"statement-1": matrix})

    def test_retriever_attaches_table_matrix_from_chunk_registry(self) -> None:
        matrix = [["Item", "2024"], ["Revenue", "100"]]
        retriever = object.__new__(QdrantRetriever)
        retriever.bm25 = SimpleNamespace(table_matrices={"statement-1": matrix})

        hit = retriever._with_table_matrix(
            {
                "chunk_id": "row-1",
                "payload": {"chunk_id": "row-1", "table_id": "statement-1"},
                "citation": {"table_group_id": "statement-1"},
            }
        )

        self.assertEqual(hit["_table_matrix"], matrix)
        self.assertEqual(hit["_table_matrix_source"], "chunk_artifact")

    def test_citation_refreshes_stale_derived_statement_family(self) -> None:
        hit = QdrantRetriever._with_citation(
            {
                "payload": {
                    "chunk_id": "risk-section",
                    "chunk_text": "Risk Factors and risks and uncertainties may affect actual results.",
                    "statement_family": "management_analysis",
                    "document_id": "microsoft_2023_annual_report",
                    "company_id": "microsoft",
                    "fiscal_year": 2023,
                    "source_format": "pdf",
                    "source_file": "microsoft_2023_annual_report.pdf",
                    "page_start": 11,
                    "page_end": 11,
                }
            }
        )

        self.assertEqual(hit["citation"]["statement_family"], "risk")

    def test_formulations_query_finds_exact_chunk(self) -> None:
        index = BM25Index.from_chunk_dir(DEFAULT_CHUNK_DIR)
        hits = index.search(
            "same retrieved passages across the generated sequence and different passages per token",
            limit=5,
        )
        self.assertIn(
            "chunk_e592d80e117dfff74ff1ed53",
            [hit["chunk_id"] for hit in hits],
        )

    def test_geography_payload_has_auditable_period_and_scope(self) -> None:
        chunk = {
            "chunk_id": "india-2022",
            "document_id": "tcs_2023_annual_report",
            "fiscal_year": 2023,
            "currency": "INR",
            "table_title": "Revenue disaggregation by geography is as follows",
            "table_context": "Standalone Financial Statements (` crore)",
            "row_labels": ["India"],
            "period_years": [2022, 2023],
            "statement_scope": "standalone",
            "measure_name": "revenue",
            "value_kind": "monetary",
            "search_text": "India; 2023=10,941; 2022=9,547",
        }
        enrich_chunk(chunk)
        payload = _payload(chunk)

        self.assertEqual(payload["region"], "India")
        self.assertEqual(payload["period_years"], [2022, 2023])
        self.assertEqual(payload["unit_scale"], "crore")
        self.assertEqual(payload["statement_scope"], "standalone")

    def test_query_request_validates_and_preserves_explicit_filters(self) -> None:
        request = QueryRequest.from_mapping(
            {
                "query": "Apple 2024 annual report",
                "top_k": 3,
                "filters": {"company_id": "apple", "fiscal_year": 2024},
            }
        )
        self.assertEqual(request.explicit_filters(), {"company_id": "apple", "fiscal_year": 2024})

    def test_annual_index_infers_company_and_year(self) -> None:
        annual_dir = Path(__file__).resolve().parents[1] / "data" / "processed" / "annual_reports" / "pdf_chunks"
        index = BM25Index.from_chunk_dir(annual_dir)
        self.assertEqual(
            index.infer_filters("What were Apple's total net sales in fiscal 2024?"),
            {"company_id": "apple", "fiscal_year": 2024},
        )

    def test_v1_chinese_company_aliases_infer_company_and_year(self) -> None:
        index = BM25Index(
            [
                {"chunk_id": "apple", "company_id": "apple", "fiscal_year": 2024, "chunk_text": "Apple"},
                {"chunk_id": "microsoft", "company_id": "microsoft", "fiscal_year": 2023, "chunk_text": "Microsoft"},
                {"chunk_id": "tcs", "company_id": "tcs", "fiscal_year": 2024, "chunk_text": "TCS"},
            ]
        )

        cases = {
            "苹果2024财年的总净销售额是多少？": {"company_id": "apple", "fiscal_year": 2024},
            "苹果公司2024财年的服务业务净销售额是多少？": {"company_id": "apple", "fiscal_year": 2024},
            "微软2023财年的营业收入是多少？": {"company_id": "microsoft", "fiscal_year": 2023},
            "微软公司2023财年的智能云收入是多少？": {"company_id": "microsoft", "fiscal_year": 2023},
            "塔塔咨询2024财年的经营活动现金流是多少？": {"company_id": "tcs", "fiscal_year": 2024},
            "塔塔咨询服务公司2024财年的总收入是多少？": {"company_id": "tcs", "fiscal_year": 2024},
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(index.infer_filters(query), expected)

    def test_historical_fact_year_is_not_document_fiscal_filter(self) -> None:
        index = BM25Index([
            {"chunk_id": "tcs", "company_id": "tcs", "fiscal_year": 2023, "chunk_type": "table", "search_text": "India revenue 2022"}
        ])
        self.assertEqual(index.infer_filters("TCS 2022 年印度区收入是多少"), {"company_id": "tcs"})

    def test_multiple_explicit_fiscal_years_do_not_create_single_year_filter(self) -> None:
        index = BM25Index(
            [
                {"chunk_id": "fy2023", "company_id": "tcs", "fiscal_year": 2023, "chunk_text": "FY2023"},
                {"chunk_id": "fy2024", "company_id": "tcs", "fiscal_year": 2024, "chunk_text": "FY2024"},
            ]
        )

        self.assertEqual(
            index.infer_filters("Compare TCS FY2023 and FY2024 revenue"),
            {"company_id": "tcs"},
        )

    def test_context_has_stable_citation_ids(self) -> None:
        context = assemble_context(
            [
                {
                    "chunk_id": "chunk_1",
                    "text": "Total net sales were 391,035.",
                    "citation": {"page": 32, "table_id": "table_1"},
                }
            ]
        )
        self.assertIn("[S1]", context["context"])
        self.assertEqual(context["citations"][0]["evidence_id"], "S1")

    def test_context_expands_raw_matrix_once_per_table_group(self) -> None:
        hits = [
            {
                "chunk_id": "row-1",
                "text": "row_number=1; Item=Revenue; 2024=100",
                "payload": {
                    "chunk_id": "row-1",
                    "chunk_text": "row_number=1; Item=Revenue; 2024=100",
                    "table_id": "statement-1",
                },
                "citation": {"table_group_id": "statement-1"},
                "_table_matrix": [
                    ["Item", "2024"],
                    ["Revenue", "100"],
                    ["Operating income", "20"],
                ],
            },
            {
                "chunk_id": "row-2",
                "text": "row_number=2; Item=Operating income; 2024=20",
                "payload": {
                    "chunk_id": "row-2",
                    "chunk_text": "row_number=2; Item=Operating income; 2024=20",
                    "table_id": "statement-1",
                },
                "citation": {"table_group_id": "statement-1"},
            },
        ]
        context = assemble_context(hits)
        self.assertIn("完整表格上下文", context["context"])
        self.assertEqual(context["context"].count("完整表格上下文"), 1)
        self.assertIn("Operating income | 20", hits[0]["_context_text"])

    def test_bm25_normalizes_legacy_object_table_rows(self) -> None:
        index = BM25Index(
            [
                {
                    "chunk_id": "legacy-table",
                    "table_id": "statement-legacy",
                    "chunk_type": "table_group",
                    "raw_matrix": [
                        {"value": ["Item", "2024"], "Count": 2},
                        {"value": ["Cash generated by operating activities", "118,254"], "Count": 2},
                    ],
                    "search_text": "Cash generated by operating activities 118,254",
                }
            ]
        )

        self.assertEqual(
            index.table_matrices["statement-legacy"],
            [["Item", "2024"], ["Cash generated by operating activities", "118,254"]],
        )

    def test_search_request_supports_all_modes_and_preserves_explicit_filters(self) -> None:
        request = QueryRequest(
            query="generic evidence request",
            document_id="document-under-test",
            top_k=2,
        )
        for mode, method_name in (
            ("dense", "search"),
            ("bm25", "search_sparse"),
            ("hybrid", "search_hybrid"),
        ):
            with self.subTest(mode=mode):
                retriever = object.__new__(QdrantRetriever)
                retriever.bm25 = SimpleNamespace(company_aliases={})
                retriever.last_search_meta = {}
                retriever.resolve_request = Mock(return_value=request)
                retriever.search = Mock(return_value=[])
                retriever.search_sparse = Mock(return_value=[])
                retriever.search_hybrid = Mock(return_value=[])

                hits = retriever.search_request(request, mode=mode)

                self.assertEqual(hits, [])
                getattr(retriever, method_name).assert_called_once_with(
                    request.query,
                    limit=request.top_k,
                    filters={"document_id": "document-under-test"},
                )

    def test_hybrid_rrf_deduplicates_chunks(self) -> None:
        retriever = object.__new__(QdrantRetriever)
        retriever.search = Mock(
            return_value=[
                {"chunk_id": "shared", "rank": 1, "score": 0.9, "payload": {"chunk_id": "shared"}},
                {"chunk_id": "dense-only", "rank": 2, "score": 0.8, "payload": {"chunk_id": "dense-only"}},
            ]
        )
        retriever.bm25 = SimpleNamespace(
            search=Mock(
                return_value=[
                    {"chunk_id": "shared", "rank": 1, "score": 3.0, "payload": {"chunk_id": "shared"}},
                    {"chunk_id": "sparse-only", "rank": 2, "score": 2.0, "payload": {"chunk_id": "sparse-only"}},
                ]
            )
        )

        hits = retriever.search_hybrid("generic evidence request", limit=3)

        chunk_ids = [hit["chunk_id"] for hit in hits]
        self.assertEqual(chunk_ids[0], "shared")
        self.assertEqual(len(chunk_ids), len(set(chunk_ids)))
        self.assertEqual(set(chunk_ids), {"shared", "dense-only", "sparse-only"})

    def test_context_honors_character_budget(self) -> None:
        context = assemble_context(
            [
                {"chunk_id": "first", "text": "first evidence", "citation": {}},
                {"chunk_id": "second", "text": "second evidence" * 100, "citation": {}},
            ],
            max_chars=300,
        )

        self.assertEqual([item["chunk_id"] for item in context["citations"]], ["first"])
        self.assertNotIn("[S2]", context["context"])

    def test_oversized_evidence_does_not_hide_later_short_evidence(self) -> None:
        context = assemble_context(
            [
                {"chunk_id": "oversized", "text": "x" * 1000, "citation": {}},
                {"chunk_id": "short", "text": "short evidence", "citation": {}},
            ],
            max_chars=300,
        )

        self.assertEqual([item["chunk_id"] for item in context["citations"]], ["short"])
        self.assertIn("short evidence", context["context"])

    def test_table_expansion_uses_only_budget_left_after_base_evidence(self) -> None:
        matrix = [["Item", "2024"]] + [[f"row-{index}", "1" * 80] for index in range(40)]
        hits = [
            {
                "chunk_id": "table",
                "text": "table row evidence",
                "payload": {"chunk_id": "table", "table_id": "table-1"},
                "citation": {"table_group_id": "table-1"},
                "_table_matrix": matrix,
            },
            {"chunk_id": "short-1", "text": "later short evidence one", "citation": {}},
            {"chunk_id": "short-2", "text": "later short evidence two", "citation": {}},
        ]

        context = assemble_context(hits, max_chars=800)

        self.assertEqual(
            [item["chunk_id"] for item in context["citations"]],
            ["table", "short-1", "short-2"],
        )
        self.assertIn("later short evidence two", context["context"])
        self.assertLessEqual(len(context["context"]), 800)

    def test_search_request_deduplicates_chunk_ids(self) -> None:
        retriever = object.__new__(QdrantRetriever)
        request = QueryRequest(query="generic evidence request", top_k=3)
        retriever.resolve_request = Mock(return_value=request)
        retriever.search = Mock(
            return_value=[
                {"chunk_id": "shared", "score": 0.9, "payload": {"chunk_id": "shared"}},
                {"chunk_id": "shared", "score": 0.8, "payload": {"chunk_id": "shared"}},
                {"chunk_id": "other", "score": 0.7, "payload": {"chunk_id": "other"}},
            ]
        )
        retriever.last_search_meta = {}

        hits = retriever.search_request(request, mode="dense")

        self.assertEqual([hit["chunk_id"] for hit in hits], ["shared", "other"])



if __name__ == "__main__":
    unittest.main()
