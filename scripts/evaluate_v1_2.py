from __future__ import annotations

"""V1.2 评测器：继承 V1.1 数据集，只修正评测契约。"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_api.schemas import QueryBody
from rag_api.service import RAGService
from rag_api.settings import Settings


CONTRACT_PATH = ROOT / "data" / "evaluation" / "test_set_v1_2.jsonl"
REPORT_PATH = ROOT / "data" / "evaluation" / "test_set_v1_2.acceptance.json"
FAILED_REPORT_PATH = ROOT / "data" / "evaluation" / "test_set_v1_2.failed_run.json"
RETRIEVAL_REPORT_PATH = ROOT / "data" / "evaluation" / "test_set_v1_2.retrieval.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_contract_and_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _load_jsonl(CONTRACT_PATH)
    contracts = [row for row in rows if row.get("record_type") == "contract"]
    if len(contracts) != 1:
        raise ValueError("V1.2 评测契约必须且只能包含一条版本声明")
    contract = contracts[0]
    source_name = str(contract.get("source_dataset") or "").strip()
    if not source_name or Path(source_name).name != source_name:
        raise ValueError("source_dataset 必须是 data/evaluation 下的文件名")
    source_cases = {
        str(row.get("case_id")): row
        for row in _load_jsonl(CONTRACT_PATH.parent / source_name)
    }
    cases: list[dict[str, Any]] = []
    for override in (row for row in rows if row.get("record_type") == "case"):
        source_case_id = str(override.get("source_case_id") or "")
        if source_case_id not in source_cases:
            raise ValueError(f"V1.2 引用了不存在的 V1.1 用例：{source_case_id}")
        case = {**source_cases[source_case_id], **override}
        case.pop("expected_facts", None)
        case.pop("expected_families", None)
        cases.append(case)
    if len(cases) != len(source_cases) or len({case["source_case_id"] for case in cases}) != len(cases):
        raise ValueError("V1.2 必须且只能覆盖每条 V1.1 用例一次")
    return contract, cases


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", text)


def numeric_value(value: Any) -> tuple[Decimal, bool] | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not re.search(r"\d", text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text.replace(",", ""))
    if cleaned.count(".") > 1 or cleaned in {"", "-", ".", "-."}:
        return None
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    number = -number if negative and number > 0 else number
    return number, "%" in text


def numeric_present(
    fact: str,
    text: str,
    *,
    allow_percentage_without_symbol: bool = False,
) -> bool:
    expected = numeric_value(fact)
    if expected is None:
        return False
    candidates = re.findall(r"\(?-?\d[\d,]*(?:\.\d+)?\)?%?", unicodedata.normalize("NFKC", text))
    for candidate in candidates:
        actual = numeric_value(candidate)
        if actual == expected:
            return True
        if (
            allow_percentage_without_symbol
            and actual is not None
            and expected[1]
            and not actual[1]
            and actual[0] == expected[0]
        ):
            return True
    return False


def text_present(fact: str, text: str) -> bool:
    return normalize_text(fact) in normalize_text(text)


def term_supported(term: str, evidence_text: str) -> bool:
    if text_present(term, evidence_text):
        return True
    term_tokens = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", term).casefold())
    if len(term_tokens) < 2:
        return False
    evidence_tokens = set(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", evidence_text).casefold()))
    return all(token in evidence_tokens for token in term_tokens)


def concept_supported(concept: dict[str, Any], evidence_text: str, task_context: str) -> bool:
    terms = [str(value) for value in concept.get("evidence_terms") or [] if str(value).strip()]
    if terms:
        return all(term_supported(term, evidence_text) for term in terms)
    name = str(concept.get("name") or "").strip()
    return bool(name and (not task_context.strip() or text_present(name, task_context)))


def run(*, retrieval_only: bool = False) -> dict[str, Any]:
    contract, cases = load_contract_and_cases()
    settings = Settings()
    service = RAGService(settings)
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            row: dict[str, Any] = {
                "case_id": case["case_id"],
                "company": case["company"],
                "query": case["query"],
                "error": None,
            }
            try:
                body = QueryBody(
                    query=case["query"],
                    top_k=5,
                    mode="hybrid",
                    generate=True,
                    **dict(case.get("filters") or {}),
                )
                if retrieval_only:
                    request, hits, _, evidence = service.retrieve(body)
                    retrieval = service._retrieval_info(request=request, hits=hits, mode=body.mode)
                    answer = ""
                    answerable = False
                    citation_valid = False
                    citation_ids: list[str] = []
                    generation: dict[str, Any] = {"status": "retrieval_only_evaluation"}
                else:
                    response = service.query(body)
                    evidence = response.evidence
                    retrieval = response.retrieval
                    answer = response.answer
                    answerable = response.answerable
                    citation_valid = response.citation_valid
                    citation_ids = response.citation_ids
                    generation = response.generation
                evidence_text = "\n".join(item.text for item in evidence)
                expected_values = list(case.get("expected_values") or [])
                acceptable_values = list(case.get("acceptable_values") or [])
                numeric_evidence = {
                    fact: numeric_present(
                        fact,
                        evidence_text,
                        allow_percentage_without_symbol=True,
                    )
                    for fact in expected_values
                }
                numeric_answer = {fact: numeric_present(fact, answer) for fact in expected_values}
                acceptable_evidence = {
                    f"group_{index}": any(
                        numeric_present(
                            value,
                            evidence_text,
                            allow_percentage_without_symbol=True,
                        )
                        for value in group
                    )
                    for index, group in enumerate(acceptable_values, 1)
                }
                acceptable_answer = {
                    f"group_{index}": any(numeric_present(value, answer) for value in group)
                    for index, group in enumerate(acceptable_values, 1)
                }
                task_context = "\n".join(
                    " ".join(
                        str(value or "")
                        for value in (
                            (task.get("requirement") or {}).get("topic"),
                            " ".join(str(item) for item in (task.get("requirement") or {}).get("metrics") or []),
                            (task.get("requirement") or {}).get("relation"),
                            " ".join(str(item) for item in (task.get("requirement") or {}).get("group_by") or []),
                        )
                    )
                    for task in retrieval.retrieval_tasks
                )
                concept_checks = {
                    str(concept.get("name") or f"concept_{index}"): concept_supported(
                        concept, evidence_text, task_context
                    )
                    for index, concept in enumerate(case.get("expected_concepts") or [], 1)
                }
                task_coverage = all(
                    bool(task.get("evidence_ids"))
                    for task in retrieval.retrieval_tasks
                ) if retrieval.planner.get("status") == "used" else True
                expected_company = str((case.get("filters") or {}).get("company_id") or "")
                evidence_companies = {
                    str(item.citation.company_id or "") for item in evidence
                }
                company_isolated = bool(evidence_companies) and evidence_companies == {expected_company}
                citation_families = {
                    str(item.citation.statement_family or "") for item in evidence
                }
                required_families = set(case.get("required_evidence_families") or [])
                family_coverage = required_families.issubset(citation_families)
                evaluator_contract_status = bool(
                    isinstance(case.get("expected_values"), list)
                    and isinstance(case.get("acceptable_values"), list)
                    and isinstance(case.get("expected_concepts"), list)
                    and isinstance(case.get("required_evidence_families"), list)
                )
                layers: dict[str, bool] = {
                    "retrieval_coverage": bool(
                        company_isolated
                        and all(numeric_evidence.values())
                        and all(acceptable_evidence.values())
                        and all(concept_checks.values())
                        and family_coverage
                        and task_coverage
                    ),
                    "evaluator_contract_status": evaluator_contract_status,
                }
                if not retrieval_only:
                    layers.update(
                        {
                            "grounded_answer": bool(
                                answerable
                                and citation_valid
                                and citation_ids
                                and all(numeric_answer.values())
                                and all(acceptable_answer.values())
                            ),
                            "answer_completeness": bool(
                                answerable
                                and all(numeric_answer.values())
                                and all(acceptable_answer.values())
                                and task_coverage
                            ),
                        }
                    )
                row.update(
                    {
                        "answer": answer if not retrieval_only else None,
                        "evidence_count": len(evidence),
                        "evidence": [
                            {
                                "evidence_id": item.evidence_id,
                                "text": item.text,
                                "retrieval_task_ids": item.retrieval_task_ids,
                                "citation": item.citation.model_dump(),
                            }
                            for item in evidence
                        ],
                        "numeric_in_evidence": numeric_evidence,
                        "numeric_in_answer": numeric_answer,
                        "acceptable_in_evidence": acceptable_evidence,
                        "acceptable_in_answer": acceptable_answer,
                        "concept_support": concept_checks,
                        "family_coverage": family_coverage,
                        "retrieval_task_coverage": task_coverage,
                        "layers": layers,
                        "retrieval": retrieval.model_dump(),
                        "generation": generation,
                    }
                )
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
    finally:
        service.close()

    report = {
        "test_set": "v1_2_retrieval" if retrieval_only else "v1_2",
        "mode": "retrieval_only" if retrieval_only else "full",
        "scope": "single_company_only",
        "source_dataset": contract["source_dataset"],
        "evaluation_contract": contract,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(rows),
        "rows": rows,
    }
    scored_layers = (
        ("retrieval_coverage", "evaluator_contract_status")
        if retrieval_only
        else (
            "retrieval_coverage",
            "grounded_answer",
            "answer_completeness",
            "evaluator_contract_status",
        )
    )
    report["scores"] = {
        layer: {
            "passed_count": sum(bool((row.get("layers") or {}).get(layer)) for row in rows),
            "pass_rate": round(
                sum(bool((row.get("layers") or {}).get(layer)) for row in rows) / max(len(rows), 1),
                4,
            ),
        }
        for layer in scored_layers
    }
    if retrieval_only:
        report_path = RETRIEVAL_REPORT_PATH
    elif rows and all(row.get("error") for row in rows):
        report_path = FAILED_REPORT_PATH
    else:
        report_path = REPORT_PATH
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("test_set", "case_count", "scores")}, ensure_ascii=False, indent=2))
    print(f"report={report_path}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行 V1.2 单公司年报问答评测")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="只调用 Planner 和召回，不调用回答/复核模型",
    )
    args = parser.parse_args()
    run(retrieval_only=args.retrieval_only)
