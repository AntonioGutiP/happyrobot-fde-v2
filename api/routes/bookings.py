"""
Booking confirmations — auto-generated when a load is booked.

This is the integration point that connects the AI agent's work
to real brokerage operations. In production, this would trigger:
- Email confirmation to carrier
- TMS booking entry
- Dispatch notification
- Accounting record
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database import get_db
from models import BookingConfirmation

router = APIRouter(prefix="/bookings", tags=["Bookings"])


@router.get("")
async def list_bookings(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all booking confirmations, most recent first."""
    result = await db.execute(
        select(BookingConfirmation)
        .order_by(BookingConfirmation.booked_at.desc())
        .limit(limit)
    )
    bookings = result.scalars().all()

    return [
        {
            "confirmation_number": b.confirmation_number,
            "load_id": b.load_id,
            "carrier_mc": b.carrier_mc,
            "carrier_name": b.carrier_name,
            "lane": f"{b.origin} → {b.destination}" if b.origin and b.destination else None,
            "agreed_rate": b.agreed_rate,
            "loadboard_rate": b.loadboard_rate,
            "rate_per_mile": round(b.agreed_rate / b.miles, 2) if b.agreed_rate and b.miles else None,
            "equipment_type": b.equipment_type,
            "pickup": b.pickup_datetime.isoformat() if b.pickup_datetime else None,
            "delivery": b.delivery_datetime.isoformat() if b.delivery_datetime else None,
            "miles": b.miles,
            "negotiation_rounds": b.negotiation_rounds,
            "cost_vs_listed": round(b.agreed_rate - b.loadboard_rate, 2) if b.agreed_rate and b.loadboard_rate else 0,
            "status": b.status,
            "booked_at": b.booked_at.isoformat() if b.booked_at else None,
        }
        for b in bookings
    ]


@router.get("/summary")
async def booking_summary(db: AsyncSession = Depends(get_db)):
    """Quick summary for dashboard header."""
    total_q = await db.execute(select(func.count(BookingConfirmation.id)))
    total = total_q.scalar() or 0

    revenue_q = await db.execute(select(func.sum(BookingConfirmation.agreed_rate)))
    revenue = float(revenue_q.scalar() or 0)

    avg_q = await db.execute(select(func.avg(BookingConfirmation.agreed_rate)))
    avg_deal = float(avg_q.scalar() or 0)

    return {
        "total_bookings": total,
        "total_revenue": round(revenue, 2),
        "avg_deal_size": round(avg_deal, 2),
    }
