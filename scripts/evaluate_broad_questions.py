from __future__ import annotations

"""按真实年报切块对宽泛问题做离线召回覆盖与事实值核验。"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from embeddings.contracts import QueryRequest
from embeddings.retrieve import QdrantRetriever, assemble_context


def _read_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_case(retriever: QdrantRetriever, case: dict[str, Any], mode: str) -> dict[str, Any]:
    request = QueryRequest.from_mapping({**case, "query": case.get("question")})
    hits = retriever.search_request(request, mode=mode)
    assembled = assemble_context(hits)
    texts = "\n".join(str(hit.get("text") or "") for hit in hits)
    pages = sorted(
        {
            int(page)
            for hit in hits
            for page in [((hit.get("citation") or {}).get("page_start"))]
            if page is not None
        }
    )
    families = sorted(
        {
            str((hit.get("citation") or {}).get("statement_family") or "other")
            for hit in hits
        }
    )
    expected_families = list(case.get("expected_topic_families") or [])
    missing_families = [family for family in expected_families if family not in families]
    expected_pages = {int(value) for value in case.get("expected_pages") or []}
    page_overlap = sorted(expected_pages.intersection(pages))
    missing_value_groups = [
        group
        for group in case.get("value_groups") or []
        if not any(str(value) in texts for value in group)
    ]
    meta = dict(retriever.last_search_meta or {})
    passed = bool(hits) and not missing_families and not (expected_pages and not page_overlap) and not missing_value_groups
    return {
        "case_id": case.get("case_id"),
        "question": case.get("question"),
        "mode": mode,
        "passed": passed,
        "hit_count": len(hits),
        "candidate_count": int(meta.get("candidate_count", len(hits))),
        "broad_query": bool(meta.get("broad_query")),
        "coverage_truncated": bool(meta.get("coverage_truncated")),
        "expected_topic_families": expected_families,
        "selected_topic_families": families,
        "missing_topic_families": missing_families,
        "expected_pages": sorted(expected_pages),
        "selected_pages": pages,
        "page_overlap": page_overlap,
        "missing_value_groups": missing_value_groups,
        "coverage": assembled.get("coverage", {}),
        "evidence": [
            {
                "evidence_id": citation.get("evidence_id"),
                "statement_family": citation.get("statement_family"),
                "statement_scope": citation.get("statement_scope"),
                "period_end": citation.get("period_end"),
                "unit": citation.get("unit"),
                "source_file": citation.get("source_file"),
                "page_start": citation.get("page_start"),
                "table_id": citation.get("table_id"),
                "table_group_id": citation.get("table_group_id"),
                "text": str(next((hit.get("text") for hit in hits if hit.get("chunk_id") == citation.get("chunk_id")), ""))[:1200],
            }
            for citation in assembled.get("citations", [])
        ],
    }


def run(
    *,
    cases_path: Path,
    output_path: Path,
    chunk_dir: Path,
    qdrant_path: Path,
    collection: str,
    mode: str,
) -> dict[str, Any]:
    retriever = QdrantRetriever(qdrant_path=qdrant_path, collection=collection, chunk_dir=chunk_dir)
    try:
        cases = [evaluate_case(retriever, case, mode) for case in _read_cases(cases_path)]
    finally:
        retriever.close()
    report = {
        "stage": "annual_report_multiformat_broad_question_retrieval_evaluation",
        "cases_file": str(cases_path),
        "collection": collection,
        "mode": mode,
        "case_count": len(cases),
        "passed_count": sum(bool(case["passed"]) for case in cases),
        "pass_rate": sum(bool(case["passed"]) for case in cases) / max(len(cases), 1),
        "cases": cases,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate broad questions against real annual-report chunks.")
    parser.add_argument("--cases", type=Path, default=ROOT / "data/evaluation/annual_report_multiformat_broad_questions.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/annual_report_multiformat_broad_questions.recheck.json")
    parser.add_argument("--chunk-dir", type=Path, default=ROOT / "data/processed/annual_reports/multiformat_v1_chunks")
    parser.add_argument("--qdrant-path", type=Path, default=ROOT / "xianlian")
    parser.add_argument("--collection", default="annual_report_multiformat_v1")
    parser.add_argument("--mode", choices=("dense", "bm25", "hybrid"), default="hybrid")
    args = parser.parse_args()
    report = run(
        cases_path=args.cases,
        output_path=args.output,
        chunk_dir=args.chunk_dir,
        qdrant_path=args.qdrant_path,
        collection=args.collection,
        mode=args.mode,
    )
    return 0 if report["passed_count"] == report["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
