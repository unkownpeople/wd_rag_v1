from __future__ import annotations

"""项目级配置：统一从 F 盘项目根目录的 .env 读取。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """RAG API、向量检索和 DeepSeek 生成层的全部运行配置。"""

    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_name: str = "Annual Report RAG API"
    app_env: Literal["local", "test", "production"] = "local"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    qdrant_path: Path = ROOT / "xianlian"
    qdrant_collection: str = "annual_report_v1_single_company"
    chunk_dir: Path = ROOT / "data" / "processed" / "annual_reports" / "v1_single_company_chunks"

    embedding_model_path: Path = ROOT / "models" / "bge-m3-embedding" / "model_quantized.onnx"
    embedding_tokenizer_path: Path = ROOT / "models" / "bge-m3"
    embedding_max_length: int = 8192

    retrieval_mode: Literal["dense", "bm25", "hybrid"] = "hybrid"
    retrieval_top_k: int = 5
    retrieval_max_context_chars: int = 64000
    query_audit_dir: Path = ROOT / 'data' / 'query_logs'
    semantic_rerank_enabled: bool = True
    coverage_probe_mode: Literal['observe', 'repair'] = 'observe'
    semantic_rerank_candidates: int = Field(default=16, ge=2, le=20)
    semantic_rerank_trigger: int = Field(default=8, ge=1, le=20)
    semantic_rerank_timeout_seconds: float = Field(default=8.0, gt=0, le=30)
    no_answer_dense_threshold: float = 0.50

    llm_enabled: bool = True
    llm_provider: Literal["deepseek", "none"] = "deepseek"
    planner_enabled: bool = True
    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "RAG_DEEPSEEK_API_KEY"),
    )
    deepseek_planner_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_PLANNER_API_KEY"),
    )
    deepseek_answer_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_ANSWER_API_KEY"),
    )
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("DEEPSEEK_ANSWER_MODEL", "DEEPSEEK_MODEL"),
    )
    deepseek_planner_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("DEEPSEEK_PLANNER_MODEL", "DEEPSEEK_MODEL"),
    )
    deepseek_thinking_mode: Literal["disabled", "enabled"] = "disabled"
    deepseek_timeout_seconds: float = 60.0
    llm_temperature: float = 0.0
    llm_max_tokens: int = 8000

    planner_max_tokens: int = 4000

    @staticmethod
    def _has_key(value: SecretStr | None) -> bool:
        return bool(value and value.get_secret_value().strip())

    def answer_api_key(self) -> SecretStr | None:
        return self.deepseek_answer_api_key or self.deepseek_api_key

    def planner_api_key(self) -> SecretStr | None:
        return self.deepseek_planner_api_key or self.deepseek_api_key

    def has_answer_key(self) -> bool:
        return self._has_key(self.answer_api_key())

    def has_planner_key(self) -> bool:
        return self._has_key(self.planner_api_key())

    def has_deepseek_key(self) -> bool:
        """兼容旧健康检查调用；现在表示回答角色可用。"""

        return self.has_answer_key()

    def validate_local_paths(self) -> list[str]:
        """返回缺失路径，不在健康检查中暴露密钥或完整异常堆栈。"""

        required = {
            "qdrant_path": self.qdrant_path,
            "chunk_dir": self.chunk_dir,
            "embedding_model_path": self.embedding_model_path,
            "embedding_tokenizer_path": self.embedding_tokenizer_path,
        }
        return [name for name, path in required.items() if not path.exists()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
