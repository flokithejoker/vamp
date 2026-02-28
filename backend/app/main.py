from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config.customer_agent import assert_customer_agent_config
from app.modules.monitoring import MonitoringConversationNotFoundError
from app.modules.smart_insights import SmartInsightsGenerationError
from app.providers.elevenlabs import BackendConfigurationError, ElevenLabsApiError
from app.providers.openai import OpenAIApiError, OpenAIConfigurationError
from app.routes.feedback import router as feedback_router
from app.routes.monitoring import router as monitoring_router
from app.routes.smart_insights import router as smart_insights_router
from app.routes.statistics import router as statistics_router

load_dotenv()
assert_customer_agent_config()

app = FastAPI(title="Dormero Viktoria Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(feedback_router)
app.include_router(monitoring_router)
app.include_router(statistics_router)
app.include_router(smart_insights_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(BackendConfigurationError)
async def handle_backend_config_error(_: Request, exc: BackendConfigurationError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": str(exc)})


@app.exception_handler(OpenAIConfigurationError)
async def handle_openai_config_error(_: Request, exc: OpenAIConfigurationError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": str(exc)})


@app.exception_handler(ElevenLabsApiError)
async def handle_elevenlabs_error(_: Request, exc: ElevenLabsApiError) -> JSONResponse:
    if exc.status_code == 429:
        return JSONResponse(
            status_code=429,
            content={
                "error": "ElevenLabs rate limit reached. Please retry shortly.",
                "details": exc.details,
            },
        )

    if exc.status_code in {401, 403}:
        return JSONResponse(
            status_code=502,
            content={
                "error": "ElevenLabs authentication failed. Check ELEVENLABS_API_KEY.",
                "details": exc.details,
            },
        )

    return JSONResponse(
        status_code=502,
        content={
            "error": str(exc),
            "details": exc.details,
        },
    )


@app.exception_handler(OpenAIApiError)
async def handle_openai_error(_: Request, exc: OpenAIApiError) -> JSONResponse:
    if exc.status_code == 429:
        return JSONResponse(
            status_code=429,
            content={
                "error": "OpenAI rate limit reached. Please retry shortly.",
                "details": exc.details,
            },
        )

    if exc.status_code in {401, 403}:
        return JSONResponse(
            status_code=502,
            content={
                "error": "OpenAI authentication failed. Check OPENAI_API_KEY.",
                "details": exc.details,
            },
        )

    return JSONResponse(
        status_code=502,
        content={
            "error": str(exc),
            "details": exc.details,
        },
    )


@app.exception_handler(MonitoringConversationNotFoundError)
async def handle_monitoring_conversation_not_found(_: Request, exc: MonitoringConversationNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": str(exc)},
    )


@app.exception_handler(SmartInsightsGenerationError)
async def handle_smart_insights_generation_error(_: Request, exc: SmartInsightsGenerationError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={"error": str(exc)},
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": "Unexpected server error while processing request."},
    )
