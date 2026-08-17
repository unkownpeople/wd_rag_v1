from __future__ import annotations

"""批量生成 BGE-M3 向量并幂等写入 Qdrant Local。"""

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from chunking.identity import make_record_id

from .onnx_encoder import (
    DEFAULT_MODEL_PATH,
    DEFAULT_TOKENIZER_PATH,
    BGEEmbeddingEncoder,
    VECTOR_DIMENSION,
)
from .semantic_metadata import enrich_chunk


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHUNK_DIR = ROOT / "data" / "processed" / "chunks" / "bge-m3-tokenizer-v1"
DEFAULT_QDRANT_PATH = ROOT / "xianlian"
DEFAULT_MANIFEST = ROOT / "data" / "processed" / "embeddings" / "bge-m3-onnx-int8.manifest.json"
COLLECTION = "rag_chunks"


def _chunk_digest(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_chunks(input_dir: Path) -> tuple[list[dict[str, Any]], str]:
    files = sorted(input_dir.glob("*.chunks.jsonl"))
    if not files:
        raise FileNotFoundError(f"没有找到切块文件：{input_dir}")
    chunks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in files:
        cleaned_name = path.name.replace(".chunks.jsonl", ".cleaned.jsonl")
        same_family_cleaned = input_dir.parent / input_dir.name.replace("_chunks", "_cleaned") / cleaned_name
        legacy_cleaned = input_dir.parent.parent / "cleaned" / cleaned_name
        cleaned_path = same_family_cleaned if same_family_cleaned.is_file() else legacy_cleaned
        source_by_id: dict[str, str] = {}
        if cleaned_path.is_file():
            cleaned_records = [
                json.loads(line)
                for line in cleaned_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            source_by_id = {
                make_record_id(record, index): str(record.get("source_file") or "")
                for index, record in enumerate(cleaned_records)
            }
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            chunk = json.loads(line)
            chunk["_chunk_file"] = path.name
            chunk["_source_file"] = next(
                (
                    source_by_id[source_id]
                    for source_id in chunk.get("source_record_ids", [])
                    if source_by_id.get(source_id)
                ),
                "",
            )
            # 语义字段由当前切块的表头、行标签和来源元数据确定性推断。
            # 即使旧切块文件尚未持久化这些字段，向量化和 BM25 也不会丢失它们。
            enrich_chunk(chunk)
            chunk_id = str(chunk.get("chunk_id") or "")
            if not chunk_id or chunk_id in seen:
                raise ValueError(f"chunk_id 缺失或重复：{path}:{line_number}")
            seen.add(chunk_id)
            chunks.append(chunk)
    return chunks, _chunk_digest(files)


def _point_id(chunk_id: str) -> str:
    return str(uuid.UUID(hashlib.sha256(chunk_id.encode("utf-8")).hexdigest()[:32]))


def _embed_text(chunk: dict[str, Any]) -> str:
    if chunk.get("embedding_text"):
        return str(chunk["embedding_text"])
    if chunk.get("chunk_type", "").startswith("table"):
        return str(chunk.get("search_text") or chunk.get("chunk_text") or "")
    return str(chunk.get("chunk_text") or "")


def _payload(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": chunk["chunk_id"],
        "chunk_index": chunk.get("chunk_index"),
        "parent_record_id": chunk.get("parent_record_id"),
        "source_record_ids": chunk.get("source_record_ids", []),
        "source_format": chunk.get("source_format"),
        "source_kind": chunk.get("source_kind"),
        "source_provider": chunk.get("source_provider"),
        "source_file": chunk.get("_source_file") or chunk.get("source_file") or chunk.get("_chunk_file"),
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "paragraph_index": chunk.get("paragraph_index"),
        "sheet_name": chunk.get("sheet_name"),
        "cell_range": chunk.get("cell_range"),
        "section_path": chunk.get("section_path"),
        "chunk_type": chunk.get("chunk_type"),
        "chunk_text": chunk.get("chunk_text"),
        "embedding_text": chunk.get("embedding_text"),
        "search_text": chunk.get("search_text"),
        "table_id": chunk.get("table_id"),
        "table_header": chunk.get("table_header", []),
        "column_headers": chunk.get("column_headers", []),
        "parent_row_labels": chunk.get("parent_row_labels", []),
        "row_labels": chunk.get("row_labels", []),
        "header_row_count": chunk.get("header_row_count"),
        "row_indices": chunk.get("row_indices", []),
        "row_matrix": chunk.get("row_matrix", []),
        "raw_matrix_sha256": chunk.get("raw_matrix_sha256"),
        "source_location": chunk.get("source_location"),
        "group_key": chunk.get("group_key"),
        "document_id": chunk.get("document_id"),
        "company_id": chunk.get("company_id"),
        "company_name": chunk.get("company_name"),
        "ticker": chunk.get("ticker"),
        "cik": chunk.get("cik"),
        "fiscal_year": chunk.get("fiscal_year"),
        "report_type": chunk.get("report_type"),
        "language": chunk.get("language"),
        "currency": chunk.get("currency"),
        "unit_scale": chunk.get("unit_scale"),
        "source_url": chunk.get("source_url"),
        "source_sha256": chunk.get("sha256") or chunk.get("source_sha256"),
        "table_title": chunk.get("table_title"),
        "table_context": chunk.get("table_context"),
        "measure_name": chunk.get("measure_name"),
        "value_kind": chunk.get("value_kind"),
        "region": chunk.get("region"),
        "period_years": chunk.get("period_years", []),
        "statement_family": chunk.get("statement_family"),
        "statement_scope": chunk.get("statement_scope"),
        "period_end": chunk.get("period_end"),
        "unit": chunk.get("unit"),
        "table_title_raw": chunk.get("table_title_raw"),
        "table_group_id": chunk.get("table_group_id"),
        "content_role": chunk.get("content_role"),
    }


def run(
    *,
    chunk_dir: Path = DEFAULT_CHUNK_DIR,
    qdrant_path: Path = DEFAULT_QDRANT_PATH,
    manifest_path: Path = DEFAULT_MANIFEST,
    model_path: Path | None = None,
    tokenizer_path: Path | None = None,
    collection: str = COLLECTION,
    upsert_batch_size: int | None = None,
    recreate_collection: bool = False,
    resume_existing: bool = False,
    batch_size: int = 8,
    max_length: int = 8192,
) -> dict[str, Any]:
    chunks, chunk_digest = _load_chunks(chunk_dir)
    encoder = BGEEmbeddingEncoder(
        model_path=model_path or DEFAULT_MODEL_PATH,
        tokenizer_path=tokenizer_path or DEFAULT_TOKENIZER_PATH,
        max_length=max_length,
    )
    model_metadata = encoder.metadata()

    from qdrant_client import QdrantClient, models

    client = QdrantClient(path=str(qdrant_path))
    try:
        collections = {item.name for item in client.get_collections().collections}
        cleanup_before_count: int | None = None
        cleanup_after_count: int | None = None
        cleanup_performed = False
        if collection in collections and recreate_collection:
            # Qdrant Local 1.19 在同名集合删除后重建时可能重新挂回旧 segment。
            # 明确删除该集合全部 points 并验零，才能保证不会留下陈旧点。
            cleanup_before_count = client.count(collection, exact=True).count
            client.delete(
                collection_name=collection,
                points_selector=models.FilterSelector(filter=models.Filter()),
                wait=True,
            )
            empty_count = client.count(collection, exact=True).count
            cleanup_after_count = empty_count
            cleanup_performed = True
            if empty_count != 0:
                raise RuntimeError(
                    f"Qdrant collection 清理后不是空集合：collection={collection}, count={empty_count}"
                )
        if collection not in collections:
            client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(
                    size=VECTOR_DIMENSION, distance=models.Distance.COSINE
                ),
            )
        if recreate_collection and client.count(collection, exact=True).count != 0:
            raise RuntimeError(f"Qdrant collection 重建前验零失败：{collection}")
        info = client.get_collection(collection)
        vector_config = info.config.params.vectors
        distance = getattr(vector_config.distance, "value", str(vector_config.distance))
        if vector_config.size != VECTOR_DIMENSION or str(distance).lower() != "cosine":
            raise ValueError(f"Qdrant 集合契约不匹配：{vector_config}")

        existing_count = client.count(collection, exact=True).count
        previous = None
        if manifest_path.is_file():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing_count and (
            not previous
            or previous.get("model", {}).get("model_sha256") != model_metadata["model_sha256"]
            or previous.get("chunk_digest") != chunk_digest
        ):
            raise RuntimeError(
                "Qdrant 已有点，但 Embedding 模型或切块摘要与当前不一致，"
                "拒绝混写；请先建立独立 Collection 或明确清理。"
            )

        existing_vectors: dict[str, Any] = {}
        existing_ids_valid = True
        if resume_existing and existing_count:
            expected_ids = {_point_id(chunk["chunk_id"]) for chunk in chunks}
            existing_points, _ = client.scroll(
                collection_name=collection,
                limit=max(existing_count, 1),
                with_payload=False,
                with_vectors=False,
            )
            existing_ids = {str(point.id) for point in existing_points}
            existing_ids_valid = existing_ids.issubset(expected_ids)
            if not existing_ids_valid:
                raise RuntimeError("Qdrant 断点续写发现不属于当前切块的点，拒绝混写")
            if existing_ids:
                stored = client.retrieve(
                    collection_name=collection,
                    ids=list(existing_ids),
                    with_payload=False,
                    with_vectors=True,
                )
                existing_vectors = {
                    str(point.id): point.vector
                    for point in stored
                    if point.vector is not None
                }
            if len(existing_vectors) != len(existing_ids):
                raise RuntimeError("Qdrant 断点续写缺少已有点向量，拒绝继续")

        missing_indices = [
            index
            for index, chunk in enumerate(chunks)
            if _point_id(chunk["chunk_id"]) not in existing_vectors
        ]
        vector_digest = hashlib.sha256()
        write_size = max(batch_size, upsert_batch_size or batch_size)
        for start in range(0, len(missing_indices), write_size):
            batch_indices = missing_indices[start : start + write_size]
            batch_chunks = [chunks[index] for index in batch_indices]
            batch_vectors = encoder.encode(
                [_embed_text(chunk) for chunk in batch_chunks], batch_size=batch_size
            )
            points = [
                models.PointStruct(
                    id=_point_id(chunk["chunk_id"]),
                    vector=batch_vectors[index].tolist(),
                    payload=_payload(chunk),
                )
                for index, chunk in enumerate(batch_chunks)
            ]
            for index, chunk in enumerate(batch_chunks):
                existing_vectors[_point_id(chunk["chunk_id"])] = batch_vectors[index].tolist()
            client.upsert(
                collection_name=collection,
                points=points,
                wait=True,
            )
            print(f"embedding: {min(start + write_size, len(missing_indices))}/{len(missing_indices)}")
        point_count = client.count(collection, exact=True).count
        if point_count != len(chunks):
            raise RuntimeError(f"Qdrant 点数不一致：expected={len(chunks)}, actual={point_count}")
        for chunk in chunks:
            vector = existing_vectors[_point_id(chunk["chunk_id"])]
            vector_digest.update(np.asarray(vector, dtype="<f4").tobytes())

        manifest = {
            "stage": "embedding",
            "collection": collection,
            "qdrant_path": str(qdrant_path),
            "chunk_dir": str(chunk_dir),
            "chunk_digest": chunk_digest,
            "chunk_count": len(chunks),
            "point_count": point_count,
            "vector_digest": vector_digest.hexdigest(),
            "collection_cleanup": {
                "requested": recreate_collection,
                "performed": cleanup_performed,
                "before_count": cleanup_before_count,
                "after_count": cleanup_after_count,
                "scope": "target_collection_points_only",
            },
            "resume_existing": resume_existing,
            "existing_ids_valid": existing_ids_valid,
            "missing_point_count_before_write": len(missing_indices),
            "model": model_metadata,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "retrieval_evaluation": "not_run",
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate BGE-M3 ONNX embeddings and upsert Qdrant.")
    parser.add_argument("--chunk-dir", type=Path, default=DEFAULT_CHUNK_DIR)
    parser.add_argument("--qdrant-path", type=Path, default=DEFAULT_QDRANT_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--tokenizer", type=Path, default=None)
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--upsert-batch-size", type=int, default=None)
    parser.add_argument("--recreate-collection", action="store_true")
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=8192)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                chunk_dir=args.chunk_dir,
                qdrant_path=args.qdrant_path,
                manifest_path=args.manifest,
                model_path=args.model,
                tokenizer_path=args.tokenizer,
                collection=args.collection,
                upsert_batch_size=args.upsert_batch_size,
                recreate_collection=args.recreate_collection,
                resume_existing=args.resume_existing,
                batch_size=args.batch_size,
                max_length=args.max_length,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
