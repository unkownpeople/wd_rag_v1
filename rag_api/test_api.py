from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from rag_api.prompts import (
    build_messages,
    build_review_messages,
    extract_citation_ids,
    prune_unsupported_claims,
    validate_answer_facts,
    validate_citations,
)
from rag_api.llm import (
    DeepSeekGenerator,
    DeepSeekPlanner,
    LLMRequestError,
    _request_error_detail,
    extract_usage,
)
from rag_api.schemas import QueryBody
from rag_api.service import RAGService
from rag_api.settings import Settings
from embeddings.contracts import QueryRequest


class FakeRetriever:
    def __init__(self) -> None:
        self.queries = []
        self.last_search_meta = {}
        self.hit = {
            "rank": 1,
            "score": 0.91,
            "dense_score": 0.91,
            "chunk_id": "chunk_demo",
            "text": "Apple 2024 total net sales were 391,035 million dollars.",
            "payload": {
                "chunk_id": "chunk_demo",
                "chunk_text": "Apple 2024 total net sales were 391,035 million dollars.",
                "document_id": "apple_2024_10k",
                "company_id": "apple",
                "company_name": "Apple Inc.",
                "fiscal_year": 2024,
                "source_format": "pdf",
                "source_file": "apple_2024_10k.pdf",
                "page_start": 25,
                "page_end": 25,
                "table_id": "apple_2024_10k_p025_t01",
            },
            "citation": {
                "document_id": "apple_2024_10k",
                "company_id": "apple",
                "company_name": "Apple Inc.",
                "fiscal_year": 2024,
                "source_format": "pdf",
                "source_file": "apple_2024_10k.pdf",
                "page": 25,
                "page_start": 25,
                "page_end": 25,
                "table_id": "apple_2024_10k_p025_t01",
            },
        }

    def search_request(self, request, *, mode="hybrid"):
        self.queries.append({"query": request.query, "top_k": request.top_k})
        hit = dict(self.hit)
        hit["chunk_id"] = f"chunk_{len(self.queries)}"
        hit["text"] = f"{request.query}: {self.hit['text']}"
        hit["payload"] = {
            **self.hit["payload"],
            "chunk_id": hit["chunk_id"],
            "chunk_text": hit["text"],
        }
        self.last_search_meta = {"candidate_count": 1, "effective_top_k": 1}
        return [hit]

    def resolve_request(self, request):
        return request

    def close(self):
        pass


class FakeGenerator:
    def __init__(self) -> None:
        self.calls = []
        self.last_usage = {}
        self.last_finish_reason = None

    def generate(self, messages):
        self.messages = messages
        self.calls.append(messages)
        self.last_usage = {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
        }
        self.last_finish_reason = "stop"
        if len(self.calls) == 1:
            return "Answer draft.[S1]"
        return "Reviewed final answer.[S1]"


class FakePlanner:
    last_usage = {"prompt_tokens": 10, "completion_tokens": 4}

    def plan(self, messages):
        self.messages = messages
        return {
            "intent": "find both requested parts",
            "entities": ["Example Company"],
            "time_scope": "requested period",
            "retrieval_tasks": [
                {
                    "requirement": {"topic": "first requested part", "metrics": []},
                    "queries": ["first evidence query"],
                    "statement_scope": "",
                },
                {
                    "requirement": {"topic": "second requested part", "metrics": []},
                    "queries": ["second evidence query"],
                    "statement_scope": "",
                },
            ],
            "needs_calculation": False,
            "clarification_needed": False,
        }


class FailingPlanner:
    def plan(self, messages):
        raise ValueError("invalid plan")


class ReviewFailureGenerator(FakeGenerator):
    def generate(self, messages):
        if self.calls:
            raise LLMRequestError("review failed")
        return super().generate(messages)


class InvalidReviewGenerator(FakeGenerator):
    def generate(self, messages):
        value = super().generate(messages)
        return value if len(self.calls) == 1 else "Invalid review.[S9]"


class InvalidDraftAndReviewGenerator(FakeGenerator):
    def generate(self, messages):
        super().generate(messages)
        return "Invalid output.[S9]"


