from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, and_
from typing import Optional
from datetime import datetime, timedelta
from database import get_db
from models import CallRecord, Load, CallOutcome, CallSentiment
from schemas import CallCreate, CallOut, CallStats

router = APIRouter(prefix="/calls", tags=["Calls"])


@router.post("", response_model=CallOut, status_code=201)
async def log_call(call: CallCreate, db: AsyncSession = Depends(get_db)):
    """
    Log a call record. Used by HappyRobot workflow after each call ends.

    The post-call workflow classifies outcome + sentiment, extracts
    data, then POSTs here. This is the integration link between
    the agent and the dashboard.
    """
    # If a load was booked, update its status
    if call.outcome == CallOutcome.booked and call.load_id:
        result = await db.execute(select(Load).where(Load.load_id == call.load_id))
        load = result.scalar_one_or_none()
        if load:
            load.status = "booked"

    record = CallRecord(**call.model_dump())
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return record


@router.get("", response_model=list[CallOut])
async def list_calls(
    outcome: Optional[str] = Query(None),
    sentiment: Optional[str] = Query(None),
    carrier_mc: Optional[str] = Query(None),
    load_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365, description="Look back N days"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List call records with filters. Used by dashboard."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = select(CallRecord).where(CallRecord.created_at >= cutoff)

    if outcome:
        query = query.where(CallRecord.outcome == outcome)
    if sentiment:
        query = query.where(CallRecord.sentiment == sentiment)
    if carrier_mc:
        query = query.where(CallRecord.carrier_mc == carrier_mc)
    if load_id:
        query = query.where(CallRecord.load_id == load_id)

    query = query.order_by(CallRecord.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats", response_model=CallStats)
async def call_stats(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """
    Aggregated call metrics for the dashboard.

    Returns the 5 decision categories:
    1. Performance — conversion funnel
    2. Revenue — booked revenue, margins
    3. Top lanes — origin→destination demand
    4. Rejection analysis — why deals fail
    5. Sentiment breakdown
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # --- Total calls ---
    total_q = await db.execute(
        select(func.count(CallRecord.call_id)).where(CallRecord.created_at >= cutoff)
    )
    total_calls = total_q.scalar() or 0

    if total_calls == 0:
        return CallStats()

    # --- By outcome ---
    outcome_q = await db.execute(
        select(CallRecord.outcome, func.count(CallRecord.call_id))
        .where(CallRecord.created_at >= cutoff)
        .group_by(CallRecord.outcome)
    )
    by_outcome = {row[0]: row[1] for row in outcome_q.all()}

    # --- By sentiment ---
    sentiment_q = await db.execute(
        select(CallRecord.sentiment, func.count(CallRecord.call_id))
        .where(CallRecord.created_at >= cutoff)
        .group_by(CallRecord.sentiment)
    )
    by_sentiment = {row[0]: row[1] for row in sentiment_q.all()}

    # --- Conversion rate ---
    booked = by_outcome.get("booked", 0)
    conversion_rate = round(booked / total_calls * 100, 1) if total_calls else 0.0

    # --- Avg negotiation rounds ---
    avg_rounds_q = await db.execute(
        select(func.avg(CallRecord.num_rounds)).where(
            and_(CallRecord.created_at >= cutoff, CallRecord.num_rounds > 0)
        )
    )
    avg_rounds = avg_rounds_q.scalar()
    avg_negotiation_rounds = round(float(avg_rounds), 1) if avg_rounds else 0.0

    # --- Revenue & margin ---
    revenue_q = await db.execute(
        select(
            func.sum(CallRecord.agreed_price),
            func.avg(
                case(
                    (
                        and_(
                            CallRecord.initial_rate.isnot(None),
                            CallRecord.agreed_price.isnot(None),
                            CallRecord.initial_rate > 0,
                        ),
                        (CallRecord.initial_rate - CallRecord.agreed_price) / CallRecord.initial_rate * 100,
                    ),
                    else_=None,
                )
            ),
        ).where(
            and_(CallRecord.created_at >= cutoff, CallRecord.outcome == "booked")
        )
    )
    rev_row = revenue_q.one()
    total_booked_revenue = float(rev_row[0]) if rev_row[0] else 0.0
    avg_margin_pct = round(float(rev_row[1]), 1) if rev_row[1] else None

    # --- Avg call duration ---
    dur_q = await db.execute(
        select(func.avg(CallRecord.call_duration)).where(
            and_(CallRecord.created_at >= cutoff, CallRecord.call_duration.isnot(None))
        )
    )
    avg_dur = dur_q.scalar()
    avg_call_duration = round(float(avg_dur), 1) if avg_dur else None

    # --- Top lanes (origin→destination from loads linked to calls) ---
    lane_q = await db.execute(
        select(
            Load.origin,
            Load.destination,
            func.count(CallRecord.call_id).label("call_count"),
            func.sum(case((CallRecord.outcome == "booked", 1), else_=0)).label("booked_count"),
        )
        .join(Load, CallRecord.load_id == Load.load_id)
        .where(CallRecord.created_at >= cutoff)
        .group_by(Load.origin, Load.destination)
        .order_by(func.count(CallRecord.call_id).desc())
        .limit(10)
    )
    top_lanes = [
        {
            "origin": row[0],
            "destination": row[1],
            "total_calls": row[2],
            "booked": row[3],
            "conversion_pct": round(row[3] / row[2] * 100, 1) if row[2] else 0,
        }
        for row in lane_q.all()
    ]

    # --- Rejection analysis ---
    rejection_q = await db.execute(
        select(CallRecord.outcome, func.count(CallRecord.call_id))
        .where(
            and_(
                CallRecord.created_at >= cutoff,
                CallRecord.outcome.in_(["rejected", "carrier_declined", "no_match"]),
            )
        )
        .group_by(CallRecord.outcome)
    )
    rejection_reasons = [
        {"reason": row[0], "count": row[1]} for row in rejection_q.all()
    ]

    return CallStats(
        total_calls=total_calls,
        by_outcome=by_outcome,
        by_sentiment=by_sentiment,
        conversion_rate=conversion_rate,
        avg_negotiation_rounds=avg_negotiation_rounds,
        avg_margin_pct=avg_margin_pct,
        total_booked_revenue=total_booked_revenue,
        avg_call_duration=avg_call_duration,
        top_lanes=top_lanes,
        rejection_reasons=rejection_reasons,
    )
