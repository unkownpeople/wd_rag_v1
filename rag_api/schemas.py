from __future__ import annotations

"""HTTP 请求和响应模型。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)
    company_id: str | None = None
    fiscal_year: int | None = None
    source_format: Literal["pdf", "docx", "xlsx"] | None = None
    document_id: str | None = None
    report_type: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1, le=20)
    mode: Literal["dense", "bm25", "hybrid"] = "hybrid"
    generate: bool = True


class Citation(BaseModel):
    model_config = ConfigDict(extra="allow")
    evidence_id: str
    chunk_id: str | None = None
    document_id: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    fiscal_year: int | None = None
    source_format: str | None = None
    source_file: str | None = None
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    table_id: str | None = None
    table_title: str | None = None
    table_context: str | None = None
    region: str | None = None
    period_years: list[int] = Field(default_factory=list)
    measure_name: str | None = None
    value_kind: str | None = None
    currency: str | None = None
    unit_scale: str | None = None
    column_headers: list[str] = Field(default_factory=list)
    parent_row_labels: list[str] = Field(default_factory=list)
    statement_family: str | None = None
    statement_scope: str | None = None
    period_end: str | None = None
    unit: str | None = None
    table_title_raw: str | None = None
    table_group_id: str | None = None
    paragraph_index: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None
    source_location: str | None = None


class Evidence(BaseModel):
    evidence_id: str
    text: str
    citation: Citation
    retrieval_task_ids: list[str] = Field(default_factory=list)


class RetrievalInfo(BaseModel):
    mode: str
    top_k: int
    effective_top_k: int | None = None
    hit_count: int
    resolved_filters: dict[str, Any]
    top_score: float | None = None
    top_dense_score: float | None = None
    candidate_count: int = 0
    coverage_truncated: bool = False
    route: Literal["direct", "planner"] = "planner"
    retrieval_tasks: list[dict[str, Any]] = Field(default_factory=list)
    planner: dict[str, Any] = Field(default_factory=dict)


class RAGResponse(BaseModel):
    answer: str
    answerable: bool
    citation_valid: bool
    citation_ids: list[str]
    citations: list[Citation]
    evidence: list[Evidence]
    retrieval: RetrievalInfo
    generation: dict[str, Any]
