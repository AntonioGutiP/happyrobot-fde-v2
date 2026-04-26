from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, and_
from typing import Optional
from datetime import datetime, timedelta
from database import get_db
from models import Load, CallRecord
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
    Search available loads ranked by highest loadboard rate first.

    Ranking by rate maximizes margin opportunity — the agent pitches
    the most profitable load first, with fallbacks for the carrier.
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

    # Rank by highest rate first (best margin opportunity)
    query = query.order_by(Load.loadboard_rate.desc()).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{load_id}/available")
async def check_availability(load_id: str, db: AsyncSession = Depends(get_db)):
    """
    Quick availability check before pitching a load.

    Prevents the agent from pitching a load that was booked
    by another call seconds ago. Returns a simple yes/no
    with the current status.
    """
    result = await db.execute(select(Load).where(Load.load_id == load_id))
    load = result.scalar_one_or_none()

    if not load:
        return {
            "load_id": load_id,
            "available": False,
            "reason": "Load not found",
        }

    return {
        "load_id": load_id,
        "available": load.status == "available",
        "status": load.status,
        "reason": "Load is available" if load.status == "available" else f"Load is {load.status}",
    }


@router.get("/{load_id}/market-context")
async def get_market_context(load_id: str, db: AsyncSession = Depends(get_db)):
    """
    Returns market intelligence for a specific load.

    Used by the agent to adjust negotiation strategy dynamically:
    - High decline count → be more flexible on price
    - Just posted, no declines → hold firm
    - Multiple pitches with no booking → consider lowering floor

    This enables dynamic pricing instead of static floor rules.
    """
    # Verify load exists
    load_result = await db.execute(select(Load).where(Load.load_id == load_id))
    load = load_result.scalar_one_or_none()
    if not load:
        raise HTTPException(status_code=404, detail=f"Load {load_id} not found")

    # Count how many times this load has been pitched
    pitch_count_q = await db.execute(
        select(func.count(CallRecord.call_id)).where(CallRecord.load_id == load_id)
    )
    pitch_count = pitch_count_q.scalar() or 0

    # Count declines and rejections for this load
    decline_count_q = await db.execute(
        select(func.count(CallRecord.call_id)).where(
            and_(
                CallRecord.load_id == load_id,
                CallRecord.outcome.in_(["carrier_declined", "rejected"]),
            )
        )
    )
    decline_count = decline_count_q.scalar() or 0

    # Days on market
    days_on_market = (datetime.utcnow() - load.pickup_datetime).days
    if days_on_market < 0:
        days_on_market = 0

    # Average offered price for declined calls on this load
    avg_declined_q = await db.execute(
        select(func.avg(CallRecord.agreed_price)).where(
            and_(
                CallRecord.load_id == load_id,
                CallRecord.outcome == "rejected",
                CallRecord.agreed_price.isnot(None),
            )
        )
    )
    avg_declined_price = avg_declined_q.scalar()

    # Determine pricing recommendation
    if decline_count >= 3:
        pricing_strategy = "flexible"
        recommendation = "This load has been declined multiple times. Consider lowering the floor price to close a deal."
    elif decline_count >= 1 and pitch_count >= 2:
        pricing_strategy = "moderate"
        recommendation = "Some carrier interest but no booking yet. Small concessions may help close."
    else:
        pricing_strategy = "firm"
        recommendation = "Fresh load with limited exposure. Hold firm on pricing."

    return {
        "load_id": load_id,
        "loadboard_rate": load.loadboard_rate,
        "pitch_count": pitch_count,
        "decline_count": decline_count,
        "book_count": pitch_count - decline_count,
        "days_on_market": days_on_market,
        "avg_declined_price": round(avg_declined_price, 2) if avg_declined_price else None,
        "pricing_strategy": pricing_strategy,
        "recommendation": recommendation,
    }


@router.get("/{load_id}", response_model=LoadOut)
async def get_load(load_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific load by ID."""
    result = await db.execute(select(Load).where(Load.load_id == load_id))
    load = result.scalar_one_or_none()

    if not load:
        raise HTTPException(status_code=404, detail=f"Load {load_id} not found")

    return load


@router.post("/reset-all")
async def reset_all_loads(db: AsyncSession = Depends(get_db)):
    """
    Reset all loads: status back to 'available' AND refresh pickup/delivery
    dates to be relative to today. Call before every demo.
    """
    from sqlalchemy import update

    # Reset status
    await db.execute(update(Load).values(status="available"))

    # Refresh dates — shift all dates so they're relative to today
    result = await db.execute(select(Load))
    loads = result.scalars().all()

    now = datetime.utcnow().replace(hour=8, minute=0, second=0, microsecond=0)

    for i, load in enumerate(loads):
        # Spread loads across the next 5 days
        day_offset = i % 5
        pickup_hour = 4 + (i * 2) % 16  # vary pickup times

        old_duration = (load.delivery_datetime - load.pickup_datetime) if load.delivery_datetime and load.pickup_datetime else timedelta(hours=24)

        load.pickup_datetime = now + timedelta(days=day_offset, hours=pickup_hour)
        load.delivery_datetime = load.pickup_datetime + old_duration

    await db.commit()

    count_q = await db.execute(select(func.count(Load.load_id)))
    count = count_q.scalar()

    return {
        "message": f"All {count} loads reset: status=available, dates refreshed to today+5 days",
        "base_date": now.strftime("%Y-%m-%d"),
    }
