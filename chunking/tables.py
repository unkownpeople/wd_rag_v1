from __future__ import annotations

"""表格切分逻辑：保留表头、逻辑行组和未改写的 raw_matrix。"""

from typing import Any

from .config import ChunkConfig
from .identity import as_text, canonical_digest, make_chunk_id, page_value
from .boundaries import estimate_tokens


_YEAR_RE = __import__("re").compile(r"\b(?:19|20)\d{2}\b")


def _table_descriptor(record: dict[str, Any]) -> str:
    return " ".join(
        as_text(record.get(key)) for key in ("table_title", "table_context")
    ).strip()


def _is_geography_table(record: dict[str, Any]) -> bool:
    text = _table_descriptor(record).casefold()
    return "geograph" in text and ("revenue" in text or "sales" in text)


def _has_numeric_value(row: list[Any]) -> bool:
    return any(any(character.isdigit() for character in as_text(cell)) for cell in row[1:])


def _geography_groups(matrix: list[list[Any]], header_count: int) -> list[list[int]]:
    groups: list[list[int]] = []
    pending_parent: list[int] = []
    for row_index in range(header_count, len(matrix)):
        row = matrix[row_index]
        if as_text(row[0]).strip() and not _has_numeric_value(row):
            pending_parent = [row_index]
            continue
        if _has_numeric_value(row):
            groups.append([*pending_parent, row_index])
            pending_parent = []
    if pending_parent:
        groups.append(pending_parent)
    return groups


def _period_years(labels: list[str]) -> list[int]:
    return sorted({int(match.group(0)) for label in labels for match in _YEAR_RE.finditer(label)})


def _selected_labels(
    matrix: list[list[Any]], row_indices: list[int], header_count: int
) -> tuple[list[str], list[str]]:
    labels = [as_text(matrix[index][0]).strip() for index in row_indices if matrix[index]]
    labels = [label for label in labels if label]
    parent_labels = [
        as_text(matrix[index][0]).strip()
        for index in row_indices
        if matrix[index] and not _has_numeric_value(matrix[index])
    ]
    return labels, parent_labels


def table_header_count(record: dict[str, Any], matrix: list[list[Any]]) -> int:
    if "header_row_count" in record:
        try:
            return max(0, min(int(record["header_row_count"]), len(matrix)))
        except (TypeError, ValueError):
            pass
    header_rows = record.get("header_rows")
    if isinstance(header_rows, list) and header_rows and isinstance(header_rows[0], list):
        return max(1, min(len(header_rows), len(matrix)))
    return 1 if matrix else 0


def header_labels(
    record: dict[str, Any], matrix: list[list[Any]], header_count: int
) -> list[str]:
    labels = record.get("table_headers")
    if isinstance(labels, list) and labels:
        return [as_text(label) for label in labels]

    width = max((len(row) for row in matrix), default=0)
    output: list[str] = []
    for index in range(width):
        parts: list[str] = []
        for row in matrix[:header_count]:
            value = as_text(row[index]).strip() if index < len(row) else ""
            if value and value not in parts:
                parts.append(value)
        output.append(" / ".join(parts) or f"column_{index + 1}")
    return output


def table_location(record: dict[str, Any]) -> str:
    location = record.get("location")
    if isinstance(location, dict):
        return ";".join(f"{key}={value}" for key, value in location.items())
    return as_text(location) or f"page={record.get('page_number')}"


