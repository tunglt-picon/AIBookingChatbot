import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

# Ensure logs are readable before first request (uvicorn may start workers later)
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────
    configure_logging()
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    await init_db()

    # Pre-warm LangGraph graph (loads LLM clients, builds graph)
    from app.agents.graph import get_graph  # noqa: F401
    get_graph()

    yield
    # ── Shutdown ──────────────────────────────────


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ── Global exception handler ──────────────────────
# Must be registered BEFORE CORSMiddleware so the response flows through it
# and gets CORS headers attached. Without this, Starlette's ServerErrorMiddleware
# (which sits OUTSIDE CORSMiddleware) returns a plain-text 500 with no CORS
# headers, and the browser misreports the error as a CORS failure.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.method} {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again."},
    )


# ── CORS ──────────────────────────────────────────
# Auth is JWT Bearer (Authorization header), NOT cookies →
# allow_credentials must be False when allow_origins=["*"].
# For cookie-based auth in future, switch to explicit origin list + True.
_cors_origins = settings.CORS_ORIGINS
_allow_credentials = "*" not in _cors_origins  # True only when origins are explicit

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],  # needed for SSE / streaming responses
)

# ── Routers ───────────────────────────────────────
from app.api.v1 import auth, chat, schedule  # noqa: E402

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat"])
app.include_router(schedule.router, prefix="/api/v1/schedule", tags=["Schedule"])

# ── Serve uploaded images ─────────────────────────
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


@app.get("/api/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/api/health/ollama", tags=["Health"])
async def health_ollama():
    """
    Quick probe of the local Ollama daemon (tags + whether configured models exist).
    Use this when chat hangs after `classify_intent LLM invoke start`.
    """
    import httpx

    if settings.LLM_PROVIDER.lower() != "ollama":
        return {
            "probe": "skipped",
            "reason": "LLM_PROVIDER is not ollama",
            "llm_provider": settings.LLM_PROVIDER,
        }

    base = settings.OLLAMA_BASE_URL.rstrip("/")
    url = f"{base}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            body = r.json()
    except httpx.ConnectError:
        return {
            "ok": False,
            "error": "connect_failed",
            "url": url,
            "hint": "Start Ollama: `ollama serve` (or the desktop app). Then retry.",
        }
    except httpx.HTTPStatusError as e:
        return {
            "ok": False,
            "error": "http_error",
            "status_code": e.response.status_code,
            "url": url,
        }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "detail": str(e), "url": url}

    names = [m.get("name", "") for m in body.get("models", [])]

    def _has(want: str) -> bool:
        return any(want == n or n.startswith(f"{want}:") for n in names)

    root = settings.ROOT_MODEL_NAME
    spec = settings.SPECIALIST_MODEL_NAME
    pull = []
    if not _has(root):
        pull.append(f"ollama pull {root}")
    if not _has(spec):
        pull.append(f"ollama pull {spec}")
    return {
        "ok": True,
        "ollama_url": base,
        "model_count": len(names),
        "models": names,
        "root_model": root,
        "root_model_available": _has(root),
        "specialist_model": spec,
        "specialist_model_available": _has(spec),
        "pull_suggestions": pull,
    }
