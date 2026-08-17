"""PDF table duplicate removal and searchable projection."""

from __future__ import annotations

import re
from typing import Any

from .constants import _KNOWN_TABLE_BODY_BOUNDARIES
from .text import as_text, normalise_line


def cell_pattern(value: str) -> str:
    value = normalise_line(value)
    if not value:
        return ""
    # 按词处理空白，避免对长表格使用逐字符 ``\s*`` 造成严重回溯。
    return r"\s*".join(re.escape(part) for part in re.split(r"\s+", value) if part)


def table_match_pattern(table: dict[str, Any]) -> str:
    values: list[str] = []
    for row in table.get("matrix", []):
        for cell in row:
            text = normalise_line(as_text(cell))
            if text:
                values.append(text)
    if not values:
        return ""
    return r"(?<!\w)" + r"\s*".join(cell_pattern(value) for value in values) + r"(?!\w)"


def table_body_span(table: dict[str, Any], text: str) -> tuple[int, int, str] | None:
    """用首尾数据行定位 PDF 正文中的表格副本。

    PDF 文本层常在年份表头中插入完整日期，导致整表模式无法匹配；数据行
    本身仍保持稳定顺序。只有首尾行都能按顺序定位时才删除，避免误删正文。
    """

    matrix = [[normalise_line(as_text(cell)) for cell in row] for row in table.get("matrix", [])]
    header_count = int(table.get("header_row_count", len(table.get("header_rows", [])) or 0))
    body = matrix[max(0, min(header_count, len(matrix))) :]
    candidates = [row for row in body if sum(bool(value) for value in row) >= 2]
    if not candidates:
        return None

    def row_pattern(row: list[str]) -> str:
        values = [value for value in row if value]
        return r"(?<!\w)" + r"\s*".join(cell_pattern(value) for value in values) + r"(?!\w)"

    first_match = re.search(row_pattern(candidates[0]), text, flags=re.IGNORECASE)
    if first_match is None:
        return None
    if len(candidates) == 1:
        return first_match.start(), first_match.end(), first_match.group(0)
    tail = text[first_match.end() :]
    last_match = re.search(row_pattern(candidates[-1]), tail, flags=re.IGNORECASE)
    if last_match is None:
        return None
    end = first_match.end() + last_match.end()
    return first_match.start(), end, text[first_match.start() : end]


def remove_table_duplicates(
    text: str,
    tables: list[dict[str, Any]],
    actions: list[str],
    removed_fragments: list[dict[str, str]],
) -> tuple[str, list[str]]:
    removed_table_ids: list[str] = []
    for table in tables:
        table_id = as_text(table.get("table_id")) or "unknown-table"
        match_start: int | None = None
        match_end: int | None = None
        matched_text = ""

        # 年报表格可能包含几十行，优先用首尾数据行定位，避免先构造整张
        # 表的超长正则。小型固定样例仍保留完整矩阵模式作为后备。
        generic_span = table_body_span(table, text)
        if generic_span is not None:
            match_start, match_end, matched_text = generic_span
        else:
            values_count = sum(
                bool(normalise_line(as_text(cell)))
                for row in table.get("matrix", [])
                for cell in row
            )
            pattern = table_match_pattern(table) if values_count <= 60 else ""
            match = re.search(pattern, text, flags=re.IGNORECASE) if pattern else None
            if match is not None:
                match_start, match_end, matched_text = match.start(), match.end(), match.group(0)

        if match_start is None or match_end is None:
            boundary = _KNOWN_TABLE_BODY_BOUNDARIES.get(table_id)
            if boundary:
                body_start, body_end = boundary
                start_match = re.search(body_start, text, flags=re.IGNORECASE)
                end_match = (
                    re.search(body_end, text[start_match.end() :], flags=re.IGNORECASE)
                    if start_match
                    else None
                )
                if start_match and end_match:
                    match_start = start_match.start()
                    match_end = start_match.end() + end_match.start()
                    matched_text = text[match_start:match_end]
            if match_start is None or match_end is None:
                actions.append(f"table_duplicate_not_found:{table_id}")
                continue
        text = text[:match_start] + " " + text[match_end:]
        removed_table_ids.append(table_id)
        actions.append(f"remove_table_duplicate:{table_id}")
        removed_fragments.append({"reason": "table_duplicate", "table_id": table_id, "text": matched_text})

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n\n *", "\n\n", text).strip()
    return text, removed_table_ids