def table_groups(
    record: dict[str, Any], matrix: list[list[Any]], config: ChunkConfig
) -> list[list[int]]:
    """小表整表；大表优先按 Task/Input 等逻辑键分组。"""

    header_count = table_header_count(record, matrix)
    body = list(range(header_count, len(matrix)))
    if _is_geography_table(record):
        return _split_groups_to_budget(
            record, matrix, _geography_groups(matrix, header_count), config
        )
    if len(body) <= config.small_table_body_rows:
        groups = [body] if body else [[]]
        return _split_groups_to_budget(record, matrix, groups, config)

    key_columns = [] if _is_geography_table(record) else logical_key_columns(record, matrix, header_count)
    if not key_columns:
        groups = [
            body[index : index + config.small_table_body_rows]
            for index in range(0, len(body), config.small_table_body_rows)
        ]
        return _split_groups_to_budget(record, matrix, groups, config)

    groups: list[list[int]] = []
    keys: list[tuple[str, ...]] = []
    current_values = [""] * len(key_columns)
    for row_index in body:
        row = matrix[row_index]
        for key_position, column_index in enumerate(key_columns):
            value = as_text(row[column_index]).strip() if column_index < len(row) else ""
            if value:
                current_values[key_position] = value
        key = tuple(current_values)
        if not groups or not any(key) or key != keys[-1]:
            groups.append([])
            keys.append(key or (f"row-{row_index}",))
        groups[-1].append(row_index)
    return _split_groups_to_budget(record, matrix, groups, config)


def _split_groups_to_budget(
    record: dict[str, Any],
    matrix: list[list[Any]],
    groups: list[list[int]],
    config: ChunkConfig,
) -> list[list[int]]:
    """在保留完整 raw_matrix 的前提下，为检索投影设置硬 Token 上限。

    HTML 年报表格可能存在跨行空白键列，单纯按逻辑键分组会把数十行
    合并成一个超长块。这里只拆分 ``row_indices``，不改写矩阵内容；
    每个表块仍继承同一表头和完整 ``raw_matrix``。
    """

    header_count = table_header_count(record, matrix)
    labels = header_labels(record, matrix, header_count)
    key_columns = [] if _is_geography_table(record) else logical_key_columns(record, matrix, header_count)
    search_rows = _search_rows_with_filled_keys(matrix, header_count, key_columns)
    location = table_location(record)

    def projected_token_count(row_indices: list[int]) -> int:
        lines = [
            f"table_id={record.get('table_id')}；location={location}；headers="
            + " | ".join(labels)
        ]
        for row_index in row_indices:
            row = search_rows.get(row_index, list(matrix[row_index]))
            pairs = []
            for column_index, label in enumerate(labels):
                value = as_text(row[column_index]).strip() if column_index < len(row) else ""
                pairs.append(f"{label}={value or '空值'}")
            lines.append(f"row_number={row_index + 1}；" + "；".join(pairs))
        return estimate_tokens("\n".join(lines), config.tokenizer)

    budget = config.max_tokens
    output: list[list[int]] = []
    for group in groups:
        if not group:
            output.append(group)
            continue
        current: list[int] = []
        for row_index in group:
            candidate = [*current, row_index]
            if current and projected_token_count(candidate) > budget:
                output.append(current)
                current = []
            current.append(row_index)
        if current:
            output.append(current)
    return output


def logical_key_columns(
    record: dict[str, Any], matrix: list[list[Any]], header_count: int
) -> list[int]:
    """返回用于分组和检索继承的 Task/Input 等逻辑键列。"""

    labels = [label.lower().strip() for label in header_labels(record, matrix, header_count)]
    key_columns = [
        index for index, label in enumerate(labels) if label in {"task", "input"}
    ]
    if not key_columns and matrix and matrix[0]:
        return [0]
    return key_columns


def _search_rows_with_filled_keys(
    matrix: list[list[Any]], header_count: int, key_columns: list[int]
) -> dict[int, list[Any]]:
    """只为检索投影填充续行键，绝不改写 raw_matrix 或 row_matrix。"""

    current_values = {column: "" for column in key_columns}
    rows: dict[int, list[Any]] = {}
    for row_index in range(header_count, len(matrix)):
        row = list(matrix[row_index])
        for column in key_columns:
            value = as_text(row[column]).strip() if column < len(row) else ""
            if value:
                current_values[column] = value
            elif current_values[column] and column < len(row):
                row[column] = current_values[column]
        rows[row_index] = row
    return rows


