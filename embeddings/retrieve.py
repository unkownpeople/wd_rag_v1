from __future__ import annotations

"""使用同一 BGE-M3 模型查询 Qdrant Local，并提供 BM25/RRF 对照。"""

from collections import Counter, defaultdict
import math
from pathlib import Path
import re
from typing import Any

from qdrant_client import QdrantClient, models

from .onnx_encoder import BGEEmbeddingEncoder
from .vectorize import (
    COLLECTION,
    DEFAULT_CHUNK_DIR,
    DEFAULT_QDRANT_PATH,
    _load_chunks,
    _payload,
    _point_id,
)
from .contracts import QueryRequest, infer_filters
from .semantic_metadata import infer_semantic_metadata


_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]|[^\W_]", re.IGNORECASE)
_ASCII_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_BM25_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "did",
    "do", "does", "for", "from", "how", "in", "is", "it", "of", "on", "or", "that",
    "the", "their", "this", "to", "was", "were", "what", "which", "who", "with", "would",
    "year", "years", "s", "annual", "report", "fiscal", "according", "much",
}
_GENERIC_ACCOUNTING_TERMS = {
    "annual", "report", "fiscal", "year", "years", "march", "ended", "date",
    "according", "statement", "statements", "financial", "consolidated", "standalone",
    "profit", "loss", "income", "amount", "figure", "overall", "performance", "business",
}

_COMPANY_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "apple": ("苹果", "苹果公司"),
    "microsoft": ("微软", "微软公司"),
    "tcs": ("塔塔咨询", "塔塔咨询服务", "塔塔咨询服务公司"),
}

def _tokenize(text: str) -> list[str]:
    """为 BM25 提供无外部依赖的中英文词元化。"""

    return [
        token
        for token in _TOKEN_RE.findall(str(text or "").casefold())
        if token not in _BM25_STOPWORDS
    ]


def _ascii_phrase_windows(text: str, *, max_width: int = 6) -> list[tuple[str, int]]:
    """提取包含至少两个实词的英文连续短语，用于年报原文标签加分。"""

    words = [word.casefold() for word in _ASCII_WORD_RE.findall(str(text or ""))]
    phrases: list[tuple[str, int]] = []
    seen: set[str] = set()
    for width in range(min(max_width, len(words)), 1, -1):
        for start in range(0, len(words) - width + 1):
            window = words[start : start + width]
            content_count = sum(
                token not in _BM25_STOPWORDS and len(token) > 1
                for token in window
            )
            if content_count < 2:
                continue
            phrase = " ".join(window)
            if phrase in seen:
                continue
            seen.add(phrase)
            phrases.append((phrase, content_count))
    return phrases


def _normalized_ascii_text(text: str) -> str:
    return " ".join(word.casefold() for word in _ASCII_WORD_RE.findall(str(text or "")))


def _normalize_table_matrix(raw_matrix: Any) -> list[list[Any]]:
    """兼容新旧切块产物的表格行格式。"""

    if not isinstance(raw_matrix, list):
        return []
    normalized: list[list[Any]] = []
    for row in raw_matrix:
        if isinstance(row, list):
            normalized.append(list(row))
            continue
        if isinstance(row, dict):
            values = row.get("value")
            if isinstance(values, list):
                normalized.append(list(values))
    return normalized


