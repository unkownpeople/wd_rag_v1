from __future__ import annotations

"""FastAPI 入口：先提供 HTTP，不依赖独立聊天网页。"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .llm import LLMConfigurationError, LLMRequestError
from .schemas import QueryBody, RAGResponse
from .service import RAGService
from .settings import Settings, get_settings


class UTF8JSONResponse(JSONResponse):
    """明确声明 UTF-8，兼容 Windows PowerShell 5.1 等旧 HTTP 客户端。"""

    media_type = "application/json; charset=utf-8"


def create_app(service: RAGService | None = None, settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    active_service = service

    def resolve_service() -> RAGService:
        nonlocal active_service
        if active_service is None:
            active_service = RAGService(active_settings)
        return active_service

    app = FastAPI(
        title=active_settings.app_name,
        version="0.1.0",
        default_response_class=UTF8JSONResponse,
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        missing = active_settings.validate_local_paths()
        return {
            "status": "ok" if not missing else "degraded",
            "app_env": active_settings.app_env,
            "collection": active_settings.qdrant_collection,
            "missing_paths": missing,
            "llm_provider": active_settings.llm_provider,
            "llm_configured": active_settings.has_deepseek_key(),
            "llm_answer_configured": active_settings.has_answer_key(),
            "llm_planner_configured": active_settings.has_planner_key(),
        }

    @app.post("/api/rag/query", response_model=RAGResponse)
    def query(body: QueryBody) -> RAGResponse:
        try:
            return resolve_service().query(body)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LLMConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except LLMRequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return app


app = create_app()
