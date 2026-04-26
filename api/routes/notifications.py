"""
Post-Call Notifications.

Triggered by HappyRobot webhook after Voice Agent ends.
Processes the most recent call and generates appropriate notifications.

In production, these would connect to:
  - SendGrid/SES for email delivery
  - Slack/Teams for team alerts
  - TMS API for system updates
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import CallRecord, Load, BookingConfirmation

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.post("/process-latest")
async def process_latest_call(db: AsyncSession = Depends(get_db)):
    """
    Called by HappyRobot webhook after every call ends.
    Checks the most recent call and routes to the right notification.
    """
    result = await db.execute(
        select(CallRecord).order_by(CallRecord.created_at.desc()).limit(1)
    )
    call = result.scalar_one_or_none()
    if not call:
        return {"status": "no_calls", "notification": None}

    if call.outcome == "booked":
        return await _booking_notification(call, db)
    elif call.outcome == "no_match":
        return await _demand_alert(call, db)
    else:
        return await _general_summary(call, db)


async def _booking_notification(call: CallRecord, db: AsyncSession):
    """Booking confirmation — dispatch + carrier notification."""
    conf_q = await db.execute(
        select(BookingConfirmation)
        .where(BookingConfirmation.call_id == call.call_id)
        .limit(1)
    )
    conf = conf_q.scalar_one_or_none()

    load = None
    if call.load_id:
        load_q = await db.execute(select(Load).where(Load.load_id == call.load_id))
        load = load_q.scalar_one_or_none()

    conf_num = conf.confirmation_number if conf else "PENDING"
    lane = f"{load.origin} to {load.destination}" if load else "Unknown"
    equipment = load.equipment_type if load else "Unknown"
    miles = load.miles if load else 0
    rpm = round(call.agreed_price / miles, 2) if call.agreed_price and miles and miles > 0 else 0
    weight = f"{load.weight:,.0f} lbs" if load and load.weight else "N/A"
    commodity = load.commodity_type if load else "N/A"
    notes = load.notes if load and load.notes else "None"
    pickup = load.pickup_datetime.strftime("%B %d, %Y %I:%M %p") if load and load.pickup_datetime else "TBD"
    delivery = load.delivery_datetime.strftime("%B %d, %Y %I:%M %p") if load and load.delivery_datetime else "TBD"
    duration = f"{call.call_duration:.0f}s" if call.call_duration else "N/A"

    email_body = f"""BOOKING CONFIRMATION — {conf_num}

Carrier: {call.carrier_name or 'Unknown'} (MC: {call.carrier_mc})
DOT: {call.carrier_dot or 'N/A'}

Load: {call.load_id}
Lane: {lane}
Equipment: {equipment}
Miles: {miles}

Rate: ${call.agreed_price:,.0f} (${rpm}/mi)
Listed: ${call.initial_rate:,.0f}
Negotiation: {call.num_rounds} round(s)

Pickup: {pickup}
Delivery: {delivery}
Commodity: {commodity}
Weight: {weight}
Notes: {notes}

Booked by: AI Agent (Alex)
Call duration: {duration}
Sentiment: {call.sentiment or 'N/A'}

---
This booking was automatically processed by the AI carrier sales system.
Dispatch: please confirm pickup appointment with carrier."""

    return {
        "status": "booking_notification_generated",
        "type": "booking",
        "confirmation_number": conf_num,
        "carrier": call.carrier_name,
        "carrier_mc": call.carrier_mc,
        "lane": lane,
        "agreed_rate": call.agreed_price,
        "rate_per_mile": rpm,
        "email": {
            "to": "dispatch@acmelogistics.com",
            "cc": "carrier-sales@acmelogistics.com",
            "subject": f"Load Booked — {conf_num} | {lane}",
            "body": email_body,
        },
        "production_actions": [
            "Send confirmation email to dispatch",
            "Send rate confirmation to carrier",
            "Update TMS with booking details",
            "Create accounting entry",
        ],
    }


async def _demand_alert(call: CallRecord, db: AsyncSession):
    """Carrier wanted loads we don't have — alert sourcing team."""
    extracted = call.extracted_data or {}
    lane = extracted.get("lane", "Unknown lane")
    equipment = extracted.get("equipment", "Unknown")
    call_time = call.created_at.strftime("%B %d, %Y %I:%M %p") if call.created_at else "Unknown"

    alert_body = f"""CARRIER DEMAND ALERT

A verified carrier called looking for loads we don't have.

Carrier: {call.carrier_name or 'Unknown'} (MC: {call.carrier_mc})
Wanted: {equipment} on {lane}
Call time: {call_time}
Sentiment: {call.sentiment or 'N/A'}

ACTION NEEDED: Source {equipment} loads on this lane.
This carrier is verified and ready to book.

---
Automated alert from AI carrier sales system."""

    return {
        "status": "demand_alert_generated",
        "type": "unmet_demand",
        "carrier": call.carrier_name,
        "carrier_mc": call.carrier_mc,
        "requested_lane": lane,
        "equipment": equipment,
        "alert": {
            "to": "sourcing@acmelogistics.com",
            "subject": f"Unmet Carrier Demand — {lane}",
            "body": alert_body,
        },
        "production_actions": [
            "Alert sourcing team to find loads on this lane",
            "Add lane to high-priority sourcing list",
            "Schedule callback to carrier when load available",
        ],
    }


async def _general_summary(call: CallRecord, db: AsyncSession):
    """Summary for declined, rejected, follow-up calls."""
    extracted = call.extracted_data or {}
    reason = extracted.get("rejection_reason", "Not specified")

    return {
        "status": "call_logged",
        "type": call.outcome,
        "carrier": call.carrier_name,
        "carrier_mc": call.carrier_mc,
        "reason": reason,
        "num_rounds": call.num_rounds,
        "fmcsa_verified": call.fmcsa_verified,
        "production_actions": [
            "Log for analytics",
            "Review if follow-up needed",
        ],
    }
