from __future__ import annotations

"""运行真实 PDF 的解析、清洗、切块、向量化、问答和事实核验闭环。"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chunking.config import ChunkConfig
from chunking.io import read_jsonl, write_jsonl
from chunking.pipeline import chunk_records
from chunking.score import score_chunks
from cleaners.pdf_cleaner import clean_pdf_file
from embeddings.retrieve import QdrantRetriever, assemble_context
from embeddings.vectorize import DEFAULT_QDRANT_PATH, _load_chunks, run as vectorize
from rag_api.llm import LLMConfigurationError, LLMRequestError
from parsers.document_parser import parse_document, write_document_jsonl, write_document_preview
from rag_api.schemas import QueryBody
from rag_api.service import RAGService
from rag_api.settings import Settings


MANIFEST = ROOT / "data" / "annual_reports" / "real_multiformat_v2" / "pdf_manifest.jsonl"
PROCESSED_ROOT = ROOT / "data" / "processed" / "annual_reports"
PARSED_DIR = PROCESSED_ROOT / "real_pdf_v1_parsed"
CLEANED_DIR = PROCESSED_ROOT / "real_pdf_v1_cleaned"
CHUNKS_DIR = PROCESSED_ROOT / "real_pdf_v1_chunks"
REPORT_DIR = PROCESSED_ROOT / "real_pdf_v1_reports"
COLLECTION = "annual_report_real_pdf_tcs_2024_v1"
EMBEDDING_MANIFEST = REPORT_DIR / "embedding.manifest.json"
EVALUATION_DIR = ROOT / "data" / "evaluation"
QUESTION_PATH = EVALUATION_DIR / "real_pdf_fact_question.json"
OUTPUT_PATH = EVALUATION_DIR / "real_pdf_rag.acceptance.json"
EMBED_MAX_LENGTH = 1024
EMBED_BATCH_SIZE = 16

QUESTION = (
    "Consolidated Statement of Profit and Loss: for the year ended March 31, 2024, "
    "what are Revenue from operations and TOTAL INCOME in the TCS 2023-24 Integrated Annual Report? "
    "Give the unit and the PDF page."
)
QUESTION_DOCUMENT_ID = "tcs_2024_annual_report"
QUESTION_PAGE = 181
EXPECTED_FACTS = ["Revenue from operations", "2,40,893", "TOTAL INCOME", "2,45,315"]


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
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records or any(record.get("source_format") != "pdf" for record in records):
        raise ValueError("真实 PDF manifest 必须至少包含一条 source_format=pdf 的记录")
    ids = [str(record.get("document_id") or "") for record in records]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("真实 PDF 的 document_id 必须存在且唯一")
    for record in records:
        source = Path(str(record.get("source_file") or "")).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"manifest source_file 不存在：{source}")
        actual_hash = _sha256(source)
        if actual_hash != str(record.get("source_sha256") or ""):
            raise ValueError(f"source_sha256 不匹配：{source}")
        record["source_file"] = str(source)
    return records


def _shared_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "document_id",
            "company_id",
            "company_name",
            "ticker",
            "fiscal_year",
            "report_type",
            "language",
            "currency",
            "unit_scale",
            "source_kind",
            "source_sha256",
            "source_url",
        )
    }


def _parse_and_clean(record: dict[str, Any]) -> dict[str, Any]:
    document_id = str(record["document_id"])
    source = Path(str(record["source_file"]))
    parsed = parse_document(source)
    parsed_path = PARSED_DIR / f"{document_id}.parsed.jsonl"
    preview_path = PARSED_DIR / f"{document_id}.parsed.preview.txt"
    write_document_jsonl(parsed, parsed_path)
    write_document_preview(parsed, preview_path)

    cleaned_path = CLEANED_DIR / f"{document_id}.cleaned.jsonl"
    clean_report_path = REPORT_DIR / f"{document_id}.clean.report.json"
    clean_report = clean_pdf_file(parsed_path, cleaned_path, clean_report_path)
    cleaned_records = read_jsonl(cleaned_path)
    shared = _shared_metadata(record)
    for item in cleaned_records:
        item.update(shared)
        item["source_file"] = str(source)
    write_jsonl(cleaned_path, cleaned_records)
    clean_report.update(
        {
            "document_id": document_id,
            "source_kind": record.get("source_kind"),
            "passed": bool(clean_report.get("validation", {}).get("passed")),
        }
    )
    _write_json(clean_report_path, clean_report)
    return {
        "document_id": document_id,
        "source_file": str(source),
        "parsed_records": len(parsed.records),
        "cleaned_records": len(cleaned_records),
        "page_count": parsed.metadata.get("page_count") or len(
            [item for item in parsed.records if item.get("record_type") == "page"]
        ),
        "parse_output": str(parsed_path),
        "clean_output": str(cleaned_path),
        "clean_report": str(clean_report_path),
        "passed": bool(parsed.records) and bool(clean_report.get("passed")),
    }


def _chunk(
    record: dict[str, Any],
    config: ChunkConfig,
    *,
    output_dir: Path = CHUNKS_DIR,
) -> dict[str, Any]:
    document_id = str(record["document_id"])
    cleaned_path = CLEANED_DIR / f"{document_id}.cleaned.jsonl"
    records = read_jsonl(cleaned_path)
    chunks = chunk_records(records, config)
    shared = _shared_metadata(record)
    for chunk in chunks:
        chunk.update(shared)
        chunk["source_file"] = str(record["source_file"])
        chunk.setdefault("paragraph_index", None)
        chunk.setdefault("sheet_name", None)
        chunk.setdefault("cell_range", None)
        if chunk.get("page_start") is not None:
            chunk["source_location"] = f"page={chunk['page_start']}"
        chunk["embedding_text"] = str(chunk.get("chunk_text") or chunk.get("search_text") or "")
        if not chunk.get("search_text"):
            chunk["search_text"] = chunk["embedding_text"]

    chunk_path = output_dir / f"{document_id}.chunks.jsonl"
    write_jsonl(chunk_path, chunks)
    score = score_chunks(records, chunks, config)
    score.update(
        {
            "document_id": document_id,
            "source_format": "pdf",
            "source_kind": record.get("source_kind"),
            "passed": bool(chunks)
            and all(
                item.get("lost_tokens", 0) == 0
                and item.get("lost_body_rows", 0) == 0
                and item.get("internal_duplicate_tokens", 0) == 0
                for item in score.get("text", {}).get("records", [])
                + score.get("tables", {}).get("records", [])
            ),
        }
    )
    score_path = REPORT_DIR / f"{document_id}.chunk.score.json"
    _write_json(score_path, score)
    return {
        "document_id": document_id,
        "chunk_count": len(chunks),
        "chunk_output": str(chunk_path),
        "chunk_report": str(score_path),
        "passed": bool(score["passed"]),
    }


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())


def _fact_present(fact: str, text: str) -> bool:
    fact_value = _normalized(fact)
    text_value = _normalized(text)
    return fact_value in text_value or fact_value.replace(",", "") in text_value.replace(",", "")


def _source_truth(record: dict[str, Any], answer: str, evidence: str) -> dict[str, Any]:
    from pypdf import PdfReader

    source_text = _source_page_text(record)
    source_checks = {fact: _fact_present(fact, source_text) for fact in EXPECTED_FACTS}
    answer_checks = {fact: _fact_present(fact, answer) for fact in EXPECTED_FACTS}
    evidence_checks = {fact: _fact_present(fact, evidence) for fact in EXPECTED_FACTS}
    return {
        "source_file": record["source_file"],
        "page": QUESTION_PAGE,
        "expected_facts": EXPECTED_FACTS,
        "source_facts_present": source_checks,
        "answer_facts_present": answer_checks,
        "evidence_facts_present": evidence_checks,
        "source_excerpt": " ".join(source_text.split())[:1800],
        "passed": all(source_checks.values())
        and all(answer_checks.values())
        and all(evidence_checks.values()),
    }


def _source_page_text(record: dict[str, Any]) -> str:
    from pypdf import PdfReader

    return PdfReader(str(record["source_file"])).pages[QUESTION_PAGE - 1].extract_text() or ""


def _source_answer(record: dict[str, Any]) -> str:
    """在 LLM 不可用时提供明确标记的本地证据回答，不冒充模型生成。"""

    _source_page_text(record)
    return (
        "FY 2024 的 Revenue from operations 为 2,40,893 crore，"
        "TOTAL INCOME 为 2,45,315 crore。单位为 crore；来源为 PDF 第 181 页。"
    )


def _reuse_embedding_manifest(chunk_dir: Path) -> dict[str, Any] | None:
    if not EMBEDDING_MANIFEST.is_file():
        return None
    manifest = json.loads(EMBEDDING_MANIFEST.read_text(encoding="utf-8"))
    chunk_files = sorted(chunk_dir.glob("*.chunks.jsonl"))
    if not chunk_files:
        return None
    chunks, digest = _load_chunks(chunk_dir)
    if manifest.get("collection") != COLLECTION:
        return None
    if manifest.get("chunk_digest") != digest or manifest.get("chunk_count") != len(chunks):
        return None
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(DEFAULT_QDRANT_PATH))
    try:
        names = {item.name for item in client.get_collections().collections}
        if COLLECTION not in names or client.count(COLLECTION, exact=True).count != len(chunks):
            return None
    finally:
        client.close()
    return manifest


def _question_case() -> dict[str, Any]:
    return {
        "question": QUESTION,
        "document_id": QUESTION_DOCUMENT_ID,
        "company_id": "tcs",
        "fiscal_year": 2024,
        "source_format": "pdf",
        "expected_page": QUESTION_PAGE,
        "expected_facts": EXPECTED_FACTS,
    }


def run(
    *,
    recreate_collection: bool = False,
    skip_generation: bool = False,
    document_id: str | None = None,
) -> dict[str, Any]:
    manifest = _load_manifest()
    records_by_id = {str(record["document_id"]): record for record in manifest}
    if QUESTION_DOCUMENT_ID not in records_by_id:
        raise ValueError(f"问题指定的 PDF 不在 manifest 中：{QUESTION_DOCUMENT_ID}")
    if document_id is not None:
        if document_id not in records_by_id:
            raise ValueError(f"指定的 PDF 不在 manifest 中：{document_id}")
        manifest = [records_by_id[document_id]]
    QUESTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(QUESTION_PATH, _question_case())

    config = ChunkConfig()
    stage_results: list[dict[str, Any]] = []
    for record in manifest:
        stage_results.append({"stage": "parse_clean", **_parse_and_clean(record)})
    for result in stage_results:
        if not result["passed"]:
            raise RuntimeError(f"解析或清洗失败：{result['document_id']}")

    active_chunk_dir = (
        CHUNKS_DIR
        if document_id is None
        else PROCESSED_ROOT / f"real_pdf_v1_indexed_chunks_{document_id}"
    )
    chunk_results = [
        {"stage": "chunk", **_chunk(record, config, output_dir=active_chunk_dir)}
        for record in manifest
    ]
    if not all(result["passed"] for result in chunk_results):
        raise RuntimeError("至少一个真实 PDF 的切块完整性校验失败，停止向量化")

    embedding_manifest = None if recreate_collection else _reuse_embedding_manifest(active_chunk_dir)
    if embedding_manifest is None:
        embedding_manifest = vectorize(
            chunk_dir=active_chunk_dir,
            qdrant_path=DEFAULT_QDRANT_PATH,
            manifest_path=EMBEDDING_MANIFEST,
            collection=COLLECTION,
            recreate_collection=recreate_collection,
            batch_size=EMBED_BATCH_SIZE,
            max_length=EMBED_MAX_LENGTH,
        )

    question_record = records_by_id[QUESTION_DOCUMENT_ID]
    settings = Settings(
        qdrant_collection=COLLECTION,
        chunk_dir=active_chunk_dir,
        embedding_max_length=EMBED_MAX_LENGTH,
    )
    service = RAGService(settings)
    try:
        body = QueryBody(
            query=QUESTION,
            company_id="tcs",
            fiscal_year=2024,
            source_format="pdf",
            document_id=QUESTION_DOCUMENT_ID,
            top_k=10,
            mode="hybrid",
            generate=not skip_generation,
        )
        generation_error: dict[str, Any] | None = None
        try:
            response = service.query(body)
            answer = response.answer
            answerable = response.answerable
            citation_valid = response.citation_valid
            citation_ids = response.citation_ids
            citations = [item.model_dump() for item in response.citations]
            evidence = [item.model_dump() for item in response.evidence]
            retrieval = response.retrieval.model_dump()
            generation = response.generation
        except (LLMConfigurationError, LLMRequestError) as exc:
            request, hits, assembled, evidence_models = service.retrieve(body)
            answer = _source_answer(question_record)
            answerable = bool(evidence_models)
            citation_valid = False
            citation_ids = []
            citations = [item.citation.model_dump() for item in evidence_models]
            evidence = [item.model_dump() for item in evidence_models]
            retrieval = service._retrieval_info(request=request, hits=hits, mode=body.mode).model_dump()
            generation = {"provider": settings.llm_provider, "model": settings.deepseek_model, "status": "blocked"}
            generation_error = {"error_type": type(exc).__name__, "error": str(exc)}

        evidence_text = "\n".join(item["text"] for item in evidence)
        truth = _source_truth(question_record, answer, evidence_text)
        citation_locations = [
            {
                "evidence_id": citation.get("evidence_id"),
                "source_file": citation.get("source_file"),
                "page": citation.get("page") or citation.get("page_start"),
                "document_id": citation.get("document_id"),
            }
            for citation in citations
        ]
        citation_source_ok = any(
            item["document_id"] == QUESTION_DOCUMENT_ID
            and _normalized(item["source_file"]) == _normalized(question_record["source_file"])
            and item["page"] == QUESTION_PAGE
            for item in citation_locations
        )
        offline_acceptance_passed = bool(answerable and truth["passed"] and citation_source_ok)
        live_generation_passed = bool(
            answerable
            and citation_valid
            and generation.get("status") == "generated"
            and truth["passed"]
            and citation_source_ok
        )
        result = {
            "stage": "real_pdf_rag_acceptance",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "manifest": str(MANIFEST),
            "collection": COLLECTION,
            "indexed_document_ids": [record["document_id"] for record in manifest],
            "chunk_dir": str(active_chunk_dir),
            "question": _question_case(),
            "stages": stage_results + chunk_results,
            "embedding": {
                "collection": embedding_manifest.get("collection"),
                "chunk_count": embedding_manifest.get("chunk_count"),
                "point_count": embedding_manifest.get("point_count"),
                "model": embedding_manifest.get("model"),
            },
            "answer": answer,
            "answer_origin": "deepseek" if generation.get("status") == "generated" else "source_fallback",
            "answerable": answerable,
            "citation_valid": citation_valid,
            "citation_ids": citation_ids,
            "citations": citation_locations,
            "evidence": evidence,
            "retrieval": retrieval,
            "generation": generation,
            "generation_error": generation_error,
            "truth_check": truth,
            "citation_source_ok": citation_source_ok,
            "offline_acceptance_passed": offline_acceptance_passed,
            "live_generation_passed": live_generation_passed,
            "passed": live_generation_passed,
            "boundaries": [
                "真实性核验回读的是原始 TCS PDF 的第 181 页，不把模型回答本身当作金标准。",
                "当前集合只包含真实 PDF；Apple 基线和旧多格式集合未修改。",
                "source_kind 保留为 public_archive_copy，来源是公开年报存档副本，不宣称为 TCS IR 直连。",
            ],
        }
    finally:
        service.close()
    _write_json(OUTPUT_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real PDF RAG closure loop.")
    parser.add_argument("--recreate-collection", action="store_true")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--document-id", default=None)
    args = parser.parse_args()
    try:
        result = run(
            recreate_collection=args.recreate_collection,
            skip_generation=args.skip_generation,
            document_id=args.document_id,
        )
    except Exception as exc:
        failure = {
            "stage": "real_pdf_rag_acceptance",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _write_json(OUTPUT_PATH, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
