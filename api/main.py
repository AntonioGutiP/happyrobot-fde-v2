"""
HappyRobot FDE Challenge — Inbound Carrier Sales API

This API serves two consumers:
  1. HappyRobot voice agent (webhook tools during calls)
     - GET /api/v1/carriers/verify/{mc}  → FMCSA verification
     - GET /api/v1/loads/search           → find matching loads
  2. Business dashboard (reads from same DB)
     - GET /api/v1/calls                  → call history
     - GET /api/v1/calls/stats            → aggregated metrics
     - POST /api/v1/calls                 → log call (agent writes)

One database. Agent writes. Dashboard reads. One system.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from database import engine, Base, async_session
from middleware import APIKeyMiddleware
from routes import health, loads, carriers, calls, preferences
from seed_data import seed_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables + seed data. Shutdown: dispose engine."""
    settings = get_settings()
    logger.info("Starting API [env=%s]", settings.environment)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")

    # Seed if empty
    async with async_session() as db:
        await seed_database(db)

    yield

    await engine.dispose()
    logger.info("API shut down")


app = FastAPI(
    title="HappyRobot Carrier Sales API",
    description="Inbound carrier sales automation — API backend for voice agent + dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

# --- Middleware ---
settings = get_settings()

app.add_middleware(APIKeyMiddleware)

origins = settings.cors_origins.split(",") if settings.cors_origins != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routes ---
app.include_router(health.router, prefix="/api/v1")
app.include_router(loads.router, prefix="/api/v1")
app.include_router(carriers.router, prefix="/api/v1")
app.include_router(calls.router, prefix="/api/v1")
app.include_router(preferences.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "service": "HappyRobot Carrier Sales API",
        "version": "1.0.0",
        "docs": "/docs",
    }
