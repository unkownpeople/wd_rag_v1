from __future__ import annotations

"""切分边界层：只负责识别 Token、句子和段落范围。"""

import re
from .tokenizer import DEFAULT_TOKENIZER, TOKEN_RE, TokenizerAdapter


CLOSING_CHARS = set('"\'”’»）)]】}』」')
CJK_SENTENCE_END = set("。！？")


def token_spans(
    text: str, tokenizer: TokenizerAdapter | None = None
) -> list[tuple[int, int]]:
    """识别文本 Token 的字符范围，用于长度估算和来源追踪。"""

    return (tokenizer or DEFAULT_TOKENIZER).token_spans(text)


def estimate_tokens(text: str, tokenizer: TokenizerAdapter | None = None) -> int:
    return len(token_spans(text, tokenizer))


def token_indices(
    all_tokens: list[tuple[int, int]], char_start: int, char_end: int
) -> tuple[int, int]:
    """把字符范围映射为来源 token 的半开区间。

    Fast Tokenizer 的某些标点 Token（例如 ``).``）可能跨过句子切分
    边界。按“有交集”映射会让同一个来源 Token 同时进入两个句子，形成
    块内重复。使用 Token 中点归属单一字符范围，既不丢 Token，也不重复。
    """

    if char_end <= char_start:
        return 0, 0
    start_index = 0
    while start_index < len(all_tokens):
        token_start, token_end = all_tokens[start_index]
        midpoint = (token_start + token_end) / 2
        if midpoint >= char_start:
            break
        start_index += 1
    end_index = start_index
    while end_index < len(all_tokens):
        token_start, token_end = all_tokens[end_index]
        midpoint = (token_start + token_end) / 2
        if midpoint >= char_end:
            break
        end_index += 1
    return start_index, end_index


def _is_sentence_end(text: str, index: int) -> bool:
    character = text[index]
    if character in CJK_SENTENCE_END:
        return True
    if character not in ".!?":
        return False
    previous = text[index - 1] if index else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if character == "." and previous.isdigit() and following.isdigit():
        return False
    return not following or following.isspace() or following in CLOSING_CHARS


def sentence_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """返回 [start, end) 内去除首尾空白的句子范围。"""

    spans: list[tuple[int, int]] = []
    sentence_start = start
    index = start
    while index < end:
        if _is_sentence_end(text, index):
            sentence_end = index + 1
            while sentence_end < end and text[sentence_end] in CLOSING_CHARS:
                sentence_end += 1
            while sentence_end < end and text[sentence_end].isspace():
                sentence_end += 1
            if text[sentence_start:sentence_end].strip():
                left = sentence_start
                right = sentence_end
                while left < right and text[left].isspace():
                    left += 1
                while right > left and text[right - 1].isspace():
                    right -= 1
                spans.append((left, right))
            sentence_start = sentence_end
            index = sentence_end
            continue
        index += 1

    left = sentence_start
    right = end
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    if text[left:right].strip():
        spans.append((left, right))
    return spans


def paragraph_spans(text: str) -> list[tuple[int, int]]:
    """返回保留原始字符偏移的非空段落范围。"""

    spans: list[tuple[int, int]] = []
    start = 0
    for separator in re.finditer(r"\n\s*\n", text):
        end = separator.start()
        left = start
        right = end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if text[left:right].strip():
            spans.append((left, right))
        start = separator.end()
    left = start
    right = len(text)
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    if text[left:right].strip():
        spans.append((left, right))
    return spans