class MixedFactGenerator(FakeGenerator):
    def generate(self, messages):
        super().generate(messages)
        return "Supported sales were 391,035.[S1]\nUnsupported sales were 999,999.[S1]"


class RagApiTests(unittest.TestCase):
    def test_planner_candidate_pool_preserves_explicit_document_budget(self):
        explicit_document = QueryRequest(
            query="fixed evaluation question",
            top_k=5,
            filters={"company_id": "tcs", "document_id": "tcs_2024_annual_report"},
        )
        frontend_query = QueryRequest(
            query="塔塔咨询服务公司2024财年的总收入是多少？",
            top_k=5,
            filters={"company_id": "tcs", "fiscal_year": 2024},
        )

        self.assertEqual(RAGService._planner_candidate_top_k(explicit_document), 10)
        self.assertEqual(RAGService._planner_candidate_top_k(frontend_query), 15)

    def test_task_selection_deduplicates_full_tables_and_exact_text(self):
        matrix = [["Item", "2024"], ["Revenue", "100"]]
        first = {
            "chunk_id": "table-row-1",
            "text": "Revenue 100",
            "payload": {"chunk_id": "table-row-1", "table_id": "table-1"},
            "citation": {"table_group_id": "table-1"},
            "_table_matrix": matrix,
        }
        same_table = {
            "chunk_id": "table-row-2",
            "text": "Operating income 20",
            "payload": {"chunk_id": "table-row-2", "table_id": "table-1"},
            "citation": {"table_group_id": "table-1"},
            "_table_matrix": matrix,
        }
        same_text = {
            "chunk_id": "duplicate-text",
            "text": "Revenue 100",
            "payload": {"chunk_id": "duplicate-text"},
        }

        selected = RAGService._select_task_hits(
            [("T1", [first]), ("T2", [same_table]), ("T3", [same_text])],
            limit=6,
        )

        self.assertEqual([hit["chunk_id"] for hit in selected], ["table-row-1"])
        self.assertEqual(selected[0]["_retrieval_task_ids"], ["T1", "T2", "T3"])

    def test_task_query_fusion_is_query_order_independent_and_deduplicated(self):
        first = [
            {"chunk_id": "a", "score": 0.9, "payload": {"chunk_id": "a"}},
            {"chunk_id": "shared", "score": 0.8, "payload": {"chunk_id": "shared"}},
        ]
        second = [
            {"chunk_id": "b", "score": 0.9, "payload": {"chunk_id": "b"}},
            {"chunk_id": "shared", "score": 0.8, "payload": {"chunk_id": "shared"}},
        ]

        forward = RAGService._fuse_task_queries([first, second])
        reverse = RAGService._fuse_task_queries([second, first])

        self.assertEqual(forward[0]["chunk_id"], "shared")
        self.assertEqual([hit["chunk_id"] for hit in forward], [hit["chunk_id"] for hit in reverse])
        self.assertEqual(len({hit["chunk_id"] for hit in forward}), len(forward))

    def test_task_priority_prefers_exact_numeric_table_row(self):
        task = {
            "requirement": {
                "topic": "经营活动现金流",
                "metrics": ["经营活动现金流"],
                "relation": "整体经营表现",
            }
        }
        header = {
            "chunk_id": "header",
            "text": (
                "row_number=4；column_1=Adjustments to reconcile net income to "
                "cash generated by operating activities:；2024=空值；2023=空值"
            ),
        }
        net_income = {
            "chunk_id": "net-income",
            "text": "row_number=3；column_1=Net income；2024=93,736；2023=96,995",
        }
        target = {
            "chunk_id": "operating-cash",
            "text": (
                "row_number=15；column_1=Cash generated by operating activities；"
                "2024=118,254；2023=110,543"
            ),
        }

        selected = RAGService._prioritize_task_hits([net_income, header, target], task)

        self.assertEqual(selected[0]["chunk_id"], "operating-cash")
        self.assertEqual(selected[1]["chunk_id"], "header")

    def test_task_priority_promotes_full_statement_matching_requested_metrics(self):
        task = {
            "requirement": {
                "topic": "资产负债表结构",
                "metrics": ["资产", "负债", "股东权益"],
            }
        }
        generic = {"chunk_id": "note", "text": "The notes discuss balance sheet dates."}
        statement = {
            "chunk_id": "balance-sheet",
            "text": (
                "BALANCE SHEETS Total assets 411,976 Total liabilities 205,753 "
                "Total stockholders' equity 206,223"
            ),
        }

        selected = RAGService._prioritize_task_hits([generic, statement], task)

        self.assertEqual(selected[0]["chunk_id"], "balance-sheet")

    def test_business_mix_expands_to_annual_report_product_breakdown_label(self):
        queries = RAGService._augment_task_queries(
            request_query="Apple 的主要业务收入来源有哪些，各自表现如何？",
            task={
                "requirement": {
                    "topic": "主要业务收入来源",
                    "metrics": ["净销售额"],
                    "group_by": ["产品类别"],
                },
                "queries": ["annual revenue by product line"],
            },
        )

        self.assertIn("net sales disaggregated by significant products and services", queries[0])

    def test_grouped_business_mix_prefers_full_category_breakdown(self):
        task = {
            "requirement": {
                "topic": "主要业务收入来源",
                "metrics": ["净销售额"],
                "group_by": ["产品类别"],
            }
        }
        total_row = {
            "chunk_id": "total-row",
            "text": "row_number=4；column_1=Total net sales；2024=391,035",
        }
        product_breakdown = {
            "chunk_id": "product-breakdown",
            "text": (
                "Net sales disaggregated by significant products and services. "
                "iPhone 201,183 Mac 29,984 Services 96,169 Total net sales 391,035"
            ),
        }

        selected = RAGService._prioritize_task_hits([total_row, product_breakdown], task)

        self.assertEqual(selected[0]["chunk_id"], "product-breakdown")

    def test_broad_fallback_diversifies_statement_families(self):
        hits = [
            {"chunk_id": "cash-1", "citation": {"statement_family": "cash_flow"}},
            {"chunk_id": "cash-2", "citation": {"statement_family": "cash_flow"}},
            {"chunk_id": "income", "citation": {"statement_family": "income_statement"}},
            {"chunk_id": "ratio", "citation": {"statement_family": "ratio"}},
        ]

        selected = RAGService._diversify_statement_families(hits)

        self.assertEqual(
            [hit["chunk_id"] for hit in selected],
            ["cash-1", "income", "ratio", "cash-2"],
        )
        self.assertTrue(
            RAGService._fallback_needs_family_diversity("请结合收入、盈利能力和现金流说明")
        )
        self.assertFalse(RAGService._fallback_needs_family_diversity("净利润是多少"))

    def test_broad_fallback_prefers_consolidated_financial_statement(self):
        hits = [
            {
                "chunk_id": "cash-standalone",
                "citation": {"statement_family": "cash_flow", "statement_scope": "standalone"},
            },
            {
                "chunk_id": "cash-consolidated",
                "citation": {"statement_family": "cash_flow", "statement_scope": "consolidated"},
            },
            {
                "chunk_id": "income",
                "citation": {"statement_family": "income_statement", "statement_scope": "consolidated"},
            },
        ]

        selected = RAGService._diversify_statement_families(hits)

        self.assertEqual(
            [hit["chunk_id"] for hit in selected],
            ["cash-consolidated", "income", "cash-standalone"],
        )

    def test_task_scope_prefers_consolidated_evidence(self):
        hits = [
            {"chunk_id": "standalone", "citation": {"statement_scope": "standalone"}},
            {"chunk_id": "consolidated", "citation": {"statement_scope": "consolidated"}},
        ]

        selected = RAGService._filter_task_scope(hits, "合并")

        self.assertEqual([hit["chunk_id"] for hit in selected], ["consolidated"])
        self.assertEqual(RAGService._filter_task_scope(hits, "未知"), hits)

    def test_task_scope_does_not_drop_product_breakdown_with_unknown_scope(self):
        hits = [
            {"chunk_id": "income", "citation": {"statement_scope": "consolidated"}},
            {"chunk_id": "product-mix", "citation": {"statement_scope": "unknown"}},
        ]

        selected = RAGService._filter_task_scope(
            hits,
            "合并",
            {"topic": "主要业务收入来源", "metrics": ["净销售额"]},
        )

        self.assertEqual(selected, hits)

    def test_prompt_citation_ids_are_bounded_by_evidence(self):
        self.assertEqual(extract_citation_ids("事实[S1][S1]，错误[S9]"), ["S1", "S9"])
        assembled = {"citations": [{"evidence_id": "S1"}]}
        self.assertFalse(validate_citations("事实[S1][S9]", assembled)["valid"])

    def test_fact_validation_normalizes_numbers_and_dates(self):
        assembled = {
            "citations": [{"evidence_id": "S1", "fiscal_year": 2024}],
            "evidence_texts": {
                "S1": "Total net sales were 1,200. The year ended 2024-09-28.",
            },
        }

        valid = validate_answer_facts("金额是 1200，日期为 2024年9月28日。[S1]", assembled)
        invalid = validate_answer_facts("金额是 999。[S1]", assembled)

        self.assertTrue(valid["valid"])
        self.assertFalse(invalid["valid"])
        self.assertEqual(
            prune_unsupported_claims("金额是 1200。[S1]\n金额是 999。[S1]", invalid),
            "金额是 1200。[S1]",
        )

    def test_service_retrieval_only_does_not_need_llm(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
        )
        result = RAGService(settings, retriever=FakeRetriever()).query(
            QueryBody(query="Apple 2024 total net sales", generate=False)
        )
        self.assertTrue(result.answerable)
        self.assertEqual(result.citation_ids, ["S1"])
        self.assertEqual(result.generation["status"], "retrieval_only")

    def test_generation_unions_original_query_with_planner_tasks(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
        )
        planner = FakePlanner()
        retriever = FakeRetriever()
        result = RAGService(
            settings,
            retriever=retriever,
            generator=FakeGenerator(),
            planner=planner,
        ).query(
            QueryBody(
                query="Explain both requested parts",
                company_id="example",
                generate=True,
            )
        )
        self.assertTrue(result.answerable)
        self.assertEqual(result.retrieval.route, "planner")
        self.assertEqual(result.retrieval.planner["status"], "used")
        self.assertEqual(result.retrieval.resolved_filters, {"company_id": "example"})
        self.assertEqual(result.retrieval.retrieval_tasks[0]["evidence_ids"], ["S2"])
        self.assertEqual(result.retrieval.retrieval_tasks[1]["evidence_ids"], ["S3"])
        self.assertEqual(result.evidence[0].retrieval_task_ids, ["ORIGINAL"])
        self.assertEqual(result.evidence[1].retrieval_task_ids, ["T1"])
        self.assertEqual(result.evidence[2].retrieval_task_ids, ["T2"])
        self.assertEqual(result.retrieval.hit_count, 3)
        self.assertTrue(hasattr(planner, "messages"))
        self.assertEqual(result.retrieval.planner["retrieval_tasks"][0]["queries"], ["first evidence query"])
        self.assertEqual([item["top_k"] for item in retriever.queries], [15, 15, 15])
        self.assertEqual(retriever.queries[0]['query'], 'Explain both requested parts')

    def test_planner_failure_runs_original_query_once(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
        )
        retriever = FakeRetriever()
        result = RAGService(
            settings,
            retriever=retriever,
            generator=FakeGenerator(),
            planner=FailingPlanner(),
        ).query(QueryBody(query="generic original question", company_id="example"))

        self.assertTrue(result.answerable)
        self.assertEqual(
            retriever.queries,
            [{"query": "generic original question", "top_k": 40}],
        )
        self.assertEqual(result.retrieval.planner["status"], "fallback")
        self.assertEqual(result.retrieval.planner["fallback_top_k"], 40)

    def test_service_generates_and_validates_citation(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
            planner_enabled=False,
        )
        generator = FakeGenerator()
        result = RAGService(
            settings,
            retriever=FakeRetriever(),
            generator=generator,
        ).query(QueryBody(query="Apple 2024 total net sales"))
        self.assertTrue(result.answerable)
        self.assertTrue(result.citation_valid)
        self.assertEqual(result.citation_ids, ["S1"])
        self.assertEqual(result.answer, "Answer draft.[S1]")
        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(result.generation["finish_reason"], "stop")
        self.assertEqual(result.generation["selected_output"], "single")
        self.assertTrue(result.generation["citation_check"]["valid"])

    def test_single_generation_completes_before_second_call_failure(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
            planner_enabled=False,
        )
        result = RAGService(
            settings,
            retriever=FakeRetriever(),
            generator=ReviewFailureGenerator(),
        ).query(QueryBody(query="Apple 2024 total net sales"))

        self.assertTrue(result.answerable)
        self.assertEqual(result.answer, "Answer draft.[S1]")
        self.assertEqual(result.generation["status"], "generated")
        self.assertEqual(result.generation["selected_output"], "single")

    def test_single_generation_returns_first_output(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
            planner_enabled=False,
        )
        result = RAGService(
            settings,
            retriever=FakeRetriever(),
            generator=InvalidReviewGenerator(),
        ).query(QueryBody(query="Apple 2024 total net sales"))

        self.assertTrue(result.answerable)
        self.assertEqual(result.answer, "Answer draft.[S1]")
        self.assertEqual(result.generation["status"], "generated")
        self.assertNotIn("review", result.generation)

    def test_invalid_draft_and_review_are_not_displayed(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
            planner_enabled=False,
        )
        result = RAGService(
            settings,
            retriever=FakeRetriever(),
            generator=InvalidDraftAndReviewGenerator(),
        ).query(QueryBody(query="Apple 2024 total net sales"))

        self.assertFalse(result.answerable)
        self.assertEqual(result.generation["selected_output"], "none")

    def test_service_retains_numeric_claims_with_observation(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
            planner_enabled=False,
        )
        result = RAGService(
            settings,
            retriever=FakeRetriever(),
            generator=MixedFactGenerator(),
        ).query(QueryBody(query="Apple 2024 total net sales"))

        self.assertTrue(result.answerable)
        self.assertEqual(result.answer, "Supported sales were 391,035.[S1]\nUnsupported sales were 999,999.[S1]")
        self.assertEqual(result.generation["status"], "generated")
        self.assertFalse(result.generation["fact_check"]["valid"])
        self.assertEqual(result.generation["fact_check"]["validation_mode"], "observe")

    def test_risk_question_stops_before_retrieval_or_generation(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
        )
        retriever = FakeRetriever()
        result = RAGService(
            settings,
            retriever=retriever,
            generator=FakeGenerator(),
        ).query(QueryBody(query="Apple 的目标价是多少？"))

        self.assertFalse(result.answerable)
        self.assertEqual(result.generation["status"], "policy_refusal")
        self.assertEqual(retriever.queries, [])

    def test_ambiguous_scope_is_clarified_before_retrieval(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
        )
        retriever = FakeRetriever()
        result = RAGService(settings, retriever=retriever, generator=FakeGenerator()).query(
            QueryBody(query="2024 年的收入是多少？")
        )

        self.assertFalse(result.answerable)
        self.assertEqual(result.generation["status"], "clarification_needed")
        self.assertEqual(result.generation["refusal"]["reason"], "company_required")
        self.assertEqual(retriever.queries, [])

    def test_cross_company_scope_is_clarified_before_retrieval(self):
        settings = Settings(
            qdrant_path=Path("."),
            chunk_dir=Path("."),
            embedding_model_path=Path("."),
            embedding_tokenizer_path=Path("."),
        )
        retriever = FakeRetriever()
        result = RAGService(settings, retriever=retriever, generator=FakeGenerator()).query(
            QueryBody(query="请比较 Apple 和 Microsoft 2023 年收入")
        )

        self.assertFalse(result.answerable)
        self.assertEqual(result.generation["status"], "clarification_needed")
        self.assertEqual(result.generation["refusal"]["reason"], "multiple_companies")
        self.assertEqual(retriever.queries, [])

    def test_planner_context_enters_answer_and_review_messages(self):
        assembled = {
            "context": "[S1] Generic evidence.",
            "citations": [{"evidence_id": "S1"}],
        }
        planner_meta = {
            "status": "used",
            "route": "planner",
            "intent": "answer every requested part",
            "retrieval_tasks": [
                {"task_id": "T1", "requirement": {"topic": "first part"}, "queries": ["first query"]},
                {"task_id": "T2", "requirement": {"topic": "second part"}, "queries": ["second query"]},
            ],
        }
        answer_messages = build_messages(
            question="Explain both requested parts.",
            assembled=assembled,
            planner_meta=planner_meta,
        )
        review_messages = build_review_messages(
            question="Explain both requested parts.",
            draft="Draft response.[S1]",
            assembled=assembled,
            planner_meta=planner_meta,
        )
        self.assertIn('"retrieval_tasks"', answer_messages[1]["content"])
        self.assertIn("first part", answer_messages[1]["content"])
        self.assertIn("Draft response.[S1]", review_messages[1]["content"])
        self.assertIn("Generic evidence.", review_messages[1]["content"])

    def test_usage_extracts_cache_fields(self):
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_cache_hit_tokens": 60,
            "prompt_cache_miss_tokens": 40,
        }
        parsed = extract_usage(usage)
        self.assertEqual(parsed["prompt_cache_total_tokens"], 100)
        self.assertEqual(parsed["prompt_cache_hit_ratio"], 0.6)

    def test_provider_error_detail_keeps_status_without_response_body(self):
        error = RuntimeError("provider body must stay hidden")
        error.status_code = 429
        error.code = "rate_limit"

        self.assertEqual(
            _request_error_detail(error),
            "RuntimeError status=429 code=rate_limit",
        )

    def test_planner_accepts_generic_retrieval_contract(self):
        plan = DeepSeekPlanner._parse_plan(
            '{"intent":"find requested evidence","entities":["Example Company"],"time_scope":"requested period",'
            '"retrieval_tasks":[{"requirement":{"topic":"requested evidence"},'
            '"queries":["generic evidence query"],"statement_scope":""}],"needs_calculation":false,'
            '"clarification_needed":false}'
        )
        self.assertEqual(plan["retrieval_tasks"][0]["queries"], ["generic evidence query"])

    def test_planner_requests_json_object_response_format(self):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(
                content=(
                    '{"intent":"find evidence","retrieval_tasks":['
                    '{"requirement":{"topic":"requested evidence"},'
                    '"queries":["generic evidence query"]}]}'
                )
            )
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(message=message, finish_reason="stop")],
            )

        planner = object.__new__(DeepSeekPlanner)
        planner.settings = SimpleNamespace(
            deepseek_planner_model="deepseek-v4-flash",
            deepseek_thinking_mode="disabled",
            llm_temperature=0.0,
            planner_max_tokens=500,
        )
        planner._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        planner.last_usage = {}
        planner.last_finish_reason = None

        planner.plan([{"role": "user", "content": "question"}])

        self.assertEqual(captured["response_format"], {"type": "json_object"})
        self.assertEqual(captured["extra_body"], {"thinking": {"type": "disabled"}})

    def test_generator_disables_flash_thinking(self):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="OK"),
                        finish_reason="stop",
                    )
                ],
            )

        generator = object.__new__(DeepSeekGenerator)
        generator.settings = SimpleNamespace(
            deepseek_model="deepseek-v4-flash",
            deepseek_thinking_mode="disabled",
            llm_temperature=0.0,
            llm_max_tokens=500,
        )
        generator._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        generator.last_usage = {}
        generator.last_finish_reason = None

        self.assertEqual(generator.generate([{"role": "user", "content": "question"}]), "OK")
        self.assertEqual(captured["extra_body"], {"thinking": {"type": "disabled"}})

    def test_planner_rejects_database_instructions(self):
        with self.assertRaises(Exception):
            DeepSeekPlanner._parse_plan(
                '{"retrieval_tasks":[{"requirement":{"topic":"requested evidence"},'
                '"queries":["generic evidence query"]}],"intent":"use Qdrant collection"}'
            )


if __name__ == "__main__":
    unittest.main()
