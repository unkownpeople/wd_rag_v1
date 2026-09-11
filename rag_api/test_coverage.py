import unittest
from types import SimpleNamespace
from .coverage import inspect_coverage


class CoverageTests(unittest.TestCase):
    def probe(self, text, metric='operating income'):
        tasks = [{'task_id': 'T1', 'requirement': {'metrics': [metric]}}]
        evidence = [SimpleNamespace(text=text, evidence_id='S1', retrieval_task_ids=['T1'])]
        return inspect_coverage(tasks, evidence, 'FY2022—2024')[0]

    def test_missing_metric_despite_task_evidence(self):
        self.assertEqual(self.probe('2022 2023 2024 Revenue 100 200 300')['status'], 'gap')

    def test_year_gap(self):
        row = self.probe('2024 2023 Operating income 100 200')
        self.assertEqual(row['missing_years'], [2022])

    def test_full_candidate_is_not_semantic_proof(self):
        self.assertEqual(self.probe('2024 2023 2022 Operating income 100 200 300')['status'], 'candidate_covered')

    def test_unsupported_label_is_explicitly_unverified(self):
        self.assertEqual(self.probe('all data', '复杂自定义经营指标')['status'], 'unverified')

    def test_definition_without_number_is_gap(self):
        self.assertEqual(self.probe('operating income is a measure')['status'], 'gap')

    def test_year_labels_alone_are_not_numeric_evidence(self):
        self.assertEqual(self.probe('2022 2023 2024 Operating income is a measure')['status'], 'gap')

    def test_relationship_description_is_not_literal_missing_metric(self):
        self.assertEqual(self.probe('Azure grew 29%', 'Azure revenue growth rate')['status'], 'unverified')
        self.assertEqual(self.probe('Operating income 100', 'impact on operating income')['status'], 'unverified')


if __name__ == '__main__':
    unittest.main()
