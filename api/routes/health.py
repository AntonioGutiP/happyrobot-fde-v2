from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from database import get_db
from config import get_settings

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    settings = get_settings()

    # Check DB connection
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "environment": settings.environment,
        "fmcsa_configured": bool(settings.fmcsa_api_key),
    }
