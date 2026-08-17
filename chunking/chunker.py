from __future__ import annotations

"""兼容门面：旧代码仍可从 chunking.chunker 导入公共 API。"""

from .boundaries import estimate_tokens, paragraph_spans, sentence_spans, token_spans
from .config import ChunkConfig
from .identity import make_record_id
from .io import read_jsonl
from .models import SourceSpan
from .pipeline import chunk_records
from .tokenizer import HuggingFaceTokenizer, RegexTokenizer, TokenizerAdapter

__all__ = [
    "ChunkConfig",
    "SourceSpan",
    "chunk_records",
    "estimate_tokens",
    "make_record_id",
    "paragraph_spans",
    "read_jsonl",
    "sentence_spans",
    "token_spans",
    "TokenizerAdapter",
    "RegexTokenizer",
    "HuggingFaceTokenizer",
]
