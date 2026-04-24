from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from models import CallOutcome, CallSentiment


# ---------------------------------------------------------------------------
# Loads
# ---------------------------------------------------------------------------

class LoadOut(BaseModel):
    load_id: str
    origin: str
    destination: str
    pickup_datetime: datetime
    delivery_datetime: datetime
    equipment_type: str
    loadboard_rate: float
    notes: Optional[str] = None
    weight: float
    commodity_type: str
    num_of_pieces: int
    miles: float
    dimensions: Optional[str] = None
    status: str

    class Config:
        from_attributes = True


class LoadSearchParams(BaseModel):
    origin: Optional[str] = None
    destination: Optional[str] = None
    equipment_type: Optional[str] = None
    min_rate: Optional[float] = None
    max_rate: Optional[float] = None
    status: Optional[str] = "available"


# ---------------------------------------------------------------------------
# Carrier verification
# ---------------------------------------------------------------------------

class CarrierVerification(BaseModel):
    mc_number: str
    dot_number: Optional[str] = None
    legal_name: Optional[str] = None
    dba_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    allow_to_operate: Optional[str] = None
    out_of_service: Optional[str] = None
    is_eligible: bool
    eligibility_reason: str
    data_source: str = "fmcsa_api"  # fmcsa_api | mock_fallback


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------

class CallCreate(BaseModel):
    carrier_mc: Optional[str] = None
    carrier_name: Optional[str] = None
    carrier_dot: Optional[str] = None
    load_id: Optional[str] = None
    outcome: CallOutcome
    sentiment: CallSentiment = CallSentiment.neutral
    initial_rate: Optional[float] = None
    agreed_price: Optional[float] = None
    counter_offers: Optional[list | dict] = None
    num_rounds: int = 0
    call_duration: Optional[float] = None
    fmcsa_verified: bool = False
    fmcsa_status: Optional[str] = None
    extracted_data: Optional[dict] = None


class CallOut(BaseModel):
    call_id: str
    carrier_mc: Optional[str] = None
    carrier_name: Optional[str] = None
    carrier_dot: Optional[str] = None
    load_id: Optional[str] = None
    outcome: str
    sentiment: str
    initial_rate: Optional[float] = None
    agreed_price: Optional[float] = None
    counter_offers: Optional[list | dict] = None
    num_rounds: int
    call_duration: Optional[float] = None
    fmcsa_verified: bool
    fmcsa_status: Optional[str] = None
    extracted_data: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Stats (dashboard aggregates)
# ---------------------------------------------------------------------------

class CallStats(BaseModel):
    total_calls: int = 0
    by_outcome: dict = Field(default_factory=dict)
    by_sentiment: dict = Field(default_factory=dict)
    conversion_rate: float = 0.0
    avg_negotiation_rounds: float = 0.0
    avg_margin_pct: Optional[float] = None
    total_booked_revenue: float = 0.0
    avg_call_duration: Optional[float] = None
    top_lanes: list = Field(default_factory=list)
    rejection_reasons: list = Field(default_factory=list)
