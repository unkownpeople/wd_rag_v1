from __future__ import annotations

"""执行 3 PDF + 3 DOCX + 3 XLSX 的独立解析、清洗、切块、向量化和检索评测。"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chunking.config import ChunkConfig
from chunking.identity import make_record_id
from chunking.io import read_jsonl, write_jsonl
from chunking.pipeline import chunk_records
from chunking.score import score_chunks
from embeddings.evaluate import run as evaluate_retrieval
from embeddings.retrieve import QdrantRetriever, assemble_context
from embeddings.vectorize import DEFAULT_QDRANT_PATH, run as vectorize
from parsers.document_parser import parse_document, write_document_jsonl, write_document_preview


MULTIFORMAT_ROOT = ROOT / "data" / "annual_reports" / "multiformat_v1"
MANIFEST_PATH = MULTIFORMAT_ROOT / "manifest.jsonl"
PROCESSED_ROOT = ROOT / "data" / "processed" / "annual_reports"
PARSED_DIR = PROCESSED_ROOT / "multiformat_v1_parsed"
CLEANED_DIR = PROCESSED_ROOT / "multiformat_v1_cleaned"
CHUNKS_DIR = PROCESSED_ROOT / "multiformat_v1_chunks"
REPORT_DIR = PROCESSED_ROOT / "multiformat_v1_reports"
EVALUATION_DIR = ROOT / "data" / "evaluation"
CASES_PATH = EVALUATION_DIR / "annual_report_multiformat_eval.jsonl"
COLLECTION = "annual_report_multiformat_v1"
EMBEDDING_MANIFEST = REPORT_DIR / "embedding.manifest.json"
ACCEPTANCE_PATH = EVALUATION_DIR / "annual_report_multiformat.acceptance.json"
BASELINE_COLLECTION = "annual_report_pdf_chunks_v2"
BASELINE_CHUNK_DIR = PROCESSED_ROOT / "pdf_chunks"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest() -> list[dict[str, Any]]:
    records = [
        json.loads(line)
        for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(records) != 9:
        raise ValueError(f"多格式 manifest 必须有 9 条记录，实际为 {len(records)}")
    formats = {str(record.get("source_format")) for record in records}
    counts = {fmt: sum(record.get("source_format") == fmt for record in records) for fmt in ("pdf", "docx", "xlsx")}
    if formats != {"pdf", "docx", "xlsx"} or any(counts[fmt] != 3 for fmt in counts):
        raise ValueError(f"多格式 manifest 必须是 PDF/DOCX/XLSX 各 3 条：{counts}")
    ids = [str(record.get("document_id") or "") for record in records]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("document_id 必须存在且唯一")
    for record in records:
        source = Path(str(record.get("source_file") or "")).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"manifest source_file 不存在：{source}")
        expected_hash = str(record.get("source_sha256") or "")
        actual_hash = _sha256(source)
        if expected_hash != actual_hash:
            raise ValueError(f"source_sha256 不匹配：{source}")
        record["source_file"] = str(source)
    return records


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(path, records)


def _run_stage(
    stage: str,
    manifest: list[dict[str, Any]],
    worker: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """逐文档执行阶段；首个失败立即停止后续阶段并留下失败记录。"""

    results: list[dict[str, Any]] = []
    for record in manifest:
        document_id = str(record.get("document_id") or "unknown")
        failure_path = REPORT_DIR / f"{document_id}.{stage}.failure.report.json"
        try:
            result = worker(record)
        except Exception as exc:
            failure = {
                "stage": stage,
                "document_id": document_id,
                "source_format": record.get("source_format"),
                "passed": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "stopped_before": "next_stage",
            }
            _write_json(failure_path, failure)
            raise RuntimeError(
                f"{stage} 阶段失败：document_id={document_id}；"
                f"已停止后续阶段；failure_report={failure_path}"
            ) from exc
        results.append(result)
        if not result.get("passed"):
            failure = {
                **result,
                "stage": stage,
                "document_id": document_id,
                "passed": False,
                "error_type": "StageValidationError",
                "error": "阶段结果 passed=false",
                "stopped_before": "next_stage",
            }
            _write_json(failure_path, failure)
            raise RuntimeError(
                f"{stage} 阶段校验失败：document_id={document_id}；"
                f"已停止后续阶段；failure_report={failure_path}"
            )
    return results


def _parse_one(record: dict[str, Any]) -> dict[str, Any]:
    document_id = str(record["document_id"])
    source = Path(str(record["source_file"]))
    parsed = parse_document(source)
    jsonl_path = PARSED_DIR / f"{document_id}.parsed.jsonl"
    preview_path = PARSED_DIR / f"{document_id}.parsed.preview.txt"
    write_document_jsonl(parsed, jsonl_path)
    write_document_preview(parsed, preview_path)
    report = {
        "stage": "parse",
        "document_id": document_id,
        "source_file": str(source),
        "source_format": parsed.source_format,
        "record_count": len(parsed.records),
        "metadata": parsed.metadata,
        "warnings": parsed.warnings,
        "output_jsonl": str(jsonl_path),
        "output_preview": str(preview_path),
        "passed": bool(parsed.records) and parsed.source_format == record["source_format"],
    }
    _write_json(REPORT_DIR / f"{document_id}.parse.report.json", report)
    return report


def _clean_one(record: dict[str, Any]) -> dict[str, Any]:
    from cleaners.excel_cleaner import clean_excel_file
    from cleaners.pdf_cleaner import clean_pdf_file
    from cleaners.word_cleaner import clean_word_file

    document_id = str(record["document_id"])
    source_format = str(record["source_format"])
    parsed_path = PARSED_DIR / f"{document_id}.parsed.jsonl"
    cleaned_path = CLEANED_DIR / f"{document_id}.cleaned.jsonl"
    report_path = REPORT_DIR / f"{document_id}.clean.report.json"
    if source_format == "pdf":
        cleaner = clean_pdf_file
    elif source_format == "docx":
        cleaner = clean_word_file
    elif source_format == "xlsx":
        cleaner = clean_excel_file
    else:
        raise ValueError(f"不支持的清洗格式：{source_format}")
    report = cleaner(parsed_path, cleaned_path, report_path)
    records = read_jsonl(cleaned_path)
    _enrich_cleaned_records(records, record)
    _write_records(cleaned_path, records)
    report["document_id"] = document_id
    report["source_kind"] = record["source_kind"]
    report["passed"] = bool(report.get("validation", {}).get("passed"))
    _write_json(report_path, report)
    return {
        "stage": "clean",
        "document_id": document_id,
        "source_format": source_format,
        "record_count": len(records),
        "report": str(report_path),
        "passed": report["passed"],
    }


def _enrich_cleaned_records(records: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    """把活动 manifest 元数据复制到清洗结果，保留各格式原始定位字段。"""

    shared = {
        key: manifest.get(key)
        for key in (
            "document_id",
            "company_id",
            "company_name",
            "ticker",
            "fiscal_year",
            "fiscal_years",
            "report_type",
            "language",
            "currency",
            "unit_scale",
            "source_kind",
            "source_sha256",
            "source_url",
        )
    }
    paragraph_index = 0
    for item in records:
        item.update(shared)
        item["source_file"] = manifest["source_file"]
        if manifest["source_format"] == "docx":
            if item.get("record_type") == "text":
                item["paragraph_index"] = paragraph_index
                paragraph_index += 1
            else:
                item["paragraph_index"] = None
        elif manifest["source_format"] == "xlsx":
            location = item.get("location") if isinstance(item.get("location"), dict) else {}
            item["sheet_name"] = location.get("sheet")
            item["cell_range"] = location.get("range")


def _location_for_chunk(chunk: dict[str, Any], source_record: dict[str, Any] | None) -> str | None:
    source_format = str(chunk.get("source_format") or "")
    if source_format == "pdf":
        return f"page={chunk.get('page_start')}" if chunk.get("page_start") is not None else None
    if source_format == "docx":
        if chunk.get("table_id"):
            return f"table_id={chunk['table_id']}"
        if chunk.get("paragraph_index") is not None:
            return f"paragraph_index={chunk['paragraph_index']}"
        return None
    if source_format == "xlsx" and source_record:
        location = source_record.get("location") if isinstance(source_record.get("location"), dict) else {}
        sheet = source_record.get("sheet_name") or location.get("sheet")
        cell_range = source_record.get("cell_range") or location.get("range")
        if sheet or cell_range:
            return f"sheet={sheet};range={cell_range}"
    return chunk.get("source_location")


def _enrich_chunks(chunks: list[dict[str, Any]], records: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    record_by_id = {
        make_record_id(record, index): record
        for index, record in enumerate(records)
    }
    shared = {
        key: manifest.get(key)
        for key in (
            "document_id",
            "company_id",
            "company_name",
            "ticker",
            "fiscal_year",
            "fiscal_years",
            "report_type",
            "language",
            "currency",
            "unit_scale",
            "source_kind",
            "source_sha256",
            "source_url",
        )
    }
    for chunk in chunks:
        chunk.update(shared)
        chunk["source_file"] = manifest["source_file"]
        source_record = next(
            (record_by_id.get(source_id) for source_id in chunk.get("source_record_ids", []) if record_by_id.get(source_id)),
            None,
        )
        if manifest["source_format"] == "docx":
            chunk["paragraph_index"] = (
                source_record.get("paragraph_index") if source_record and chunk.get("chunk_type") == "text" else None
            )
            if source_record and source_record.get("table_id"):
                chunk["table_id"] = source_record["table_id"]
        elif manifest["source_format"] == "xlsx" and source_record:
            location = source_record.get("location") if isinstance(source_record.get("location"), dict) else {}
            chunk["sheet_name"] = source_record.get("sheet_name") or location.get("sheet")
            chunk["cell_range"] = source_record.get("cell_range") or location.get("range")
        elif manifest["source_format"] == "pdf":
            chunk.setdefault("paragraph_index", None)
            chunk.setdefault("sheet_name", None)
            chunk.setdefault("cell_range", None)
        chunk["embedding_text"] = str(chunk.get("chunk_text") or chunk.get("search_text") or "")
        if not chunk.get("search_text"):
            chunk["search_text"] = chunk["embedding_text"]
        chunk["source_location"] = _location_for_chunk(chunk, source_record)


def _chunk_one(record: dict[str, Any], config: ChunkConfig) -> dict[str, Any]:
    document_id = str(record["document_id"])
    cleaned_path = CLEANED_DIR / f"{document_id}.cleaned.jsonl"
    records = read_jsonl(cleaned_path)
    chunks = chunk_records(records, config)
    _enrich_chunks(chunks, records, record)
    chunk_path = CHUNKS_DIR / f"{document_id}.chunks.jsonl"
    _write_records(chunk_path, chunks)
    score = score_chunks(records, chunks, config)
    score["document_id"] = document_id
    score["source_format"] = record["source_format"]
    score["source_kind"] = record["source_kind"]
    score["passed"] = bool(chunks) and all(
        item.get("lost_tokens", 0) == 0
        and item.get("lost_body_rows", 0) == 0
        and item.get("internal_duplicate_tokens", 0) == 0
        for item in score.get("text", {}).get("records", []) + score.get("tables", {}).get("records", [])
    )
    score_path = REPORT_DIR / f"{document_id}.chunk.score.json"
    _write_json(score_path, score)
    return {
        "stage": "chunk",
        "document_id": document_id,
        "source_format": record["source_format"],
        "chunk_count": len(chunks),
        "score": score.get("overall_score"),
        "tokenizer": config.tokenizer_name,
        "report": str(score_path),
        "passed": score["passed"],
    }


def _baseline_snapshot() -> dict[str, Any]:
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(DEFAULT_QDRANT_PATH))
    try:
        names = {item.name for item in client.get_collections().collections}
        if BASELINE_COLLECTION not in names:
            return {"collection": BASELINE_COLLECTION, "exists": False, "passed": False}
        info = client.get_collection(BASELINE_COLLECTION)
        vector_config = info.config.params.vectors
        distance = getattr(vector_config.distance, "value", str(vector_config.distance))
        count = client.count(BASELINE_COLLECTION, exact=True).count
        return {
            "collection": BASELINE_COLLECTION,
            "exists": True,
            "point_count": count,
            "vector_size": vector_config.size,
            "distance": str(distance),
            "chunk_dir": str(BASELINE_CHUNK_DIR),
            "passed": count == 1491 and vector_config.size == 1024 and str(distance).lower() == "cosine",
        }
    finally:
        client.close()


def _generate_cases(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从固定切块内容生成可复现的 3*9 答案问题，再附加 5 条无答案问题。"""

    cases: list[dict[str, Any]] = []

    def add_case(record: dict[str, Any], chunk: dict[str, Any], kind: str, query: str) -> None:
        source_format = str(record["source_format"])
        case: dict[str, Any] = {
            "case_id": f"{record['document_id']}_{kind}",
            "query": query,
            "answerable": True,
            "filters": {
                "document_id": record["document_id"],
                "company_id": record["company_id"],
                "fiscal_year": record["fiscal_year"],
                "source_format": source_format,
            },
            "expected_chunk_ids": [chunk["chunk_id"]],
            "expected_source_suffix": str(record["source_file"]).replace("\\", "/").split("/")[-1],
            "expected_document_id": record["document_id"],
            "expected_company_id": record["company_id"],
            "expected_fiscal_year": record["fiscal_year"],
            "expected_source_format": source_format,
            "expected_page": chunk.get("page_start") if source_format == "pdf" else None,
            "expected_table_id": chunk.get("table_id") if kind == "table" else None,
            "expected_paragraph_index": chunk.get("paragraph_index") if source_format == "docx" and kind == "text" else None,
            "expected_sheet_name": chunk.get("sheet_name") if source_format == "xlsx" else None,
            "expected_cell_range": chunk.get("cell_range") if source_format == "xlsx" else None,
        }
        cases.append(case)

    for record in manifest:
        chunks = [
            json.loads(line)
            for line in (CHUNKS_DIR / f"{record['document_id']}.chunks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        text_chunks = [chunk for chunk in chunks if chunk.get("chunk_type") == "text"]
        table_chunks = [chunk for chunk in chunks if str(chunk.get("chunk_type") or "").startswith("table")]
        if not text_chunks and not table_chunks:
            raise ValueError(f"文档没有可评测切块：{record['document_id']}")

        text_chunk = text_chunks[0] if text_chunks else table_chunks[0]
        table_chunk = table_chunks[0] if table_chunks else text_chunk
        numeric_chunk = table_chunks[1] if len(table_chunks) > 1 else (text_chunks[1] if len(text_chunks) > 1 else table_chunk)

        def terms(chunk: dict[str, Any], limit: int = 18) -> str:
            text = str(chunk.get("search_text") or chunk.get("chunk_text") or "")
            words = [word.strip("；=,，。:：") for word in text.replace("\n", " ").split()]
            words = [word for word in words if len(word) > 1]
            return " ".join(words[:limit])

        prefix = f"{record['company_name']} {record['fiscal_year']} {record['source_format']}"
        add_case(record, text_chunk, "text", f"{prefix}正文 {terms(text_chunk)}")
        add_case(record, table_chunk, "table", f"{prefix}表格 {terms(table_chunk)}")
        add_case(record, numeric_chunk, "numeric_source", f"{prefix}数字来源 {terms(numeric_chunk)}")

    for index in range(1, 6):
        cases.append(
            {
                "case_id": f"no_answer_{index:02d}",
                "query": f"不存在的 {2090 + index} 年 quantum satellite revenue evidence",
                "answerable": False,
                "filters": {"company_id": f"no_such_company_{index}", "fiscal_year": 2090 + index},
                "expected_chunk_ids": [],
            }
        )
    CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASES_PATH.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    return cases


def _citation_rule_ok(case: dict[str, Any], citation: dict[str, Any]) -> bool:
    if not citation:
        return False
    if citation.get("document_id") != case.get("expected_document_id"):
        return False
    if citation.get("company_id") != case.get("expected_company_id"):
        return False
    if citation.get("fiscal_year") != case.get("expected_fiscal_year"):
        return False
    source_format = case.get("expected_source_format")
    if citation.get("source_format") != source_format:
        return False
    suffix = str(case.get("expected_source_suffix") or "").lower()
    if suffix and not str(citation.get("source_file") or "").replace("\\", "/").lower().endswith(suffix):
        return False
    if source_format == "pdf":
        if citation.get("page_start") is None or citation.get("page_end") is None:
            return False
        if case.get("expected_table_id") and citation.get("table_id") != case["expected_table_id"]:
            return False
    elif source_format == "docx":
        if case.get("expected_table_id"):
            return citation.get("table_id") == case["expected_table_id"]
        return citation.get("paragraph_index") is not None
    elif source_format == "xlsx":
        return bool(citation.get("sheet_name")) and bool(citation.get("cell_range"))
    return True


def _filter_fields_ok(case: dict[str, Any], citation: dict[str, Any]) -> bool:
    """只检查过滤字段；格式专用定位由 _citation_rule_ok 单独检查。"""

    return bool(citation) and all(
        citation.get(citation_key) == case.get(case_key)
        for citation_key, case_key in (
            ("document_id", "expected_document_id"),
            ("company_id", "expected_company_id"),
            ("fiscal_year", "expected_fiscal_year"),
            ("source_format", "expected_source_format"),
        )
    )


def _evaluate_acceptance(
    manifest: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    baseline: dict[str, Any],
    vector_manifest: dict[str, Any],
    stage_results: dict[str, Any],
    reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    retriever = QdrantRetriever(
        qdrant_path=DEFAULT_QDRANT_PATH,
        collection=COLLECTION,
        chunk_dir=CHUNKS_DIR,
    )
    try:
        sample = retriever.search_request(
            {"query": "Apple 2024 PDF total net sales", "top_k": 5}, mode="hybrid"
        )
        context = assemble_context(sample)
    finally:
        retriever.close()

    hybrid = reports["hybrid"]
    answerable = [case for case in cases if case.get("answerable", True)]
    hybrid_rows = {row["case_id"]: row for row in hybrid.get("cases", [])}
    filter_errors = 0
    citation_rule_failures = 0
    for case in answerable:
        row = hybrid_rows.get(case["case_id"], {})
        citations = row.get("citations") or []
        if any(not _filter_fields_ok(case, citation or {}) for citation in citations):
            filter_errors += 1
        if not any(_citation_rule_ok(case, citation or {}) for citation in citations):
            citation_rule_failures += 1
    filter_error_rate = round(filter_errors / len(answerable), 6) if answerable else 0.0
    citation_rule_pass_rate = round(
        (len(answerable) - citation_rule_failures) / len(answerable), 6
    ) if answerable else 0.0

    vector_contract = {
        "collection": COLLECTION,
        "point_count": vector_manifest.get("point_count"),
        "chunk_count": vector_manifest.get("chunk_count"),
        "vector_size": vector_manifest.get("model", {}).get("vector_dimension"),
        "distance": vector_manifest.get("model", {}).get("distance"),
    }
    stage_checks = {
        "manifest_passed": len(manifest) == 9,
        "format_counts": {fmt: sum(item["source_format"] == fmt for item in manifest) == 3 for fmt in ("pdf", "docx", "xlsx")},
        "parse_passed": all(item.get("passed") for item in stage_results["parse"]),
        "clean_passed": all(item.get("passed") for item in stage_results["clean"]),
        "chunk_passed": all(item.get("passed") for item in stage_results["chunk"]),
        "vector_count_matches_chunks": vector_manifest.get("point_count") == vector_manifest.get("chunk_count"),
        "vector_size": vector_contract["vector_size"] == 1024,
        "vector_distance": str(vector_contract["distance"]).lower() == "cosine",
        "source_kind_counts_recorded": all(item.get("source_kind") in {"original", "fixture"} for item in manifest),
        "baseline_preserved": baseline.get("passed") is True,
    }
    retrieval_checks = {
        "dense_recall_at_5": reports["dense"].get("metrics", {}).get("recall_at_5", 0) >= 0.90,
        "bm25_recall_at_5": reports["bm25"].get("metrics", {}).get("recall_at_5", 0) >= 0.90,
        "hybrid_recall_at_5": reports["hybrid"].get("metrics", {}).get("recall_at_5", 0) >= 0.95,
        "hybrid_citation_alignment_at_5": reports["hybrid"].get("metrics", {}).get("citation_alignment_at_5", 0) == 1.0,
        "filter_error_rate_zero": filter_error_rate == 0.0,
        "no_answer_refusal_rate": reports["hybrid"].get("metrics", {}).get("no_answer_refusal_rate", 0) >= 0.90,
        "format_specific_citation_rules": citation_rule_pass_rate == 1.0,
        "context_has_citations": bool(context.get("citations")),
    }
    result = {
        "stage": "annual_report_multiformat_acceptance",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "collection": COLLECTION,
        "manifest": str(MANIFEST_PATH),
        "cases": str(CASES_PATH),
        "source_kind_counts": {
            kind: sum(item.get("source_kind") == kind for item in manifest)
            for kind in ("original", "fixture")
        },
        "format_counts": {
            fmt: sum(item.get("source_format") == fmt for item in manifest)
            for fmt in ("pdf", "docx", "xlsx")
        },
        "document_count": len(manifest),
        "answerable_case_count": len(answerable),
        "no_answer_case_count": len(cases) - len(answerable),
        "vector_contract": vector_contract,
        "baseline": baseline,
        "stages": stage_results,
        "reports": {
            mode: str(EVALUATION_DIR / f"annual_report_multiformat.{mode}.report.json")
            for mode in reports
        },
        "metrics": {
            mode: reports[mode].get("metrics", {}) for mode in reports
        },
        "filter_error_rate": filter_error_rate,
        "format_specific_citation_rule_pass_rate": citation_rule_pass_rate,
        "sample_context": context,
        "checks": {**stage_checks, **retrieval_checks},
        "passed": all(stage_checks.values()) and all(retrieval_checks.values()),
        "boundaries": [
            "3 条 PDF 为真实 Apple 原始文件；3 条 DOCX 和 3 条 XLSX 标记为 fixture，只能证明格式链路和引用契约。",
            "本次未接入 LLM 生成、rerank、财务计算、网页展示、DOCX 视觉渲染或人工答案评分。",
            "Citation Alignment@5 是检索命中块与来源元数据对齐，不等同于完整问答答案正确。",
        ],
    }
    _write_json(ACCEPTANCE_PATH, result)
    return result


def run(*, recreate_collection: bool = False) -> dict[str, Any]:
    baseline = _baseline_snapshot()
    manifest = _load_manifest()
    stage_results: dict[str, Any] = {"parse": [], "clean": [], "chunk": []}
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    CLEANED_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    stage_results["parse"] = _run_stage("parse", manifest, _parse_one)
    stage_results["clean"] = _run_stage("clean", manifest, _clean_one)
    config = ChunkConfig()
    stage_results["chunk"] = _run_stage(
        "chunk",
        manifest,
        lambda record: _chunk_one(record, config),
    )

    aggregate_chunk_report = {
        "stage": "multiformat_chunking",
        "document_count": len(manifest),
        "chunk_count": sum(item["chunk_count"] for item in stage_results["chunk"]),
        "tokenizer": config.tokenizer_name,
        "files": stage_results["chunk"],
        "passed": all(item["passed"] for item in stage_results["chunk"]),
    }
    _write_json(REPORT_DIR / "chunking.report.json", aggregate_chunk_report)
    if not all(item["passed"] for stage in stage_results.values() for item in stage):
        raise RuntimeError("解析、清洗或切块阶段存在失败，已停止向量化")

    expected_chunk_count = sum(item["chunk_count"] for item in stage_results["chunk"])
    existing_manifest = _read_json(EMBEDDING_MANIFEST) if EMBEDDING_MANIFEST.is_file() else None
    if (
        existing_manifest
        and not recreate_collection
        and existing_manifest.get("collection") == COLLECTION
        and existing_manifest.get("chunk_count") == expected_chunk_count
        and existing_manifest.get("point_count") == expected_chunk_count
    ):
        vector_manifest = existing_manifest
        vector_stage = "reused_existing_collection"
    else:
        vector_manifest = vectorize(
            chunk_dir=CHUNKS_DIR,
            qdrant_path=DEFAULT_QDRANT_PATH,
            manifest_path=EMBEDDING_MANIFEST,
            collection=COLLECTION,
            recreate_collection=recreate_collection,
        )
        vector_stage = "vectorize"
    stage_results["vectorize"] = {
        "stage": vector_stage,
        "collection": COLLECTION,
        "chunk_count": vector_manifest.get("chunk_count"),
        "point_count": vector_manifest.get("point_count"),
        "passed": vector_manifest.get("chunk_count") == vector_manifest.get("point_count"),
    }
    if not stage_results["vectorize"]["passed"]:
        raise RuntimeError("向量点数与切块数不一致")

    cases = _generate_cases(manifest)
    reports: dict[str, dict[str, Any]] = {}
    for mode in ("dense", "bm25", "hybrid"):
        reports[mode] = evaluate_retrieval(
            cases_path=CASES_PATH,
            output_path=EVALUATION_DIR / f"annual_report_multiformat.{mode}.report.json",
            mode=mode,
            qdrant_path=DEFAULT_QDRANT_PATH,
            collection=COLLECTION,
            chunk_dir=CHUNKS_DIR,
        )
    result = _evaluate_acceptance(manifest, cases, baseline, vector_manifest, stage_results, reports)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="执行独立的 9 条多格式年报 RAG 流水线")
    parser.add_argument(
        "--recreate-collection",
        action="store_true",
        help="仅重建本脚本专用的 annual_report_multiformat_v1 集合，不触碰 Apple 基线集合",
    )
    args = parser.parse_args()
    try:
        result = run(recreate_collection=args.recreate_collection)
    except Exception as exc:
        failure = {
            "stage": "annual_report_multiformat_acceptance",
            "passed": False,
            "error": str(exc),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(ACCEPTANCE_PATH, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
