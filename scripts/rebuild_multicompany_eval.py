from __future__ import annotations

"""基于已有三份年报文件，重建多公司 PDF 的解析到向量库闭环。"""

import argparse
import gc
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chunking.config import ChunkConfig
from chunking.io import read_jsonl, write_jsonl
from chunking.pipeline import chunk_records
from chunking.score import score_chunks
from cleaners.pdf_cleaner import clean_pdf_file
from embeddings.vectorize import run as vectorize
from parsers.pdf_parser import parse_pdf, write_jsonl as write_parsed_jsonl, write_preview


DEFAULT_MANIFEST = ROOT / "data" / "annual_reports" / "multicompany_eval_v1" / "manifest.jsonl"
DEFAULT_PROCESSED_ROOT = ROOT / "data" / "processed" / "annual_reports"
DEFAULT_QDRANT_PATH = ROOT / "xianlian_multicompany_eval"
DEFAULT_COLLECTION = "annual_report_multicompany_eval_v1"
DEFAULT_REPORT = ROOT / "data" / "evaluation" / "multicompany_pdf_rag.rebuild.json"

METADATA_KEYS = (
    "company_id",
    "company_name",
    "ticker",
    "cik",
    "document_id",
    "fiscal_year",
    "report_type",
    "language",
    "currency",
    "unit_scale",
    "source_url",
    "source_provider",
    "source_kind",
    "source_sha256",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normal_path(value: Any) -> str:
    return str(Path(str(value)).resolve())


def _raw_path(record: dict[str, Any]) -> Path:
    value = record.get("raw_path") or record.get("source_file")
    if not value:
        raise ValueError(f"manifest 缺少 raw_path/source_file：{record}")
    return Path(str(value)).resolve()


def _manifest_metadata(record: dict[str, Any]) -> dict[str, Any]:
    metadata = {key: record.get(key) for key in METADATA_KEYS}
    metadata["sha256"] = record.get("source_sha256")
    return metadata


def _enrich_cleaned_records(
    cleaned_path: Path, source: Path, manifest_record: dict[str, Any]
) -> list[dict[str, Any]]:
    records = read_jsonl(cleaned_path)
    metadata = _manifest_metadata(manifest_record)
    for record in records:
        record.update(metadata)
        record["source_file"] = str(source)
        page_number = record.get("page_number")
        if record.get("record_type") == "table":
            record["source_location"] = (
                f"page={page_number};table={record.get('table_id', 'unknown-table')}"
            )
        else:
            record["source_location"] = f"page={page_number}"
    write_jsonl(cleaned_path, records)
    return records


def _parse_and_clean(
    manifest_record: dict[str, Any], parsed_dir: Path, cleaned_dir: Path, reports_dir: Path
) -> dict[str, Any]:
    document_id = str(manifest_record["document_id"])
    source = _raw_path(manifest_record)
    if not source.is_file():
        raise FileNotFoundError(f"manifest raw_path 不存在：{source}")

    parsed = parse_pdf(source)
    parsed_path = parsed_dir / f"{document_id}.parsed.jsonl"
    preview_path = parsed_dir / f"{document_id}.parsed.preview.txt"
    parse_report_path = reports_dir / f"{document_id}.parse.report.json"
    cleaned_path = cleaned_dir / f"{document_id}.cleaned.jsonl"
    clean_report_path = reports_dir / f"{document_id}.clean.report.json"
    write_parsed_jsonl(parsed, parsed_path)
    write_preview(parsed, preview_path)

    pages = parsed.pages
    parse_report = {
        "stage": "parse",
        "document_id": document_id,
        "source_file": str(source),
        "source_format": "pdf",
        "page_count": parsed.page_count,
        "text_pages": sum(bool(page.text.strip()) for page in pages),
        "table_pages": sum(page.table_count > 0 for page in pages),
        "table_count": sum(page.table_count for page in pages),
        "image_or_ocr_pending_pages": sum(
            "ocr:pending" in page.parse_branches for page in pages
        ),
        "error_pages": sum(page.status == "error" for page in pages),
        "parsed_jsonl": str(parsed_path),
        "preview": str(preview_path),
    }
    parse_report["passed"] = (
        parse_report["page_count"] > 0
        and parse_report["text_pages"] > 0
        and parse_report["error_pages"] == 0
    )
    _write_json(parse_report_path, parse_report)

    clean_report = clean_pdf_file(parsed_path, cleaned_path, clean_report_path)
    cleaned_records = _enrich_cleaned_records(cleaned_path, source, manifest_record)
    clean_report.update({"document_id": document_id, "source_file": str(source)})
    _write_json(clean_report_path, clean_report)
    return {
        "document_id": document_id,
        "source_file": str(source),
        "parsed_records": len(parsed.pages),
        "cleaned_records": len(cleaned_records),
        "parse_report": str(parse_report_path),
        "clean_report": str(clean_report_path),
        "passed": bool(parse_report["passed"])
        and bool(clean_report.get("validation", {}).get("passed")),
    }


def _chunk_one(
    manifest_record: dict[str, Any],
    cleaned_dir: Path,
    chunks_dir: Path,
    reports_dir: Path,
    config: ChunkConfig,
) -> dict[str, Any]:
    document_id = str(manifest_record["document_id"])
    cleaned_path = cleaned_dir / f"{document_id}.cleaned.jsonl"
    records = read_jsonl(cleaned_path)
    chunks = chunk_records(records, config)
    shared = _manifest_metadata(manifest_record)
    for chunk in chunks:
        for key, value in shared.items():
            if chunk.get(key) in (None, "") and value not in (None, ""):
                chunk[key] = value
        chunk["source_file"] = str(_raw_path(manifest_record))
        chunk.setdefault("paragraph_index", None)
        chunk.setdefault("sheet_name", None)
        chunk.setdefault("cell_range", None)
        chunk["embedding_text"] = str(chunk.get("chunk_text") or chunk.get("search_text") or "")
        if not chunk.get("search_text"):
            chunk["search_text"] = chunk["embedding_text"]

    chunk_path = chunks_dir / f"{document_id}.chunks.jsonl"
    score_path = reports_dir / f"{document_id}.chunk.score.json"
    write_jsonl(chunk_path, chunks)
    score = score_chunks(records, chunks, config)
    comparison = score.get("comparison", {})
    quality_passed = (
        bool(chunks)
        and float(score.get("overall_score", 0.0)) >= 99.0
        and comparison.get("table_lost_body_rows", 0) == 0
        and comparison.get("internal_duplicate_tokens", 0) == 0
    )
    score.update(
        {
            "document_id": document_id,
            "source_format": "pdf",
            "passed": quality_passed,
            "pass_rule": "overall_score>=99; table_lost_body_rows=0; internal_duplicate_tokens=0",
        }
    )
    _write_json(score_path, score)
    return {
        "document_id": document_id,
        "chunk_count": len(chunks),
        "chunk_file": str(chunk_path),
        "chunk_report": str(score_path),
        "empty_chunk_count": sum(not str(item.get("chunk_text") or "").strip() for item in chunks),
        "passed": bool(score["passed"]),
    }


def _scroll_all(client: Any, collection: str) -> list[Any]:
    points: list[Any] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None or not batch:
            return points


def _snapshot_collection(qdrant_path: Path, collection: str) -> dict[str, Any]:
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(qdrant_path))
    try:
        names = {item.name for item in client.get_collections().collections}
        if collection not in names:
            return {"collection": collection, "exists": False, "count": 0, "source_files": []}
        points = _scroll_all(client, collection)
        payloads = [point.payload or {} for point in points]
        return {
            "collection": collection,
            "exists": True,
            "count": len(points),
            "source_files": sorted(
                {str(payload.get("source_file")) for payload in payloads if payload.get("source_file")}
            ),
            "company_ids": dict(Counter(str(payload.get("company_id")) for payload in payloads)),
            "missing_filter_fields": {
                key: sum(payload.get(key) in (None, "") for payload in payloads)
                for key in ("company_id", "document_id", "fiscal_year")
            },
        }
    finally:
        client.close()


