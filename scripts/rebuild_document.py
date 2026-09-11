from __future__ import annotations

"""按 document_id 将当前 chunks 安全写回本地 Qdrant 集合。"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qdrant_client import QdrantClient, models

from embeddings.onnx_encoder import BGEEmbeddingEncoder, VECTOR_DIMENSION
from embeddings.vectorize import (
    COLLECTION,
    DEFAULT_CHUNK_DIR,
    DEFAULT_QDRANT_PATH,
    _embed_text,
    _load_chunks,
    _payload,
    _point_id,
)


def _document_chunks(chunks: list[dict[str, Any]], document_id: str) -> list[dict[str, Any]]:
    selected = [chunk for chunk in chunks if str(chunk.get("document_id") or "") == document_id]
    if not selected:
        raise ValueError(f"当前 chunks 中不存在 document_id：{document_id}")
    return selected


def _report_path(document_id: str) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", document_id).strip("_") or "document"
    return ROOT / "data" / "evaluation" / f"v2_document_rebuild_{safe_name}.json"


def _report(
    *,
    document_id: str,
    chunks: list[dict[str, Any]],
    chunk_digest: str,
    dry_run: bool,
    collection: str,
    max_length: int,
) -> dict[str, Any]:
    payloads = [_payload(chunk) for chunk in chunks]
    point_ids = sorted(_point_id(str(chunk["chunk_id"])) for chunk in chunks)
    return {
        "stage": "v2_document_rebuild",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document_id": document_id,
        "collection": collection,
        "max_length": max_length,
        "dry_run": dry_run,
        "chunk_digest": chunk_digest,
        "chunk_count": len(chunks),
        "expected_point_count": len(point_ids),
        "expected_point_id_digest": hashlib.sha256(
            "\n".join(point_ids).encode("utf-8")
        ).hexdigest(),
        "source_revisions": sorted(
            {str(payload.get("source_revision") or "") for payload in payloads if payload.get("source_revision")}
        ),
    }


def _document_point_ids(client: QdrantClient, collection: str, document_id: str) -> set[str]:
    # ponytail: one local scroll is sufficient for a single annual-report document; paginate when documents exceed 10k chunks.
    points, _ = client.scroll(
        collection_name=collection,
        scroll_filter=models.Filter(
            must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
        ),
        limit=10_000,
        with_payload=False,
        with_vectors=False,
    )
    return {str(point.id) for point in points}


def run(
    *,
    document_id: str,
    chunk_dir: Path = DEFAULT_CHUNK_DIR,
    qdrant_path: Path = DEFAULT_QDRANT_PATH,
    collection: str = COLLECTION,
    model_path: Path | None = None,
    tokenizer_path: Path | None = None,
    batch_size: int = 8,
    max_length: int = 8192,
    dry_run: bool = False,
) -> dict[str, Any]:
    chunks, chunk_digest = _load_chunks(chunk_dir)
    selected = _document_chunks(chunks, document_id)
    result = _report(
        document_id=document_id,
        chunks=selected,
        chunk_digest=chunk_digest,
        dry_run=dry_run,
        collection=collection,
        max_length=max_length,
    )
    if dry_run:
        return result

    encoder_options: dict[str, Path] = {}
    if model_path is not None:
        encoder_options["model_path"] = model_path
    if tokenizer_path is not None:
        encoder_options["tokenizer_path"] = tokenizer_path
    encoder_options["max_length"] = max_length
    encoder = BGEEmbeddingEncoder(**encoder_options)
    client = QdrantClient(path=str(qdrant_path))
    try:
        info = client.get_collection(collection)
        vector_config = info.config.params.vectors
        if vector_config.size != VECTOR_DIMENSION:
            raise ValueError(
                f"集合向量维度不匹配：expected={VECTOR_DIMENSION}, actual={vector_config.size}"
            )

        expected_ids = {_point_id(str(chunk["chunk_id"])) for chunk in selected}
        existing_ids = _document_point_ids(client, collection, document_id)
        vectors = encoder.encode([_embed_text(chunk) for chunk in selected], batch_size=batch_size)
        client.upsert(
            collection_name=collection,
            points=[
                models.PointStruct(
                    id=_point_id(str(chunk["chunk_id"])),
                    vector=vectors[index].tolist(),
                    payload=_payload(chunk),
                )
                for index, chunk in enumerate(selected)
            ],
            wait=True,
        )
        stale_ids = sorted(existing_ids - expected_ids)
        if stale_ids:
            client.delete(
                collection_name=collection,
                points_selector=models.PointIdsList(points=stale_ids),
                wait=True,
            )
        actual_ids = _document_point_ids(client, collection, document_id)
        if actual_ids != expected_ids:
            raise RuntimeError(
                f"逐文档重建验收失败：expected={len(expected_ids)}, actual={len(actual_ids)}"
            )
        result.update(
            {
                "existing_point_count_before": len(existing_ids),
                "upserted_point_count": len(selected),
                "stale_deleted_count": len(stale_ids),
                "final_point_count": len(actual_ids),
                "passed": True,
            }
        )
        return result
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild one current document into local Qdrant")
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--chunk-dir", type=Path, default=DEFAULT_CHUNK_DIR)
    parser.add_argument("--qdrant-path", type=Path, default=DEFAULT_QDRANT_PATH)
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--tokenizer", type=Path, default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=8192)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = _report_path(args.document_id)
    try:
        result = run(
            document_id=args.document_id,
            chunk_dir=args.chunk_dir,
            qdrant_path=args.qdrant_path,
            collection=args.collection,
            model_path=args.model,
            tokenizer_path=args.tokenizer,
            batch_size=args.batch_size,
            max_length=args.max_length,
            dry_run=args.dry_run,
        )
        status = 0
    except Exception as exc:
        result = {
            "stage": "v2_document_rebuild",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "document_id": args.document_id,
            "dry_run": args.dry_run,
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        status = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"report={path}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
