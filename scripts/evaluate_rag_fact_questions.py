from __future__ import annotations

"""评估 Word/PDF/XLSX 事实证据；默认不调用外部 LLM。"""

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from embeddings.retrieve import QdrantRetriever, assemble_context
from embeddings.vectorize import DEFAULT_QDRANT_PATH
from rag_api.schemas import QueryBody
from rag_api.service import RAGService
from rag_api.settings import Settings


DEFAULT_CASES = ROOT / "data" / "evaluation" / "rag_fact_questions.jsonl"
DEFAULT_CHUNKS = ROOT / "data" / "processed" / "annual_reports" / "multiformat_v1_chunks"
DEFAULT_COLLECTION = "annual_report_multiformat_v1"
DEFAULT_OUTPUT = ROOT / "data" / "evaluation" / "rag_fact_questions.acceptance.json"


def _load(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())


_FACT_ALIASES: dict[str, tuple[str, ...]] = {
    "dimensions": ("dimensions", "dimension", "维度"),
    "sourcepages": ("sourcepages", "pages", "page", "来源页数", "页数", "页面"),
    "annualreport": ("annualreport", "年报", "年度报告"),
    "change": ("change", "增长", "变化", "增幅"),
    "unfavorablemovement": ("unfavorablemovement", "不利汇率变动", "不利变动"),
}


def _fact_present(fact: Any, text: Any) -> bool:
    """允许中英文单位表达差异，但仍要求数字和语义单位同时出现。"""

    fact_text = _norm(fact)
    text_value = _norm(text)
    if not fact_text:
        return True
    if fact_text in text_value:
        return True
    fact_compact = fact_text.replace(",", "")
    text_compact = text_value.replace(",", "")
    if fact_compact in text_compact:
        return True
    direct_aliases = _FACT_ALIASES.get(fact_text)
    if direct_aliases and any(_norm(alias) in text_value for alias in direct_aliases):
        return True
    numbers = re.findall(r"\d+(?:[.,]\d+)?", fact_compact)
    if not numbers or not all(number.replace(",", "") in text_compact for number in numbers):
        return False
    words = re.sub(r"\d+(?:[.,]\d+)?", "", fact_compact).strip("=%:-")
    if not words:
        return True
    aliases = _FACT_ALIASES.get(words, (words,))
    return any(_norm(alias) in text_value for alias in aliases)


def _citation_ok(hit: dict[str, Any], case: dict[str, Any]) -> bool:
    payload = hit.get("payload") or {}
    if str(payload.get("chunk_id")) not in {str(v) for v in case.get("expected_chunk_ids", [])}:
        return False
    expected_suffix = _norm(str(case.get("expected_source_suffix") or "").replace("\\", "/"))
    actual_source = _norm(str(payload.get("source_file") or "").replace("\\", "/"))
    if expected_suffix and not actual_source.endswith(expected_suffix):
        return False
    checks = {
        "expected_document_id": "document_id",
        "expected_company_id": "company_id",
        "expected_fiscal_year": "fiscal_year",
        "expected_source_format": "source_format",
        "expected_page": "page_start",
        "expected_table_id": "table_id",
        "expected_paragraph_index": "paragraph_index",
        "expected_sheet_name": "sheet_name",
        "expected_cell_range": "cell_range",
    }
    for expected_key, payload_key in checks.items():
        expected = case.get(expected_key)
        if expected is not None and payload.get(payload_key) != expected:
            return False
    return True


def _calculation_ok(calculation: dict[str, Any] | None) -> tuple[bool, float | None]:
    if not calculation:
        return True, None
    if calculation.get("operation") != "percent_change":
        return False, None
    old = float(calculation["old"])
    new = float(calculation["new"])
    actual = (new - old) / old * 100
    expected = float(calculation["expected"])
    tolerance = float(calculation.get("tolerance", 0.01))
    return math.isclose(actual, expected, abs_tol=tolerance), round(actual, 6)


