from __future__ import annotations

"""通过真实 TCP HTTP 验收 RAG 召回和 DeepSeek 生成，并逐题记录事实评估。"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_rag_fact_questions import _calculation_ok, _citation_ok, _fact_present, _load, _norm


FACTS = ROOT / "data" / "evaluation" / "rag_fact_questions.jsonl"
BROAD = ROOT / "data" / "evaluation" / "annual_report_multiformat_broad_questions.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "evaluation" / "rag_http_real.acceptance.json"


def _request_json(url: str, payload: dict[str, Any] | None = None, *, timeout: float) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return int(response.status), json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            detail = {"detail": body[:500]}
        return int(exc.code), detail if isinstance(detail, dict) else {"detail": str(detail)}
    except Exception as exc:
        return 0, {"error_type": type(exc).__name__, "detail": str(exc)[:500]}


def _body(case: dict[str, Any], *, generate: bool) -> dict[str, Any]:
    filters = dict(case.get("filters") or {})
    for key in ("company_id", "fiscal_year", "source_format", "document_id", "report_type"):
        if case.get(key) is not None:
            filters[key] = case[key]
    body: dict[str, Any] = {
        "query": case.get("query") or case.get("question"),
        "top_k": int(case.get("top_k") or (10 if case.get("case_id", "").startswith("b") else 5)),
        "mode": "hybrid",
        "generate": generate,
        "filters": filters,
    }
    for key in ("company_id", "fiscal_year", "source_format", "document_id", "report_type"):
        if key in filters:
            body[key] = filters[key]
    return body


def _response_evidence_text(response: dict[str, Any]) -> str:
    return "\n".join(str(item.get("text") or "") for item in response.get("evidence") or [])


def _fact_present_http(fact: Any, text: Any) -> bool:
    """允许年报括号负数和答案中的负号表达互相核验。"""

    if _fact_present(fact, text):
        return True
    fact_text = _norm(fact)
    text_value = _norm(text)
    numbers = re.findall(r"\d+(?:[.,]\d+)?", fact_text)
    if fact_text.startswith("(") and fact_text.endswith(")") and numbers:
        compact_text = text_value.replace(",", "")
        return all(number.replace(",", "") in compact_text for number in numbers)
    return False


def _citation_invariants(response: dict[str, Any]) -> dict[str, Any]:
    evidence_ids = {str(item.get("evidence_id")) for item in response.get("evidence") or []}
    citation_ids = [str(value) for value in response.get("citation_ids") or []]
    citations = response.get("citations") or []
    citation_object_ids = {str(item.get("evidence_id")) for item in citations}
    valid = bool(response.get("citation_valid"))
    valid = valid and bool(citation_ids) and set(citation_ids).issubset(evidence_ids)
    valid = valid and citation_object_ids.issubset(evidence_ids)
    valid = valid and all(item.get("source_file") for item in citations if item.get("evidence_id") in citation_ids)
    return {
        "valid": bool(valid),
        "response_citation_valid": bool(response.get("citation_valid")),
        "citation_ids": citation_ids,
        "evidence_ids": sorted(evidence_ids),
        "unknown_citation_ids": sorted(set(citation_ids) - evidence_ids),
    }


def _answer_contains_facts(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    answer = _norm(response.get("answer"))
    evidence = _norm(_response_evidence_text(response))
    facts = [str(value) for value in case.get("expected_facts", [])]
    answer_values = {fact: _fact_present_http(fact, answer) for fact in facts}
    evidence_values = {fact: _fact_present_http(fact, evidence) for fact in facts}
    calculation_ok, calculated_value = _calculation_ok(case.get("calculation"))
    return {
        "expected_facts": facts,
        "fact_in_answer": all(answer_values.values()) if facts else True,
        "fact_in_evidence": all(evidence_values.values()) if facts else True,
        "fact_checks": answer_values,
        "evidence_fact_checks": evidence_values,
        "calculation_ok": calculation_ok,
        "calculated_value": calculated_value,
    }


def _fact_retrieval_check(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    hits = response.get("evidence") or []
    hit_ids = [str(item.get("citation", {}).get("chunk_id")) for item in hits]
    expected_answerable = bool(case.get("answerable", True))
    expected_ids = {str(value) for value in case.get("expected_chunk_ids", [])}
    citation_match = bool(expected_ids.intersection(hit_ids)) if expected_answerable else not hits
    payload_like_hits = [
        {"payload": {**(item.get("citation") or {}), "chunk_id": item.get("citation", {}).get("chunk_id")}}
        for item in hits
    ]
    source_match = any(_citation_ok(hit, case) for hit in payload_like_hits) if expected_answerable else not hits
    answer_facts = _answer_contains_facts(case, response)
    retrieval = response.get("retrieval") or {}
    passed = bool(
        int(response.get("_http_status", 0)) == 200
        and bool(response.get("answerable")) is expected_answerable
        and citation_match
        and source_match
        and (answer_facts["fact_in_evidence"] if expected_answerable else not hits)
    )
    return {
        "passed": passed,
        "answerable": response.get("answerable"),
        "expected_answerable": expected_answerable,
        "hit_count": len(hits),
        "hit_chunk_ids": hit_ids,
        "expected_chunk_ids": sorted(expected_ids),
        "citation_match": citation_match,
        "source_metadata_match": source_match,
        "fact_in_evidence": answer_facts["fact_in_evidence"],
        "retrieval": retrieval,
        "citation_invariants": _citation_invariants(response),
    }


def _broad_retrieval_check(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    evidence = response.get("evidence") or []
    citations = response.get("citations") or []
    retrieval = response.get("retrieval") or {}
    selected_families = {
        str(value) for value in retrieval.get("selected_families") or []
    }
    selected_families.update(str(item.get("statement_family") or "other") for item in citations)
    expected_families = {str(value) for value in case.get("expected_topic_families") or []}
    pages = {
        int(item.get("page_start"))
        for item in citations
        if item.get("page_start") is not None
    }
    expected_pages = {int(value) for value in case.get("expected_pages") or []}
    page_overlap = sorted(expected_pages.intersection(pages))
    text = _response_evidence_text(response)
    missing_value_groups = [
        group
        for group in case.get("value_groups") or []
        if not any(_fact_present_http(value, text) for value in group)
    ]
    invariants = _citation_invariants(response)
    passed = bool(
        int(response.get("_http_status", 0)) == 200
        and response.get("answerable") is True
        and bool(evidence)
        and invariants["valid"]
        and expected_families.issubset(selected_families)
        and (not expected_pages or bool(page_overlap))
        and not missing_value_groups
    )
    return {
        "passed": passed,
        "answerable": response.get("answerable"),
        "hit_count": len(evidence),
        "citation_invariants": invariants,
        "expected_topic_families": sorted(expected_families),
        "selected_topic_families": sorted(selected_families),
        "missing_topic_families": sorted(expected_families - selected_families),
        "expected_pages": sorted(expected_pages),
        "selected_pages": sorted(pages),
        "page_overlap": page_overlap,
        "missing_value_groups": missing_value_groups,
        "retrieval": retrieval,
    }


def _broad_generation_check(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    evidence = response.get("evidence") or []
    citations = response.get("citations") or []
    answer = _norm(response.get("answer"))
    expected_families = {str(value) for value in case.get("expected_topic_families") or []}
    selected_families = {
        str(value)
        for value in (response.get("retrieval") or {}).get("selected_families") or []
    }
    selected_families.update(str(item.get("statement_family") or "other") for item in citations)
    expected_pages = {int(value) for value in case.get("expected_pages") or []}
    pages = {
        int(item.get("page_start"))
        for item in citations
        if item.get("page_start") is not None
    }
    page_overlap = sorted(expected_pages.intersection(pages))
    value_checks = {
        ";".join(str(value) for value in group): any(
            _fact_present_http(value, answer) for value in group
        )
        for group in case.get("value_groups") or []
        for value in [group]
    }
    invariants = _citation_invariants(response)
    citation_alignment = bool(invariants["valid"] and set(invariants["citation_ids"]).issubset(
        {str(item.get("evidence_id")) for item in citations}
    ))
    fact_ok = all(value_checks.values()) if value_checks else bool(answer.strip())
    passed = bool(
        int(response.get("_http_status", 0)) == 200
        and response.get("answerable") is True
        and response.get("generation", {}).get("status") == "generated"
        and citation_alignment
        and expected_families.issubset(selected_families)
        and (not expected_pages or bool(page_overlap))
        and fact_ok
    )
    return {
        "passed": passed,
        "answerable": response.get("answerable"),
        "generation_status": (response.get("generation") or {}).get("status"),
        "citation_alignment": citation_alignment,
        "expected_topic_families": sorted(expected_families),
        "selected_topic_families": sorted(selected_families),
        "missing_topic_families": sorted(expected_families - selected_families),
        "expected_pages": sorted(expected_pages),
        "selected_pages": sorted(pages),
        "page_overlap": page_overlap,
        "value_checks": value_checks,
        "fact_in_answer": fact_ok,
        "citation_invariants": invariants,
        "retrieval": response.get("retrieval") or {},
        "evidence_count": len(evidence),
    }


def _run_case(base_url: str, case: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    is_broad = case_id.startswith("b")
    result: dict[str, Any] = {
        "case_id": case_id,
        "question": case.get("query") or case.get("question"),
        "dataset": case.get("dataset") or "annual_report_broad",
        "retrieval_only": None,
        "generation": None,
    }
    retrieval_status, retrieval = _request_json(
        f"{base_url}/api/rag/query", _body(case, generate=False), timeout=timeout
    )
    retrieval["_http_status"] = retrieval_status
    retrieval_evaluation = (
        _broad_retrieval_check(case, retrieval)
        if is_broad
        else _fact_retrieval_check(case, retrieval)
    )
    result["retrieval_only"] = {
        "http_status": retrieval_status,
        "passed": retrieval_evaluation["passed"],
        "evaluation": retrieval_evaluation,
    }
    generation_status, generated = _request_json(
        f"{base_url}/api/rag/query", _body(case, generate=True), timeout=timeout
    )
    generated["_http_status"] = generation_status
    if is_broad:
        generation_evaluation = _broad_generation_check(case, generated)
    else:
        expected_answerable = bool(case.get("answerable", True))
        fact_checks = _answer_contains_facts(case, generated)
        citation = _citation_invariants(generated)
        generation_evaluation = {
            "passed": bool(
                generation_status == 200
                and generated.get("answerable") is expected_answerable
                and (
                    (not expected_answerable and not generated.get("citation_ids"))
                    or (
                        generated.get("generation", {}).get("status") == "generated"
                        and citation["valid"]
                        and fact_checks["fact_in_answer"]
                        and fact_checks["fact_in_evidence"]
                        and fact_checks["calculation_ok"]
                    )
                )
            ),
            "expected_answerable": expected_answerable,
            "answerable": generated.get("answerable"),
            "generation_status": (generated.get("generation") or {}).get("status"),
            "citation_alignment": citation["valid"],
            **fact_checks,
            "citation_invariants": citation,
            "retrieval": generated.get("retrieval") or {},
        }
    result["generation"] = {
        "http_status": generation_status,
        "answer": generated.get("answer"),
        "evaluation": generation_evaluation,
        "generation_metadata": generated.get("generation") or {},
        "citation_ids": generated.get("citation_ids") or [],
        "citations": generated.get("citations") or [],
    }
    result["passed"] = bool(
        result["retrieval_only"]["passed"]
        and result["generation"]["evaluation"]["passed"]
    )
    return result


def run(*, base_url: str, output_path: Path, timeout: float, limit: int | None = None) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    health_status, health = _request_json(f"{base_url}/health", timeout=timeout)
    cases = _load(FACTS) + _load(BROAD)
    if limit is not None:
        cases = cases[:limit]
    rows: list[dict[str, Any]] = []
    for case in cases:
        rows.append(_run_case(base_url, case, timeout=timeout))
    fact_rows = [row for row in rows if not row["case_id"].startswith("b")]
    broad_rows = [row for row in rows if row["case_id"].startswith("b")]

    def metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(items)
        return {
            "case_count": total,
            "retrieval_only_passed": sum(bool(row["retrieval_only"]["passed"]) for row in items),
            "generation_passed": sum(bool(row["generation"]["evaluation"]["passed"]) for row in items),
            "end_to_end_passed": sum(bool(row["passed"]) for row in items),
            "retrieval_only_pass_rate": round(sum(bool(row["retrieval_only"]["passed"]) for row in items) / max(total, 1), 6),
            "generation_pass_rate": round(sum(bool(row["generation"]["evaluation"]["passed"]) for row in items) / max(total, 1), 6),
            "end_to_end_pass_rate": round(sum(bool(row["passed"]) for row in items) / max(total, 1), 6),
        }

    report = {
        "stage": "rag_real_http_deepseek_acceptance",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "health": {"http_status": health_status, **health},
        "question_source": {
            "fact_cases": str(FACTS),
            "broad_cases": str(BROAD),
            "broad_questions_are_data_derived": True,
            "docx_xlsx_cases_are_fixtures": True,
        },
        "metrics": {
            "all": metrics(rows),
            "fact_cases": metrics(fact_rows),
            "broad_cases": metrics(broad_rows),
        },
        "passed": health_status == 200 and bool(health.get("status") == "ok") and all(bool(row["passed"]) for row in rows),
        "cases": rows,
        "limitations": [
            "本报告评估真实 HTTP socket、检索证据和 DeepSeek 生成；不把检索通过自动等同于生成通过。",
            "DOCX/XLSX 用例是项目现有 fixture，不代表真实公开 Word/Excel 年报。",
            "答案中的事实检查是确定性字符串/数字检查，不能替代人工复核表达质量。",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "health": report["health"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real HTTP RAG and DeepSeek acceptance.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=75.0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    report = run(base_url=args.base_url, output_path=args.output, timeout=args.timeout, limit=args.limit)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
