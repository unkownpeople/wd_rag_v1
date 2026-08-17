from __future__ import annotations

"""Audit the active TCS Qdrant payloads against chunk artifacts and source PDFs."""

import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from qdrant_client import QdrantClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from embeddings.retrieve import assemble_context


QDRANT_PATH = ROOT / "xianlian"
COLLECTION = "annual_report_v1_single_company"
CHUNK_DIR = ROOT / "data" / "processed" / "annual_reports" / "v1_single_company_chunks"
PDF_MANIFEST = ROOT / "data" / "annual_reports" / "real_multiformat_v2" / "pdf_manifest.jsonl"
OUTPUT = ROOT / "data" / "evaluation" / "tcs_pdf_qdrant_content_audit.json"

TABLE_TYPES = {"table", "table_group"}
COMPARE_FIELDS = (
    "document_id",
    "company_id",
    "fiscal_year",
    "source_format",
    "page_start",
    "page_end",
    "chunk_type",
    "source_location",
    "raw_matrix_sha256",
)
TOKEN_RE = re.compile(r"[a-z]{2,}|\d[\d,.]*%?|[\u4e00-\u9fff]+", re.IGNORECASE)
NUMBER_RE = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?%?\)?")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.split())


def _canonical_number(value: str) -> str:
    result = unicodedata.normalize("NFKC", value).strip().replace(",", "")
    negative = result.startswith("(") and result.endswith(")")
    result = result.strip("()")
    if negative and not result.startswith("-"):
        result = "-" + result
    return result


def _numbers(value: Any) -> set[str]:
    return {_canonical_number(match.group(0)) for match in NUMBER_RE.finditer(str(value or ""))}


def _tokens(value: Any) -> set[str]:
    return {token.casefold().replace(",", "") for token in TOKEN_RE.findall(_normalize_text(value))}


def _matrix_text(matrix: Any) -> str:
    if not isinstance(matrix, list):
        return ""
    return "\n".join(
        " | ".join(str(cell or "") for cell in row)
        for row in matrix
        if isinstance(row, list)
    )


