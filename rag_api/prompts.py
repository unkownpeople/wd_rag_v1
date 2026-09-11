from __future__ import annotations

"""年报问答提示词和引用编号校验。"""

import json
import re
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any
from .calculations import EQUATION, canonical_number, verify_equations


ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "prompts" / "annual_report_qa.md"
PLANNER_PROMPT_PATH = ROOT / "prompts" / "query_planner.md"
REVIEW_PROMPT_PATH = ROOT / "prompts" / "annual_report_answer_review.md"
_CITATION_RE = re.compile(r"\[(S\d+)\]")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;\n])")
_DATE_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*(?:[-/.]|年)\s*(\d{1,2})\s*(?:[-/.]|月)\s*(\d{1,2})(?:日)?(?!\d)"
)
_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z_])\(?-?\d+(?:[,，]\d+)*(?:\.\d+)?\)?%?")
_MONTHS = {name: index for index, name in enumerate(
    ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'), 1)}
_MONTH_NAMES = '|'.join(_MONTHS)


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
            f"source_revision={citation.get('source_revision')}; "
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
                for key in ("task_id", "requirement", "queries", "statement_scope", "evidence_ids", "comparison_basis")
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


def _fact_values(value: str) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace('−', '-')
    text = re.sub(r'\b(' + _MONTH_NAMES + r')\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b',
                  lambda m: f'{m[3]}年{_MONTHS[m[1].lower()]}月{m[2]}日', text, flags=re.I)
    text = re.sub(r'\b(' + _MONTH_NAMES + r')\s+((?:19|20)\d{2})\b',
                  lambda m: f'{m[2]}年{_MONTHS[m[1].lower()]}月', text, flags=re.I)
    text = re.sub(r'^\s*\d+[.)、]\s+', '', text, flags=re.M)
    text = re.sub(r'(?<=\d)\s+%', '%', text)
    facts: set[str] = set()
    for match in _DATE_RE.finditer(text):
        facts.add(
            f"date:{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        )
        facts.add(f"month:{int(match.group(1)):04d}-{int(match.group(2)):02d}")
    text = _DATE_RE.sub(" ", text)
    month_pattern = r'((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月'
    for match in re.finditer(month_pattern, text):
        facts.add(f'month:{int(match[1]):04d}-{int(match[2]):02d}')
    text = re.sub(month_pattern, ' ', text)
    for raw in _NUMBER_RE.findall(text):
        normalized = raw.replace(",", "").replace("，", "").replace(" ", "").strip()
        if normalized and normalized not in {"-", "%"}:
            facts.add(f"number:{canonical_number(normalized)}")
    return facts


def _evidence_text_by_id(assembled: dict[str, Any]) -> dict[str, str]:
    texts = {
        str(key): str(value)
        for key, value in dict(assembled.get("evidence_texts") or {}).items()
        if str(key).strip()
    }
    citations = assembled.get("citations") or []
    for citation in citations:
        evidence_id = str(citation.get("evidence_id") or "")
        if not evidence_id:
            continue
        metadata = json.dumps({key: citation.get(key) for key in ("fiscal_year", "period_years", "period_end")}, ensure_ascii=False)
        texts[evidence_id] = f"{texts.get(evidence_id, '')}\n{metadata}".strip()
    return texts


def _cited_claims(answer: str) -> list[str]:
    """把句末独立的 `[S#]` 归回紧邻的事实句。"""

    pending: list[str] = []
    claims: list[str] = []
    for raw_part in _CLAUSE_SPLIT_RE.split(str(answer or "")):
        part = raw_part.strip()
        citation_ids = extract_citation_ids(part)
        content = _CITATION_RE.sub("", part).strip()
        if citation_ids and not content and pending:
            claims.append(f"{pending.pop()}{raw_part}".strip())
        elif citation_ids:
            # 同一行“增量算式；占比算式 [S#]”共用句末引用，两个
            # 算式均需进入依赖校验，不能丢弃前半句的输入来源。
            while pending and '计算' in pending[-1] and pending[-1].endswith(('；', ';')):
                part = pending.pop().strip() + part
            claims.append(part)
        elif part:
            pending.append(raw_part)
        elif '\n' in raw_part:
            pending.clear()
    return claims


def validate_answer_facts(answer: str, assembled: dict[str, Any]) -> dict[str, Any]:
    """校验带引用论断中的日期、金额和百分比是否出现在所引 Evidence。"""

    evidence_texts = _evidence_text_by_id(assembled)
    # 算式形成带来源的依赖链：先验证输入，再允许同来源的后文引用其结果。
    verified: dict[str, set[frozenset[str]]] = {}
    claims = _cited_claims(answer)
    for _ in range(3):
        for claim in claims:
            ids = frozenset(extract_citation_ids(claim))
            def verify_with(roots, equation):
                source = '\n'.join(str(assembled.get('evidence_texts', {}).get(eid, '')) for eid in roots)
                source += '\n' + ' '.join(fact.removeprefix('number:') for fact, known in verified.items() if any(root <= roots for root in known))
                return verify_equations(equation, source)
            for equation in EQUATION.finditer(claim):
                # 独立检查每条算式的充分来源，避免额外引用污染后文依赖。
                candidates = [frozenset([eid]) for eid in sorted(ids)]
                minimal = ids
                for eid in sorted(ids):
                    trial = minimal - {eid}
                    values, errors = verify_with(trial, equation.group(0))
                    if values and not errors:
                        minimal = trial
                candidates.append(minimal)
                for roots in candidates:
                    values, errors = verify_with(roots, equation.group(0))
                    if not errors:
                        for value in values:
                            verified.setdefault(value, set()).add(roots)
    unsupported: list[dict[str, Any]] = []
    checked_claim_count = 0
    for claim in claims:
        citation_ids = extract_citation_ids(claim)
        if not claim or not citation_ids:
            continue
        facts = _fact_values(_CITATION_RE.sub("", claim))
        if not facts:
            continue
        checked_claim_count += 1
        evidence_facts: set[str] = set()
        for evidence_id in citation_ids:
            evidence_facts.update(_fact_values(evidence_texts.get(evidence_id, "")))
        available = {fact for fact, roots in verified.items() if any(root <= set(citation_ids) for root in roots)}
        evidence_facts.update(available)
        # 现金流支出以正数表达其流出规模时，允许与原文括号负值对应。
        if re.search(r'支出|流出|减少|下降|decrease|outflow|payments', claim, re.I):
            for fact in list(evidence_facts):
                if fact.startswith('number:(') and fact.endswith(')'):
                    evidence_facts.add('number:' + fact[8:-1])
                elif fact.startswith('number:-'):
                    evidence_facts.add('number:' + fact[8:])
        derived, calculation_errors = verify_equations(claim, '\n'.join(
            str(assembled.get('evidence_texts', {}).get(eid, '')) for eid in citation_ids
        ) + '\n' + ' '.join(fact.removeprefix('number:') for fact in available))
        evidence_facts.update(derived)
        if derived and not calculation_errors:
            facts = _fact_values(EQUATION.sub('', _CITATION_RE.sub('', claim)))
        missing = sorted(facts - evidence_facts)
        if missing or calculation_errors:
            unsupported.append(
                {
                    "claim": claim,
                    "citation_ids": citation_ids,
                    "missing_facts": missing,
                    "calculation_errors": calculation_errors,
                }
            )
    return {
        "checked_claim_count": checked_claim_count,
        "unsupported_claims": unsupported,
        "valid": not unsupported,
    }


def prune_unsupported_claims(answer: str, fact_check: dict[str, Any]) -> str:
    """按句/行移除无证据支持的数值论断，保留其余已验证内容。"""

    unsupported = {
        str(item.get("claim") or "").strip()
        for item in fact_check.get("unsupported_claims") or []
    }
    if not unsupported:
        return str(answer or "").strip()
    result = str(answer or "")
    for claim in unsupported:
        result = result.replace(claim, "", 1)
    return result.strip()


def repair_arithmetic(answer: str, assembled: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """仅替换已核实输入的同号算式结果；不改输入、单位、来源或其他论断。"""
    repairs = []
    check = validate_answer_facts(answer, assembled)
    for claim in check['unsupported_claims']:
        for error in claim.get('calculation_errors', []):
            if error.get('reason') != 'incorrect result' or not error.get('computed_result'):
                continue
            expression = error['expression']
            match = EQUATION.search(expression)
            if not match:
                continue
            old = Decimal(match[2].replace(',', ''))
            new_text = error['computed_result'].removesuffix('%')
            if old * Decimal(new_text) < 0:
                continue
            replacement = expression[:match.start(2)] + new_text + expression[match.end(2):]
            answer = answer.replace(expression, replacement)
            repairs.append({'expression': expression, 'replacement': replacement, 'citation_ids': claim['citation_ids']})
    return answer, repairs