class BM25Index:
    """从当前切块 JSONL 构建只读 BM25 倒排索引。"""

    def __init__(self, chunks: list[dict[str, Any]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.postings: dict[str, dict[str, int]] = defaultdict(dict)
        self.document_lengths: dict[str, int] = {}
        self.payloads: dict[str, dict[str, Any]] = {}
        self.table_matrices: dict[str, list[list[Any]]] = {}
        self.company_aliases: dict[str, str] = {}
        self.known_years: set[int] = set()
        self.known_formats: set[str] = set()
        self.known_report_types: set[str] = set()
        for chunk in chunks:
            chunk_id = str(chunk.get("chunk_id") or "")
            if not chunk_id:
                continue
            text = (
                chunk.get("search_text")
                if str(chunk.get("chunk_type") or "").startswith("table")
                else chunk.get("chunk_text")
            )
            terms = _tokenize(str(text or ""))
            frequencies = Counter(terms)
            self.document_lengths[chunk_id] = len(terms)
            self.payloads[chunk_id] = _payload(chunk)
            payload = self.payloads[chunk_id]
            table_group_id = str(payload.get("table_group_id") or payload.get("table_id") or "")
            matrix = _normalize_table_matrix(chunk.get("raw_matrix"))
            if table_group_id and matrix:
                existing = self.table_matrices.get(table_group_id)
                if existing is not None and existing != matrix:
                    raise ValueError(f"同一 table_group_id 存在不同 raw_matrix：{table_group_id}")
                self.table_matrices.setdefault(table_group_id, matrix)
            company_id = str(payload.get("company_id") or "").strip()
            if company_id:
                self.company_aliases[company_id.casefold()] = company_id
                for alias in _COMPANY_QUERY_ALIASES.get(company_id.casefold(), ()):
                    self.company_aliases[alias.casefold()] = company_id
            for key in ("company_name", "ticker"):
                alias = str(payload.get(key) or "").strip()
                if alias and company_id:
                    self.company_aliases[alias.casefold()] = company_id
            if payload.get("fiscal_year") is not None:
                try:
                    self.known_years.add(int(payload["fiscal_year"]))
                except (TypeError, ValueError):
                    pass
            for key, target in (("source_format", self.known_formats), ("report_type", self.known_report_types)):
                value = str(payload.get(key) or "").strip()
                if value:
                    target.add(value)
            for term, frequency in frequencies.items():
                self.postings[term][chunk_id] = frequency
        total_length = sum(self.document_lengths.values())
        self.average_document_length = total_length / max(len(self.document_lengths), 1)

    def infer_filters(self, query: str) -> dict[str, Any]:
        return infer_filters(
            query,
            aliases=self.company_aliases,
            known_years=self.known_years,
            known_formats=self.known_formats,
            known_report_types=self.known_report_types,
        )

    @classmethod
    def from_chunk_dir(cls, chunk_dir: Path = DEFAULT_CHUNK_DIR) -> "BM25Index":
        chunks, _ = _load_chunks(chunk_dir)
        return cls(chunks)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        ignored_terms: set[str] = set()
        for value in (filters or {}).values():
            ignored_terms.update(_tokenize(str(value)))
        query_terms = set(_tokenize(query)) - ignored_terms
        phrase_windows = _ascii_phrase_windows(query)
        metric_terms = {
            term
            for term in query_terms
            if len(term) > 2
            and not term.isdigit()
            and term not in _GENERIC_ACCOUNTING_TERMS
        }
        scores: dict[str, float] = defaultdict(float)
        document_count = len(self.document_lengths)
        for term in query_terms:
            matching = self.postings.get(term)
            if not matching:
                continue
            document_frequency = len(matching)
            inverse_frequency = math.log(
                1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            for chunk_id, term_frequency in matching.items():
                if filters and any(
                    value is not None and self.payloads[chunk_id].get(key) != value
                    for key, value in filters.items()
                ):
                    continue
                length = self.document_lengths[chunk_id]
                denominator = term_frequency + self.k1 * (
                    1.0 - self.b + self.b * length / max(self.average_document_length, 1e-12)
                )
                scores[chunk_id] += inverse_frequency * (
                    term_frequency * (self.k1 + 1.0) / max(denominator, 1e-12)
                )

        # 年报表格的 search_text 是结构化行投影；同一页的正文副本通常包含更多
        # "net sales" 重复词。对表格块做有限提升，避免 BM25 只返回正文副本而
        # 丢失 table_id、表头和行号等引用元数据。
        for chunk_id in list(scores):
            if str(self.payloads[chunk_id].get("chunk_type") or "").startswith("table"):
                payload_text = str(
                    self.payloads[chunk_id].get("search_text")
                    or self.payloads[chunk_id].get("chunk_text")
                    or ""
                )
                coverage = sum(term in _tokenize(payload_text) for term in query_terms)
                coverage_ratio = coverage / max(len(query_terms), 1)
                scores[chunk_id] *= 2.0 + 0.75 * coverage_ratio

        # 连续原文标签是年报检索中的强证据。例如 ``Revenue from operations``
        # 与 ``TOTAL ASSETS`` 应优先于只命中 report/year 等泛词的表格块。
        # 该加分从查询动态提取，不绑定公司、页码或固定测试答案。
        if phrase_windows or metric_terms:
            for chunk_id in list(scores):
                payload = self.payloads[chunk_id]
                payload_text = payload.get("search_text") or payload.get("chunk_text") or ""
                normalized_payload_text = f" {_normalized_ascii_text(str(payload_text))} "
                payload_terms = set(_tokenize(str(payload_text)))
                metric_hits = metric_terms.intersection(payload_terms)
                if metric_hits:
                    scores[chunk_id] += 80.0 * len(metric_hits)
                matched = [
                    (phrase, content_count)
                    for phrase, content_count in phrase_windows
                    if f" {phrase} " in normalized_payload_text
                    and (
                        not metric_terms
                        or any(token in metric_terms for token in phrase.split())
                    )
                ]
                if matched:
                    phrase_score = sum(
                        24.0 * len(phrase.split()) + 16.0 * content_count
                        for phrase, content_count in matched
                    )
                    scores[chunk_id] += min(phrase_score, 420.0)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return [
            {
                "rank": rank,
                "score": float(score),
                "chunk_id": chunk_id,
                "payload": self.payloads[chunk_id],
            }
            for rank, (chunk_id, score) in enumerate(ranked, 1)
        ]


class QdrantRetriever:
    def __init__(
        self,
        *,
        qdrant_path: Path = DEFAULT_QDRANT_PATH,
        collection: str = COLLECTION,
        encoder: BGEEmbeddingEncoder | None = None,
        chunk_dir: Path = DEFAULT_CHUNK_DIR,
        bm25: BM25Index | None = None,
    ) -> None:
        self.client = QdrantClient(path=str(qdrant_path))
        self.collection = collection
        self.encoder = encoder or BGEEmbeddingEncoder()
        self.bm25 = bm25 or BM25Index.from_chunk_dir(chunk_dir)
        self.last_search_meta: dict[str, Any] = {}

    def _with_table_matrix(self, hit: dict[str, Any]) -> dict[str, Any]:
        """从切块产物注册表关联完整表格，不向每个向量点重复持久化整表。"""

        result = dict(hit)
        payload = result.get("payload") or {}
        citation = result.get("citation") or {}
        table_group_id = str(
            citation.get("table_group_id")
            or payload.get("table_group_id")
            or payload.get("table_id")
            or ""
        )
        matrices = getattr(getattr(self, "bm25", None), "table_matrices", {})
        matrix = matrices.get(table_group_id) if table_group_id else None
        if matrix:
            result["_table_matrix"] = matrix
            result["_table_matrix_source"] = "chunk_artifact"
        return result

    @staticmethod
    def _qdrant_filter(filters: dict[str, Any] | None) -> models.Filter | None:
        if not filters:
            return None
        conditions = [
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in filters.items()
            if value is not None
        ]
        return models.Filter(must=conditions) if conditions else None

    @staticmethod
    def _with_citation(hit: dict[str, Any]) -> dict[str, Any]:
        payload = hit.get("payload") or {}
        semantic = infer_semantic_metadata(payload)
        page_start = payload.get("page_start")
        page_end = payload.get("page_end")
        citation = {
            "document_id": payload.get("document_id"),
            "company_id": payload.get("company_id"),
            "company_name": payload.get("company_name"),
            "fiscal_year": payload.get("fiscal_year"),
            "source_file": payload.get("source_file"),
            "source_url": payload.get("source_url"),
            "source_format": payload.get("source_format"),
            "source_kind": payload.get("source_kind"),
            "page": page_start if page_start == page_end or page_end is None else None,
            "page_start": page_start,
            "page_end": page_end,
            "paragraph_index": payload.get("paragraph_index"),
            "table_id": payload.get("table_id"),
            "table_title": payload.get("table_title"),
            "table_context": payload.get("table_context"),
            "region": payload.get("region") or semantic.get("region"),
            "period_years": payload.get("period_years") or semantic.get("period_years", []),
            "measure_name": payload.get("measure_name") or semantic.get("measure_name"),
            "value_kind": payload.get("value_kind") or semantic.get("value_kind"),
            "currency": payload.get("currency"),
            "unit_scale": payload.get("unit_scale") or semantic.get("unit_scale"),
            "column_headers": payload.get("column_headers", []),
            "parent_row_labels": payload.get("parent_row_labels", []),
            "statement_family": (
                semantic["statement_family"]
                if semantic["statement_family"] != "other"
                else payload.get("statement_family") or "other"
            ),
            "statement_scope": payload.get("statement_scope") or semantic["statement_scope"],
            "period_end": payload.get("period_end") or semantic["period_end"],
            "unit": payload.get("unit") or semantic["unit"],
            "table_title_raw": payload.get("table_title_raw") or semantic["table_title_raw"],
            "table_group_id": payload.get("table_group_id") or semantic["table_group_id"],
            "sheet_name": payload.get("sheet_name"),
            "cell_range": payload.get("cell_range"),
            "source_location": payload.get("source_location"),
        }
        result = dict(hit)
        result["chunk_id"] = payload.get("chunk_id") or result.get("chunk_id")
        result["text"] = payload.get("chunk_text") or payload.get("search_text") or ""
        result["citation"] = citation
        return result

    def resolve_request(self, request: QueryRequest) -> QueryRequest:
        inferred = self.bm25.infer_filters(request.query)
        inferred.update(request.explicit_filters())
        return request.with_filters(inferred)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        vector = self.encoder.encode([query])[0].tolist()
        response = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            with_payload=True,
            query_filter=self._qdrant_filter(filters),
        )
        return [
            self._with_table_matrix(
                self._with_citation(
                    {
                        "rank": rank,
                        "score": float(point.score),
                        "point_id": str(point.id),
                        "payload": point.payload or {},
                    }
                )
            )
            for rank, point in enumerate(response.points, 1)
        ]

    def search_sparse(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            self._with_table_matrix(
                self._with_citation(
                    {
                        "rank": hit["rank"],
                        "score": hit["score"],
                        "point_id": _point_id(hit["chunk_id"]),
                        "payload": hit["payload"],
                        "dense_score": None,
                        "bm25_score": hit["score"],
                        "rrf_score": None,
                    }
                )
            )
            for hit in self.bm25.search(query, limit=limit, filters=filters)
        ]

    def search_hybrid(
        self,
        query: str,
        *,
        limit: int = 5,
        rrf_k: int = 60,
        dense_limit: int | None = None,
        sparse_limit: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """用 Dense 与 BM25 的排名做 Reciprocal Rank Fusion。"""

        if limit <= 0:
            return []
        dense_hits = self.search(query, limit=dense_limit or max(limit * 4, 20), filters=filters)
        sparse_hits = self.bm25.search(query, limit=sparse_limit or max(limit * 4, 20), filters=filters)
        sparse_by_id = {str(hit["chunk_id"]): hit for hit in sparse_hits}
        dense_by_id = {str(hit["chunk_id"]): hit for hit in dense_hits}
        all_ids = set(sparse_by_id) | set(dense_by_id)
        fused = []
        for chunk_id in all_ids:
            dense_rank = dense_by_id.get(chunk_id, {}).get("rank")
            sparse_rank = sparse_by_id.get(chunk_id, {}).get("rank")
            score = (1.0 / (rrf_k + dense_rank) if dense_rank else 0.0) + (1.0 / (rrf_k + sparse_rank) if sparse_rank else 0.0)
            base = dense_by_id.get(chunk_id) or self._with_citation({"rank": sparse_rank, "score": 0.0, "point_id": _point_id(chunk_id), "payload": sparse_by_id[chunk_id]["payload"]})
            item = dict(base)
            item["dense_score"] = dense_by_id.get(chunk_id, {}).get("score")
            item["bm25_score"] = sparse_by_id.get(chunk_id, {}).get("score")
            item["rrf_score"] = score
            item["score"] = score
            fused.append(self._with_table_matrix(item))
        return sorted(fused, key=lambda hit: (-float(hit["rrf_score"]), str(hit.get("chunk_id"))))[:limit]

    def search_request(
        self,
        request: QueryRequest | dict[str, Any],
        *,
        mode: str = "hybrid",
    ) -> list[dict[str, Any]]:
        normalized = request if isinstance(request, QueryRequest) else QueryRequest.from_mapping(request)
        resolved = self.resolve_request(normalized)
        filters = resolved.explicit_filters()
        if mode == "dense":
            candidates = self.search(resolved.query, limit=resolved.top_k, filters=filters)
        elif mode == "bm25":
            candidates = self.search_sparse(resolved.query, limit=resolved.top_k, filters=filters)
        elif mode == "hybrid":
            candidates = self.search_hybrid(resolved.query, limit=resolved.top_k, filters=filters)
        else:
            raise ValueError(f"不支持的检索模式：{mode}")

        selected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for hit in candidates:
            chunk_id = str(hit.get("chunk_id") or (hit.get("payload") or {}).get("chunk_id") or "")
            if chunk_id and chunk_id not in seen_ids:
                seen_ids.add(chunk_id)
                selected.append(self._with_table_matrix(hit))
        meta = {
            "candidate_count": len(candidates),
            "effective_top_k": resolved.top_k,
            "coverage_truncated": len(selected) < len(candidates),
        }
        self.last_search_meta = meta
        for rank, hit in enumerate(selected, 1):
            hit["rank"] = rank
            hit["_retrieval_meta"] = meta
        return selected

    def close(self) -> None:
        self.client.close()


def assemble_context(hits: list[dict[str, Any]], *, max_chars: int = 12000) -> dict[str, Any]:
    """将召回结果转换成带稳定证据编号的上下文，不调用 LLM。"""

    prepared: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    seen_table_groups: set[str] = set()
    used = 0
    for hit in hits:
        hit.pop("_context_text", None)
        payload = hit.get("payload", {})
        text = str(hit.get("text") or payload.get("chunk_text") or "").strip()
        if not text:
            continue
        citation = dict(hit.get("citation") or {})
        semantic = infer_semantic_metadata(payload)
        for key, value in semantic.items():
            if citation.get(key) in (None, ""):
                citation[key] = value
        table_group_id = str(citation.get("table_group_id") or "")
        raw_matrix = _normalize_table_matrix(
            hit.get("_table_matrix") or payload.get("raw_matrix")
        )
        has_full_table = isinstance(raw_matrix, list) and bool(raw_matrix)
        if text in seen_texts:
            continue
        if table_group_id and has_full_table and table_group_id in seen_table_groups:
            continue
        evidence_id = f"S{len(prepared) + 1}"
        semantic_line = (
            f"semantic_family={citation.get('statement_family')}; "
            f"scope={citation.get('statement_scope')}; "
            f"period_end={citation.get('period_end')}; "
            f"unit={citation.get('unit')}; "
            f"table_group_id={citation.get('table_group_id')}"
        )
        block = f"[{evidence_id}]\n{semantic_line}\n{text}"
        separator_size = 2 if prepared else 0
        if used + separator_size + len(block) > max_chars:
            continue
        hit["_context_text"] = text
        prepared.append(
            {
                "hit": hit,
                "text": text,
                "block": block,
                "semantic_line": semantic_line,
                "citation": {
                    "evidence_id": evidence_id,
                    **citation,
                    "chunk_id": hit.get("chunk_id"),
                },
                "table_group_id": table_group_id,
                "raw_matrix": raw_matrix if has_full_table else None,
            }
        )
        seen_texts.add(text)
        if table_group_id and has_full_table:
            seen_table_groups.add(table_group_id)
        used += separator_size + len(block)

    table_records = [record for record in prepared if record["raw_matrix"]]
    for index, record in enumerate(table_records):
        remaining = max_chars - used
        groups_left = len(table_records) - index
        allocation = min(6002, remaining // max(groups_left, 1))
        if allocation <= 2:
            continue
        rows = [
            " | ".join(str(cell or "").strip() for cell in row)
            for row in record["raw_matrix"]
            if isinstance(row, list)
        ]
        full_table = "完整表格上下文：\n" + "\n".join(rows)
        extra_budget = allocation - 2
        if len(full_table) > extra_budget:
            marker = "\n[表格上下文已截断]"
            if extra_budget <= len("完整表格上下文：\n") + len(marker):
                continue
            full_table = full_table[: extra_budget - len(marker)].rstrip() + marker
        extra = f"\n\n{full_table}"
        record["text"] += extra
        record["hit"]["_context_text"] = record["text"]
        record["block"] = (
            f"[{record['citation']['evidence_id']}]\n"
            f"{record['semantic_line']}\n{record['text']}"
        )
        used += len(extra)

    blocks = [record["block"] for record in prepared]
    citations = [record["citation"] for record in prepared]
    return {
        "context": "\n\n".join(blocks),
        "citations": citations,
        "coverage": {
            "statement_families": sorted({str(item.get("statement_family") or "other") for item in citations}),
            "statement_scopes": sorted({str(item.get("statement_scope") or "unknown") for item in citations}),
            "period_ends": sorted({str(item.get("period_end") or "unknown") for item in citations}),
            "units": sorted({str(item.get("unit") or "unknown") for item in citations}),
            "table_groups": len({str(item.get("table_group_id") or item.get("chunk_id")) for item in citations}),
        },
    }
