from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from .api.errors import register_error_handlers
from .api.internal import router as internal_router
from .api.middleware import RequestContextMiddleware, configure_logging
from .api.v1.router import router as v1_router
from .database import database_is_ready
from .settings import get_settings

settings = get_settings()
configure_logging()

app = FastAPI(
    title="Nigeria Forest Monitor API",
    version=settings.api_version,
    description="Private operational API for the forest monitoring platform.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-CSRF-Token",
        "X-Request-ID",
        "X-API-Key",
    ],
)
app.add_middleware(RequestContextMiddleware)
register_error_handlers(app)
app.include_router(v1_router)
app.include_router(internal_router)


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready() -> dict[str, str]:
    try:
        await database_is_ready()
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from error
    return {"status": "ready"}


@app.get("/api/v1/system/info", tags=["system"])
async def system_info() -> dict[str, str]:
    return {
        "service": "nigeria-forest-monitor-api",
        "version": settings.api_version,
        "environment": settings.environment,
    }