def _clear_collection(qdrant_path: Path, collection: str) -> dict[str, Any]:
    from qdrant_client import QdrantClient

    storage_dir = (qdrant_path / "collection" / collection).resolve()
    expected_parent = (qdrant_path / "collection").resolve()
    if storage_dir.parent != expected_parent:
        raise RuntimeError(f"拒绝清理非目标集合目录：{storage_dir}")
    client = QdrantClient(path=str(qdrant_path))
    try:
        names = {item.name for item in client.get_collections().collections}
        before = client.count(collection, exact=True).count if collection in names else 0
        deleted = collection not in names or bool(client.delete_collection(collection))
        after_names = {item.name for item in client.get_collections().collections}
        result = {
            "collection": collection,
            "points_before": before,
            "deleted": deleted,
            "points_after": 0 if collection not in after_names else client.count(collection, exact=True).count,
        }
    finally:
        client.close()
    del client
    gc.collect()
    physical_deleted = False
    if storage_dir.is_dir():
        for attempt in range(20):
            try:
                shutil.rmtree(storage_dir)
                physical_deleted = True
                break
            except PermissionError:
                if attempt == 19:
                    raise
                gc.collect()
                time.sleep(0.25)
    result["physical_storage"] = str(storage_dir)
    result["physical_deleted"] = physical_deleted
    return result


