from __future__ import annotations

"""检索请求和可选过滤条件的稳定数据契约。"""

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


def _contains_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    if re.search(r"[a-zA-Z]", alias):
        return bool(re.search(rf"(?<![a-zA-Z]){re.escape(alias.casefold())}(?![a-zA-Z])", text))
    return alias.casefold() in text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


@dataclass
class QueryRequest:
    """统一的检索请求。

    顶层字段用于常见过滤条件，``filters`` 用于保留未来扩展字段。
    显式过滤条件优先于从自然语言中推断出的条件。
    """

    query: str
    company_id: str | None = None
    fiscal_year: int | None = None
    source_format: str | None = None
    document_id: str | None = None
    report_type: str | None = None
    statement_family: str | None = None
    statement_scope: str | None = None
    top_k: int = 5
    filters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.query = str(self.query or "").strip()
        if not self.query:
            raise ValueError("query 不能为空")
        self.company_id = _optional_text(self.company_id)
        self.source_format = _optional_text(self.source_format)
        self.document_id = _optional_text(self.document_id)
        self.report_type = _optional_text(self.report_type)
        self.statement_family = _optional_text(self.statement_family)
        self.statement_scope = _optional_text(self.statement_scope)
        if self.fiscal_year is not None:
            try:
                self.fiscal_year = int(self.fiscal_year)
            except (TypeError, ValueError) as exc:
                raise ValueError("fiscal_year 必须是整数") from exc
        self.top_k = int(self.top_k)
        if not 1 <= self.top_k <= 100:
            raise ValueError("top_k 必须在 1 到 100 之间")
        self.filters = {
            str(key): value
            for key, value in dict(self.filters or {}).items()
            if value is not None and str(value).strip() != ""
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "QueryRequest":
        if not isinstance(value, Mapping):
            raise TypeError("检索请求必须是映射对象")
        filters = dict(value.get("filters") or {})
        for key in (
            "company_id", "fiscal_year", "source_format", "document_id", "report_type",
            "statement_family", "statement_scope",
        ):
            if value.get(key) is not None:
                filters[key] = value[key]
        return cls(
            query=str(value.get("query") or ""),
            company_id=value.get("company_id"),
            fiscal_year=value.get("fiscal_year"),
            source_format=value.get("source_format"),
            document_id=value.get("document_id"),
            report_type=value.get("report_type"),
            statement_family=value.get("statement_family"),
            statement_scope=value.get("statement_scope"),
            top_k=value.get("top_k", 5),
            filters=filters,
        )

    def explicit_filters(self) -> dict[str, Any]:
        """返回不应被自然语言推断覆盖的显式过滤条件。"""

        filters = dict(self.filters)
        if self.company_id is not None:
            filters["company_id"] = self.company_id
        if self.fiscal_year is not None:
            filters["fiscal_year"] = self.fiscal_year
        if self.source_format is not None:
            filters["source_format"] = self.source_format
        if self.document_id is not None:
            filters["document_id"] = self.document_id
        if self.report_type is not None:
            filters["report_type"] = self.report_type
        if self.statement_family is not None:
            filters["statement_family"] = self.statement_family
        if self.statement_scope is not None:
            filters["statement_scope"] = self.statement_scope
        return filters

    def with_filters(self, filters: Mapping[str, Any]) -> "QueryRequest":
        merged = dict(filters)
        merged.update(self.explicit_filters())
        return QueryRequest(query=self.query, top_k=self.top_k, filters=merged)


def infer_filters(
    query: str,
    *,
    aliases: Mapping[str, str] | None = None,
    known_years: set[int] | None = None,
    known_formats: set[str] | None = None,
    known_report_types: set[str] | None = None,
) -> dict[str, Any]:
    """只根据当前索引中确实存在的值推断过滤条件。

    未识别到公司或年份时保持为空，避免猜测造成漏召回。
    """

    normalized = re.sub(r"\s+", " ", str(query or "").casefold()).strip()
    result: dict[str, Any] = {}
    aliases = aliases or {}
    for alias, company_id in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if alias and alias in normalized:
            result["company_id"] = company_id
            break

    years = known_years or set()
    fiscal_year_pattern = (
        r"(?:fiscal|fy|财年|财务年度|年度报告|annual report)[^0-9]{0,8}((?:19|20)\d{2})"
        r"|((?:19|20)\d{2})[^a-z0-9]{0,4}(?:fiscal|fy|财年|财务年度|年度报告|annual report)"
    )
    explicit_fiscal_years = {
        int(next(group for group in match.groups() if group))
        for match in re.finditer(fiscal_year_pattern, normalized)
    }
    known_fiscal_years = {
        year for year in explicit_fiscal_years if not years or year in years
    }
    mentioned_years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', normalized))
    if len(explicit_fiscal_years) == 1 and len(known_fiscal_years) == 1 and len(mentioned_years) == 1:
        result["fiscal_year"] = next(iter(known_fiscal_years))

    formats = {str(value).casefold() for value in (known_formats or set())}
    for source_format in formats:
        if re.search(rf"(?<![a-z]){re.escape(source_format)}(?![a-z])", normalized):
            result["source_format"] = source_format
            break

    report_types = {str(value).casefold(): value for value in (known_report_types or set())}
    if ("annual report" in normalized or "年报" in normalized) and report_types:
        result["report_type"] = report_types.get("annual_report", next(iter(report_types.values())))
    if re.search(r"\bconsolidated\b|合并", normalized):
        result["statement_scope"] = "consolidated"
    elif re.search(r"\bstandalone\b|\bseparate financial\b|单体|独立口径", normalized):
        result["statement_scope"] = "standalone"
    return result
