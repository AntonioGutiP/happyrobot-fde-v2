"""
Deterministic Negotiation Engine with Session Tracking.

The LLM handles the conversation. This endpoint handles the math.
Each negotiate call is stored in a server-side session keyed by load_id.
When log_call fires, it pulls the full negotiation history automatically.

Design principles:
  - Floor price is absolute. No exceptions.
  - Counter-offers follow a graduated concession curve.
  - Every round is recorded and auditable.
  - Market context adjusts strategy, not the floor.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime
from database import get_db
from models import Load
from config import get_settings
import time

router = APIRouter(prefix="/negotiate", tags=["Negotiation"])

# ---------------------------------------------------------------------------
# Server-side negotiation session tracking
# Keyed by load_id — stores every round for auto-populating counter_offers
# ---------------------------------------------------------------------------
_negotiation_sessions: dict[str, dict] = {}

# Also track call start times for duration estimation
_call_start_times: dict[str, float] = {}


def get_session(load_id: str) -> list:
    """Get the negotiation history for a load."""
    if load_id in _negotiation_sessions:
        return _negotiation_sessions[load_id]["rounds"]
    return []


def get_call_duration(carrier_mc: str) -> Optional[float]:
    """Estimate call duration from first API interaction to now."""
    if carrier_mc in _call_start_times:
        return round(time.time() - _call_start_times[carrier_mc], 1)
    return None


def record_call_start(carrier_mc: str):
    """Record when we first interact with this carrier (for duration tracking)."""
    if carrier_mc not in _call_start_times:
        _call_start_times[carrier_mc] = time.time()


class NegotiateRequest(BaseModel):
    load_id: str
    carrier_offer: float
    current_round: int = 1
    pricing_strategy: str = "firm"
    opening_rate: Optional[float] = None
    is_per_mile: bool = False  # If true, carrier_offer is $/mile — multiply by load miles

    @field_validator("carrier_offer", "opening_rate", mode="before")
    @classmethod
    def coerce_float(cls, v):
        if v is None or v == "":
            return None
        return float(v)

    @field_validator("current_round", mode="before")
    @classmethod
    def coerce_int(cls, v):
        if v is None or v == "":
            return 1
        return int(v)

    @field_validator("is_per_mile", mode="before")
    @classmethod
    def coerce_bool(cls, v):
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v) if v is not None else False


class NegotiateResponse(BaseModel):
    action: str
    counter_offer: Optional[float] = None
    counter_per_mile: Optional[float] = None  # counter as $/mile
    floor_price: float
    loadboard_rate: float
    carrier_offer: float
    carrier_offer_per_mile: Optional[float] = None  # carrier's offer as $/mile
    rate_per_mile: Optional[float] = None  # loadboard rate as $/mile
    current_round: int
    max_rounds: int = 3
    margin_at_carrier_offer: float
    margin_at_counter: Optional[float] = None
    reasoning: str
    guidance: str
    negotiation_history: list = []  # cumulative history of all rounds


@router.post("", response_model=NegotiateResponse)
async def negotiate(req: NegotiateRequest, db: AsyncSession = Depends(get_db)):
    """
    Deterministic negotiation with per-mile support.
    Tracks all rounds server-side for counter_offers logging.
    """
    settings = get_settings()

    result = await db.execute(select(Load).where(Load.load_id == req.load_id))
    load = result.scalar_one_or_none()

    if not load:
        return NegotiateResponse(
            action="reject", floor_price=0, loadboard_rate=0,
            carrier_offer=req.carrier_offer, current_round=req.current_round,
            margin_at_carrier_offer=0,
            reasoning="Load not found",
            guidance="I can't find that load in our system. Let me look into it.",
        )

    loadboard_rate = load.loadboard_rate
    opening = req.opening_rate or loadboard_rate
    miles = load.miles or 1

    # Convert per-mile offer to flat rate if needed
    offer = req.carrier_offer
    if req.is_per_mile and miles > 0:
        offer = round(req.carrier_offer * miles, 2)

    # Per-mile reference values
    rpm = round(loadboard_rate / miles, 2) if miles > 0 else None
    offer_rpm = round(offer / miles, 2) if miles > 0 else None

    # Floor calculation
    base_floor_pct = settings.floor_rate_pct
    if req.pricing_strategy == "flexible":
        floor_pct = base_floor_pct - 0.02
    elif req.pricing_strategy == "moderate":
        floor_pct = base_floor_pct - 0.01
    else:
        floor_pct = base_floor_pct

    floor_price = round(loadboard_rate * floor_pct, 2)

    margin_at_carrier = round(
        (loadboard_rate - offer) / loadboard_rate * 100, 1
    ) if loadboard_rate > 0 else 0

    # Initialize session
    if req.load_id not in _negotiation_sessions:
        _negotiation_sessions[req.load_id] = {
            "loadboard_rate": loadboard_rate,
            "opening_rate": opening,
            "floor_price": floor_price,
            "rounds": [],
            "started_at": datetime.utcnow().isoformat(),
        }
    session = _negotiation_sessions[req.load_id]

    # Helper to build response with per-mile fields
    def resp(**kwargs):
        return NegotiateResponse(
            rate_per_mile=rpm,
            carrier_offer_per_mile=offer_rpm,
            negotiation_history=session["rounds"],
            **kwargs,
        )

    # --- ABSURD OFFER ---
    if offer < loadboard_rate * 0.50:
        session["rounds"].append({"round": req.current_round, "carrier_offer": offer, "our_response": "rejected_absurd", "our_counter": None})
        return resp(
            action="reject", floor_price=floor_price, loadboard_rate=loadboard_rate,
            carrier_offer=offer, current_round=req.current_round,
            margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Offer ${offer:.0f} is below 50% of rate. Not serious.",
            guidance=f"That's well below where I can go on this lane. I'm at ${opening:.0f}. Got a number closer to that?",
        )

    # --- ACCEPT ---
    if offer >= floor_price:
        session["rounds"].append({"round": req.current_round, "carrier_offer": offer, "our_response": "accepted", "our_counter": None})
        return resp(
            action="accept", floor_price=floor_price, loadboard_rate=loadboard_rate,
            carrier_offer=offer, current_round=req.current_round,
            margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Offer ${offer:.0f} >= floor ${floor_price:.0f}. Accept.",
            guidance=f"I can make that work. ${offer:.0f} it is — let me get you connected.",
        )

    # --- WALK AWAY ---
    if req.current_round > 3:
        session["rounds"].append({"round": req.current_round, "carrier_offer": offer, "our_response": "walk_away", "our_counter": None})
        return resp(
            action="walk_away", floor_price=floor_price, loadboard_rate=loadboard_rate,
            carrier_offer=offer, current_round=req.current_round,
            margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Round {req.current_round} exceeds max. Walking away.",
            guidance="I've gone as far as I can on this one. Appreciate the call — hope we line up next time.",
        )

    # --- COUNTER-OFFER ---
    range_total = opening - floor_price

    if req.current_round == 1:
        counter = round(opening - (range_total * 0.30), -1)
        counter_rpm = round(counter / miles, 2) if miles > 0 else None
        guidance = f"I appreciate the counter. Best I can do right now is ${counter:.0f} — that's about ${counter_rpm} a mile. Tight on margin with this one."
    elif req.current_round == 2:
        counter = round(opening - (range_total * 0.60), -1)
        counter_rpm = round(counter / miles, 2) if miles > 0 else None
        guidance = f"I want to make this work for you. I can stretch to ${counter:.0f} — ${counter_rpm} a mile. That's about as far as I can go."
    else:
        counter = round(floor_price + (range_total * 0.05), -1)
        if counter < floor_price:
            counter = round(floor_price, -1)
        counter_rpm = round(counter / miles, 2) if miles > 0 else None
        guidance = f"Alright, my absolute best is ${counter:.0f} — ${counter_rpm} a mile. That's the ceiling on my end."

    if counter < floor_price:
        counter = floor_price
    if counter > opening:
        counter = opening

    counter_rpm = round(counter / miles, 2) if miles > 0 else None
    margin_at_counter = round(
        (loadboard_rate - counter) / loadboard_rate * 100, 1
    ) if loadboard_rate > 0 else 0

    session["rounds"].append({"round": req.current_round, "carrier_offer": offer, "our_response": "counter", "our_counter": counter})

    return resp(
        action="counter", counter_offer=counter, counter_per_mile=counter_rpm,
        floor_price=floor_price, loadboard_rate=loadboard_rate,
        carrier_offer=offer, current_round=req.current_round,
        margin_at_carrier_offer=margin_at_carrier, margin_at_counter=margin_at_counter,
        reasoning=f"Round {req.current_round}: Carrier ${offer:.0f}, counter ${counter:.0f}. Floor ${floor_price:.0f}.",
        guidance=guidance,
    )


@router.get("/session/{load_id}")
async def get_negotiation_session(load_id: str):
    """Get the full negotiation session for a load. Used for debugging."""
    if load_id in _negotiation_sessions:
        return _negotiation_sessions[load_id]
    return {"message": "No negotiation session found for this load"}
