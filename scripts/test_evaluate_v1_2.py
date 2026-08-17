from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import evaluate_v1_2
from scripts.evaluate_v1_2 import concept_supported, numeric_present, term_supported


class EvaluateV12Tests(unittest.TestCase):
    def test_english_concept_terms_allow_different_word_order(self) -> None:
        evidence = "LTM attrition in IT services was 12.5% across 601,546 employees."

        self.assertTrue(term_supported("IT services attrition", evidence))

    def test_empty_concept_terms_use_complete_task_context(self) -> None:
        concept = {"name": "整体经营表现", "evidence_terms": []}
        task_context = "现金流 经营活动现金流 整体经营表现"

        self.assertTrue(concept_supported(concept, "", task_context))

    def test_empty_concept_terms_do_not_fail_without_planner_tasks(self) -> None:
        concept = {"name": "整体经营表现", "evidence_terms": []}

        self.assertTrue(concept_supported(concept, "", ""))

    def test_evidence_percentage_column_may_omit_repeated_percent_symbol(self) -> None:
        evidence = "EBIT margin 59,311 24.6 9.4"

        self.assertTrue(
            numeric_present(
                "24.6%",
                evidence,
                allow_percentage_without_symbol=True,
            )
        )
        self.assertFalse(numeric_present("24.6%", evidence))

    def test_all_error_run_uses_failed_report_path(self) -> None:
        class FailingService:
            def __init__(self, settings):
                pass

            def query(self, body):
                raise RuntimeError("provider unavailable")

            def close(self):
                pass

        contract = {"source_dataset": "test_set_v1_1.jsonl"}
        cases = [
            {
                "case_id": "case-1",
                "company": "Example",
                "query": "Question",
                "filters": {"company_id": "example"},
            }
        ]
        project_tmp = Path(__file__).resolve().parents[1] / "tmp"
        project_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_tmp) as directory:
            failed_path = Path(directory) / "failed.json"
            acceptance_path = Path(directory) / "acceptance.json"
            with (
                patch.object(evaluate_v1_2, "RAGService", FailingService),
                patch.object(evaluate_v1_2, "load_contract_and_cases", return_value=(contract, cases)),
                patch.object(evaluate_v1_2, "FAILED_REPORT_PATH", failed_path),
                patch.object(evaluate_v1_2, "REPORT_PATH", acceptance_path),
            ):
                report = evaluate_v1_2.run()

            self.assertFalse(acceptance_path.exists())
            self.assertTrue(failed_path.exists())
            self.assertEqual(json.loads(failed_path.read_text(encoding="utf-8"))["case_count"], 1)
            self.assertEqual(report["rows"][0]["error"], "RuntimeError: provider unavailable")


if __name__ == "__main__":
    unittest.main()