def make_table_chunk(
    record: dict[str, Any],
    record_id: str,
    record_index: int,
    matrix: list[list[Any]],
    row_indices: list[int],
    ordinal: int,
    config: ChunkConfig | None = None,
) -> dict[str, Any]:
    """创建一张小表或一个逻辑行组的检索投影。"""

    config = config or ChunkConfig()
    header_count = table_header_count(record, matrix)
    labels = header_labels(record, matrix, header_count)
    key_columns = [] if _is_geography_table(record) else logical_key_columns(record, matrix, header_count)
    search_rows = _search_rows_with_filled_keys(matrix, header_count, key_columns)
    location = table_location(record)
    header_rows = matrix[:header_count]
    lines = [
        f"table_id={record.get('table_id') or record_id}；location={location}；headers="
        + " | ".join(labels)
    ]
    if record.get("table_title"):
        lines.append(f"table_title={as_text(record['table_title'])}")
    if record.get("table_context"):
        lines.append(f"table_context={as_text(record['table_context'])}")
    first_search_row = search_rows.get(row_indices[0], []) if row_indices else []
    group_key = ";".join(
        f"{labels[column]}={as_text(first_search_row[column]).strip()}"
        for column in key_columns
        if column < len(first_search_row) and as_text(first_search_row[column]).strip()
    )
    for row_index in row_indices:
        row = search_rows.get(row_index, list(matrix[row_index]))
        pairs = []
        for column_index, label in enumerate(labels):
            value = as_text(row[column_index]).strip() if column_index < len(row) else ""
            pairs.append(f"{label}={value or '空值'}")
        lines.append(f"row_number={row_index + 1}；" + "；".join(pairs))

    search_text = "\n".join(lines)
    chunk_text = search_text
    parent_id = record_id
    page = page_value(record)
    selected_labels, parent_labels = _selected_labels(matrix, row_indices, header_count)
    descriptor = _table_descriptor(record)
    measure_name = "revenue" if "revenue" in descriptor.casefold() else None
    value_kind = "monetary" if any(
        token in descriptor.casefold()
        for token in ("crore", "million", "billion", "revenue", "sales")
    ) else "numeric"
    return {
        "chunk_id": make_chunk_id(parent_id, "table", ordinal, chunk_text, []),
        "parent_record_id": parent_id,
        "source_record_ids": [record_id],
        "source_format": as_text(record.get("source_format")),
        "page_start": page,
        "page_end": page,
        "section_path": f"page:{page}" if page is not None else location,
        "chunk_type": "table" if len(row_indices) == len(matrix) - header_count else "table_group",
        "chunk_text": chunk_text,
        "token_count": estimate_tokens(search_text, config.tokenizer),
        "source_token_count": estimate_tokens(
            "\n".join(
                as_text(cell)
                for row in header_rows + [matrix[index] for index in row_indices]
                for cell in row
            ),
            config.tokenizer,
        ),
        "overlap_from": None,
        "overlap_token_count": 0,
        "boundary_reason": "table_group",
        "table_id": as_text(record.get("table_id")) or record_id,
        "table_header": header_rows,
        "column_headers": labels,
        "parent_row_labels": parent_labels,
        "row_labels": selected_labels,
        "measure_name": measure_name,
        "value_kind": value_kind,
        "period_years": _period_years(labels),
        "table_title": record.get("table_title"),
        "table_context": record.get("table_context"),
        "statement_scope": record.get("statement_scope"),
        "header_row_count": header_count,
        "row_indices": row_indices,
        "row_matrix": [list(row) for row in header_rows]
        + [list(matrix[index]) for index in row_indices],
        # 每个表块都保留完整原矩阵，投影文本不能覆盖原始数据。
        "raw_matrix": [list(row) for row in matrix],
        "raw_matrix_sha256": canonical_digest(matrix),
        "source_record_index": record_index,
        "source_location": location,
        "group_key": group_key,
        "search_text": search_text,
    }


def make_table_chunks(
    record: dict[str, Any],
    record_id: str,
    record_index: int,
    matrix: list[list[Any]],
    config: ChunkConfig,
    ordinal_start: int,
) -> list[dict[str, Any]]:
    return [
        make_table_chunk(
            record,
            record_id,
            record_index,
            matrix,
            row_indices,
            ordinal_start + offset,
            config,
        )
        for offset, row_indices in enumerate(table_groups(record, matrix, config))
    ]
