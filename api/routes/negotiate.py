"""
Deterministic Negotiation Engine — Handles BOTH directions.

UPWARD (common): Carrier wants MORE than listed rate.
  → Agent makes small concessions UP from opening toward ceiling.
  → Ceiling = 110% of loadboard_rate (configurable).
  → Graduated: hold firm → small stretch → final offer.

DOWNWARD (rare): Carrier offers LESS than listed rate.
  → If above floor: accept immediately (broker saves money).
  → If below floor: counter near opening to bring them up.
  → Floor = 85% of loadboard_rate (configurable).

Session tracking: every round stored server-side.
When log_call fires, counter_offers auto-populated.
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

# Server-side session tracking
_negotiation_sessions: dict[str, dict] = {}
_call_start_times: dict[str, float] = {}


def get_session(load_id: str) -> list:
    if load_id in _negotiation_sessions:
        return _negotiation_sessions[load_id]["rounds"]
    return []


def get_call_duration(carrier_mc: str) -> Optional[float]:
    if carrier_mc in _call_start_times:
        return round(time.time() - _call_start_times[carrier_mc], 1)
    return None


def record_call_start(carrier_mc: str):
    if carrier_mc not in _call_start_times:
        _call_start_times[carrier_mc] = time.time()


class NegotiateRequest(BaseModel):
    load_id: str
    carrier_offer: float
    current_round: int = 1
    pricing_strategy: str = "firm"
    opening_rate: Optional[float] = None
    is_per_mile: bool = False

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
    action: str           # accept | counter | reject | walk_away
    counter_offer: Optional[float] = None
    counter_per_mile: Optional[float] = None
    floor_price: float
    ceiling_price: float = 0
    loadboard_rate: float
    carrier_offer: float
    carrier_offer_per_mile: Optional[float] = None
    rate_per_mile: Optional[float] = None
    current_round: int
    max_rounds: int = 3
    direction: str = ""   # "up" | "down" | "at_rate"
    margin_at_carrier_offer: float = 0
    margin_at_counter: Optional[float] = None
    reasoning: str
    guidance: str
    negotiation_history: list = []


@router.post("", response_model=NegotiateResponse)
async def negotiate(req: NegotiateRequest, db: AsyncSession = Depends(get_db)):
    settings = get_settings()

    result = await db.execute(select(Load).where(Load.load_id == req.load_id))
    load = result.scalar_one_or_none()

    if not load:
        return NegotiateResponse(
            action="reject", floor_price=0, ceiling_price=0, loadboard_rate=0,
            carrier_offer=req.carrier_offer, current_round=req.current_round,
            reasoning="Load not found",
            guidance="I can't find that load in our system. Let me look into it.",
        )

    loadboard_rate = load.loadboard_rate
    opening = req.opening_rate or loadboard_rate
    miles = load.miles or 1

    # Convert per-mile to flat
    offer = req.carrier_offer
    if req.is_per_mile and miles > 0:
        offer = round(req.carrier_offer * miles, 2)

    # Reference rates
    rpm = round(loadboard_rate / miles, 2) if miles > 0 else None
    offer_rpm = round(offer / miles, 2) if miles > 0 else None

    # Floor and Ceiling
    base_floor_pct = settings.floor_rate_pct
    base_ceiling_pct = settings.ceiling_rate_pct

    if req.pricing_strategy == "flexible":
        floor_pct = base_floor_pct - 0.02
        ceiling_pct = base_ceiling_pct + 0.02  # more room to stretch up
    elif req.pricing_strategy == "moderate":
        floor_pct = base_floor_pct - 0.01
        ceiling_pct = base_ceiling_pct + 0.01
    else:
        floor_pct = base_floor_pct
        ceiling_pct = base_ceiling_pct

    floor_price = round(loadboard_rate * floor_pct, 2)
    ceiling_price = round(loadboard_rate * ceiling_pct, 2)

    # How far is carrier from our rate (negative = carrier wants more, positive = carrier wants less)
    margin_at_carrier = round(
        (loadboard_rate - offer) / loadboard_rate * 100, 1
    ) if loadboard_rate > 0 else 0

    # Session init
    if req.load_id not in _negotiation_sessions:
        _negotiation_sessions[req.load_id] = {
            "loadboard_rate": loadboard_rate,
            "opening_rate": opening,
            "floor_price": floor_price,
            "ceiling_price": ceiling_price,
            "rounds": [],
            "started_at": datetime.utcnow().isoformat(),
        }
    session = _negotiation_sessions[req.load_id]

    def resp(**kwargs):
        return NegotiateResponse(
            rate_per_mile=rpm,
            carrier_offer_per_mile=offer_rpm,
            floor_price=floor_price,
            ceiling_price=ceiling_price,
            loadboard_rate=loadboard_rate,
            negotiation_history=session["rounds"],
            **kwargs,
        )

    def record(response_type, our_counter=None):
        session["rounds"].append({
            "round": req.current_round,
            "carrier_offer": offer,
            "our_response": response_type,
            "our_counter": our_counter,
        })

    # =======================================================================
    # DIRECTION DETECTION
    # =======================================================================

    # --- CARRIER ACCEPTS OR OFFERS AT OUR RATE ---
    if offer == opening:
        record("accepted")
        return resp(
            action="accept", carrier_offer=offer, current_round=req.current_round,
            direction="at_rate", margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Carrier accepted at ${offer:.0f} (our opening rate).",
            guidance=f"Done. ${offer:.0f} it is — let me get you connected.",
        )

    # --- CARRIER PUSHES UP (wants more money) — MOST COMMON ---
    if offer > opening:
        # Absurd high (more than 150% of loadboard)
        if offer > loadboard_rate * 1.50:
            record("rejected_absurd_high")
            return resp(
                action="reject", carrier_offer=offer, current_round=req.current_round,
                direction="up", margin_at_carrier_offer=margin_at_carrier,
                reasoning=f"Carrier wants ${offer:.0f}, way above budget. Reject.",
                guidance=f"That's way above what this lane pays. I'm at ${opening:.0f} — about ${round(opening/miles,2)} a mile. That's the market on this one.",
            )

        # Max rounds exceeded
        if req.current_round > 3:
            record("walk_away")
            return resp(
                action="walk_away", carrier_offer=offer, current_round=req.current_round,
                direction="up", margin_at_carrier_offer=margin_at_carrier,
                reasoning=f"Round {req.current_round}, carrier still above budget. Walking away.",
                guidance="I've stretched as far as I can on this one. Appreciate the call — hope we can make it work next time.",
            )

        # Graduated concession UPWARD
        upward_range = ceiling_price - opening  # room to move up

        if req.current_round == 1:
            # Hold firm first — don't move on round 1
            counter = opening
            counter_rpm = round(counter / miles, 2) if miles > 0 else None
            guidance = f"I can't go that high on this lane. My rate is ${counter:.0f} — about ${counter_rpm} a mile. That's where we're at."
        elif req.current_round == 2:
            # Small concession up — 40% of upward range
            counter = round(opening + (upward_range * 0.40), -1)
            counter_rpm = round(counter / miles, 2) if miles > 0 else None
            guidance = f"Tell you what, I can stretch to ${counter:.0f} — that's ${counter_rpm} a mile. That's me going above my rate for you."
        else:
            # Final stretch — 80% of upward range
            counter = round(opening + (upward_range * 0.80), -1)
            if counter > ceiling_price:
                counter = round(ceiling_price, -1)
            counter_rpm = round(counter / miles, 2) if miles > 0 else None
            guidance = f"Alright, absolute max I can do is ${counter:.0f} — ${counter_rpm} a mile. That's me dipping into margin. Final offer."

        # If carrier's offer is at or below our counter, accept
        if offer <= counter:
            record("accepted", counter)
            return resp(
                action="accept", carrier_offer=offer, current_round=req.current_round,
                direction="up", margin_at_carrier_offer=margin_at_carrier,
                reasoning=f"Carrier wants ${offer:.0f}, within our stretch to ${counter:.0f}. Accept.",
                guidance=f"I can make ${offer:.0f} work. Let me get you connected.",
            )

        margin_at_counter = round(
            (loadboard_rate - counter) / loadboard_rate * 100, 1
        ) if loadboard_rate > 0 else 0

        record("counter_up", counter)
        return resp(
            action="counter", counter_offer=counter, counter_per_mile=counter_rpm,
            carrier_offer=offer, current_round=req.current_round,
            direction="up", margin_at_carrier_offer=margin_at_carrier,
            margin_at_counter=margin_at_counter,
            reasoning=f"Round {req.current_round} UP: Carrier ${offer:.0f}, counter ${counter:.0f}. Ceiling ${ceiling_price:.0f}.",
            guidance=guidance,
        )

    # --- CARRIER PUSHES DOWN (offers less than our rate) — LESS COMMON ---

    # Absurd low
    if offer < loadboard_rate * 0.50:
        record("rejected_absurd_low")
        return resp(
            action="reject", carrier_offer=offer, current_round=req.current_round,
            direction="down", margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Offer ${offer:.0f} is below 50% of rate. Not serious.",
            guidance=f"That's well below what this lane pays. I'm at ${opening:.0f}. Got a number closer to that?",
        )

    # Above floor — accept (broker saves money!)
    if offer >= floor_price:
        record("accepted")
        return resp(
            action="accept", carrier_offer=offer, current_round=req.current_round,
            direction="down", margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Carrier offers ${offer:.0f}, above floor ${floor_price:.0f}. Accept — broker saves ${opening - offer:.0f}.",
            guidance=f"I can make that work. ${offer:.0f} it is — let me get you connected.",
        )

    # Below floor — counter to bring them up
    if req.current_round > 3:
        record("walk_away")
        return resp(
            action="walk_away", carrier_offer=offer, current_round=req.current_round,
            direction="down", margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Round {req.current_round}, carrier still below floor. Walking away.",
            guidance="I've gone as low as I can on this one. Appreciate the call — hope we line up next time.",
        )

    # Counter upward toward opening
    downward_range = opening - floor_price

    if req.current_round == 1:
        counter = round(opening - (downward_range * 0.30), -1)
        counter_rpm = round(counter / miles, 2) if miles > 0 else None
        guidance = f"I appreciate the offer but I can't go that low. Best I can do is ${counter:.0f} — about ${counter_rpm} a mile."
    elif req.current_round == 2:
        counter = round(opening - (downward_range * 0.60), -1)
        counter_rpm = round(counter / miles, 2) if miles > 0 else None
        guidance = f"I want to make this work. I can come down to ${counter:.0f} — ${counter_rpm} a mile. That's a stretch."
    else:
        counter = round(floor_price + (downward_range * 0.05), -1)
        if counter < floor_price:
            counter = round(floor_price, -1)
        counter_rpm = round(counter / miles, 2) if miles > 0 else None
        guidance = f"My absolute floor is ${counter:.0f} — ${counter_rpm} a mile. Can't go lower than that."

    if counter < floor_price:
        counter = floor_price
    if counter > opening:
        counter = opening

    counter_rpm = round(counter / miles, 2) if miles > 0 else None
    margin_at_counter = round(
        (loadboard_rate - counter) / loadboard_rate * 100, 1
    ) if loadboard_rate > 0 else 0

    record("counter_down", counter)
    return resp(
        action="counter", counter_offer=counter, counter_per_mile=counter_rpm,
        carrier_offer=offer, current_round=req.current_round,
        direction="down", margin_at_carrier_offer=margin_at_carrier,
        margin_at_counter=margin_at_counter,
        reasoning=f"Round {req.current_round} DOWN: Carrier ${offer:.0f}, counter ${counter:.0f}. Floor ${floor_price:.0f}.",
        guidance=guidance,
    )


@router.get("/session/{load_id}")
async def get_negotiation_session(load_id: str):
    if load_id in _negotiation_sessions:
        return _negotiation_sessions[load_id]
    return {"message": "No negotiation session found for this load"}
