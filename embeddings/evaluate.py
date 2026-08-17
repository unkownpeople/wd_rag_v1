from __future__ import annotations

"""固定评测集上的 Dense Retrieval 指标，不包含 LLM 生成评价。"""

import argparse
import json
from pathlib import Path
from typing import Any

from .contracts import QueryRequest
from .retrieve import QdrantRetriever
from .vectorize import COLLECTION, DEFAULT_CHUNK_DIR, DEFAULT_QDRANT_PATH


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "evaluation" / "retrieval_eval.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "evaluation" / "retrieval_eval.report.json"
DEFAULT_TOP_K = 5
DEFAULT_NO_ANSWER_THRESHOLD = 0.50


def _load_cases(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _is_expected_hit(hit: dict[str, Any], case: dict[str, Any]) -> bool:
    expected = set(case.get("expected_chunk_ids", []))
    return str(hit.get("payload", {}).get("chunk_id")) in expected


def _citation_aligned(hit: dict[str, Any], case: dict[str, Any]) -> bool:
    if not _is_expected_hit(hit, case):
        return False
    payload = hit.get("payload", {})
    expected_suffix = str(case.get("expected_source_suffix") or "").replace("\\", "/").lower()
    actual_source = str(payload.get("source_file") or "").replace("\\", "/").lower()
    if expected_suffix and not actual_source.endswith(expected_suffix):
        return False
    expected_page = case.get("expected_page")
    if expected_page is not None and payload.get("page_start") != expected_page:
        return False
    expected_table = case.get("expected_table_id")
    if expected_table is not None and payload.get("table_id") != expected_table:
        return False
    expected_document = case.get("expected_document_id")
    if expected_document is not None and payload.get("document_id") != expected_document:
        return False
    for key in ("expected_company_id", "expected_fiscal_year", "expected_source_format"):
        payload_key = {
            "expected_company_id": "company_id",
            "expected_fiscal_year": "fiscal_year",
            "expected_source_format": "source_format",
        }[key]
        expected_value = case.get(key)
        if expected_value is not None and payload.get(payload_key) != expected_value:
            return False
    expected_paragraph = case.get("expected_paragraph_index")
    if expected_paragraph is not None and payload.get("paragraph_index") != expected_paragraph:
        return False
    expected_sheet = case.get("expected_sheet_name")
    if expected_sheet is not None and payload.get("sheet_name") != expected_sheet:
        return False
    expected_range = case.get("expected_cell_range")
    return expected_range is None or payload.get("cell_range") == expected_range


def run(
    *,
    cases_path: Path = DEFAULT_CASES,
    output_path: Path = DEFAULT_OUTPUT,
    top_k: int = DEFAULT_TOP_K,
    no_answer_threshold: float = DEFAULT_NO_ANSWER_THRESHOLD,
    mode: str = "dense",
    qdrant_path: Path = DEFAULT_QDRANT_PATH,
    collection: str = COLLECTION,
    chunk_dir: Path = DEFAULT_CHUNK_DIR,
) -> dict[str, Any]:
    if mode not in {"dense", "bm25", "hybrid"}:
        raise ValueError(f"不支持的检索模式：{mode}")
    cases = _load_cases(cases_path)
    retriever = QdrantRetriever(
        qdrant_path=qdrant_path,
        collection=collection,
        chunk_dir=chunk_dir,
    )
    answerable = [case for case in cases if case.get("answerable", True)]
    results: list[dict[str, Any]] = []
    try:
        for case in cases:
            request = QueryRequest.from_mapping(
                {
                    "query": case["query"],
                    "top_k": top_k,
                    "filters": case.get("filters") or {},
                }
            )
            resolved_request = retriever.resolve_request(request)
            hits = retriever.search_request(request, mode=mode)
            relevant_ranks = [hit["rank"] for hit in hits if _is_expected_hit(hit, case)]
            first_rank = min(relevant_ranks) if relevant_ranks else None
            top_dense_score = (
                float(hits[0].get("dense_score"))
                if hits and hits[0].get("dense_score") is not None
                else (float(hits[0]["score"]) if hits and mode == "dense" else None)
            )
            result = {
                "case_id": case["case_id"],
                "answerable": bool(case.get("answerable", True)),
                "query": case["query"],
                "filters": resolved_request.explicit_filters(),
                "top_score": hits[0]["score"] if hits else None,
                "top_dense_score": top_dense_score,
                "hit_chunk_ids": [hit["payload"].get("chunk_id") for hit in hits],
                "first_relevant_rank": first_rank,
                "recall_at_1": bool(first_rank and first_rank <= 1),
                "recall_at_3": bool(first_rank and first_rank <= 3),
                "recall_at_5": bool(first_rank and first_rank <= 5),
                "citation_aligned_at_5": any(_citation_aligned(hit, case) for hit in hits),
                "citations": [hit.get("citation") for hit in hits],
                "no_answer_refused": (top_dense_score < no_answer_threshold)
                if top_dense_score is not None
                else True,
            }
            results.append(result)
    finally:
        retriever.close()

    def average(key: str, rows: list[dict[str, Any]]) -> float:
        return round(sum(float(row[key]) for row in rows) / len(rows), 6) if rows else 0.0

    mrr_values = [
        (1.0 / row["first_relevant_rank"] if row["first_relevant_rank"] else 0.0)
        for row in results
        if row["answerable"]
    ]
    answerable_results = [row for row in results if row["answerable"]]
    no_answer_results = [row for row in results if not row["answerable"]]
    report = {
        "stage": "retrieval_evaluation",
        "evaluation_type": f"{mode}_retrieval_only",
        "retrieval_mode": mode,
        "collection": collection,
        "chunk_dir": str(chunk_dir),
        "qdrant_path": str(qdrant_path),
        "cases_path": str(cases_path),
        "top_k": top_k,
        "no_answer_threshold": no_answer_threshold,
        "case_count": len(cases),
        "answerable_count": len(answerable),
        "no_answer_count": len(no_answer_results),
        "metrics": {
            "recall_at_1": average("recall_at_1", answerable_results),
            "recall_at_3": average("recall_at_3", answerable_results),
            "recall_at_5": average("recall_at_5", answerable_results),
            "mrr": round(sum(mrr_values) / len(mrr_values), 6) if mrr_values else 0.0,
            "citation_alignment_at_5": average("citation_aligned_at_5", answerable_results),
            "no_answer_refusal_rate": average("no_answer_refused", no_answer_results),
        },
        "limitations": [
            "未运行 LLM 生成，因此 citation_alignment_at_5 只表示检索命中与来源元数据对齐。",
            "no_answer_refusal_rate 使用 Dense 相似度阈值，是检索层拒答，不是完整问答拒答。",
            "未包含 rerank、Latency 或人工答案正确率。",
        ],
        "cases": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Qdrant dense retrieval with a fixed JSONL set.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--no-answer-threshold", type=float, default=DEFAULT_NO_ANSWER_THRESHOLD)
    parser.add_argument("--mode", choices=["dense", "bm25", "hybrid"], default="dense")
    parser.add_argument("--qdrant-path", type=Path, default=DEFAULT_QDRANT_PATH)
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--chunk-dir", type=Path, default=DEFAULT_CHUNK_DIR)
    args = parser.parse_args()
    print(json.dumps(run(cases_path=args.cases, output_path=args.output, top_k=args.top_k, no_answer_threshold=args.no_answer_threshold, mode=args.mode, qdrant_path=args.qdrant_path, collection=args.collection, chunk_dir=args.chunk_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