def _verify(
    qdrant_path: Path,
    collection: str,
    chunk_files: list[Path],
) -> dict[str, Any]:
    from qdrant_client import QdrantClient, models

    chunks = [item for path in chunk_files for item in _read_jsonl(path)]
    expected_by_id = {str(item["chunk_id"]): item for item in chunks}
    client = QdrantClient(path=str(qdrant_path))
    try:
        info = client.get_collection(collection)
        vector_config = info.config.params.vectors
        distance = getattr(vector_config.distance, "value", str(vector_config.distance))
        points = _scroll_all(client, collection)
        payloads = [point.payload or {} for point in points]
        field_coverage = {
            key: sum(payload.get(key) not in (None, "") for payload in payloads)
            for key in (
                "source_file",
                "source_location",
                "company_id",
                "document_id",
                "fiscal_year",
                "source_url",
                "chunk_text",
            )
        }
        company_counts = dict(Counter(str(payload.get("company_id")) for payload in payloads))
        source_counts = dict(Counter(str(payload.get("source_file")) for payload in payloads))
        blank_chunks = [payload.get("chunk_id") for payload in payloads if not str(payload.get("chunk_text") or "").strip()]
        table_payloads = [
            payload
            for payload in payloads
            if str(payload.get("chunk_type") or "").startswith("table")
        ]
        table_hash_match = all(
            payload.get("raw_matrix_sha256") == expected_by_id.get(str(payload.get("chunk_id")), {}).get("raw_matrix_sha256")
            for payload in table_payloads
        )
        table_rows_match = all(
            payload.get("row_matrix")
            == expected_by_id.get(str(payload.get("chunk_id")), {}).get("row_matrix")
            and payload.get("row_indices")
            == expected_by_id.get(str(payload.get("chunk_id")), {}).get("row_indices")
            for payload in table_payloads
        )
        filter_counts: dict[str, int] = {}
        for company_id in sorted(company_counts):
            filtered, _ = client.scroll(
                collection_name=collection,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(key="company_id", match=models.MatchValue(value=company_id))]
                ),
                limit=10000,
                with_payload=False,
                with_vectors=False,
            )
            filter_counts[company_id] = len(filtered)
        checks = {
            "point_count_matches_chunks": len(points) == len(chunks),
            "vector_dimension": vector_config.size == 1024,
            "distance_cosine": str(distance).lower() == "cosine",
            "all_filter_fields_present": all(
                field_coverage[key] == len(points)
                for key in ("source_file", "company_id", "document_id", "fiscal_year")
            ),
            "no_blank_chunk_text": not blank_chunks,
            "table_hashes_match_files": table_hash_match,
            "table_rows_match_files": table_rows_match,
            "company_filter_matches_distribution": filter_counts == company_counts,
        }
        return {
            "count": len(points),
            "vector_size": vector_config.size,
            "distance": str(distance),
            "field_coverage": field_coverage,
            "company_counts": company_counts,
            "source_counts": source_counts,
            "filter_counts": filter_counts,
            "blank_chunk_ids": blank_chunks,
            "table_count": len(table_payloads),
            "table_hash_match": table_hash_match,
            "table_rows_match": table_rows_match,
            "checks": checks,
            "passed": all(checks.values()),
        }
    finally:
        client.close()


