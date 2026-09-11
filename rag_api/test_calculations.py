import unittest
from .calculations import canonical_number, verify_equations
from .prompts import validate_answer_facts
from .service import RAGService

class CalculationTests(unittest.TestCase):
    def test_specific_task_never_inherits_unrelated_question_aliases(self):
        queries = RAGService._augment_task_queries(request_query='收入、利润、现金流', task={
            'requirement': {'topic': '购建固定资产支出'}, 'queries': ['payments for property plant equipment']})
        self.assertEqual(queries, ['payments for property plant equipment'])

    def test_tax_note_survives_statement_scope_filter(self):
        hits = [{'citation': {'statement_scope': 'unknown'}}, {'citation': {'statement_scope': 'consolidated'}}]
        self.assertEqual(RAGService._filter_task_scope(hits, '合并', {'topic': '所得税原因解释'}), hits)

    def test_growth_is_verified_from_source_inputs(self):
        source = {'evidence_texts': {'S1': 'FY2024 391,035 FY2023 383,285'}, 'citations': []}
        answer = '同比，计算：(391035 - 383285) / 383285 * 100 = 2.02% [S1]'
        self.assertTrue(validate_answer_facts(answer, source)['valid'])
        self.assertFalse(validate_answer_facts(answer.replace('2.02%', '12.02%'), source)['valid'])

    def test_operand_must_be_in_cited_source(self):
        values, errors = verify_equations('计算：500 - 100 = 400', 'Revenue 391035')
        self.assertFalse(values)
        self.assertTrue(errors)

    def test_adjacent_table_numbers_are_distinct(self):
        source = {'evidence_texts': {'S1': '2024 391,035 2023 383,285'}, 'citations': []}
        self.assertTrue(validate_answer_facts('收入391,035 [S1]', source)['valid'])

    def test_verified_results_can_be_reused_only_with_sources(self):
        source = {'evidence_texts': {'S1': '201183 200583', 'S2': 'unrelated'}, 'citations': []}
        answer = '增量，计算：201183 - 200583 = 600 [S1]\n增量600 [S1]'
        self.assertTrue(validate_answer_facts(answer, source)['valid'])
        self.assertFalse(validate_answer_facts(answer.replace('增量600 [S1]', '增量600 [S2]'), source)['valid'])

    def test_cash_outflow_magnitude_and_spaced_percentage(self):
        source = {'evidence_texts': {'S1': 'Payments (10,708). Tax 24.1 %'}, 'citations': []}
        self.assertTrue(validate_answer_facts('资本支出10,708，税率24.1% [S1]', source)['valid'])
        self.assertFalse(validate_answer_facts('收入10,708 [S1]', source)['valid'])

    def test_list_number_is_not_a_financial_claim(self):
        source = {'evidence_texts': {'S1': 'Tax expense increased'}, 'citations': []}
        self.assertTrue(validate_answer_facts('4. 所得税上升 [S1]', source)['valid'])

    def test_reuse_with_sufficient_subset_of_original_citations(self):
        source = {'evidence_texts': {'S1': '201183', 'S2': '201183 200583', 'S3': 'unrelated'}, 'citations': []}
        answer = '计算：201183 - 200583 = 600 [S1][S2]\n增量600 [S2]'
        self.assertTrue(validate_answer_facts(answer, source)['valid'])
        self.assertFalse(validate_answer_facts(answer.replace('增量600 [S2]', '增量600 [S1]'), source)['valid'])

    def test_accounting_negative_and_decimal_normalization(self):
        self.assertEqual(canonical_number('(-1,606)'), '-1606')
        source = {'evidence_texts': {'S1': 'Other (2,266) ratio 1.140'}, 'citations': []}
        self.assertTrue(validate_answer_facts('其他-2,266，比率1.14 [S1]', source)['valid'])
        self.assertFalse(validate_answer_facts('其他2,266 [S1]', source)['valid'])

    def test_cagr_bounded_power(self):
        values, errors = verify_equations('计算：(121 / 100)^(1/2) * 100 - 100 = 10.00%', '100 121')
        self.assertFalse(errors)
        self.assertIn('number:10%', values)
        self.assertTrue(verify_equations('计算：121 ** 999 = 10', '121')[1])
        self.assertTrue(verify_equations('计算：(999 / 100)^(1/2) = 3.16', '121')[1])

    def test_explicit_unit_conversion(self):
        source = {'evidence_texts': {'S1': 'Operating income effect $3.7 billion', 'S2': '88523 83383'}, 'citations': []}
        answer = '换算为million，计算：3.7 * 1000 = 3700 [S1]\n敏感性增量，计算：88523 - 83383 - 3700 = 1440 [S1][S2]'
        self.assertTrue(validate_answer_facts(answer, source)['valid'])
        self.assertFalse(validate_answer_facts(answer.replace('1440', '2440'), source)['valid'])

    def test_arithmetic_error_includes_recomputed_result(self):
        values, errors = verify_equations('计算：((69274 / 53915)^(1/2) - 1) * 100 = 13.36%', '69274 53915')
        self.assertFalse(values)
        self.assertEqual(errors[0]['computed_result'], '13.35%')

    def test_answer_retains_arithmetic_with_observation(self):
        check = RAGService._validate_answer_output('计算：200 - 150 = 99 [S1]', {
            'evidence_texts': {'S1': '200 150'}, 'citations': [{
                'evidence_id': 'S1', 'source_file': 'report.pdf', 'company_id': 'apple',
                'fiscal_year': 2024, 'source_format': 'pdf', 'statement_family': 'income_statement',
                'statement_scope': 'consolidated', 'period_end': '2024-09-28',
                'unit': 'USD million', 'table_group_id': 'income'}]})
        self.assertEqual(check['status'], 'valid')
        self.assertEqual(check['answer'], '计算：200 - 150 = 99 [S1]')
        self.assertEqual(check['fact_check']['unsupported_claims'][0]['calculation_errors'][0]['computed_result'], '50')

    def test_same_line_equations_share_trailing_citation(self):
        source = {'evidence_texts': {'S1': '200 150'}, 'citations': []}
        answer = '增量，计算：200 - 150 = 50；占比，计算：50 / 200 * 100 = 25% [S1]'
        self.assertTrue(validate_answer_facts(answer, source)['valid'])
        self.assertFalse(validate_answer_facts(answer.replace('= 50；', '= 60；'), source)['valid'])
        self.assertFalse(validate_answer_facts(answer.replace('；', '；\n'), source)['valid'])

if __name__ == '__main__':
    unittest.main()
