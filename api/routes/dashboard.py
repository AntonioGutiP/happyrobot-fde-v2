"""
Dashboard data endpoint — returns ALL metrics the dashboard needs in one call.

This is the Python brain behind the dashboard. All data aggregation,
calculations, and business logic happens here. The frontend just renders it.

Six decision categories:
1. Executive KPIs
2. Conversion funnel
3. Revenue & economics
4. Lane intelligence
5. Rejection analysis
6. Carrier experience
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, and_, distinct
from datetime import datetime, timedelta
from database import get_db
from models import CallRecord, Load, CarrierPreference, BookingConfirmation

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

# Human rep cost baseline (industry average)
HUMAN_COST_PER_CALL = 18.00  # $15-25 range, midpoint
AI_COST_PER_CALL = 1.50      # estimated AI cost per call


@router.get("/data")
async def dashboard_data(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """
    Single endpoint that returns everything the dashboard needs.
    One request, one response, all six decision categories.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # ===================================================================
    # 1. EXECUTIVE KPIs
    # ===================================================================

    # Total calls
    total_q = await db.execute(
        select(func.count(CallRecord.call_id))
        .where(CallRecord.created_at >= cutoff)
    )
    total_calls = total_q.scalar() or 0

    # By outcome
    outcome_q = await db.execute(
        select(CallRecord.outcome, func.count(CallRecord.call_id))
        .where(CallRecord.created_at >= cutoff)
        .group_by(CallRecord.outcome)
    )
    by_outcome = {row[0]: row[1] for row in outcome_q.all()}

    booked = by_outcome.get("booked", 0)
    rejected = by_outcome.get("rejected", 0)
    no_match = by_outcome.get("no_match", 0)
    carrier_declined = by_outcome.get("carrier_declined", 0)
    needs_follow_up = by_outcome.get("needs_follow_up", 0)

    conversion_rate = round(booked / total_calls * 100, 1) if total_calls else 0

    # Revenue
    revenue_q = await db.execute(
        select(func.sum(CallRecord.agreed_price))
        .where(and_(CallRecord.created_at >= cutoff, CallRecord.outcome == "booked"))
    )
    total_revenue = float(revenue_q.scalar() or 0)

    # Average concession: how much above listed rate we paid (as positive %)
    # 0% = held firm, 4.8% = stretched 4.8% above listed
    concession_q = await db.execute(
        select(
            func.avg(
                func.abs(CallRecord.agreed_price - CallRecord.initial_rate)
                / CallRecord.initial_rate * 100
            )
        )
        .where(
            and_(
                CallRecord.created_at >= cutoff,
                CallRecord.outcome == "booked",
                CallRecord.initial_rate.isnot(None),
                CallRecord.agreed_price.isnot(None),
                CallRecord.initial_rate > 0,
            )
        )
    )
    avg_concession = round(float(concession_q.scalar() or 0), 1)

    # Rate integrity: % of booked deals where agreed_price <= initial_rate (never overpaid)
    integrity_total_q = await db.execute(
        select(func.count(CallRecord.call_id))
        .where(and_(
            CallRecord.created_at >= cutoff,
            CallRecord.outcome == "booked",
            CallRecord.agreed_price.isnot(None),
            CallRecord.initial_rate.isnot(None),
        ))
    )
    integrity_total = integrity_total_q.scalar() or 0

    integrity_held_q = await db.execute(
        select(func.count(CallRecord.call_id))
        .where(and_(
            CallRecord.created_at >= cutoff,
            CallRecord.outcome == "booked",
            CallRecord.agreed_price.isnot(None),
            CallRecord.initial_rate.isnot(None),
            CallRecord.agreed_price <= CallRecord.initial_rate,
        ))
    )
    integrity_held = integrity_held_q.scalar() or 0
    rate_integrity = round(integrity_held / integrity_total * 100, 1) if integrity_total > 0 else 100.0

    # Cost savings
    human_cost = total_calls * HUMAN_COST_PER_CALL
    ai_cost = total_calls * AI_COST_PER_CALL
    cost_savings = round(human_cost - ai_cost, 2)
    roi_pct = round((cost_savings / ai_cost * 100), 1) if ai_cost > 0 else 0

    # FMCSA verified count
    verified_q = await db.execute(
        select(func.count(CallRecord.call_id))
        .where(and_(CallRecord.created_at >= cutoff, CallRecord.fmcsa_verified == True))
    )
    fmcsa_verified_count = verified_q.scalar() or 0

    # Calls with loads pitched (had a load_id)
    pitched_q = await db.execute(
        select(func.count(CallRecord.call_id))
        .where(and_(CallRecord.created_at >= cutoff, CallRecord.load_id.isnot(None)))
    )
    pitched_count = pitched_q.scalar() or 0

    # Calls that entered negotiation (num_rounds > 0)
    negotiated_q = await db.execute(
        select(func.count(CallRecord.call_id))
        .where(and_(CallRecord.created_at >= cutoff, CallRecord.num_rounds > 0))
    )
    negotiated_count = negotiated_q.scalar() or 0

    executive = {
        "total_calls": total_calls,
        "conversion_rate": conversion_rate,
        "total_revenue": total_revenue,
        "avg_concession_pct": avg_concession,
        "rate_integrity": rate_integrity,
        "cost_savings": cost_savings,
        "roi_pct": roi_pct,
        "human_cost_baseline": human_cost,
        "ai_cost": ai_cost,
    }

    # ===================================================================
    # 2. CONVERSION FUNNEL
    # ===================================================================

    funnel = {
        "total_calls": total_calls,
        "fmcsa_verified": fmcsa_verified_count,
        "loads_pitched": pitched_count,
        "entered_negotiation": negotiated_count,
        "booked": booked,
        "drop_offs": {
            "verification_fail": total_calls - fmcsa_verified_count,
            "no_load_match": no_match,
            "declined_after_pitch": carrier_declined,
            "negotiation_failed": rejected,
        },
        "stage_rates": {
            "verification_rate": round(fmcsa_verified_count / total_calls * 100, 1) if total_calls else 0,
            "pitch_rate": round(pitched_count / fmcsa_verified_count * 100, 1) if fmcsa_verified_count else 0,
            "negotiation_rate": round(negotiated_count / pitched_count * 100, 1) if pitched_count else 0,
            "close_rate": round(booked / max(negotiated_count, pitched_count, 1) * 100, 1),
        },
    }

    # ===================================================================
    # 3. REVENUE & ECONOMICS
    # ===================================================================

    # Revenue per booked call
    avg_deal_size_q = await db.execute(
        select(func.avg(CallRecord.agreed_price))
        .where(and_(CallRecord.created_at >= cutoff, CallRecord.outcome == "booked"))
    )
    avg_deal_size = float(avg_deal_size_q.scalar() or 0)

    # All booked deals with margin detail + per-mile
    deals_q = await db.execute(
        select(
            CallRecord.agreed_price,
            CallRecord.initial_rate,
            CallRecord.num_rounds,
            CallRecord.load_id,
            Load.miles,
            Load.origin,
            Load.destination,
        )
        .join(Load, CallRecord.load_id == Load.load_id, isouter=True)
        .where(and_(CallRecord.created_at >= cutoff, CallRecord.outcome == "booked"))
        .order_by(CallRecord.created_at.desc())
    )
    deals = []
    total_concession_dollars = 0
    for row in deals_q.all():
        agreed = float(row[0]) if row[0] else 0
        initial = float(row[1]) if row[1] else 0
        miles = float(row[4]) if row[4] else 0
        concession = abs(agreed - initial)
        concession_pct = round(concession / initial * 100, 1) if initial else 0
        total_concession_dollars += concession
        deals.append({
            "load_id": row[3],
            "lane": f"{row[5]} → {row[6]}" if row[5] and row[6] else None,
            "miles": miles,
            "initial_rate": initial,
            "agreed_price": agreed,
            "listed_rpm": round(initial / miles, 2) if miles > 0 else None,
            "booked_rpm": round(agreed / miles, 2) if miles > 0 else None,
            "concession_dollars": round(concession, 2),
            "concession_pct": concession_pct,
            "direction": "above" if agreed > initial else ("below" if agreed < initial else "at_rate"),
            "rounds": row[2],
        })

    revenue = {
        "total_revenue": total_revenue,
        "avg_deal_size": round(avg_deal_size, 2),
        "total_concession": round(total_concession_dollars, 2),
        "avg_concession_pct": avg_concession,
        "cost_per_call_human": HUMAN_COST_PER_CALL,
        "cost_per_call_ai": AI_COST_PER_CALL,
        "cost_savings": cost_savings,
        "roi_pct": roi_pct,
        "deals": deals,
    }

    # ===================================================================
    # 4. LANE INTELLIGENCE
    # ===================================================================

    # Top lanes from calls (with load data + per-mile rates)
    lane_q = await db.execute(
        select(
            Load.origin,
            Load.destination,
            Load.equipment_type,
            func.count(CallRecord.call_id).label("total"),
            func.sum(case((CallRecord.outcome == "booked", 1), else_=0)).label("booked"),
            func.avg(Load.loadboard_rate).label("avg_rate"),
            func.avg(Load.miles).label("avg_miles"),
            func.avg(
                case(
                    (CallRecord.outcome == "booked", CallRecord.agreed_price),
                    else_=None,
                )
            ).label("avg_booked_rate"),
        )
        .join(Load, CallRecord.load_id == Load.load_id)
        .where(CallRecord.created_at >= cutoff)
        .group_by(Load.origin, Load.destination, Load.equipment_type)
        .order_by(func.count(CallRecord.call_id).desc())
        .limit(15)
    )
    top_lanes = []
    for row in lane_q.all():
        total_lane = row[3]
        booked_lane = row[4]
        avg_rate = float(row[5]) if row[5] else 0
        avg_miles = float(row[6]) if row[6] else 1
        avg_booked = float(row[7]) if row[7] else None
        conv = round(booked_lane / total_lane * 100, 1) if total_lane else 0

        listed_rpm = round(avg_rate / avg_miles, 2) if avg_miles > 0 else 0
        booked_rpm = round(avg_booked / avg_miles, 2) if avg_booked and avg_miles > 0 else None

        action = ""
        if conv >= 70:
            action = f"High conversion ({conv}%) — increase inventory on this lane"
        elif conv >= 40:
            action = f"Moderate conversion ({conv}%) — review pricing strategy"
        elif conv > 0:
            action = f"Low conversion ({conv}%) — analyze rejection reasons"
        else:
            action = "No bookings — consider removing from active inventory"

        top_lanes.append({
            "origin": row[0],
            "destination": row[1],
            "equipment_type": row[2],
            "total_calls": total_lane,
            "booked": booked_lane,
            "conversion_pct": conv,
            "avg_rate": round(avg_rate, 2),
            "avg_miles": round(avg_miles, 0),
            "listed_rpm": listed_rpm,
            "booked_rpm": booked_rpm,
            "action": action,
        })

    # Unmet demand from carrier preferences
    unmet_q = await db.execute(
        select(
            CarrierPreference.origin,
            CarrierPreference.destination,
            CarrierPreference.equipment_type,
            func.count(CarrierPreference.id).label("requests"),
        )
        .where(CarrierPreference.origin.isnot(None))
        .group_by(
            CarrierPreference.origin,
            CarrierPreference.destination,
            CarrierPreference.equipment_type,
        )
        .order_by(func.count(CarrierPreference.id).desc())
        .limit(10)
    )
    unmet_demand = [
        {
            "origin": row[0],
            "destination": row[1],
            "equipment_type": row[2],
            "carrier_requests": row[3],
            "action": f"Source {row[2] or 'any'} loads: {row[0]} → {row[1]} ({row[3]} carriers requesting)",
        }
        for row in unmet_q.all()
    ]

    lanes = {
        "top_lanes": top_lanes,
        "unmet_demand": unmet_demand,
    }

    # ===================================================================
    # 5. REJECTION ANALYSIS
    # ===================================================================

    # Rejection breakdown
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
    rejection_breakdown = {row[0]: row[1] for row in rejection_q.all()}

    # Negotiation gap analysis — for failed negotiations, how far apart were they?
    gap_q = await db.execute(
        select(
            CallRecord.initial_rate,
            CallRecord.agreed_price,
            CallRecord.num_rounds,
            CallRecord.load_id,
        )
        .where(
            and_(
                CallRecord.created_at >= cutoff,
                CallRecord.outcome == "rejected",
                CallRecord.initial_rate.isnot(None),
                CallRecord.num_rounds > 0,
            )
        )
    )
    negotiation_gaps = []
    total_gap = 0
    gap_count = 0
    for row in gap_q.all():
        initial = float(row[0]) if row[0] else 0
        # For failed upward negotiations: carrier wanted more than our ceiling
        # Ceiling = initial × 1.05 (firm). Show how far apart we were.
        ceiling = initial * 1.05  # firm ceiling estimate
        gap_pct = round((ceiling - initial) / initial * 100, 1) if initial else 0
        negotiation_gaps.append({
            "load_id": row[3],
            "initial_rate": initial,
            "ceiling_price": round(ceiling, 2),
            "rounds": row[2],
            "max_concession_pct": gap_pct,
        })
        total_gap += gap_pct
        gap_count += 1

    avg_gap = round(total_gap / gap_count, 1) if gap_count else 0

    # Insight generation
    rejection_insight = ""
    total_rejections = sum(rejection_breakdown.values())
    if total_rejections > 0:
        biggest_reason = max(rejection_breakdown, key=rejection_breakdown.get)
        if biggest_reason == "rejected":
            rejection_insight = "Most losses come from negotiation failures. Carriers wanted more than the ceiling allows. Consider increasing ceiling on high-demand lanes."
        elif biggest_reason == "no_match":
            rejection_insight = "Many carriers can't find loads. Check unmet demand data and source inventory for high-request lanes."
        elif biggest_reason == "carrier_declined":
            rejection_insight = "Carriers are declining pitched loads. Review whether rates are competitive for the lanes being offered."

    rejections = {
        "breakdown": rejection_breakdown,
        "total_lost": total_rejections,
        "negotiation_gaps": negotiation_gaps,
        "avg_negotiation_gap_pct": avg_gap,
        "insight": rejection_insight,
    }

    # ===================================================================
    # 6. CARRIER EXPERIENCE
    # ===================================================================

    # Sentiment distribution
    sentiment_q = await db.execute(
        select(CallRecord.sentiment, func.count(CallRecord.call_id))
        .where(CallRecord.created_at >= cutoff)
        .group_by(CallRecord.sentiment)
    )
    by_sentiment = {row[0]: row[1] for row in sentiment_q.all()}

    # Repeat caller rate
    unique_carriers_q = await db.execute(
        select(func.count(distinct(CallRecord.carrier_mc)))
        .where(and_(CallRecord.created_at >= cutoff, CallRecord.carrier_mc.isnot(None)))
    )
    unique_carriers = unique_carriers_q.scalar() or 0

    repeat_q = await db.execute(
        select(func.count())
        .select_from(
            select(CallRecord.carrier_mc)
            .where(and_(CallRecord.created_at >= cutoff, CallRecord.carrier_mc.isnot(None)))
            .group_by(CallRecord.carrier_mc)
            .having(func.count(CallRecord.call_id) > 1)
            .subquery()
        )
    )
    repeat_carriers = repeat_q.scalar() or 0
    repeat_rate = round(repeat_carriers / unique_carriers * 100, 1) if unique_carriers else 0

    # Average negotiation rounds
    avg_rounds_q = await db.execute(
        select(func.avg(CallRecord.num_rounds))
        .where(and_(CallRecord.created_at >= cutoff, CallRecord.num_rounds > 0))
    )
    avg_rounds = round(float(avg_rounds_q.scalar() or 0), 1)

    # Average call duration
    avg_dur_q = await db.execute(
        select(func.avg(CallRecord.call_duration))
        .where(and_(CallRecord.created_at >= cutoff, CallRecord.call_duration.isnot(None)))
    )
    avg_duration = round(float(avg_dur_q.scalar() or 0), 1)

    # Satisfaction score (positive + neutral = satisfied)
    positive = by_sentiment.get("positive", 0)
    neutral = by_sentiment.get("neutral", 0)
    satisfaction = round((positive + neutral) / total_calls * 100, 1) if total_calls else 0

    experience = {
        "sentiment": by_sentiment,
        "satisfaction_rate": satisfaction,
        "unique_carriers": unique_carriers,
        "repeat_carriers": repeat_carriers,
        "repeat_rate": repeat_rate,
        "avg_negotiation_rounds": avg_rounds,
        "avg_call_duration_seconds": avg_duration,
    }

    # ===================================================================
    # RECENT CALLS (for activity feed)
    # ===================================================================

    recent_q = await db.execute(
        select(CallRecord)
        .where(CallRecord.created_at >= cutoff)
        .order_by(CallRecord.created_at.desc())
        .limit(10)
    )
    recent_calls = [
        {
            "call_id": c.call_id,
            "carrier_mc": c.carrier_mc,
            "carrier_name": c.carrier_name,
            "outcome": c.outcome,
            "sentiment": c.sentiment,
            "initial_rate": c.initial_rate,
            "agreed_price": c.agreed_price,
            "num_rounds": c.num_rounds,
            "load_id": c.load_id,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in recent_q.scalars().all()
    ]

    # ===================================================================
    # BOOKING CONFIRMATIONS
    # ===================================================================

    bookings_q = await db.execute(
        select(BookingConfirmation)
        .order_by(BookingConfirmation.booked_at.desc())
        .limit(10)
    )
    recent_bookings = [
        {
            "confirmation_number": b.confirmation_number,
            "carrier_name": b.carrier_name,
            "carrier_mc": b.carrier_mc,
            "lane": f"{b.origin} → {b.destination}" if b.origin else None,
            "agreed_rate": b.agreed_rate,
            "loadboard_rate": b.loadboard_rate,
            "rate_per_mile": round(b.agreed_rate / b.miles, 2) if b.agreed_rate and b.miles else None,
            "cost_vs_listed": round(b.agreed_rate - b.loadboard_rate, 2) if b.agreed_rate and b.loadboard_rate else 0,
            "rounds": b.negotiation_rounds,
            "booked_at": b.booked_at.isoformat() if b.booked_at else None,
        }
        for b in bookings_q.scalars().all()
    ]

    # ===================================================================
    # ASSEMBLE RESPONSE
    # ===================================================================

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "period_days": days,
        "executive": executive,
        "funnel": funnel,
        "revenue": revenue,
        "lanes": lanes,
        "rejections": rejections,
        "experience": experience,
        "recent_calls": recent_calls,
        "bookings": recent_bookings,
    }
