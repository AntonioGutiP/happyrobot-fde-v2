"""
Deterministic Negotiation Engine.

The LLM handles the conversation. This endpoint handles the math.

Design principles:
  - Floor price is absolute. No exceptions. No rounding errors.
  - Counter-offers follow a graduated concession curve.
  - Each round concedes less than the previous (diminishing returns signal).
  - Market context adjusts initial aggression, not the floor.
  - Every decision is explainable and auditable.

Concession strategy:
  Round 1: Concede up to 3% from opening rate. "Tight margins" framing.
  Round 2: Concede up to 5% cumulative from opening. "Stretching" framing.
  Round 3: Final offer at or near floor. "Absolute best" framing.

  If carrier's offer >= floor at any point → accept immediately.
  If carrier's offer is absurd (< 50% of rate) → reject without counter.
  After round 3 with no deal → walk away.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, field_validator
from typing import Optional
from database import get_db
from models import Load
from config import get_settings

router = APIRouter(prefix="/negotiate", tags=["Negotiation"])


class NegotiateRequest(BaseModel):
    load_id: str
    carrier_offer: float
    current_round: int = 1
    pricing_strategy: str = "firm"  # firm | moderate | flexible
    opening_rate: Optional[float] = None  # what we initially offered

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


class NegotiateResponse(BaseModel):
    action: str           # "accept" | "counter" | "reject" | "walk_away"
    counter_offer: Optional[float] = None
    floor_price: float
    loadboard_rate: float
    carrier_offer: float
    current_round: int
    max_rounds: int = 3
    margin_at_carrier_offer: float  # % margin if we accept carrier's price
    margin_at_counter: Optional[float] = None
    reasoning: str
    guidance: str         # what the agent should communicate


@router.post("", response_model=NegotiateResponse)
async def negotiate(req: NegotiateRequest, db: AsyncSession = Depends(get_db)):
    """
    Deterministic negotiation decision engine.

    The agent calls this with the carrier's offer. The engine returns
    exactly what to do: accept, counter (with specific dollar amount),
    or walk away. The agent never does math — it just delivers the message.

    This ensures:
    - Floor price is NEVER breached
    - Counter-offers follow a consistent, professional pattern
    - Every negotiation is auditable
    - Pricing strategy adapts to market conditions
    """
    settings = get_settings()

    # Get the load's actual rate from DB
    result = await db.execute(select(Load).where(Load.load_id == req.load_id))
    load = result.scalar_one_or_none()

    if not load:
        return NegotiateResponse(
            action="reject",
            floor_price=0,
            loadboard_rate=0,
            carrier_offer=req.carrier_offer,
            current_round=req.current_round,
            margin_at_carrier_offer=0,
            reasoning="Load not found in database",
            guidance="I'm sorry, I can't find that load in our system. Let me look into it.",
        )

    loadboard_rate = load.loadboard_rate
    opening = req.opening_rate or loadboard_rate

    # ===================================================================
    # FLOOR CALCULATION — configurable, absolute minimum
    # ===================================================================
    # Base floor from settings (default 85%)
    base_floor_pct = settings.floor_rate_pct

    # Adjust floor based on market context / pricing strategy
    if req.pricing_strategy == "flexible":
        # Load has been declined multiple times — widen floor slightly
        floor_pct = base_floor_pct - 0.02  # 83% instead of 85%
    elif req.pricing_strategy == "moderate":
        floor_pct = base_floor_pct - 0.01  # 84%
    else:
        floor_pct = base_floor_pct  # 85% — standard

    floor_price = round(loadboard_rate * floor_pct, 2)

    # ===================================================================
    # MARGIN CALCULATIONS
    # ===================================================================
    margin_at_carrier = round(
        (loadboard_rate - req.carrier_offer) / loadboard_rate * 100, 1
    ) if loadboard_rate > 0 else 0

    # ===================================================================
    # DECISION LOGIC
    # ===================================================================

    # ABSURD OFFER — below 50% of rate, don't even counter
    if req.carrier_offer < loadboard_rate * 0.50:
        return NegotiateResponse(
            action="reject",
            floor_price=floor_price,
            loadboard_rate=loadboard_rate,
            carrier_offer=req.carrier_offer,
            current_round=req.current_round,
            margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Carrier offer ${req.carrier_offer:.0f} is below 50% of rate ${loadboard_rate:.0f}. Not a serious offer.",
            guidance=f"That's well below where I can go on this lane. My rate is ${opening:.0f}. Is there a number closer to that which works for you?",
        )

    # CARRIER OFFER AT OR ABOVE FLOOR — accept immediately
    if req.carrier_offer >= floor_price:
        return NegotiateResponse(
            action="accept",
            counter_offer=None,
            floor_price=floor_price,
            loadboard_rate=loadboard_rate,
            carrier_offer=req.carrier_offer,
            current_round=req.current_round,
            margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Carrier offer ${req.carrier_offer:.0f} is at or above floor ${floor_price:.0f}. Accept.",
            guidance=f"I can make that work. ${req.carrier_offer:.0f} it is. Let me get you connected to finalize.",
        )

    # ROUND CHECK — if we've exhausted 3 rounds, walk away
    if req.current_round > 3:
        return NegotiateResponse(
            action="walk_away",
            floor_price=floor_price,
            loadboard_rate=loadboard_rate,
            carrier_offer=req.carrier_offer,
            current_round=req.current_round,
            margin_at_carrier_offer=margin_at_carrier,
            reasoning=f"Round {req.current_round} exceeds max 3 rounds. Walking away.",
            guidance="I've gone as far as I can on this one. I appreciate your time — hope we can work together on the next load.",
        )

    # ===================================================================
    # COUNTER-OFFER CALCULATION — graduated concession curve
    # ===================================================================
    #
    # The concession narrows each round, signaling firmness:
    #   Round 1: Counter at ~midpoint between opening and a moderate concession
    #   Round 2: Counter closer to floor, showing we're stretching
    #   Round 3: Counter at or very near floor — final offer
    #
    # We never counter below floor. We never counter above opening.

    range_total = opening - floor_price  # total room we have

    if req.current_round == 1:
        # Concede ~30% of available range
        concession = range_total * 0.30
        counter = round(opening - concession, -1)  # round to nearest $10
        framing = "firm"
        guidance = f"I appreciate the counter. Best I can do right now is ${counter:.0f}. We're tight on margin with this one."

    elif req.current_round == 2:
        # Concede ~60% of available range
        concession = range_total * 0.60
        counter = round(opening - concession, -1)
        framing = "stretching"
        guidance = f"I want to make this work for you. I can stretch to ${counter:.0f} — that's about as far as I can go."

    else:  # Round 3
        # Final offer — at floor or just barely above
        counter = round(floor_price + (range_total * 0.05), -1)  # 5% above floor
        if counter < floor_price:
            counter = round(floor_price, -1)
        framing = "final"
        guidance = f"Alright, my absolute best is ${counter:.0f}. That's the ceiling on my end."

    # Safety: ensure counter never goes below floor
    if counter < floor_price:
        counter = round(floor_price, -1)
        if counter < floor_price:
            counter = floor_price

    # Safety: ensure counter doesn't exceed opening
    if counter > opening:
        counter = opening

    margin_at_counter = round(
        (loadboard_rate - counter) / loadboard_rate * 100, 1
    ) if loadboard_rate > 0 else 0

    return NegotiateResponse(
        action="counter",
        counter_offer=counter,
        floor_price=floor_price,
        loadboard_rate=loadboard_rate,
        carrier_offer=req.carrier_offer,
        current_round=req.current_round,
        margin_at_carrier_offer=margin_at_carrier,
        margin_at_counter=margin_at_counter,
        reasoning=f"Round {req.current_round} ({framing}): Carrier at ${req.carrier_offer:.0f}, countering at ${counter:.0f}. Floor is ${floor_price:.0f}. Room remaining: ${counter - floor_price:.0f}.",
        guidance=guidance,
    )
