from __future__ import annotations

"""重建 TCS 真实 PDF/DOCX/XLSX 数据及其独立 Qdrant collection。"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from collections import defaultdict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chunking.config import ChunkConfig
from embeddings.semantic_metadata import enrich_chunk
from embeddings.vectorize import _load_chunks, run as vectorize
import scripts.run_multiformat_annual_pipeline as pipeline


SOURCE_ROOT = ROOT / "data" / "annual_reports" / "real_multiformat_v2"
MANIFEST_PATHS = (SOURCE_ROOT / "pdf_manifest.jsonl", SOURCE_ROOT / "manifest.jsonl")
PROCESSED_ROOT = ROOT / "data" / "processed" / "annual_reports"
PARSED_DIR = PROCESSED_ROOT / "tcs_real_multiformat_v2_parsed"
CLEANED_DIR = PROCESSED_ROOT / "tcs_real_multiformat_v2_cleaned"
CHUNKS_DIR = PROCESSED_ROOT / "tcs_real_multiformat_v2_chunks"
REPORT_DIR = PROCESSED_ROOT / "tcs_real_multiformat_v2_reports"
COLLECTION = "annual_report_tcs_real_multiformat_v2"
EMBEDDING_MANIFEST = REPORT_DIR / "embedding.manifest.json"
ACCEPTANCE_PATH = ROOT / "data" / "evaluation" / "tcs_real_multiformat_v2.rebuild.json"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_manifest() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in MANIFEST_PATHS:
        records.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    ids = [str(record.get("document_id") or "") for record in records]
    if len(records) != 5 or not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("真实多格式 manifest 必须包含 5 个不重复的 document_id")
    for record in records:
        source = Path(str(record.get("source_file") or "")).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"来源文件不存在：{source}")
        if pipeline._sha256(source) != str(record.get("source_sha256") or ""):
            raise ValueError(f"来源文件 SHA-256 不匹配：{source}")
        record["source_file"] = str(source)
    return records


def _clear_stage_outputs() -> None:
    for directory in (PARSED_DIR, CLEANED_DIR, CHUNKS_DIR, REPORT_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.iterdir():
            if path.is_file():
                path.unlink()


def _configure_pipeline() -> None:
    pipeline.PARSED_DIR = PARSED_DIR
    pipeline.CLEANED_DIR = CLEANED_DIR
    pipeline.CHUNKS_DIR = CHUNKS_DIR
    pipeline.REPORT_DIR = REPORT_DIR


def _india_2022_evidence(chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for chunk in chunks:
        enrich_chunk(chunk)
        if (
            chunk.get("document_id") == "tcs_2023_annual_report"
            and chunk.get("page_start") == 296
            and chunk.get("region") == "India"
            and 2022 in chunk.get("period_years", [])
            and "9,547" in str(chunk.get("search_text") or "")
        ):
            return {
                key: chunk.get(key)
                for key in (
                    "chunk_id",
                    "document_id",
                    "page_start",
                    "table_id",
                    "region",
                    "period_years",
                    "currency",
                    "unit_scale",
                    "statement_scope",
                    "measure_name",
                )
            }
    return None


def _duplicate_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """区分真正重复与切块 overlap，避免清理合法的预留重叠。"""

    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        text = re.sub(
            r"\s+",
            " ",
            str(chunk.get("chunk_text") or chunk.get("search_text") or ""),
        ).strip().casefold()
        key = (
            str(chunk.get("document_id") or ""),
            str(chunk.get("chunk_type") or ""),
            str(chunk.get("table_id") or ""),
            str(chunk.get("page_start") or ""),
            text,
        )
        groups[key].append(chunk)
    duplicate_groups = [group for group in groups.values() if len(group) > 1]
    unexpected = [
        group
        for group in duplicate_groups
        if not any(int(chunk.get("overlap_token_count") or 0) > 0 for chunk in group)
    ]
    return {
        "exact_content_groups": len(duplicate_groups),
        "exact_content_extra": sum(len(group) - 1 for group in duplicate_groups),
        "overlap_chunks": sum(
            int(chunk.get("overlap_token_count") or 0) > 0 for chunk in chunks
        ),
        "duplicate_groups_with_overlap": sum(
            any(int(chunk.get("overlap_token_count") or 0) > 0 for chunk in group)
            for group in duplicate_groups
        ),
        "unexpected_non_overlap_duplicate_groups": len(unexpected),
        "overlap_duplicates_preserved": True,
    }


def _reuse_stage_results(manifest: list[dict[str, Any]]) -> dict[str, Any]:
    stages: dict[str, list[dict[str, Any]]] = {"parse": [], "clean": [], "chunk": []}
    for record in manifest:
        document_id = str(record["document_id"])
        required = {
            "parse": (PARSED_DIR / f"{document_id}.parsed.jsonl", REPORT_DIR / f"{document_id}.parse.report.json"),
            "clean": (CLEANED_DIR / f"{document_id}.cleaned.jsonl", REPORT_DIR / f"{document_id}.clean.report.json"),
            "chunk": (CHUNKS_DIR / f"{document_id}.chunks.jsonl", REPORT_DIR / f"{document_id}.chunk.score.json"),
        }
        for stage, (data_path, report_path) in required.items():
            if not data_path.is_file() or not report_path.is_file():
                raise FileNotFoundError(f"不能复用 {stage} 阶段，缺少文件：{data_path} 或 {report_path}")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if stage == "chunk":
                passed = bool(report.get("passed"))
                result = {
                    "stage": "chunk",
                    "document_id": document_id,
                    "source_format": record["source_format"],
                    "chunk_count": report.get("chunk_count"),
                    "score": report.get("overall_score"),
                    "report": str(report_path),
                    "passed": passed,
                }
            else:
                passed = bool(report.get("passed"))
                result = report
            if not passed:
                raise RuntimeError(f"不能复用未通过的 {stage} 阶段：{document_id}")
            stages[stage].append(result)
    return stages


def run(*, reuse_stages: bool = False, resume_embedding: bool = False) -> dict[str, Any]:
    previous_result = (
        json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8"))
        if ACCEPTANCE_PATH.is_file()
        else {}
    )
    manifest = _load_manifest()
    _configure_pipeline()
    if reuse_stages:
        stages = _reuse_stage_results(manifest)
    else:
        _clear_stage_outputs()
        stages: dict[str, Any] = {}
        stages["parse"] = pipeline._run_stage("parse", manifest, pipeline._parse_one)
        stages["clean"] = pipeline._run_stage("clean", manifest, pipeline._clean_one)
        config = ChunkConfig()
        stages["chunk"] = pipeline._run_stage(
            "chunk", manifest, lambda record: pipeline._chunk_one(record, config)
        )

    chunks, chunk_digest = _load_chunks(CHUNKS_DIR)
    chunk_ids = [str(chunk["chunk_id"]) for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("切块输出存在重复 chunk_id，已停止写库")

    evidence = _india_2022_evidence(chunks)
    if evidence is None:
        raise RuntimeError("未找到结构化的 India 2022 / 9,547 证据，已停止写库")
    duplicate_summary = _duplicate_summary(chunks)

    embedding = vectorize(
        chunk_dir=CHUNKS_DIR,
        qdrant_path=pipeline.DEFAULT_QDRANT_PATH,
        manifest_path=EMBEDDING_MANIFEST,
        collection=COLLECTION,
        recreate_collection=not resume_embedding,
        resume_existing=resume_embedding,
        batch_size=32,
        upsert_batch_size=64,
    )
    checks = {
        "source_count_is_5": len(manifest) == 5,
        "document_ids_unique": len({record["document_id"] for record in manifest}) == 5,
        "chunk_ids_unique": len(chunk_ids) == len(set(chunk_ids)),
        "point_count_matches_chunks": embedding.get("point_count") == len(chunks),
        "india_2022_structured_evidence": evidence is not None,
        "no_non_overlap_content_duplicates": duplicate_summary[
            "unexpected_non_overlap_duplicate_groups"
        ] == 0,
        "overlap_duplicates_preserved": duplicate_summary["overlap_duplicates_preserved"],
        "target_collection_recreated": embedding.get("collection") == COLLECTION,
        "target_collection_cleared_before_write": (
            (
                embedding.get("collection_cleanup", {}).get("requested") is True
                and embedding.get("collection_cleanup", {}).get("after_count") == 0
            )
            or (
                resume_embedding
                and embedding.get("resume_existing") is True
                and embedding.get("existing_ids_valid") is True
            )
        ),
    }
    previous_cleanup = (
        previous_result.get("cleanup_scope", {}).get("database_cleanup_history", {})
    )
    current_cleanup = embedding.get("collection_cleanup", {})
    initial_rebuild_cleared = bool(
        (
            current_cleanup.get("performed") is True
            and current_cleanup.get("after_count") == 0
        )
        or previous_cleanup.get("initial_rebuild_cleared_before_write") is True
    )
    resumed_after_interruption = bool(
        previous_cleanup.get("resume_completed_after_interrupted_writes") is True
        or (
            resume_embedding
            and int(embedding.get("missing_point_count_before_write") or 0) > 0
        )
    )
    result = {
        "stage": "tcs_real_multiformat_v2_rebuild",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "collection": COLLECTION,
        "qdrant_path": str(pipeline.DEFAULT_QDRANT_PATH),
        "manifest_files": [str(path) for path in MANIFEST_PATHS],
        "document_count": len(manifest),
        "chunk_count": len(chunks),
        "chunk_digest": chunk_digest,
        "point_count": embedding.get("point_count"),
        "india_2022_evidence": evidence,
        "duplicate_summary": duplicate_summary,
        "stages": stages,
        "checks": checks,
        "passed": all(checks.values()),
        "cleanup_scope": {
            "stage_output_directories": [str(path) for path in (PARSED_DIR, CLEANED_DIR, CHUNKS_DIR, REPORT_DIR)],
            "qdrant_collection": COLLECTION,
            "other_collections_touched": False,
            "database_cleanup_history": {
                "target_collection_only": True,
                "initial_rebuild_cleared_before_write": initial_rebuild_cleared,
                "resume_completed_after_interrupted_writes": resumed_after_interruption,
                "note": "断点续写只补缺失点；不删除 overlap 预留块。",
            },
        },
    }
    _write_json(ACCEPTANCE_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="重建 TCS 真实多格式 RAG collection")
    parser.add_argument(
        "--reuse-stages",
        action="store_true",
        help="复用已通过校验的解析、清洗、切块产物，只重建目标 collection",
    )
    parser.add_argument(
        "--resume-embedding",
        action="store_true",
        help="校验当前目标 collection 的已有点，只补写缺失向量",
    )
    args = parser.parse_args()
    try:
        result = run(reuse_stages=args.reuse_stages, resume_embedding=args.resume_embedding)
    except Exception as exc:
        failure = {
            "stage": "tcs_real_multiformat_v2_rebuild",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _write_json(ACCEPTANCE_PATH, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
