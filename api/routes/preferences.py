from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from pydantic import BaseModel, field_validator
from database import get_db
from models import CarrierPreference

router = APIRouter(prefix="/carrier-preferences", tags=["Carrier Preferences"])


class PreferenceCreate(BaseModel):
    carrier_mc: str
    carrier_name: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    equipment_type: Optional[str] = None
    min_rate: Optional[float] = None
    notes: Optional[str] = None

    @field_validator("carrier_mc", "carrier_name", mode="before")
    @classmethod
    def coerce_str(cls, v):
        if v is None or v == "":
            return None
        return str(v)

    @field_validator("min_rate", mode="before")
    @classmethod
    def coerce_float(cls, v):
        if v is None or v == "":
            return None
        return float(v)


class PreferenceOut(BaseModel):
    id: int
    carrier_mc: str
    carrier_name: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    equipment_type: Optional[str] = None
    min_rate: Optional[float] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


@router.post("", status_code=201)
async def save_preference(pref: PreferenceCreate, db: AsyncSession = Depends(get_db)):
    """
    Save carrier lane/equipment preferences when no loads match.

    Called by the agent when search returns empty results.
    Stores what the carrier wanted so the brokerage can:
    - Source inventory for high-demand lanes
    - Proactively call back when loads become available

    Feeds dashboard's 'unmet demand' analytics.
    """
    record = CarrierPreference(**pref.model_dump())
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return {
        "id": record.id,
        "message": "Carrier preferences saved — will notify when matching loads are available",
    }


@router.get("")
async def list_preferences(
    equipment_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List all saved carrier preferences."""
    query = select(CarrierPreference)
    if equipment_type:
        query = query.where(CarrierPreference.equipment_type.ilike(f"%{equipment_type}%"))
    query = query.order_by(CarrierPreference.created_at.desc()).limit(limit)

    result = await db.execute(query)
    prefs = result.scalars().all()

    return [
        {
            "id": p.id,
            "carrier_mc": p.carrier_mc,
            "carrier_name": p.carrier_name,
            "origin": p.origin,
            "destination": p.destination,
            "equipment_type": p.equipment_type,
            "min_rate": p.min_rate,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in prefs
    ]


@router.get("/unmet-demand")
async def unmet_demand_analytics(db: AsyncSession = Depends(get_db)):
    """
    Aggregates carrier preferences into unmet demand insights.

    Shows which lanes and equipment types carriers are requesting
    but the brokerage has no inventory for. Directly actionable:
    "5 carriers wanted Flatbed Houston→OKC this week — source it."
    """
    # Top requested lanes
    lane_q = await db.execute(
        select(
            CarrierPreference.origin,
            CarrierPreference.destination,
            CarrierPreference.equipment_type,
            func.count(CarrierPreference.id).label("request_count"),
        )
        .where(CarrierPreference.origin.isnot(None))
        .group_by(
            CarrierPreference.origin,
            CarrierPreference.destination,
            CarrierPreference.equipment_type,
        )
        .order_by(func.count(CarrierPreference.id).desc())
        .limit(20)
    )

    unmet_lanes = [
        {
            "origin": row[0],
            "destination": row[1],
            "equipment_type": row[2],
            "request_count": row[3],
            "action": f"Source {row[2] or 'any'} loads from {row[0]} to {row[1]}",
        }
        for row in lane_q.all()
    ]

    # Total unmet requests
    total_q = await db.execute(select(func.count(CarrierPreference.id)))
    total = total_q.scalar() or 0

    return {
        "total_unmet_requests": total,
        "top_unmet_lanes": unmet_lanes,
        "insight": "These lanes have carrier demand but no matching loads — source inventory here for higher conversion.",
    }
