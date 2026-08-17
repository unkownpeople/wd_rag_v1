from __future__ import annotations

"""DeepSeek OpenAI-compatible 客户端；密钥只从 Settings 的 SecretStr 读取。"""

import json
import re
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .settings import Settings


class TextGenerator(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> str:
        ...


class LLMConfigurationError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


PlannerLabel = Annotated[str, Field(max_length=160)]
PlannerQuery = Annotated[str, Field(min_length=1, max_length=240)]


class PlannerEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=160)
    metrics: list[PlannerLabel] = Field(default_factory=list, max_length=12)
    relation: str = Field(default="", max_length=120)
    group_by: list[PlannerLabel] = Field(default_factory=list, max_length=8)


class PlannerRetrievalTaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: PlannerEvidenceModel
    queries: list[PlannerQuery] = Field(min_length=1, max_length=4)
    statement_scope: str = Field(default="", max_length=120)


class PlannerOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(default="", max_length=240)
    entities: list[str] = Field(default_factory=list, max_length=16)
    time_scope: str = Field(default="", max_length=120)
    retrieval_tasks: list[PlannerRetrievalTaskModel] = Field(min_length=1, max_length=12)
    needs_calculation: bool = False
    clarification_needed: bool = False


def _usage_value(usage: Any, name: str) -> int | None:
    if usage is None:
        return None
    value = getattr(usage, name, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(name)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def extract_usage(usage: Any) -> dict[str, Any]:
    """保留 DeepSeek usage 中与提示词缓存相关的可审计字段。"""

    fields = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    )
    result = {field: value for field in fields if (value := _usage_value(usage, field)) is not None}
    hit = result.get("prompt_cache_hit_tokens")
    miss = result.get("prompt_cache_miss_tokens")
    if hit is not None or miss is not None:
        hit = int(hit or 0)
        miss = int(miss or 0)
        result["prompt_cache_total_tokens"] = hit + miss
        result["prompt_cache_hit_ratio"] = round(hit / max(hit + miss, 1), 4)
    return result


def _request_error_detail(exc: Exception) -> str:
    parts = [type(exc).__name__]
    status_code = getattr(exc, "status_code", None)
    error_code = getattr(exc, "code", None)
    if status_code is not None:
        parts.append(f"status={status_code}")
    if error_code not in (None, ""):
        parts.append(f"code={error_code}")
    return " ".join(parts)


class DeepSeekGenerator:
    def __init__(self, settings: Settings, *, api_key: Any | None = None) -> None:
        if not settings.llm_enabled or settings.llm_provider == "none":
            raise LLMConfigurationError("LLM 当前未启用")
        if settings.llm_provider != "deepseek":
            raise LLMConfigurationError(f"不支持的 LLM provider：{settings.llm_provider}")
        self.api_key = api_key or settings.answer_api_key()
        if not settings._has_key(self.api_key):
            raise LLMConfigurationError("未配置回答角色的 DeepSeek Key，请填写项目目录下的 .env")
        self.settings = settings
        self._client: Any | None = None
        self.last_usage: dict[str, Any] = {}
        self.last_finish_reason: str | None = None

    def _client_instance(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - environment guard
                raise LLMConfigurationError(
                    "缺少 openai 依赖，请在 .venv_rag 中安装 requirements-rag.txt"
                ) from exc
            self._client = OpenAI(
                api_key=self.api_key.get_secret_value(),
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.deepseek_timeout_seconds,
            )
        return self._client

    def generate(self, messages: list[dict[str, str]]) -> str:
        try:
            response = self._client_instance().chat.completions.create(
                model=self.settings.deepseek_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.llm_max_tokens,
                extra_body={"thinking": {"type": self.settings.deepseek_thinking_mode}},
            )
            self.last_usage = extract_usage(getattr(response, "usage", None))
            choice = response.choices[0]
            self.last_finish_reason = str(getattr(choice, "finish_reason", "") or "") or None
            content = choice.message.content or ""
            if not content.strip():
                raise LLMRequestError("DeepSeek 返回空内容")
            return content.strip()
        except LLMRequestError:
            raise
        except Exception as exc:  # pragma: no cover - provider-specific network error
            raise LLMRequestError(f"DeepSeek 请求失败：{_request_error_detail(exc)}") from exc


class DeepSeekPlanner:
    """输出通用检索计划，不生成最终答案或数据库表达式。"""

    def __init__(self, settings: Settings, *, api_key: Any | None = None) -> None:
        if not settings.llm_enabled or settings.llm_provider == "none":
            raise LLMConfigurationError("LLM 当前未启用")
        if settings.llm_provider != "deepseek":
            raise LLMConfigurationError(f"不支持的 LLM provider：{settings.llm_provider}")
        self.api_key = api_key or settings.planner_api_key()
        if not settings._has_key(self.api_key):
            raise LLMConfigurationError("未配置查询规划角色的 DeepSeek Key，请填写项目目录下的 .env")
        self.settings = settings
        self._client: Any | None = None
        self.last_usage: dict[str, Any] = {}
        self.last_finish_reason: str | None = None

    def _client_instance(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - environment guard
                raise LLMConfigurationError(
                    "缺少 openai 依赖，请在 .venv_rag 中安装 requirements-rag.txt"
                ) from exc
            self._client = OpenAI(
                api_key=self.api_key.get_secret_value(),
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.deepseek_timeout_seconds,
            )
        return self._client

    @staticmethod
    def _parse_plan(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not match:
                raise LLMRequestError("查询规划结果不是 JSON")
            try:
                value = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise LLMRequestError("查询规划结果 JSON 无法解析") from exc
        if not isinstance(value, dict):
            raise LLMRequestError("查询规划结果不是对象")

        serialized = json.dumps(value, ensure_ascii=False).casefold()
        forbidden = ("qdrant", "sql", "database_path", "collection", "api_key")
        if any(token in serialized for token in forbidden):
            raise LLMRequestError("查询规划结果包含禁止的数据库或密钥字段")

        try:
            parsed = PlannerOutputModel.model_validate(value)
        except Exception as exc:
            raise LLMRequestError("查询规划结果未通过 Pydantic 契约校验") from exc
        return parsed.model_dump()

    def plan(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        try:
            response = self._client_instance().chat.completions.create(
                model=self.settings.deepseek_planner_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.planner_max_tokens,
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": self.settings.deepseek_thinking_mode}},
            )
            self.last_usage = extract_usage(getattr(response, "usage", None))
            choice = response.choices[0]
            self.last_finish_reason = str(getattr(choice, "finish_reason", "") or "") or None
            content = choice.message.content or ""
            if not content.strip():
                raise LLMRequestError("DeepSeek 查询规划返回空内容")
            return self._parse_plan(content)
        except LLMRequestError:
            raise
        except Exception as exc:  # pragma: no cover - provider-specific network error
            raise LLMRequestError(
                f"DeepSeek 查询规划请求失败：{_request_error_detail(exc)}"
            ) from exc
