from __future__ import annotations

"""复核多公司向量库字段、表格回链和指定公司真实召回。"""

import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from embeddings.contracts import QueryRequest
from embeddings.onnx_encoder import BGEEmbeddingEncoder
from embeddings.retrieve import QdrantRetriever
from scripts.rebuild_multicompany_eval import _verify


CHUNK_DIR = ROOT / "data" / "processed" / "annual_reports" / "multicompany_eval_v1_chunks"
QDRANT_PATH = ROOT / "xianlian_multicompany_eval"
COLLECTION = "annual_report_multicompany_eval_v1"
OUTPUT = ROOT / "data" / "evaluation" / "multicompany_pdf_rag.final_verification.json"


def run() -> dict[str, Any]:
    verification = _verify(
        QDRANT_PATH,
        COLLECTION,
        sorted(CHUNK_DIR.glob("*.chunks.jsonl")),
    )
    encoder = BGEEmbeddingEncoder(max_length=8192)
    retriever = QdrantRetriever(
        qdrant_path=QDRANT_PATH,
        collection=COLLECTION,
        chunk_dir=CHUNK_DIR,
        encoder=encoder,
    )
    cases = (
        ("apple", "apple_2024_10k", 2024, "What were the company total assets?"),
        ("microsoft", "microsoft_2023_annual_report", 2023, "What were the company total assets?"),
        ("tcs", "tcs_2024_annual_report", 2024, "What was revenue from operations?"),
    )
    recalls: list[dict[str, Any]] = []
    try:
        for company_id, document_id, fiscal_year, query in cases:
            request = QueryRequest(
                query=query,
                company_id=company_id,
                document_id=document_id,
                fiscal_year=fiscal_year,
                source_format="pdf",
                top_k=5,
            )
            hits = retriever.search_request(request, mode="dense")
            recalls.append(
                {
                    "company_id": company_id,
                    "document_id": document_id,
                    "fiscal_year": fiscal_year,
                    "query": query,
                    "hit_count": len(hits),
                    "all_filters_match": bool(hits)
                    and all(
                        hit["payload"].get("company_id") == company_id
                        and hit["payload"].get("document_id") == document_id
                        and hit["payload"].get("fiscal_year") == fiscal_year
                        for hit in hits
                    ),
                    "top_hits": [
                        {
                            "chunk_id": hit.get("chunk_id"),
                            "score": hit.get("score"),
                            "page_start": hit["payload"].get("page_start"),
                            "chunk_type": hit["payload"].get("chunk_type"),
                            "statement_family": hit["payload"].get("statement_family"),
                        }
                        for hit in hits[:3]
                    ],
                }
            )
    finally:
        retriever.close()

    result = {
        "stage": "multicompany_pdf_rag_final_verification",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "qdrant_path": str(QDRANT_PATH),
        "collection": COLLECTION,
        "verification": verification,
        "filtered_dense_recall": recalls,
        "storage_note": (
            "Qdrant payload 保存命中表格片段的 table_header、row_indices、row_matrix "
            "及完整 raw_matrix 的 SHA-256；完整 raw_matrix 保留在 UTF-8 切块 JSONL。"
        ),
        "passed": bool(verification["passed"])
        and all(item["all_filters_match"] for item in recalls),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(0 if run()["passed"] else 1)
