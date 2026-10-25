from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (
    analytics,
    auth,
    chat,
    documents,
    errors,
    exports,
    health,
    internal,
    metrics,
    orders,
    reviews,
    security,
    telemetry,
    tickets,
)
from app.config import StorageBackend, get_settings
from app.core.log_hygiene import install_redaction
from app.core.request_metrics import Flusher
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Fail fast: invalid or missing settings stop startup with a validation error (§5.2).
    settings = get_settings()
    install_redaction(settings.log_level)  # uvicorn has configured its handlers by now (§11.2 log hygiene)
    if settings.storage_backend is StorageBackend.LOCAL:
        settings.storage_local_dir.mkdir(parents=True, exist_ok=True)
    flusher = Flusher(get_engine(), settings.metrics_flush_seconds)  # §13.1 request metrics (ADR-021)
    flusher.start()
    yield
    flusher.stop()


app = FastAPI(title="KnowledgeAI API", version="0.1.0", lifespan=lifespan)
errors.install(app)
security.install(app)
telemetry.install(app)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(orders.router)
app.include_router(tickets.router)
app.include_router(internal.router)
app.include_router(analytics.router)
app.include_router(metrics.router)
app.include_router(reviews.router)
app.include_router(exports.router)