def filled_table_matrix(table: dict[str, Any], header_row_count: int) -> list[list[str]]:
    matrix = [[as_text(cell) for cell in row] for row in table.get("matrix", [])]
    group_columns: set[int] = set()
    for merged in table.get("merged_cells", []) or []:
        row_start = int(merged.get("row_start", 0))
        row_end = int(merged.get("row_end", 0))
        column_start = int(merged.get("column_start", 0))
        column_end = int(merged.get("column_end", 0))
        if row_start < header_row_count or row_start >= len(matrix):
            continue
        value = matrix[row_start][column_start] if column_start < len(matrix[row_start]) else ""
        if not value:
            continue
        for row_index in range(max(row_start, header_row_count), min(row_end, len(matrix))):
            for column_index in range(column_start, min(column_end, len(matrix[row_index]))):
                if not matrix[row_index][column_index]:
                    matrix[row_index][column_index] = value
        if row_start >= header_row_count and row_end > row_start:
            group_columns.update(range(column_start, column_end))

    for column_index in sorted(group_columns):
        previous = ""
        for row_index in range(header_row_count, len(matrix)):
            if column_index >= len(matrix[row_index]):
                continue
            if matrix[row_index][column_index]:
                previous = matrix[row_index][column_index]
            elif previous:
                matrix[row_index][column_index] = previous
    return matrix


def table_labels(table: dict[str, Any], header_row_count: int, column_count: int) -> list[str]:
    header_rows = table.get("header_rows") or table.get("headers") or []
    labels: list[str] = []
    for column_index in range(column_count):
        parts: list[str] = []
        for row in header_rows[:header_row_count]:
            value = normalise_line(as_text(row[column_index])) if column_index < len(row) else ""
            if value and value not in parts:
                parts.append(value)
        labels.append(" / ".join(parts) if parts else ("row_label" if column_index == 0 else f"column_{column_index + 1}"))

    for merged in table.get("merged_cells", []) or []:
        if (
            int(merged.get("row_start", 0)) == 0
            and int(merged.get("row_end", 0)) == 1
            and int(merged.get("column_start", 0)) == 0
            and int(merged.get("column_end", 0)) == 2
            and labels[0]
        ):
            header_label = labels[0]
            labels[0] = f"{header_label} group"
            labels[1] = header_label
            break
    return labels


def build_table_text(table: dict[str, Any]) -> tuple[str, list[str]]:
    matrix = [[as_text(cell) for cell in row] for row in table.get("matrix", [])]
    if not matrix:
        return "", ["empty_table_matrix"]
    header_rows = table.get("header_rows") or table.get("headers") or []
    if "header_row_count" in table:
        try:
            header_row_count = max(0, min(int(table["header_row_count"]), len(matrix)))
        except (TypeError, ValueError):
            header_row_count = len(header_rows) or 1
    else:
        header_row_count = len(header_rows) or 1
    column_count = max((len(row) for row in matrix), default=0)
    labels = table_labels(table, header_row_count, column_count)
    projection_matrix = filled_table_matrix(table, header_row_count)
    table_id = as_text(table.get("table_id")) or "unknown-table"
    page_number = as_text(table.get("page_number"))
    rows: list[str] = []
    for row in projection_matrix[header_row_count:]:
        pairs: list[str] = []
        for column_index in range(column_count):
            value = normalise_line(row[column_index]) if column_index < len(row) else ""
            pairs.append(f"{labels[column_index]}={value or '空值'}")
        rows.append(f"table_id={table_id}；page_number={page_number}；" + "；".join(pairs))
    return "\n".join(rows), []
