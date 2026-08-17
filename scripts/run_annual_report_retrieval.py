from __future__ import annotations

"""执行年报专用 Dense、BM25、Hybrid 检索评测并生成验收文件。"""

import json
from pathlib import Path

from embeddings.evaluate import run as evaluate
from embeddings.retrieve import QdrantRetriever, assemble_context
from embeddings.vectorize import DEFAULT_QDRANT_PATH


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data" / "evaluation" / "annual_report_retrieval_eval.jsonl"
CHUNKS = ROOT / "data" / "processed" / "annual_reports" / "pdf_chunks"
COLLECTION = "annual_report_pdf_chunks_v2"
OUTPUT_DIR = ROOT / "data" / "evaluation"
SUMMARY = OUTPUT_DIR / "annual_report_retrieval.acceptance.json"


def run() -> dict[str, object]:
    reports: dict[str, dict[str, object]] = {}
    for mode in ("dense", "bm25", "hybrid"):
        output = OUTPUT_DIR / f"annual_report_retrieval.{mode}.report.json"
        reports[mode] = evaluate(
            cases_path=CASES,
            output_path=output,
            mode=mode,
            qdrant_path=DEFAULT_QDRANT_PATH,
            collection=COLLECTION,
            chunk_dir=CHUNKS,
        )

    retriever = QdrantRetriever(
        qdrant_path=DEFAULT_QDRANT_PATH,
        collection=COLLECTION,
        chunk_dir=CHUNKS,
    )
    try:
        sample = retriever.search_request(
            {"query": "Apple 2024 annual report total net sales in the PDF", "top_k": 3},
            mode="hybrid",
        )
        context = assemble_context(sample)
    finally:
        retriever.close()

    client_count = None
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(DEFAULT_QDRANT_PATH))
    try:
        client_count = client.count(COLLECTION, exact=True).count
        vector_config = client.get_collection(COLLECTION).config.params.vectors
        vector_size = vector_config.size
        distance = getattr(vector_config.distance, "value", str(vector_config.distance))
    finally:
        client.close()

    hybrid_metrics = reports["hybrid"].get("metrics", {})
    checks = {
        "vector_count": client_count == 1491,
        "vector_dimension": vector_size == 1024,
        "vector_distance": str(distance).lower() == "cosine",
        "dense_filter_and_citation": reports["dense"].get("metrics", {}).get("citation_alignment_at_5", 0) >= 0.8,
        "bm25_filter_and_citation": reports["bm25"].get("metrics", {}).get("citation_alignment_at_5", 0) >= 0.8,
        "hybrid_recall_at_5": hybrid_metrics.get("recall_at_5") == 1.0,
        "hybrid_citation_alignment_at_5": hybrid_metrics.get("citation_alignment_at_5") == 1.0,
        "no_answer_refusal": hybrid_metrics.get("no_answer_refusal_rate") == 1.0,
        "context_has_citations": bool(context["citations"]),
    }
    result = {
        "stage": "annual_report_retrieval_acceptance",
        "collection": COLLECTION,
        "chunk_dir": str(CHUNKS),
        "cases": str(CASES),
        "reports": {
            mode: str(OUTPUT_DIR / f"annual_report_retrieval.{mode}.report.json")
            for mode in reports
        },
        "vector_contract": {"point_count": client_count, "vector_size": vector_size, "distance": str(distance)},
        "sample_context": context,
        "checks": checks,
        "passed": all(checks.values()),
        "boundaries": [
            "本次只覆盖年报检索、过滤、引用对象和上下文拼装。",
            "尚未接入 LLM 生成、rerank、财务计算执行和网页展示。",
        ],
    }
    SUMMARY.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
