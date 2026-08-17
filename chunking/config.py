from __future__ import annotations

"""切分参数：只保存策略参数，不处理文件或业务流程。"""

from dataclasses import dataclass, field

from .tokenizer import TokenizerAdapter, get_default_tokenizer


@dataclass(frozen=True)
class ChunkConfig:
    """切分长度、重叠和表格分组的可复用配置。"""

    target_tokens: int = 400
    max_tokens: int = 600
    min_tokens: int = 80
    sentence_overlap: int = 1
    token_overlap: int = 50
    small_table_body_rows: int = 10
    # 旧基线（已停用，仅保留作对照）：
    # tokenizer: TokenizerAdapter = field(default_factory=RegexTokenizer, repr=False, compare=False)
    tokenizer: TokenizerAdapter = field(
        default_factory=get_default_tokenizer,
        repr=False,
        compare=False,
    )

    @property
    def tokenizer_name(self) -> str:
        """用于报告的稳定名称，不把 Tokenizer 对象直接序列化。"""

        return self.tokenizer.name
