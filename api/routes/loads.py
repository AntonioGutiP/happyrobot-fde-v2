from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from database import get_db
from models import Load
from schemas import LoadOut

router = APIRouter(prefix="/loads", tags=["Loads"])


@router.get("/search", response_model=list[LoadOut])
async def search_loads(
    origin: Optional[str] = Query(None, description="Filter by origin (partial match)"),
    destination: Optional[str] = Query(None, description="Filter by destination (partial match)"),
    equipment_type: Optional[str] = Query(None, description="Dry Van, Reefer, Flatbed, etc."),
    min_rate: Optional[float] = Query(None, description="Minimum loadboard rate"),
    max_rate: Optional[float] = Query(None, description="Maximum loadboard rate"),
    status: Optional[str] = Query("available", description="Load status filter"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Search available loads. Used by HappyRobot agent during calls
    to find matching loads for a carrier.

    Supports partial matching on origin/destination (case-insensitive).
    """
    query = select(Load)

    if origin:
        query = query.where(Load.origin.ilike(f"%{origin}%"))
    if destination:
        query = query.where(Load.destination.ilike(f"%{destination}%"))
    if equipment_type:
        query = query.where(Load.equipment_type.ilike(f"%{equipment_type}%"))
    if min_rate is not None:
        query = query.where(Load.loadboard_rate >= min_rate)
    if max_rate is not None:
        query = query.where(Load.loadboard_rate <= max_rate)
    if status:
        query = query.where(Load.status == status)

    query = query.order_by(Load.pickup_datetime).limit(limit)

    result = await db.execute(query)
    loads = result.scalars().all()

    return loads


@router.get("/{load_id}", response_model=LoadOut)
async def get_load(load_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific load by ID."""
    result = await db.execute(select(Load).where(Load.load_id == load_id))
    load = result.scalar_one_or_none()

    if not load:
        raise HTTPException(status_code=404, detail=f"Load {load_id} not found")

    return load
