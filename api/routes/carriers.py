from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from typing import Optional
from database import get_db
from models import CallRecord, Load
from schemas import CarrierVerification
from services.fmcsa import verify_carrier_by_mc, verify_carrier_by_dot, search_carrier_by_name

router = APIRouter(prefix="/carriers", tags=["Carriers"])


@router.get("/verify/{mc_number}", response_model=CarrierVerification)
async def verify_carrier(mc_number: str):
    """
    Verify carrier eligibility via FMCSA QCMobile API.
    Used by HappyRobot agent during calls.
    Also records call start time for duration tracking.
    """
    from routes.negotiate import record_call_start
    clean_mc = mc_number.upper().replace("MC-", "").replace("MC", "").strip().lstrip("0")
    record_call_start(clean_mc)
    record_call_start(mc_number)  # record both forms
    return await verify_carrier_by_mc(mc_number)


@router.get("/verify-dot/{dot_number}", response_model=CarrierVerification)
async def verify_carrier_dot(dot_number: str):
    """Alternative verification by DOT number."""
    return await verify_carrier_by_dot(dot_number)


@router.get("/search-name")
async def search_by_name(name: str = Query(..., min_length=2)):
    """Search FMCSA by carrier company name."""
    results = await search_carrier_by_name(name)
    if not results:
        return {"matches": [], "message": "No carriers found matching that name"}
    return {"matches": results, "count": len(results)}


@router.get("/history/{mc_number}")
async def carrier_history(mc_number: str, db: AsyncSession = Depends(get_db)):
    """
    Returns past interaction history for a carrier.

    Used by the agent for repeat caller detection:
    - If carrier has called before → personalize greeting
    - Shows past loads discussed, outcomes, preferred lanes
    - Enables "Welcome back!" experience

    Also feeds dashboard's repeat caller rate metric.
    """
    # Clean MC number
    clean_mc = mc_number.strip().lstrip("0")

    # Find all calls from this carrier (match on raw or cleaned)
    result = await db.execute(
        select(CallRecord)
        .options(selectinload(CallRecord.load))
        .where(
            (CallRecord.carrier_mc == clean_mc) |
            (CallRecord.carrier_mc == mc_number) |
            (CallRecord.carrier_mc == f"MC-{clean_mc}")
        )
        .order_by(CallRecord.created_at.desc())
        .limit(10)
    )
    calls = result.scalars().all()

    if not calls:
        return {
            "mc_number": mc_number,
            "is_repeat_caller": False,
            "total_previous_calls": 0,
            "message": "First-time caller",
            "history": [],
        }

    # Summarize history
    total_calls = len(calls)
    booked_count = sum(1 for c in calls if c.outcome == "booked")
    declined_count = sum(1 for c in calls if c.outcome in ("rejected", "carrier_declined"))
    positive_count = sum(1 for c in calls if c.sentiment == "positive")
    negative_count = sum(1 for c in calls if c.sentiment in ("negative", "hostile"))
    last_call = calls[0]

    # --- Carrier Qualification Score ---
    # Score 0-100 based on:
    #   - Booking rate (40% weight): booked / total calls
    #   - Sentiment (30% weight): positive ratio
    #   - Reliability (30% weight): repeat engagement
    booking_rate = booked_count / total_calls if total_calls > 0 else 0
    sentiment_score = (positive_count / total_calls) if total_calls > 0 else 0.5
    reliability_score = min(total_calls / 5, 1.0)  # maxes at 5 calls

    raw_score = (booking_rate * 40) + (sentiment_score * 30) + (reliability_score * 30)
    qualification_score = round(min(raw_score, 100), 0)

    # Tier assignment
    if qualification_score >= 70:
        tier = "preferred"
        tier_guidance = "High-value carrier. Prioritize their requests, offer best rates first."
    elif qualification_score >= 40:
        tier = "standard"
        tier_guidance = "Regular carrier. Standard service, normal negotiation approach."
    else:
        tier = "new"
        tier_guidance = "New or low-engagement carrier. Build the relationship, be welcoming."

    # Extract preferred lanes from past calls
    lanes = []
    for c in calls:
        if c.load:
            lanes.append(f"{c.load.origin} → {c.load.destination}")
        elif c.extracted_data and isinstance(c.extracted_data, dict):
            origin = c.extracted_data.get("wanted_origin", c.extracted_data.get("origin", ""))
            dest = c.extracted_data.get("wanted_dest", c.extracted_data.get("destination", ""))
            if origin and dest:
                lanes.append(f"{origin} → {dest}")

    history = [
        {
            "call_id": c.call_id,
            "date": c.created_at.isoformat() if c.created_at else None,
            "outcome": c.outcome,
            "load_id": c.load_id,
            "agreed_price": c.agreed_price,
            "sentiment": c.sentiment,
        }
        for c in calls[:5]
    ]

    return {
        "mc_number": mc_number,
        "is_repeat_caller": True,
        "total_previous_calls": total_calls,
        "booked_count": booked_count,
        "declined_count": declined_count,
        "carrier_name": last_call.carrier_name,
        "qualification_score": qualification_score,
        "tier": tier,
        "tier_guidance": tier_guidance,
        "last_call_date": last_call.created_at.isoformat() if last_call.created_at else None,
        "last_outcome": last_call.outcome,
        "preferred_lanes": list(set(lanes))[:5],
        "message": f"Repeat caller — {total_calls} previous calls, {booked_count} booked",
        "history": history,
    }
