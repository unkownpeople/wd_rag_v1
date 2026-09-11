from __future__ import annotations

"""V2 本地深度/广度检索评测，不调用回答模型。"""

import json
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_api.schemas import QueryBody
from rag_api.service import RAGService
from rag_api.settings import Settings


CASE_PATH = ROOT / "data" / "evaluation" / "test_set_v2_deep_breadth.jsonl"
REPORT_PATH = ROOT / "data" / "evaluation" / "test_set_v2_deep_breadth.report.json"


def _read_cases(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def _number(value: Any) -> tuple[Decimal, bool] | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not re.search(r"\d", text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text.replace(",", ""))
    if cleaned in {"", "-", ".", "-."} or cleaned.count(".") > 1:
        return None
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    if negative and number > 0:
        number = -number
    return number, "%" in text


def _number_present(expected: str, text: str) -> bool:
    target = _number(expected)
    if target is None:
        return False
    for candidate in re.findall(r"\(?-?\d[\d,]*(?:\.\d+)?\)?%?", unicodedata.normalize("NFKC", text)):
        actual = _number(candidate)
        if actual == target:
            return True
        if actual and target[1] and actual[0] == target[0] and not actual[1]:
            return True
    return False


def _body(case: dict[str, Any], *, generate: bool) -> QueryBody:
    return QueryBody(
        query=str(case["query"]),
        company_id=case.get("company_id"),
        fiscal_year=case.get("fiscal_year"),
        source_format=case.get("source_format"),
        top_k=8,
        mode="hybrid",
        generate=generate,
    )


def _evaluate_case(service: RAGService, case: dict[str, Any]) -> dict[str, Any]:
    outcome = str(case.get("expected_outcome") or "normal")
    base: dict[str, Any] = {
        "case_id": case["case_id"],
        "dimension": case.get("dimension", "breadth"),
        "query": case["query"],
        "expected_outcome": outcome,
        "error": None,
    }
    try:
        if outcome == "risk_refusal":
            response = service.query(_body(case, generate=True))
            passed = response.generation.get("status") == "policy_refusal" and not response.answerable
            base.update(
                {
                    "passed": passed,
                    "observed_outcome": response.generation.get("status"),
                    "answerable": response.answerable,
                    "evidence_count": len(response.evidence),
                    "excluded_from_score": False,
                }
            )
            return base

        request, hits, _, evidence = service.retrieve(_body(case, generate=False))
        citations = [item.citation for item in evidence]
        evidence_text = "\n".join(item.text for item in evidence)
        companies = {str(item.company_id or "") for item in citations if item.company_id}
        years = {int(item.fiscal_year) for item in citations if item.fiscal_year is not None}
        documents = {str(item.document_id or "") for item in citations if item.document_id}
        formats = {str(item.source_format or "") for item in citations if item.source_format}
        families = {str(item.statement_family or "") for item in citations if item.statement_family}
        term_groups = [
            any(_norm(term) in _norm(evidence_text) for term in group)
            for group in case.get("expected_term_groups") or []
        ]
        numbers = {
            value: _number_present(value, evidence_text)
            for value in case.get("expected_numbers") or []
        }
        number_groups = {
            f"group_{index}": any(_number_present(value, evidence_text) for value in group)
            for index, group in enumerate(case.get("expected_number_groups") or [], 1)
        }
        company_ok = not case.get("company_id") or companies == {str(case["company_id"])}
        year_ok = case.get("fiscal_year") is None or int(case["fiscal_year"]) in years
        terms_ok = all(term_groups)
        numbers_ok = all(numbers.values()) and all(number_groups.values())
        documents_ok = all(value in documents for value in case.get("expected_documents") or [])
        format_ok = not case.get("source_format") or str(case["source_format"]) in formats
        families_ok = all(value in families for value in case.get("required_families") or [])
        no_evidence_ok = outcome != "no_evidence" or not hits
        checks = {
            "has_evidence": bool(evidence),
            "company_isolated": company_ok,
            "year_present": year_ok,
            "terms_present": terms_ok,
            "numbers_present": numbers_ok,
            "documents_present": documents_ok,
            "source_format_present": format_ok,
            "families_present": families_ok,
            "no_evidence": no_evidence_ok,
        }
        passed = (
            no_evidence_ok
            if outcome == "no_evidence"
            else bool(evidence) and all(checks[key] for key in (
                "company_isolated",
                "year_present",
                "terms_present",
                "numbers_present",
                "documents_present",
                "source_format_present",
                "families_present",
            ))
        )
        base.update(
            {
                "passed": passed,
                "excluded_from_score": outcome == "manual_review",
                "hit_count": len(hits),
                "evidence_count": len(evidence),
                "observed_companies": sorted(companies),
                "observed_years": sorted(years),
                "observed_documents": sorted(documents),
                "observed_formats": sorted(formats),
                "observed_families": sorted(families),
                "evidence_preview": [
                    {
                        "evidence_id": item.evidence_id,
                        "document_id": item.citation.document_id,
                        "fiscal_year": item.citation.fiscal_year,
                        "source_format": item.citation.source_format,
                        "statement_family": item.citation.statement_family,
                        "page_start": item.citation.page_start,
                        "text": item.text[:600],
                    }
                    for item in evidence
                ],
                "checks": checks,
                "retrieval": {
                    "route": service._last_planner_meta.get("route"),
                    "rerank_applied": bool(getattr(service.retriever, "last_search_meta", {}).get("rerank_applied")),
                    "effective_top_k": len(hits),
                },
            }
        )
    except Exception as exc:
        base.update({"passed": False, "excluded_from_score": False, "error": f"{type(exc).__name__}: {exc}"})
    return base


def run(*, case_path: Path = CASE_PATH, report_path: Path = REPORT_PATH) -> dict[str, Any]:
    cases = _read_cases(case_path)
    service = RAGService(Settings())
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            rows.append(_evaluate_case(service, case))
    finally:
        service.close()

    score_rows = [row for row in rows if not row.get("excluded_from_score")]
    breadth_rows = [row for row in score_rows if row.get("dimension") == "breadth"]
    depth_rows = [row for row in score_rows if row.get("dimension") == "depth"]
    normal_rows = [row for row in score_rows if row.get("expected_outcome") == "normal"]
    report = {
        "stage": "v2_deep_breadth_retrieval_evaluation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "retrieval_only_plus_policy_gate",
        "case_count": len(rows),
        "scoreable_case_count": len(score_rows),
        "manual_review_case_count": sum(row.get("excluded_from_score", False) for row in rows),
        "scores": {
            "all_scoreable": sum(bool(row.get("passed")) for row in score_rows),
            "breadth": {"passed": sum(bool(row.get("passed")) for row in breadth_rows), "total": len(breadth_rows)},
            "depth": {"passed": sum(bool(row.get("passed")) for row in depth_rows), "total": len(depth_rows)},
            "normal_retrieval": {"passed": sum(bool(row.get("passed")) for row in normal_rows), "total": len(normal_rows)},
        },
        "rows": rows,
        "boundaries": [
            "只验证本地检索、证据覆盖、公司/年份/格式/报表族隔离和风险拒答闸门。",
            "不调用回答模型，不产生外部 API 费用；完整生成质量另需单独授权后测试。",
            "manual_review 用例只记录观察结果，不计入自动通过率。",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("case_count", "scoreable_case_count", "scores")}, ensure_ascii=False, indent=2))
    print(f"report={report_path}")
    return report


if __name__ == "__main__":
    run()