def run(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    processed_root: Path = DEFAULT_PROCESSED_ROOT,
    qdrant_path: Path = DEFAULT_QDRANT_PATH,
    collection: str = DEFAULT_COLLECTION,
    report_path: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    manifest = _read_jsonl(manifest_path)
    if not manifest:
        raise ValueError(f"manifest 为空：{manifest_path}")
    if any(item.get("source_format") != "pdf" for item in manifest):
        raise ValueError("本次重建只接受 source_format=pdf 的输入")

    before = _snapshot_collection(qdrant_path, collection)
    indexed_sources = {_normal_path(path) for path in before.get("source_files", [])}
    manifest_sources = {_normal_path(_raw_path(item)) for item in manifest}
    database_selection = "existing_source_files"
    if not indexed_sources and before.get("count", 0) == 0:
        indexed_sources = manifest_sources
        database_selection = "empty_database_after_verified_cleanup"
    if indexed_sources != manifest_sources:
        raise RuntimeError(
            "数据库中的 source_file 集合与 manifest 不一致，停止清理以避免误收录："
            f" database_only={sorted(indexed_sources - manifest_sources)},"
            f" manifest_only={sorted(manifest_sources - indexed_sources)}"
        )

    parsed_dir = processed_root / "multicompany_eval_v1_parsed"
    cleaned_dir = processed_root / "multicompany_eval_v1_cleaned"
    chunks_dir = processed_root / "multicompany_eval_v1_chunks"
    reports_dir = processed_root / "multicompany_eval_v1_reports"
    for path in (parsed_dir, cleaned_dir, chunks_dir, reports_dir):
        path.mkdir(parents=True, exist_ok=True)

    config = ChunkConfig()
    parse_clean_results = [
        _parse_and_clean(item, parsed_dir, cleaned_dir, reports_dir) for item in manifest
    ]
    if not all(item["passed"] for item in parse_clean_results):
        raise RuntimeError("解析或清洗未全部通过，未清理向量集合")

    chunk_results = [
        _chunk_one(item, cleaned_dir, chunks_dir, reports_dir, config) for item in manifest
    ]
    if not all(item["passed"] for item in chunk_results):
        raise RuntimeError("切块完整性未全部通过，未清理向量集合")

    chunk_files = sorted(chunks_dir.glob("*.chunks.jsonl"))
    clear_result = _clear_collection(qdrant_path, collection)
    embedding_manifest = vectorize(
        chunk_dir=chunks_dir,
        qdrant_path=qdrant_path,
        manifest_path=processed_root / "multicompany_eval_v1_embedding.manifest.json",
        collection=collection,
        recreate_collection=False,
        batch_size=16,
        max_length=8192,
    )
    verification = _verify(qdrant_path, collection, chunk_files)
    result = {
        "stage": "multicompany_pdf_rag_rebuild",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path),
        "database_before": before,
        "database_selection": database_selection,
        "selected_source_files": sorted(indexed_sources),
        "parse_clean": parse_clean_results,
        "chunk": chunk_results,
        "database_clear": clear_result,
        "embedding": {
            "collection": embedding_manifest.get("collection"),
            "chunk_count": embedding_manifest.get("chunk_count"),
            "point_count": embedding_manifest.get("point_count"),
            "model": embedding_manifest.get("model"),
        },
        "verification": verification,
        "storage_note": (
            "Qdrant payload 保存命中表格片段的 table_header、row_indices、row_matrix "
            "及完整 raw_matrix 的 SHA-256；完整 raw_matrix 保留在 UTF-8 切块 JSONL，"
            "避免在每个向量点重复整表。"
        ),
        "passed": bool(verification["passed"]),
    }
    _write_json(report_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the isolated multi-company annual-report vector collection.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--processed-root", type=Path, default=DEFAULT_PROCESSED_ROOT)
    parser.add_argument("--qdrant-path", type=Path, default=DEFAULT_QDRANT_PATH)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = run(
        manifest_path=args.manifest.resolve(),
        processed_root=args.processed_root.resolve(),
        qdrant_path=args.qdrant_path.resolve(),
        collection=args.collection,
        report_path=args.report.resolve(),
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
