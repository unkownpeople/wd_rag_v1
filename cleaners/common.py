from __future__ import annotations

"""Shared deterministic helpers for the non-PDF cleaning branches."""

import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


CLEANING_VERSION = "clean-v1"

_LIGATURES = str.maketrans(
    {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
    }
)
_PROTECTED_TOKEN_RE = re.compile(
    r"\[\d+\]|(?<![\w])[-+]?\d+(?:[.,]\d+)*(?:%|[A-Za-zµμ]+)?"
)


def as_text(value: Any) -> str:
    return "" if value is None else str(value)


def source_name(source_file: str) -> str:
    return Path(as_text(source_file)).name


def source_digest(source_file: str) -> str:
    """Return a stable source id without writing anything beside the source."""

    path = Path(as_text(source_file))
    if path.is_file():
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return "sha256:" + hashlib.sha256(as_text(source_file).encode("utf-8")).hexdigest()


def normalise_line(value: Any) -> str:
    text = as_text(value)
    text = text.replace("\u00a0", " ").replace("\u2007", " ").replace("\u202f", " ")
    text = text.translate(_LIGATURES)
    return re.sub(r"[ \t]+", " ", text).strip()


def clean_text(value: Any, actions: list[str]) -> str:
    """Clean layout whitespace while preserving paragraph boundaries and facts."""

    raw = as_text(value)
    normalised = raw.replace("\r\n", "\n").replace("\r", "\n")
    if normalised != raw:
        actions.append("normalize_line_endings")
    if raw.translate(_LIGATURES) != raw:
        actions.append("normalize_ligatures")

    lines = [normalise_line(line) for line in normalised.split("\n")]
    if any(line != original.strip() for line, original in zip(lines, normalised.split("\n"))):
        actions.append("normalize_whitespace")

    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if current:
            previous = current[-1]
            if previous.endswith("-") and line[:1].islower() and not previous.endswith("--"):
                current[-1] = previous[:-1] + line
                actions.append("join_hyphenated_line")
            else:
                current.append(line)
        else:
            current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs).strip()


def protected_tokens(value: Any) -> list[str]:
    return [token.lower() for token in _PROTECTED_TOKEN_RE.findall(as_text(value).translate(_LIGATURES))]


def _unique_labels(matrix: list[list[Any]], header_row_count: int, column_count: int) -> list[str]:
    labels: list[str] = []
    used: set[str] = set()
    for column_index in range(column_count):
        parts: list[str] = []
        for row in matrix[:header_row_count]:
            value = normalise_line(row[column_index]) if column_index < len(row) else ""
            if value and value not in parts:
                parts.append(value)
        label = " / ".join(parts) or f"column_{column_index + 1}"
        if label in used:
            label = f"{label} / column_{column_index + 1}"
        used.add(label)
        labels.append(label)
    return labels


def build_table_projection(
    matrix: list[list[Any]],
    *,
    table_id: str,
    location: str,
    header_row_count: int = 1,
) -> tuple[str, list[str], list[str]]:
    """Create a searchable projection without changing the source matrix."""

    warnings: list[str] = []
    if not matrix:
        return "", [], ["empty_table_matrix"]

    column_count = max((len(row) for row in matrix), default=0)
    if any(len(row) != column_count for row in matrix):
        warnings.append("Rows had unequal lengths; projection padded missing cells as empty values.")
    header_row_count = max(0, min(header_row_count, len(matrix)))
    labels = _unique_labels(matrix, header_row_count, column_count)
    projection_rows = [
        list(row) + [None] * (column_count - len(row))
        for row in matrix[header_row_count:]
    ]

    lines: list[str] = []
    for row_number, row in enumerate(projection_rows, start=header_row_count + 1):
        pairs = []
        for column_index, label in enumerate(labels):
            value = normalise_line(row[column_index])
            pairs.append(f"{label}={value or '空值'}")
        lines.append(
            f"table_id={table_id}；location={location}；row_number={row_number}；"
            + "；".join(pairs)
        )

    if not lines:
        warnings.append("Table has headers but no body rows; headers remain in raw_matrix.")
    return "\n".join(lines), labels, warnings


def matrix_from_excel_content(raw_content: dict[str, Any]) -> list[list[Any]]:
    headers = list(raw_content.get("headers") or [])
    rows = [list(row) for row in raw_content.get("rows", [])]
    return [headers, *rows] if headers or rows else []


def cloned_matrix(rows: Any) -> list[list[Any]]:
    return deepcopy([list(row) for row in (rows or [])])
