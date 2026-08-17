"""从 PDF 源页面无损提取表格标题、章节上下文和明确口径标签。"""

from __future__ import annotations

import re
from typing import Any


_TITLE_HINT = re.compile(r"\b(?:table|statement(?:s)?|schedule|note)\b", re.IGNORECASE)
_SCOPE_LABEL = re.compile(r"\b(standalone|consolidated)\b", re.IGNORECASE)


def _normalized_lines(values: list[str]) -> list[str]:
    return [line for value in values if (line := " ".join(str(value or "").split()))]


def _leading_page_lines(page_text: str, *, limit: int = 12) -> list[str]:
    return _normalized_lines(str(page_text or "").splitlines())[:limit]


def _layout_lines_above_table(
    words: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
    *,
    lookback_points: float = 160.0,
) -> list[str]:
    table_top = float(bbox[1])
    nearby = [
        word
        for word in words
        if table_top - lookback_points <= float(word.get("top", 0)) < table_top
    ]
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(nearby, key=lambda item: (float(item.get("top", 0)), float(item.get("x0", 0)))):
        top = float(word.get("top", 0))
        if not rows or abs(top - float(rows[-1][0].get("top", 0))) > 3:
            rows.append([word])
        else:
            rows[-1].append(word)
    return _normalized_lines(
        [
            " ".join(
                str(word.get("text", ""))
                for word in sorted(row, key=lambda item: float(item.get("x0", 0)))
            )
            for row in rows
        ]
    )


def explicit_statement_scope(text: str) -> str:
    """只返回源文档明确出现且不冲突的口径标签。"""

    labels = {match.casefold() for match in _SCOPE_LABEL.findall(str(text or ""))}
    if len(labels) == 1:
        return next(iter(labels))
    return "unknown"


def table_source_metadata(
    *,
    words: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
    page_text: str,
) -> dict[str, str | None]:
    """提取表格前已有的源文字，不依据公司、页码、指标或事实值推断。"""

    layout_lines = _layout_lines_above_table(words, bbox)
    leading_lines = _leading_page_lines(page_text)
    context_lines = layout_lines[-8:] or leading_lines
    context = "\n".join(context_lines) or None
    title = next(
        (line for line in reversed(context_lines) if _TITLE_HINT.search(line)),
        None,
    )
    if title is None and context_lines != leading_lines:
        title = next(
            (line for line in reversed(leading_lines) if _TITLE_HINT.search(line)),
            None,
        )
    scope_text = "\n".join(context_lines or leading_lines)
    scope = explicit_statement_scope(scope_text)
    if scope == "unknown" and context_lines != leading_lines:
        scope = explicit_statement_scope("\n".join(leading_lines))
    return {
        "table_title": title,
        "table_context": context,
        "statement_scope": scope,
    }
