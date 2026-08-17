from __future__ import annotations

"""切分阶段使用的轻量数据模型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpan:
    """一个切分单元在清洗源记录中的字符和 token 范围。"""

    record_id: str
    record_index: int
    text: str
    char_start: int
    char_end: int
    token_start: int
    token_end: int
    section_path: str
    unit_type: str
    paragraph_index: int
