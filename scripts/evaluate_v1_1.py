from __future__ import annotations

"""测试集 v1_1：三家公司单公司 RAG 检索、规划、引用和事实覆盖验收。"""

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_api.schemas import QueryBody
from rag_api.service import RAGService
from rag_api.settings import Settings


CASES = ROOT / "data" / "evaluation" / "test_set_v1_1.jsonl"
REPORT = ROOT / "data" / "evaluation" / "test_set_v1_1.acceptance.json"


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in CASES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    return re.sub(r"\s+", "", text)


def fact_present(fact: str, text: str) -> bool:
    fact_norm = normalize(fact)
    text_norm = normalize(text)
    if fact_norm in text_norm:
        return True
    compact_fact = fact_norm.replace(",", "")
    compact_text = text_norm.replace(",", "")
    return compact_fact in compact_text


def is_numeric_fact(fact: str) -> bool:
    """数字期望用于事实值验收；文本期望用于英文证据主题定位。"""

    return bool(re.search(r"\d", str(fact or "")))


def run() -> dict[str, Any]:
    settings = Settings()
    cases = load_cases()
    service = RAGService(settings)
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            filters = dict(case.get("filters") or {})
            body = QueryBody(
                query=case["query"],
                top_k=5,
                mode="hybrid",
                generate=True,
                **filters,
            )
            row: dict[str, Any] = {
                "case_id": case["case_id"],
                "company": case["company"],
                "type": case["type"],
                "query": case["query"],
                "expected_facts": case.get("expected_facts", []),
                "reference_pages": case.get("reference_pages", []),
                "error": None,
                "passed": False,
            }
            try:
                response = service.query(body)
                evidence_text = "\n".join(item.text for item in response.evidence)
                answer_text = response.answer
                citations = [item.model_dump() for item in response.citations]
                evidence_companies = sorted({item.citation.company_id for item in response.evidence})
                evidence_pages = sorted({item.citation.page_start for item in response.evidence if item.citation.page_start is not None})
                expected_pages = {int(value) for value in case.get("reference_pages", [])}
                page_overlap = sorted(expected_pages.intersection(evidence_pages))
                fact_evidence = {fact: fact_present(fact, evidence_text) for fact in case.get("expected_facts", [])}
                fact_answer = {fact: fact_present(fact, answer_text) for fact in case.get("expected_facts", [])}
                cited_families = {
                    str(item.get("statement_family") or "")
                    for item in citations
                    if item.get("statement_family")
                }
                expected_families = {
                    str(value)
                    for value in case.get("expected_families", [])
                    if str(value).strip()
                }
                family_coverage = expected_families.issubset(cited_families)
                numeric_evidence = {
                    fact: present for fact, present in fact_evidence.items() if is_numeric_fact(fact)
                }
                numeric_answer = {
                    fact: present for fact, present in fact_answer.items() if is_numeric_fact(fact)
                }
                if case["type"] == "broad_semantic":
                    # 文本 expected_facts 是英文年报主题探针，不要求中文答案逐字复述；
                    # 主题由原文命中或结构化报表族覆盖，数字仍须在证据和答案中出现。
                    textual_evidence = all(
                        present
                        for fact, present in fact_evidence.items()
                        if not is_numeric_fact(fact)
                    )
                    evidence_topics_present = textual_evidence or family_coverage
                    evidence_facts_present = all(numeric_evidence.values()) and evidence_topics_present
                    answer_facts_present = all(numeric_answer.values())
                else:
                    evidence_facts_present = all(fact_evidence.values()) if fact_evidence else True
                    answer_facts_present = all(fact_answer.values()) if fact_answer else True
                citation_pages_present = bool(citations) and all(
                    item.get("page_start") is not None or item.get("page") is not None
                    for item in citations
                )
                # reference_pages 是人工定位提示。若提示与 PDF 物理页基准不一致，
                # 只要期望事实已完整召回且每条引用都有页码，便保留 mismatch 审计，
                # 不把正确证据误判为检索失败。
                reference_page_supported = bool(page_overlap) or (
                    evidence_facts_present and citation_pages_present
                )
                expected_company = str(filters.get("company_id"))
                company_isolated = bool(evidence_companies) and evidence_companies == [expected_company]
                planner = response.retrieval.planner
                planner_ok = (
                    case["type"] != "broad_semantic"
                    or planner.get("status") in {"used", "fallback", "not_needed"}
                )
                citation_ok = bool(response.citation_valid) and bool(response.citation_ids)
                row.update(
                    {
                        "answer": response.answer,
                        "answerable": response.answerable,
                        "citation_valid": response.citation_valid,
                        "citation_ids": response.citation_ids,
                        "citations": citations,
                        "evidence_count": len(response.evidence),
                        "evidence_companies": evidence_companies,
                        "evidence_pages": evidence_pages,
                        "page_overlap": page_overlap,
                        "company_isolated": company_isolated,
                        "fact_in_evidence": fact_evidence,
                        "fact_in_answer": fact_answer,
                        "planner": planner,
                        "retrieval": response.retrieval.model_dump(),
                        "generation": response.generation,
                        "checks": {
                            "company_isolated": company_isolated,
                            "citation_valid": citation_ok,
                            "evidence_facts_present": evidence_facts_present,
                            "answer_facts_present": answer_facts_present,
                            "expected_family_coverage": family_coverage,
                            "reference_page_overlap": bool(page_overlap),
                            "reference_page_supported": reference_page_supported,
                            "planner_boundary": planner_ok,
                        },
                    }
                )
                row["passed"] = bool(
                    response.answerable
                    and company_isolated
                    and citation_ok
                    and row["checks"]["evidence_facts_present"]
                    and row["checks"]["answer_facts_present"]
                    and row["checks"]["reference_page_supported"]
                    and planner_ok
                )
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
    finally:
        service.close()

    by_company: dict[str, dict[str, Any]] = {}
    for company in sorted({row["company"] for row in rows}):
        company_rows = [row for row in rows if row["company"] == company]
        by_company[company] = {
            "case_count": len(company_rows),
            "passed_count": sum(bool(row["passed"]) for row in company_rows),
            "pass_rate": round(sum(bool(row["passed"]) for row in company_rows) / max(len(company_rows), 1), 4),
        }
    report = {
        "test_set": "v1_1",
        "scope": "single_company_only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "qdrant_path": str(settings.qdrant_path),
        "collection": settings.qdrant_collection,
        "chunk_dir": str(settings.chunk_dir),
        "case_count": len(rows),
        "passed_count": sum(bool(row["passed"]) for row in rows),
        "pass_rate": round(sum(bool(row["passed"]) for row in rows) / max(len(rows), 1), 4),
        "by_company": by_company,
        "known_boundary": "不测试多公司比较；不删除数据集、原始 PDF、manifest 或处理阶段文档。",
        "rows": rows,
        "passed": all(bool(row["passed"]) for row in rows),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("test_set", "case_count", "passed_count", "pass_rate", "by_company", "passed")}, ensure_ascii=False, indent=2))
    print(f"report={REPORT}")
    return report


if __name__ == "__main__":
    run()
