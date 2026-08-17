from __future__ import annotations

"""Token 化适配层：默认使用 BGE-M3 的真实 fast Tokenizer。"""

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol


TOKEN_RE = re.compile(
    r"[\u4e00-\u9fff]|[^\W\d_]+(?:['’\-][^\W\d_]+)*|\d+(?:[.,]\d+)*|[^\w\s]",
    re.UNICODE,
)


class TokenizerAdapter(Protocol):
    """切分阶段需要的最小 Tokenizer 契约。"""

    name: str

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        """返回 Token 在原文中的字符范围。"""


@dataclass(frozen=True)
class RegexTokenizer:
    """历史基线 Token 估算器，仅用于对照，不作为默认流程。"""

    name: str = "heuristic-regex-v1"

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return [(match.start(), match.end()) for match in TOKEN_RE.finditer(text)]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BGE_M3_MODEL_ID = "BAAI/bge-m3"
DEFAULT_BGE_M3_DIR = PROJECT_ROOT / "models" / "bge-m3"
DEFAULT_HF_CACHE_DIR = PROJECT_ROOT / ".hf_cache"


class HuggingFaceTokenizer:
    """基于 Hugging Face fast tokenizer 的真实 Token 边界适配器。

    要求 tokenizer 返回 offset mapping，保证 Token 数变化后仍能把来源范围
    映射回清洗原文。模型缓存默认位于项目 F 盘目录，不写入 C 盘用户缓存。
    """

    def __init__(
        self,
        model_name: str,
        *,
        revision: str | None = None,
        cache_dir: str | Path | None = DEFAULT_HF_CACHE_DIR,
        local_files_only: bool = False,
        display_name: str | None = None,
    ) -> None:
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "使用 HuggingFaceTokenizer 需要在项目虚拟环境安装 transformers。"
            ) from exc

        self.name = f"huggingface:{display_name or model_name}"
        self.model_name = model_name
        self.revision = revision
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            revision=revision,
            use_fast=True,
            cache_dir=str(cache_dir) if cache_dir else None,
            local_files_only=local_files_only,
        )
        if not getattr(self._tokenizer, "is_fast", False):
            raise ValueError("必须使用 fast tokenizer，才能返回来源字符 offset mapping。")

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        encoded = self._tokenizer(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            truncation=False,
        )
        offsets = encoded.get("offset_mapping")
        if offsets is None:
            raise ValueError("真实 Tokenizer 没有返回 offset mapping，无法追踪来源范围。")
        spans: list[tuple[int, int]] = []
        for start, end in offsets:
            start = int(start)
            end = int(end)
            if end > start and text[start:end].strip():
                spans.append((start, end))
        return spans


@lru_cache(maxsize=1)
def get_default_tokenizer() -> HuggingFaceTokenizer:
    """加载已经准备好的 BGE-M3 Tokenizer，不在切块时隐式下载模型。"""

    configured_path = os.environ.get("RAG_BGE_M3_TOKENIZER_PATH")
    model_path = Path(configured_path) if configured_path else DEFAULT_BGE_M3_DIR
    if not model_path.is_dir():
        raise RuntimeError(
            "未找到 BGE-M3 Tokenizer。请先运行 "
            ".\\.venv_rag\\Scripts\\python.exe "
            "scripts\\prepare_bge_m3_tokenizer.py"
        )
    return HuggingFaceTokenizer(
        str(model_path),
        cache_dir=DEFAULT_HF_CACHE_DIR,
        local_files_only=True,
        display_name=BGE_M3_MODEL_ID,
    )


class _LazyDefaultTokenizer:
    """延迟加载默认 Tokenizer，避免 import 时触发模型读取。"""

    name = f"huggingface:{BGE_M3_MODEL_ID}"

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return get_default_tokenizer().token_spans(text)


# 旧基线（已停用，仅保留作离线对照）：
# DEFAULT_TOKENIZER = RegexTokenizer()
# 默认流程必须使用 BGE-M3；此惰性门面保持旧调用方兼容且不覆盖基线输出。
DEFAULT_TOKENIZER = _LazyDefaultTokenizer()
