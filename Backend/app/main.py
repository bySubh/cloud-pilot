from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_health import router as health_router
from app.api.routes_runs import router as runs_router
from app.config import ensure_directories, get_settings
from app.persistence import init_db
from app.rag.ingest import ingest_infra_templates
from app.utils.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(
    title="Cloud Pilot API",
    description="AI-powered GCP Infrastructure-as-Code generation, validation and self-healing.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, tags=["health"])
app.include_router(runs_router, tags=["runs"])


@app.on_event("startup")
def on_startup() -> None:
    ensure_directories()
    init_db()
    ingest_infra_templates()