def _scroll_points(client: QdrantClient) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    offset: Any | None = None
    while True:
        points, next_offset = client.scroll(
            collection_name=COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        result.extend(dict(point.payload or {}) for point in points)
        if next_offset is None:
            break
        offset = next_offset
    return result


def _coverage(required: set[str], observed: set[str]) -> float | None:
    if not required:
        return None
    return len(required & observed) / len(required)


def run() -> dict[str, Any]:
    manifests = {item["document_id"]: item for item in _read_jsonl(PDF_MANIFEST)}
    chunks = [
        item
        for path in sorted(CHUNK_DIR.glob("*.chunks.jsonl"))
        for item in _read_jsonl(path)
    ]
    chunks_by_id = {str(item.get("chunk_id") or ""): item for item in chunks}
    table_matrix_registry: dict[str, list[list[Any]]] = {}
    table_matrix_conflicts: list[str] = []
    for chunk in chunks:
        if chunk.get("chunk_type") not in TABLE_TYPES:
            continue
        table_group_id = str(chunk.get("table_group_id") or chunk.get("table_id") or "")
        raw_matrix = chunk.get("raw_matrix")
        if not table_group_id or not isinstance(raw_matrix, list) or not raw_matrix:
            continue
        matrix = [list(row) for row in raw_matrix if isinstance(row, list)]
        existing = table_matrix_registry.get(table_group_id)
        if existing is not None and existing != matrix:
            table_matrix_conflicts.append(table_group_id)
            continue
        table_matrix_registry.setdefault(table_group_id, matrix)

    client = QdrantClient(path=str(QDRANT_PATH))
    try:
        points = _scroll_points(client)
        collection_count = client.count(COLLECTION, exact=True).count
    finally:
        client.close()
    points_by_id = {str(item.get("chunk_id") or ""): item for item in points}

    chunk_ids = set(chunks_by_id)
    point_ids = set(points_by_id)
    missing_point_ids = sorted(chunk_ids - point_ids)
    extra_point_ids = sorted(point_ids - chunk_ids)

    exact_text_matches = 0
    field_mismatches: Counter[str] = Counter()
    mismatch_examples: list[dict[str, Any]] = []
    for chunk_id in sorted(chunk_ids & point_ids):
        chunk = chunks_by_id[chunk_id]
        payload = points_by_id[chunk_id]
        if str(chunk.get("chunk_text") or "") == str(payload.get("chunk_text") or ""):
            exact_text_matches += 1
        elif len(mismatch_examples) < 20:
            mismatch_examples.append({"chunk_id": chunk_id, "field": "chunk_text"})
        for field in COMPARE_FIELDS:
            if chunk.get(field) != payload.get(field):
                field_mismatches[field] += 1
                if len(mismatch_examples) < 20:
                    mismatch_examples.append(
                        {
                            "chunk_id": chunk_id,
                            "field": field,
                            "chunk": chunk.get(field),
                            "payload": payload.get(field),
                        }
                    )

    source_checks: dict[str, Any] = {}
    page_text_by_document: dict[str, list[str]] = {}
    for document_id, manifest in manifests.items():
        source = Path(str(manifest["source_file"]))
        reader = PdfReader(str(source))
        page_texts = [page.extract_text() or "" for page in reader.pages]
        page_text_by_document[document_id] = page_texts
        actual_sha256 = _sha256(source)
        source_checks[document_id] = {
            "source_file": str(source),
            "page_count": len(page_texts),
            "manifest_sha256": manifest.get("source_sha256"),
            "actual_sha256": actual_sha256,
            "sha256_matches": actual_sha256 == manifest.get("source_sha256"),
            "company_id": manifest.get("company_id"),
            "fiscal_year": manifest.get("fiscal_year"),
        }

    # 活动集合可以并列多家公司；本脚本的 PDF 内容核验仍只针对 TCS 来源，
    # 全集合数量和 payload 闭环在上面的通用检查中完成。
    pdf_chunks = [
        item
        for item in chunks
        if item.get("source_format") == "pdf" and item.get("company_id") == "tcs"
    ]
    pdf_table_chunks = [item for item in pdf_chunks if item.get("chunk_type") in TABLE_TYPES]
    page_bounds_failures: list[dict[str, Any]] = []
    metadata_failures: list[dict[str, Any]] = []
    numeric_coverages: list[float] = []
    token_coverages: list[float] = []
    low_numeric_examples: list[dict[str, Any]] = []
    low_token_examples: list[dict[str, Any]] = []

    document_chunk_counts: Counter[str] = Counter()
    document_table_counts: Counter[str] = Counter()
    for chunk in pdf_chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        document_id = str(chunk.get("document_id") or "")
        document_chunk_counts[document_id] += 1
        if chunk.get("chunk_type") in TABLE_TYPES:
            document_table_counts[document_id] += 1
        manifest = manifests.get(document_id) or {}
        if (
            chunk.get("company_id") != manifest.get("company_id")
            or chunk.get("fiscal_year") != manifest.get("fiscal_year")
            or chunk.get("source_sha256") != manifest.get("source_sha256")
        ):
            if len(metadata_failures) < 50:
                metadata_failures.append(
                    {
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "company_id": chunk.get("company_id"),
                        "fiscal_year": chunk.get("fiscal_year"),
                        "source_sha256": chunk.get("source_sha256"),
                    }
                )

        pages = page_text_by_document.get(document_id) or []
        page_start = chunk.get("page_start")
        page_end = chunk.get("page_end") or page_start
        if not isinstance(page_start, int) or not isinstance(page_end, int) or not (
            1 <= page_start <= page_end <= len(pages)
        ):
            if len(page_bounds_failures) < 50:
                page_bounds_failures.append(
                    {
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "page_start": page_start,
                        "page_end": page_end,
                        "source_page_count": len(pages),
                    }
                )
            continue

        source_text = "\n".join(pages[page_start - 1 : page_end])
        chunk_source_text = str(chunk.get("chunk_text") or "")
        if chunk.get("chunk_type") in TABLE_TYPES:
            chunk_source_text += "\n" + _matrix_text(chunk.get("raw_matrix"))
        numeric = _coverage(_numbers(chunk_source_text), _numbers(source_text))
        token = _coverage(_tokens(chunk_source_text), _tokens(source_text))
        if numeric is not None:
            numeric_coverages.append(numeric)
            if numeric < 1.0 and len(low_numeric_examples) < 50:
                low_numeric_examples.append(
                    {
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "page_start": page_start,
                        "page_end": page_end,
                        "chunk_type": chunk.get("chunk_type"),
                        "coverage": round(numeric, 6),
                        "missing_numbers": sorted(_numbers(chunk_source_text) - _numbers(source_text))[:30],
                    }
                )
        if token is not None:
            token_coverages.append(token)
            if token < 0.95 and len(low_token_examples) < 50:
                low_token_examples.append(
                    {
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "page_start": page_start,
                        "page_end": page_end,
                        "chunk_type": chunk.get("chunk_type"),
                        "coverage": round(token, 6),
                    }
                )

    pdf_point_payloads = [
        item
        for item in points
        if item.get("source_format") == "pdf" and item.get("company_id") == "tcs"
    ]
    pdf_table_payloads = [
        item for item in pdf_point_payloads if item.get("chunk_type") in TABLE_TYPES
    ]
    pdf_table_group_ids = {
        str(item.get("table_group_id") or item.get("table_id") or "")
        for item in pdf_table_payloads
        if item.get("table_group_id") or item.get("table_id")
    }
    unresolved_table_groups = sorted(pdf_table_group_ids - set(table_matrix_registry))
    sample_full_table_expanded = False
    if pdf_table_payloads:
        sample_payload = pdf_table_payloads[0]
        sample_group_id = str(
            sample_payload.get("table_group_id") or sample_payload.get("table_id") or ""
        )
        sample_matrix = table_matrix_registry.get(sample_group_id)
        if sample_matrix:
            sample_context = assemble_context(
                [
                    {
                        "chunk_id": sample_payload.get("chunk_id"),
                        "text": sample_payload.get("chunk_text"),
                        "payload": sample_payload,
                        "citation": {"table_group_id": sample_group_id},
                        "_table_matrix": sample_matrix,
                    }
                ]
            )
            sample_full_table_expanded = "完整表格上下文" in sample_context["context"]

    known_fact_checks = []
    for page, expected, expected_scope in (
        (184, {"56827", "44338"}, "consolidated"),
        (254, {"39142"}, "standalone"),
    ):
        source_numbers = _numbers(page_text_by_document["tcs_2024_annual_report"][page - 1])
        page_chunks = [
            chunk
            for chunk in pdf_chunks
            if chunk.get("document_id") == "tcs_2024_annual_report"
            and chunk.get("page_start") == page
        ]
        page_payloads = [
            payload
            for payload in pdf_point_payloads
            if payload.get("document_id") == "tcs_2024_annual_report"
            and payload.get("page_start") == page
        ]
        chunk_numbers = set().union(*[_numbers(item.get("chunk_text")) for item in page_chunks])
        payload_numbers = set().union(*[_numbers(item.get("chunk_text")) for item in page_payloads])
        known_fact_checks.append(
            {
                "document_id": "tcs_2024_annual_report",
                "page": page,
                "expected_numbers": sorted(expected),
                "expected_scope": expected_scope,
                "source_pdf_contains_all": expected <= source_numbers,
                "chunk_artifacts_contain_all": expected <= chunk_numbers,
                "qdrant_payloads_contain_all": expected <= payload_numbers,
                "qdrant_scopes_on_page": sorted(
                    {str(item.get("statement_scope") or "") for item in page_payloads}
                ),
                "qdrant_chunk_count_on_page": len(page_payloads),
            }
        )

    report = {
        "stage": "tcs_pdf_qdrant_content_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "qdrant_path": str(QDRANT_PATH),
        "collection": COLLECTION,
        "collection_count": collection_count,
        "chunk_artifact_count": len(chunks),
        "qdrant_payload_count": len(points),
        "missing_point_count": len(missing_point_ids),
        "extra_point_count": len(extra_point_ids),
        "missing_point_examples": missing_point_ids[:20],
        "extra_point_examples": extra_point_ids[:20],
        "payload_artifact_checks": {
            "exact_chunk_text_matches": exact_text_matches,
            "field_mismatch_counts": dict(field_mismatches),
            "mismatch_examples": mismatch_examples,
        },
        "payload_field_distribution": {
            "company_id": dict(Counter(str(item.get("company_id")) for item in points)),
            "fiscal_year": dict(Counter(str(item.get("fiscal_year")) for item in points)),
            "source_format": dict(Counter(str(item.get("source_format")) for item in points)),
            "document_id": dict(Counter(str(item.get("document_id")) for item in points)),
        },
        "pdf_scope": {
            "pdf_document_count": len(manifests),
            "pdf_chunk_count": len(pdf_chunks),
            "pdf_table_chunk_count": len(pdf_table_chunks),
            "document_chunk_counts": dict(document_chunk_counts),
            "document_table_counts": dict(document_table_counts),
            "source_checks": source_checks,
            "metadata_failure_count": len(metadata_failures),
            "metadata_failure_examples": metadata_failures,
            "page_bounds_failure_count": len(page_bounds_failures),
            "page_bounds_failure_examples": page_bounds_failures,
        },
        "source_pdf_content_checks": {
            "numeric_chunks_checked": len(numeric_coverages),
            "numeric_full_coverage_count": sum(value == 1.0 for value in numeric_coverages),
            "numeric_full_coverage_rate": round(
                sum(value == 1.0 for value in numeric_coverages) / max(len(numeric_coverages), 1), 6
            ),
            "numeric_average_coverage": round(
                sum(numeric_coverages) / max(len(numeric_coverages), 1), 6
            ),
            "low_numeric_examples": low_numeric_examples,
            "token_chunks_checked": len(token_coverages),
            "token_at_least_95_percent_count": sum(value >= 0.95 for value in token_coverages),
            "token_at_least_95_percent_rate": round(
                sum(value >= 0.95 for value in token_coverages) / max(len(token_coverages), 1), 6
            ),
            "token_average_coverage": round(
                sum(token_coverages) / max(len(token_coverages), 1), 6
            ),
            "low_token_examples": low_token_examples,
        },
        "runtime_table_context_contract": {
            "pdf_table_payload_count": len(pdf_table_payloads),
            "payloads_with_row_matrix": sum(bool(item.get("row_matrix")) for item in pdf_table_payloads),
            "payloads_with_raw_matrix_sha256": sum(
                bool(item.get("raw_matrix_sha256")) for item in pdf_table_payloads
            ),
            "payloads_with_raw_matrix": sum(bool(item.get("raw_matrix")) for item in pdf_table_payloads),
            "table_matrix_registry_count": len(table_matrix_registry),
            "pdf_table_group_count": len(pdf_table_group_ids),
            "unresolved_table_group_count": len(unresolved_table_groups),
            "unresolved_table_group_examples": unresolved_table_groups[:20],
            "table_matrix_conflict_count": len(set(table_matrix_conflicts)),
            "sample_full_table_expanded": sample_full_table_expanded,
            "matrix_source": "chunk_artifact_registry",
        },
        "known_fact_checks": known_fact_checks,
    }
    report["checks"] = {
        "chunk_point_count_closed": (
            collection_count == len(chunks) == len(points)
            and not missing_point_ids
            and not extra_point_ids
        ),
        "payload_text_exact": exact_text_matches == len(chunks),
        "payload_fields_exact": not field_mismatches,
        "source_pdf_hashes_exact": all(
            item["sha256_matches"] for item in source_checks.values()
        ),
        "pdf_company_year_metadata_exact": not metadata_failures,
        "pdf_page_bounds_valid": not page_bounds_failures,
        "known_facts_survive_pdf_to_qdrant": all(
            item["source_pdf_contains_all"]
            and item["chunk_artifacts_contain_all"]
            and item["qdrant_payloads_contain_all"]
            for item in known_fact_checks
        ),
        "runtime_full_table_context_available": (
            bool(pdf_table_group_ids)
            and not unresolved_table_groups
            and not table_matrix_conflicts
            and sample_full_table_expanded
        ),
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "collection_count": report["collection_count"],
                "pdf_chunk_count": report["pdf_scope"]["pdf_chunk_count"],
                "pdf_table_chunk_count": report["pdf_scope"]["pdf_table_chunk_count"],
                "checks": report["checks"],
                "numeric_full_coverage_rate": report["source_pdf_content_checks"][
                    "numeric_full_coverage_rate"
                ],
                "token_at_least_95_percent_rate": report["source_pdf_content_checks"][
                    "token_at_least_95_percent_rate"
                ],
                "output": str(OUTPUT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return report


if __name__ == "__main__":
    run()
