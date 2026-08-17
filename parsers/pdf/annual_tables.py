"""确定性重建带文本层的年报 PDF 表格。"""

from __future__ import annotations

import re
from typing import Any

from .table_context import table_source_metadata


def _cluster_segments(page: Any, bbox: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    x0, top, x1, bottom = bbox
    groups: list[list[float]] = []
    horizontal_objects = list(page.lines) + [
        rect
        for rect in page.rects
        if float(rect.get("height", 0)) <= 2.5 and float(rect.get("width", 0)) >= 20
    ]
    for line in horizontal_objects:
        line_x0 = float(line.get("x0", 0))
        line_x1 = float(line.get("x1", 0))
        line_top = float(line.get("top", 0))
        if line_x1 - line_x0 < 20:
            continue
        # 年报常把表头横线放在第一行数据 bbox 之上约 10-20pt，适当
        # 向上扩展才能恢复 Change/年份等列；仍以候选表的左右范围约束。
        if line_top < top - 40 or line_top > bottom + 3:
            continue
        if line_x0 < x0 - 3 or line_x1 > x1 + 3:
            continue
        matched = None
        for group in groups:
            if abs(line_x0 - group[0]) <= 3 and abs(line_x1 - group[1]) <= 5:
                matched = group
                break
        if matched is None:
            groups.append([line_x0, line_x1, 1])
        else:
            matched[0] = (matched[0] * matched[2] + line_x0) / (matched[2] + 1)
            matched[1] = (matched[1] * matched[2] + line_x1) / (matched[2] + 1)
            matched[2] += 1
    return sorted((round(group[0], 2), round(group[1], 2)) for group in groups)


def _column_ranges(
    bbox: tuple[float, float, float, float], segments: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """从财务表横线恢复“行标签 + 数值列”的稳定范围。

    部分年报会同时绘制每个年度数值列的短横线和跨多个年度的总横线。
    总横线不能作为列边界，否则第一年度数值会落入列间空隙。这里保留右侧
    的最小非包含线段，并把其左侧统一作为行标签列。
    """

    if len(segments) < 2:
        return []
    page_width = bbox[2] - bbox[0]
    right_side = [
        segment
        for segment in segments
        if segment[0] >= bbox[0] + page_width * 0.35 and segment[1] - segment[0] >= 20
    ]
    minimal: list[tuple[float, float]] = []
    for segment in right_side:
        contains_smaller = any(
            other != segment
            and segment[0] <= other[0] + 1
            and segment[1] >= other[1] - 1
            and (segment[1] - segment[0]) > (other[1] - other[0]) + 8
            for other in right_side
        )
        if not contains_smaller:
            minimal.append(segment)

    unique: list[tuple[float, float]] = []
    for segment in sorted(minimal):
        if unique and abs(segment[0] - unique[-1][0]) <= 3 and abs(segment[1] - unique[-1][1]) <= 5:
            continue
        unique.append(segment)
    if len(unique) < 2:
        return []
    label_range = (bbox[0], unique[0][0] - 1)
    if label_range[0] >= label_range[1]:
        return []
    return [label_range, *unique]


def _numeric_word_count(words: list[dict[str, Any]]) -> int:
    return sum(bool(re.search(r"\d|[%$]|[—–-]", str(word.get("text", "")))) for word in words)


def _likely_annual_table_page(page: Any) -> bool:
    """过滤明显不是财务表的页面，避免对整份年报做昂贵几何扫描。"""

    text = str(page.extract_text() or "")
    if len(re.findall(r"\d", text)) < 8:
        return False
    return bool(
        re.search(
            r"following table|following tables|consolidated statements|balance sheets|"
            r"cash flows|shareholders.? equity|in millions|segment information|"
            r"net sales by|revenue disaggregation|reconciliation of",
            text,
            flags=re.IGNORECASE,
        )
    )


def _expanded_table_bbox(
    words: list[dict[str, Any]], bbox: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """把只覆盖数值列的候选扩展到同一行的左侧标签列。"""

    row_words = [
        word
        for word in words
        if float(word.get("top", 0)) >= bbox[1] - 1
        and float(word.get("bottom", 0)) <= bbox[3] + 1
    ]
    left = min((float(word.get("x0", bbox[0])) for word in row_words), default=bbox[0])
    return (left, bbox[1], bbox[2], bbox[3])


def _header_prefix_count(rows: list[list[str]]) -> int:
    count = 0
    for row in rows:
        first = row[0].strip() if row else ""
        rest = " ".join(row[1:])
        if first or not rest.strip():
            break
        count += 1
    # “首列为空、其余列非空”只是弱表头特征，不能据此吞掉整张表。
    # 全部行都命中时放弃该判断，改由版面位置提取表头。
    return 0 if count == len(rows) else count


def _cell_text(words: list[dict[str, Any]], left: float, right: float, top: float, bottom: float) -> str:
    selected = [
        word
        for word in words
        if float(word.get("x0", 0)) >= left - 1
        and float(word.get("x1", 0)) <= right + 1
        and float(word.get("top", 0)) >= top - 1
        and float(word.get("bottom", 0)) <= bottom + 1
    ]
    selected.sort(key=lambda word: (float(word.get("top", 0)), float(word.get("x0", 0))))
    return " ".join(str(word.get("text", "")) for word in selected).strip()


def _header_rows(
    words: list[dict[str, Any]],
    ranges: list[tuple[float, float]],
    bbox: tuple[float, float, float, float],
) -> list[list[str]]:
    candidates = [
        word
        for word in words
        if float(word.get("top", 0)) >= bbox[1] - 42
        and float(word.get("bottom", 0)) <= bbox[1] + 1
        and float(word.get("x0", 0)) >= bbox[0] - 2
        and float(word.get("x1", 0)) <= bbox[2] + 2
    ]
    tops: list[float] = []
    for word in sorted(candidates, key=lambda item: float(item.get("top", 0))):
        top = float(word.get("top", 0))
        if not tops or abs(top - tops[-1]) > 3:
            tops.append(top)
    output: list[list[str]] = []
    for top in tops:
        line_words = [
            word for word in candidates if abs(float(word.get("top", 0)) - top) <= 3
        ]
        line_bottom = max((float(word.get("bottom", 0)) for word in line_words), default=top + 10)
        row = [_cell_text(line_words, left, right, top - 1, line_bottom + 1) for left, right in ranges]
        year_cells = sum(bool(re.search(r"\b(?:19|20)\d{2}\b", value)) for value in row[1:])
        if year_cells >= 2:
            output.append(row)
    return output[-2:]


def extract_annual_tables(
    page: Any,
    page_number: int,
    document_stem: str,
    *,
    page_text: str = "",
) -> list[dict[str, Any]]:
    """提取年报页面中具有稳定版面线索的表格候选。

    表格没有竖线时，pdfplumber 默认会把整行合并为一个单元格；这里使用
    横线的重复 x 区间恢复数值列，再用文本坐标归行。无横线或数字密度不足
    的候选（目录、普通段落）不升级为表格记录。
    """

    if not _likely_annual_table_page(page):
        return []
    tables = page.find_tables() or []
    words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
    output: list[dict[str, Any]] = []
    for candidate_index, table in enumerate(tables, start=1):
        numeric_bbox = tuple(float(value) for value in table.bbox)
        bbox = _expanded_table_bbox(words, numeric_bbox)
        rows = list(table.rows)
        if len(rows) < 3:
            continue
        candidate_words = [
            word
            for word in words
            if float(word.get("x0", 0)) >= bbox[0] - 2
            and float(word.get("x1", 0)) <= bbox[2] + 2
            and float(word.get("top", 0)) >= bbox[1] - 2
            and float(word.get("bottom", 0)) <= bbox[3] + 2
        ]
        if _numeric_word_count(candidate_words) < len(rows) + 2:
            continue
        segments = _cluster_segments(page, numeric_bbox)
        ranges = _column_ranges(numeric_bbox, segments)
        if not ranges:
            widest = sorted(segments, key=lambda segment: segment[1] - segment[0], reverse=True)
            max_width = widest[0][1] - widest[0][0] if widest else 0
            numeric_ranges = sorted(
                segment for segment in segments if segment[1] - segment[0] >= max_width * 0.9
            )
            label_range = (
                (bbox[0], numeric_ranges[0][0] - 1)
                if numeric_ranges
                else None
            )
            ranges = (
                [label_range, *numeric_ranges]
                if label_range is not None and label_range[0] < label_range[1]
                else []
            )
        if not ranges:
            continue
        rows_output: list[list[str]] = []
        for row in rows:
            row_top = float(row.bbox[1])
            row_bottom = float(row.bbox[3])
            values = [
                _cell_text(candidate_words, left, right, row_top, row_bottom)
                for left, right in ranges
            ]
            if any(values):
                rows_output.append(values)
        header_count = _header_prefix_count(rows_output)
        headers = rows_output[:header_count] or _header_rows(words, ranges, bbox)
        body = rows_output[header_count:]
        matrix = [*headers, *body]
        if len(body) < 3 or len(matrix[0]) < 3:
            continue
        table_id = f"{document_stem}_p{page_number:03d}_t{candidate_index:02d}"
        source_text = page_text or str(page.extract_text() or "")
        source_metadata = table_source_metadata(
            words=words,
            bbox=bbox,
            page_text=source_text,
        )
        output.append(
            {
                "table_id": table_id,
                "page_number": page_number,
                "bbox": list(bbox),
                "content_type": "table",
                "matrix": matrix,
                "header_rows": headers,
                "headers": headers,
                "header_row_count": len(headers),
                **source_metadata,
                "rows": body,
                "row_count": len(body),
                "column_count": max(len(row) for row in matrix),
                "merged_cells": [],
                "extraction_method": "pdfplumber_annual_lines_and_words",
                "region_status": "accepted_heuristic",
                "warnings": ["年报 PDF 自动表格候选；需用固定样例继续做视觉/数值对比"],
            }
        )
    return output
