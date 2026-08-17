from __future__ import annotations

"""SEC 10-K HTML 的确定性正文/表格解析器。"""

import hashlib
from pathlib import Path
import re
from typing import Any

from lxml import html


BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _matrix(table: Any) -> list[list[str]]:
    matrix: list[list[str]] = []
    for row in table.xpath(".//tr"):
        values: list[str] = []
        for cell in row.xpath("./th|./td"):
            value = _text(cell.text_content())
            colspan = int(cell.get("colspan", "1") or "1")
            values.extend([value] * max(1, colspan))
        if values and any(values):
            matrix.append(values)
    return matrix


def _table_record(
    table: Any,
    *,
    metadata: dict[str, Any],
    ordinal: int,
    order: int,
    last_heading: str,
) -> dict[str, Any] | None:
    matrix = _matrix(table)
    if len(matrix) < 2 or max((len(row) for row in matrix), default=0) < 2:
        return None
    table_id = f"{metadata['document_id']}_table_{ordinal:04d}"
    header_rows = [matrix[0]]
    return {
        **metadata,
        "record_type": "table",
        "source_format": "html",
        "table_id": table_id,
        "table_title": last_heading,
        "raw_matrix": matrix,
        "header_rows": header_rows,
        "html_order": order,
        "source_location": f"html_table={ordinal}",
        "parser": "lxml-html-table",
    }


def parse_annual_html(source_file: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    source_file = source_file.resolve()
    if not source_file.is_file():
        raise FileNotFoundError(f"年报 HTML 不存在：{source_file}")
    root = html.fromstring(source_file.read_bytes())
    for node in root.xpath("//script|//style|//noscript"):
        node.drop_tree()

    records: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    table_ordinal = 0
    block_ordinal = 0
    last_heading = ""
    for order, node in enumerate(root.iter()):
        tag = str(node.tag).lower() if isinstance(node.tag, str) else ""
        if tag == "table":
            table_ordinal += 1
            table = _table_record(
                node,
                metadata=metadata,
                ordinal=table_ordinal,
                order=order,
                last_heading=last_heading,
            )
            if table is not None:
                records.append(table)
            continue
        if not tag or node.xpath("ancestor::table"):
            continue
        if tag in BLOCK_TAGS:
            selected = True
        elif tag in {"div", "span"}:
            selected = not node.xpath(".//p|.//h1|.//h2|.//h3|.//h4|.//h5|.//h6|.//li|.//table")
        else:
            selected = False
        if not selected or node.xpath("ancestor::p|ancestor::h1|ancestor::h2|ancestor::h3|ancestor::h4|ancestor::h5|ancestor::h6|ancestor::li"):
            continue
        value = _text(node.text_content())
        if len(value) < 2:
            continue
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
        if digest in seen_text:
            continue
        seen_text.add(digest)
        block_ordinal += 1
        role = "heading" if tag in HEADING_TAGS else "paragraph"
        if role == "heading":
            last_heading = value
        records.append(
            {
                **metadata,
                "record_type": "text",
                "source_format": "html",
                "content_role": role,
                "raw_text": value,
                "html_order": order,
                "block_ordinal": block_ordinal,
                "source_location": f"html_block={block_ordinal}",
            }
        )

    records.sort(key=lambda item: item.get("html_order", 0))
    return {
        "record_type": "document_meta",
        "source_file": str(source_file),
        "source_format": "html",
        "metadata": {**metadata, "parser": "lxml-html-annual-v1"},
        "record_count": len(records),
        "text_count": sum(record.get("record_type") == "text" for record in records),
        "table_count": sum(record.get("record_type") == "table" for record in records),
        "records": records,
    }


def write_parsed_jsonl(result: dict[str, Any], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    meta = {key: value for key, value in result.items() if key != "records"}
    with output_file.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json_dumps({**meta, "metadata": result.get("metadata", {})}) + "\n")
        for record in result["records"]:
            stream.write(json_dumps(record) + "\n")


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
