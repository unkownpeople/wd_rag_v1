import json
import tempfile
import unittest
from pathlib import Path
from embeddings.contracts import infer_filters
from .comparative import segment_comparatives, requested_years
from .audit import write_query_record
from .prompts import validate_answer_facts, repair_arithmetic
from .service import RAGService
from embeddings.contracts import QueryRequest
from .settings import Settings
from .schemas import QueryBody
from .test_api import FakeRetriever, FakePlanner, FakeGenerator

ROOT = Path(__file__).resolve().parents[1]


class CompoundRegressionTests(unittest.TestCase):
    def test_observation_failure_preserves_answer(self):
        from unittest.mock import patch
        generator = FakeGenerator()
        with patch('rag_api.service.validate_answer_facts', side_effect=ValueError('diagnostic error')):
            result = RAGService(Settings(planner_enabled=False), retriever=FakeRetriever(),
                                generator=generator).query(QueryBody(query='Apple FY2024 revenue'))
        self.assertEqual(result.answer, 'Answer draft.[S1]')
        self.assertEqual(len(generator.calls), 1)
        self.assertTrue(result.answerable)
        self.assertIsNone(result.generation['fact_check']['valid'])
        self.assertEqual(result.generation['fact_check']['status'], 'failed')

    def test_api_planned_retrieval_skips_answer_generation(self):
        generator = FakeGenerator()
        service = RAGService(Settings(planner_enabled=True), retriever=FakeRetriever(),
                             planner=FakePlanner(), generator=generator)
        result = service.query(QueryBody(query='Apple FY2024 revenue', generate=False, plan_retrieval=True))
        self.assertEqual(result.generation['status'], 'retrieval_only')
        self.assertEqual(result.retrieval.planner['status'], 'used')
        self.assertEqual(generator.calls, [])
        self.assertEqual(result.retrieval.planner['coverage_check']['mode'], 'observe')

    def test_compound_answer_is_preserved_in_single_pass(self):
        class Repairable(FakeGenerator):
            def generate(self, messages):
                self.calls.append(messages)
                self.last_finish_reason = 'stop'
                if len(self.calls) < 3:
                    return '计算：391035 / 391035 * 100 = 99% [S1]\n比率99% [S1]'
                return '计算：391035 / 391035 * 100 = 100% [S1]\n比率100% [S1]'
        generator = Repairable()
        result = RAGService(Settings(planner_enabled=True), retriever=FakeRetriever(),
                            planner=FakePlanner(), generator=generator).query(QueryBody(query='Apple FY2024 收入与利润'))
        self.assertEqual(len(generator.calls), 1)
        self.assertNotIn('final_repair', result.generation)
        self.assertEqual(result.generation['status'], 'generated')
        self.assertEqual(result.answer, '计算：391035 / 391035 * 100 = 99% [S1]\n比率99% [S1]')
        self.assertFalse(result.generation['fact_check']['valid'])

    def test_unicode_minus_is_a_negative_number(self):
        source = {'evidence_texts': {'S1': 'Other (1,606)'}, 'citations': []}
        self.assertTrue(validate_answer_facts('其他−1,606 [S1]', source)['valid'])
        self.assertFalse(validate_answer_facts('其他1,606 [S1]', source)['valid'])
    def test_supported_arithmetic_repair_never_changes_inputs_or_sign(self):
        source = {'evidence_texts': {'S1': '123216 114301'}, 'citations': []}
        result, repairs = repair_arithmetic('计算：123216 - 114301 = 7,915 [S1]', source)
        self.assertEqual(result, '计算：123216 - 114301 = 8915 [S1]')
        self.assertEqual(len(repairs), 1)
        self.assertFalse(repair_arithmetic('计算：123216 - 114301 = -7915 [S1]', source)[1])
        self.assertFalse(repair_arithmetic('计算：999 - 222 = 100 [S1]', source)[1])
    def test_translated_month_keeps_date_semantics(self):
        source = {'evidence_texts': {'S1': 'In July 2022 we completed an assessment.'}, 'citations': []}
        self.assertTrue(validate_answer_facts('2022 年 7 月完成评估 [S1]', source)['valid'])
        self.assertFalse(validate_answer_facts('2022 年 8 月完成评估 [S1]', source)['valid'])
        self.assertFalse(validate_answer_facts('费用7 [S1]', source)['valid'])
    def test_explicit_scope_is_used_for_clarification(self):
        service = object.__new__(RAGService)
        self.assertIsNone(service._clarification_reason(QueryRequest(query='收入是多少', filters={'company_id': 'microsoft', 'fiscal_year': 2023})))
        self.assertEqual(service._clarification_reason(QueryRequest(query='微软收入是多少')), 'fiscal_year_required')

    def test_growth_explanation_prefers_quantified_entity_evidence(self):
        task = {'requirement': {'topic': 'Azure与分部增速差异原因', 'metrics': ['Azure revenue growth'], 'relation': '原因解释'}}
        hits = [{'text': 'Azure is included in the segment.'}, {'text': 'Azure and other cloud services revenue grew 29%.'}]
        self.assertIs(RAGService._prioritize_task_hits(hits, task)[0], hits[1])

    def test_year_range_is_not_a_single_report_filter(self):
        for question in ('Microsoft FY2021—2023 revenue', 'Microsoft fiscal 2021 to 2023 revenue',
                         '微软 FY2021、2022、2023 分部收入'):
            self.assertNotIn('fiscal_year', infer_filters(question, known_years={2021, 2022, 2023}))
        self.assertEqual(infer_filters('Microsoft FY2023 revenue')['fiscal_year'], 2023)
        self.assertEqual(requested_years('FY2021—FY2023'), {2021, 2022, 2023})

    def test_real_segment_table_spans_and_comparable_start_year(self):
        path = ROOT / 'data/processed/annual_reports/v1_single_company_chunks'
        chunks = [json.loads(line) for name in ('microsoft_2021_annual_report', 'microsoft_2022_annual_report', 'microsoft_2023_annual_report')
                  for line in (path / f'{name}.chunks.jsonl').read_text(encoding='utf-8').splitlines()]
        payloads = {p['chunk_id']: p for p in chunks}
        task = {'requirement': {'topic': '报告分部收入和营业利润', 'metrics': ['revenue', 'operating income']}}
        result = segment_comparatives(payloads, task, {'company_id': 'microsoft'}, '微软 FY2021—2023')
        self.assertEqual(result[0]['fiscal_year'], 2023)
        self.assertEqual(result[0]['comparison_years'], [2021, 2022, 2023])
        self.assertEqual(len(result[0]['context_chunk_ids']), 2)
        for value in ('59,728', '54,445', '26,471', '19,094'):
            self.assertIn(value, result[0]['chunk_text'])
        self.assertEqual(segment_comparatives(payloads, task, {'company_id': 'apple'}, 'FY2021—2023'), [])
        restricted = segment_comparatives(payloads, task, {'document_id': 'microsoft_2021_annual_report'}, 'FY2021—2023')
        self.assertTrue(all(p['fiscal_year'] == 2021 for p in restricted))

    def test_definition_task_does_not_receive_financial_table(self):
        self.assertEqual(segment_comparatives({}, {'requirement': {'topic': '分部业务定义'}}, {}, 'FY2021—2023'), [])

    def test_audit_records_utf8_and_redacts_key(self):
        directory = ROOT / '.tmp'
        directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            write_query_record(Path(folder), 'example', {'query': '微软 sk-' + 'a' * 24},
                               {'answer': '有依据的回答'}, 1.25)
            saved = (Path(folder) / 'example.json').read_text(encoding='utf-8')
            self.assertNotIn('sk-' + 'a' * 24, saved)
            self.assertIn('微软', saved)
            self.assertEqual(json.loads(saved)['elapsed_seconds'], 1.25)


if __name__ == '__main__':
    unittest.main()
