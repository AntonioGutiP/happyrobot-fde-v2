"""
Deterministic Negotiation Engine.

The carrier calls in wanting to haul a load. We offer the loadboard_rate.
The carrier may accept or push for more money. We can stretch UP to a
ceiling to close the deal. The ceiling depends on how long the load
has been sitting (pricing_strategy from market context).

  firm     = max 5% above loadboard  (fresh load)
  moderate = max 7% above loadboard  (some declines)
  flexible = max 10% above loadboard (load is sitting)

Concession curve:
  Round 1: Hold at loadboard_rate. Don't move.
  Round 2: Stretch 40% of the way toward ceiling.
  Round 3: Stretch 80% of the way toward ceiling.
  After 3: Walk away.

If the carrier accepts at or below our offer at any point → done.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime
from database import get_db
from models import Load
import time

router = APIRouter(prefix="/negotiate", tags=["Negotiation"])

# Session tracking
_sessions: dict[str, dict] = {}
_call_starts: dict[str, float] = {}


def get_session(load_id: str) -> list:
    return _sessions[load_id]["rounds"] if load_id in _sessions else []


def get_call_duration(mc: str) -> Optional[float]:
    return round(time.time() - _call_starts[mc], 1) if mc in _call_starts else None


def record_call_start(mc: str):
    if mc not in _call_starts:
        _call_starts[mc] = time.time()


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
    action: str               # accept | counter | reject | walk_away
    counter_offer: Optional[float] = None
    counter_per_mile: Optional[float] = None
    opening_rate: float = 0
    ceiling: float = 0
    carrier_offer: float = 0
    carrier_offer_per_mile: Optional[float] = None
    rate_per_mile: Optional[float] = None
    current_round: int = 0
    max_rounds: int = 3
    concession_pct: float = 0  # how much we stretched above opening
    reasoning: str = ""
    guidance: str = ""         # what the agent says to the carrier
    negotiation_history: list = []


@router.post("", response_model=NegotiateResponse)
async def negotiate(req: NegotiateRequest, db: AsyncSession = Depends(get_db)):

    # Get the load
    result = await db.execute(select(Load).where(Load.load_id == req.load_id))
    load = result.scalar_one_or_none()
    if not load:
        return NegotiateResponse(
            action="reject",
            reasoning="Load not found",
            guidance="I can't find that load. Let me look into it.",
        )

    loadboard_rate = load.loadboard_rate
    opening = req.opening_rate or loadboard_rate
    miles = load.miles or 1

    # Per-mile conversion
    offer = req.carrier_offer
    if req.is_per_mile and miles > 0:
        offer = round(req.carrier_offer * miles, 2)

    rpm = round(opening / miles, 2) if miles > 0 else None
    offer_rpm = round(offer / miles, 2) if miles > 0 else None

    # Ceiling
    if req.pricing_strategy == "flexible":
        ceiling = round(loadboard_rate * 1.10, 2)
    elif req.pricing_strategy == "moderate":
        ceiling = round(loadboard_rate * 1.07, 2)
    else:
        ceiling = round(loadboard_rate * 1.05, 2)

    stretch_range = ceiling - opening

    # Init session
    if req.load_id not in _sessions:
        _sessions[req.load_id] = {
            "opening": opening,
            "ceiling": ceiling,
            "rounds": [],
        }
    session = _sessions[req.load_id]

    def record(action, our_counter=None):
        session["rounds"].append({
            "round": req.current_round,
            "carrier_offer": offer,
            "action": action,
            "our_counter": our_counter,
        })

    def build(action, guidance, counter=None, concession=0, **kw):
        counter_rpm = round(counter / miles, 2) if counter and miles > 0 else None
        return NegotiateResponse(
            action=action,
            counter_offer=counter,
            counter_per_mile=counter_rpm,
            opening_rate=opening,
            ceiling=ceiling,
            carrier_offer=offer,
            carrier_offer_per_mile=offer_rpm,
            rate_per_mile=rpm,
            current_round=req.current_round,
            concession_pct=round(concession, 1),
            reasoning=kw.get("reasoning", ""),
            guidance=guidance,
            negotiation_history=session["rounds"],
        )

    # =================================================================
    # DECISION LOGIC
    # =================================================================

    # 1. Carrier accepts at or below our rate → best outcome
    if offer <= opening:
        record("accepted")
        return build(
            "accept",
            f"Done. ${offer:.0f} works. Let me get you connected.",
            reasoning=f"Carrier offered ${offer:.0f}, at or below opening ${opening:.0f}.",
        )

    # 2. Absurd ask (>150% of loadboard)
    if offer > loadboard_rate * 1.50:
        record("rejected_absurd")
        return build(
            "reject",
            f"That's way above what this lane pays. We're at ${opening:.0f} — about ${rpm} a mile.",
            reasoning=f"${offer:.0f} exceeds 150% of loadboard ${loadboard_rate:.0f}.",
        )

    # 3. Exceeded 3 rounds
    if req.current_round > 3:
        record("walk_away")
        return build(
            "walk_away",
            "I've stretched as far as I can. Appreciate the call — hope we line up next time.",
            reasoning=f"Round {req.current_round} exceeds max.",
        )

    # 4. Calculate our counter for this round
    if req.current_round == 1:
        counter = opening  # hold firm
        concession = 0
    elif req.current_round == 2:
        counter = round(opening + (stretch_range * 0.40), -1)
        concession = (counter - opening) / opening * 100
    else:
        counter = round(opening + (stretch_range * 0.80), -1)
        if counter > ceiling:
            counter = round(ceiling, -1)
        concession = (counter - opening) / opening * 100

    # Safety
    if counter > ceiling:
        counter = ceiling
    counter_rpm = round(counter / miles, 2) if miles > 0 else None
    concession = (counter - opening) / opening * 100

    # 5. If carrier is within our counter → accept their number
    if offer <= counter:
        actual_concession = (offer - opening) / opening * 100
        record("accepted", counter)
        return build(
            "accept",
            f"I can make ${offer:.0f} work. Let me get you connected.",
            concession=actual_concession,
            reasoning=f"Carrier ${offer:.0f} ≤ our R{req.current_round} counter ${counter:.0f}. Accept.",
        )

    # 6. Counter — carrier is above our offer
    record("counter", counter)

    if req.current_round == 1:
        guidance = f"Can't go that high on this one. We're at ${counter:.0f} — about ${counter_rpm} a mile."
    elif req.current_round == 2:
        guidance = f"Tell you what, I can stretch to ${counter:.0f} — that's ${counter_rpm} a mile. That's me going above rate for you."
    else:
        guidance = f"Alright, absolute max I can do is ${counter:.0f} — ${counter_rpm} a mile. That's my ceiling."

    return build(
        "counter", guidance,
        counter=counter,
        concession=concession,
        reasoning=f"R{req.current_round}: Carrier ${offer:.0f}, counter ${counter:.0f}. Ceiling ${ceiling:.0f}.",
    )


@router.get("/session/{load_id}")
async def get_negotiation_session(load_id: str):
    return _sessions.get(load_id, {"message": "No session found"})
