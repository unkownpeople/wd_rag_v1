from __future__ import annotations

"""年报问答提示词和引用编号校验。"""

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "prompts" / "annual_report_qa.md"
PLANNER_PROMPT_PATH = ROOT / "prompts" / "query_planner.md"
REVIEW_PROMPT_PATH = ROOT / "prompts" / "annual_report_answer_review.md"
_CITATION_RE = re.compile(r"\[(S\d+)\]")


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_planner_prompt() -> str:
    return PLANNER_PROMPT_PATH.read_text(encoding="utf-8")


def load_review_prompt() -> str:
    return REVIEW_PROMPT_PATH.read_text(encoding="utf-8")


def _location(citation: dict[str, Any]) -> str:
    parts: list[str] = []
    if citation.get("page") is not None:
        parts.append(f"page={citation['page']}")
    elif citation.get("page_start") is not None:
        parts.append(f"page={citation['page_start']}-{citation.get('page_end')}")
    if citation.get("table_id"):
        parts.append(f"table_id={citation['table_id']}")
    if citation.get("paragraph_index") is not None:
        parts.append(f"paragraph_index={citation['paragraph_index']}")
    if citation.get("sheet_name"):
        parts.append(f"sheet={citation['sheet_name']}")
    if citation.get("cell_range"):
        parts.append(f"range={citation['cell_range']}")
    return "; ".join(parts) or "location=not-provided"


def render_context(assembled: dict[str, Any]) -> str:
    """给模型同时提供证据正文和可读来源索引。"""

    context = str(assembled.get("context") or "").strip()
    citations = assembled.get("citations") or []
    source_lines = ["来源索引："]
    for citation in citations:
        source_lines.append(
            f"[{citation.get('evidence_id')}] "
            f"document={citation.get('document_id')}; "
            f"company={citation.get('company_name') or citation.get('company_id')}; "
            f"fiscal_year={citation.get('fiscal_year')}; "
            f"source_file={citation.get('source_file')}; "
            f"format={citation.get('source_format')}; "
            f"family={citation.get('statement_family')}; "
            f"scope={citation.get('statement_scope')}; "
            f"period_end={citation.get('period_end')}; "
            f"unit={citation.get('unit')}; "
            f"table_title_raw={citation.get('table_title_raw')}; "
            f"table_group_id={citation.get('table_group_id')}; {_location(citation)}"
        )
    return "\n\n".join(part for part in (context, "\n".join(source_lines)) if part)


def render_planner_context(planner_meta: dict[str, Any] | None) -> str:
    meta = dict(planner_meta or {})
    if not meta or meta.get("status") == "not_needed":
        return json.dumps(
            {"status": "not_needed", "route": meta.get("route", "direct")},
            ensure_ascii=False,
            sort_keys=True,
        )
    fields = (
        "status",
        "route",
        "intent",
        "entities",
        "time_scope",
        "retrieval_tasks",
        "needs_calculation",
        "clarification_needed",
    )
    result = {key: meta[key] for key in fields if key in meta}
    if "retrieval_tasks" in result:
        result["retrieval_tasks"] = [
            {
                key: task[key]
                for key in ("task_id", "requirement", "queries", "statement_scope", "evidence_ids")
                if key in task
            }
            for task in result["retrieval_tasks"]
        ]
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def build_messages(
    *,
    question: str,
    assembled: dict[str, Any],
    planner_meta: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": load_system_prompt()},
        {
            "role": "user",
            "content": (
                f"用户原问题：\n{question.strip()}\n\n"
                "查询规划结果（仅用于理解问题，不是事实来源）：\n"
                f"{render_planner_context(planner_meta)}\n\n"
                "证据区：\n"
                f"{render_context(assembled)}\n\n"
                "请仅基于以上 Evidence 回答用户原问题。"
            ),
        },
    ]


def build_review_messages(
    *,
    question: str,
    draft: str,
    assembled: dict[str, Any],
    planner_meta: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": load_review_prompt()},
        {
            "role": "user",
            "content": (
                f"用户原问题：\n{question.strip()}\n\n"
                "查询规划结果（仅用于理解问题，不是事实来源）：\n"
                f"{render_planner_context(planner_meta)}\n\n"
                f"证据区：\n{render_context(assembled)}\n\n"
                f"待复核回答草稿：\n{draft.strip()}\n\n"
                "请返回可直接展示的最终答案，不要返回复核过程或评分。"
            ),
        },
    ]


def build_planner_messages(
    *,
    question: str,
    company_id: str | None = None,
    fiscal_year: int | None = None,
    document_id: str | None = None,
) -> list[dict[str, str]]:
    scope = {
        "company_id": company_id,
        "fiscal_year": fiscal_year,
        "document_id": document_id,
        "single_company_v1": True,
    }
    return [
        {"role": "system", "content": load_planner_prompt()},
        {
            "role": "user",
            "content": (
                f"检索范围：{scope}\n"
                f"用户问题：\n{question.strip()}"
            ),
        },
    ]


def extract_citation_ids(answer: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _CITATION_RE.findall(str(answer or "")):
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def validate_citations(answer: str, assembled: dict[str, Any]) -> dict[str, Any]:
    cited = extract_citation_ids(answer)
    citations = assembled.get("citations") or []
    allowed = [str(item.get("evidence_id")) for item in citations]
    unknown = [value for value in cited if value not in allowed]
    by_id = {str(item.get("evidence_id")): item for item in citations}
    metadata_errors: dict[str, list[str]] = {}
    required = (
        "source_file", "company_id", "fiscal_year", "source_format",
        "statement_family", "statement_scope", "period_end", "unit", "table_group_id",
    )
    for evidence_id in cited:
        citation = by_id.get(evidence_id)
        if citation is None:
            continue
        missing = [field for field in required if citation.get(field) in (None, "")]
        if missing:
            metadata_errors[evidence_id] = missing
    cited_metadata = [by_id[evidence_id] for evidence_id in cited if evidence_id in by_id]
    scopes = sorted({str(item.get("statement_scope")) for item in cited_metadata})
    periods = sorted({str(item.get("period_end")) for item in cited_metadata})
    units = sorted({str(item.get("unit")) for item in cited_metadata})
    sources = sorted({str(item.get("source_file")) for item in cited_metadata})
    return {
        "citation_ids": cited, "allowed_ids": allowed, "unknown_ids": unknown,
        "metadata_errors": metadata_errors, "cited_scopes": scopes,
        "cited_periods": periods, "cited_units": units, "cited_sources": sources,
        "mixed_scopes": len(scopes) > 1, "mixed_periods": len(periods) > 1,
        "mixed_units": len(units) > 1,
        "valid": bool(cited) and not unknown and not metadata_errors,
    }