def evaluate_offline(
    *,
    cases_path: Path,
    output_path: Path,
    qdrant_path: Path,
    collection: str,
    chunk_dir: Path,
    top_k: int,
) -> dict[str, Any]:
    cases = _load(cases_path)
    retriever = QdrantRetriever(
        qdrant_path=qdrant_path,
        collection=collection,
        chunk_dir=chunk_dir,
    )
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            request = {
                "query": case["query"],
                "top_k": top_k,
                "filters": case.get("filters") or {},
            }
            hits = retriever.search_request(request, mode="hybrid")
            assembled = assemble_context(hits)
            evidence_text = "\n".join(str(hit.get("text") or "") for hit in hits)
            expected_facts = [str(value) for value in case.get("expected_facts", [])]
            fact_ok = all(_fact_present(fact, evidence_text) for fact in expected_facts)
            citation_ok = any(_citation_ok(hit, case) for hit in hits) if case.get("answerable", True) else not hits
            calculation_ok, calculated_value = _calculation_ok(case.get("calculation"))
            answerable = bool(case.get("answerable", True))
            no_answer_ok = bool(not hits) if not answerable else True
            passed = citation_ok and fact_ok and calculation_ok and no_answer_ok
            rows.append(
                {
                    "case_id": case["case_id"],
                    "dataset": case.get("dataset"),
                    "query": case["query"],
                    "answerable": answerable,
                    "hit_chunk_ids": [hit.get("chunk_id") for hit in hits],
                    "citation_ok": citation_ok,
                    "fact_evidence_ok": fact_ok,
                    "calculation_ok": calculation_ok,
                    "calculated_value": calculated_value,
                    "no_answer_ok": no_answer_ok,
                    "passed": passed,
                    "citations": assembled.get("citations", []),
                }
            )
    finally:
        retriever.close()

    report = {
        "stage": "rag_fact_evaluation",
        "evaluation_mode": "offline_source_evidence",
        "cases_path": str(cases_path),
        "collection": collection,
        "chunk_dir": str(chunk_dir),
        "case_count": len(rows),
        "passed_count": sum(bool(row["passed"]) for row in rows),
        "metrics": {
            "case_pass_rate": round(sum(bool(row["passed"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "citation_accuracy": round(sum(bool(row["citation_ok"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "fact_evidence_accuracy": round(sum(bool(row["fact_evidence_ok"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "calculation_accuracy": round(sum(bool(row["calculation_ok"]) for row in rows) / len(rows), 6) if rows else 0.0,
        },
        "limitations": [
            "离线模式验证召回块、来源定位、原始事实和确定性计算，不等同于 LLM 最终答案评分。",
            "真实 LLM 生成使用 scripts/evaluate_rag_fact_questions.py --live，需先在项目 .env 配置 DEEPSEEK_API_KEY。",
            "DOCX/XLSX 当前仍是 fixture，真实 Word/Excel 年报需要另行验收。",
        ],
        "cases": rows,
        "passed": all(bool(row["passed"]) for row in rows),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def evaluate_live(*, cases_path: Path, output_path: Path, limit: int | None = None) -> dict[str, Any]:
    cases = _load(cases_path)
    if limit is not None:
        cases = cases[:limit]
    settings = Settings()
    service = RAGService(settings)
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            expected_answerable = case.get("answerable")
            if expected_answerable is None:
                expected_answerable = True
            row: dict[str, Any] = {
                "case_id": case["case_id"],
                "query": case["query"],
                "expected_answerable": bool(expected_answerable),
                "answer": None,
                "answerable": None,
                "citation_valid": False,
                "citation_ok": False,
                "citation_ids": [],
                "fact_in_answer": False,
                "fact_in_evidence": False,
                "answerability_ok": False,
                "no_answer_ok": False,
                "error": None,
                "generation": {"status": "not_started"},
                "passed": False,
            }
            try:
                body = QueryBody(
                    query=case["query"],
                    top_k=5,
                    mode="hybrid",
                    generate=True,
                    **{key: value for key, value in (case.get("filters") or {}).items() if key in {"company_id", "fiscal_year", "source_format", "document_id", "report_type"}},
                )
                response = service.query(body)
                answer = _norm(response.answer)
                evidence_text = _norm("\n".join(item.text for item in response.evidence))
                expected_facts = [str(value) for value in case.get("expected_facts", [])]
                fact_in_answer = all(_fact_present(fact, answer) for fact in expected_facts)
                fact_in_evidence = all(_fact_present(fact, evidence_text) for fact in expected_facts)
                answerability_ok = bool(response.answerable) is bool(expected_answerable)
                citation_ok = (
                    bool(response.citation_valid)
                    if expected_answerable
                    else not bool(response.citation_ids)
                )
                no_answer_ok = (
                    (not response.answerable and not response.citation_ids)
                    if not expected_answerable
                    else answerability_ok
                )
                row.update(
                    {
                        "answer": response.answer,
                        "answerable": response.answerable,
                        "citation_valid": response.citation_valid,
                        "citation_ok": citation_ok,
                        "citation_ids": response.citation_ids,
                        "fact_in_answer": fact_in_answer,
                        "fact_in_evidence": fact_in_evidence,
                        "answerability_ok": answerability_ok,
                        "no_answer_ok": no_answer_ok,
                        "generation": response.generation,
                        "passed": bool(
                            citation_ok
                            and fact_in_answer
                            and fact_in_evidence
                            and answerability_ok
                            and no_answer_ok
                        ),
                    }
                )
            except Exception as exc:
                # LLM/网络错误按题记录；异常信息由 LLM 客户端保持为不含密钥的类型信息。
                row["error"] = f"{type(exc).__name__}: {exc}"
                row["generation"] = {"status": "error", "error_type": type(exc).__name__}
            rows.append(row)
    finally:
        service.close()
    report = {
        "stage": "rag_fact_evaluation",
        "evaluation_mode": "live_deepseek_generation",
        "cases_path": str(cases_path),
        "case_count": len(rows),
        "passed_count": sum(bool(row["passed"]) for row in rows),
        "metrics": {
            "case_pass_rate": round(sum(bool(row["passed"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "citation_valid_rate": round(sum(bool(row["citation_ok"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "raw_citation_valid_rate": round(sum(bool(row["citation_valid"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "fact_in_answer_rate": round(sum(bool(row["fact_in_answer"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "fact_in_evidence_rate": round(sum(bool(row["fact_in_evidence"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "answerability_accuracy": round(sum(bool(row["answerability_ok"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "no_answer_accuracy": round(sum(bool(row["no_answer_ok"]) for row in rows) / len(rows), 6) if rows else 0.0,
            "error_count": sum(bool(row["error"]) for row in rows),
        },
        "blocked": any(bool(row["error"]) for row in rows),
        "passed": bool(rows) and all(bool(row["passed"]) for row in rows),
        "cases": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--qdrant-path", type=Path, default=DEFAULT_QDRANT_PATH)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--chunk-dir", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--live", action="store_true", help="调用 DeepSeek 生成并检查答案，默认不调用外部 API")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.live:
        report = evaluate_live(cases_path=args.cases, output_path=args.output, limit=args.limit)
    else:
        report = evaluate_offline(
            cases_path=args.cases,
            output_path=args.output,
            qdrant_path=args.qdrant_path,
            collection=args.collection,
            chunk_dir=args.chunk_dir,
            top_k=args.top_k,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
