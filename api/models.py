import uuid
from datetime import datetime
from sqlalchemy import (
    String, Float, Integer, Boolean, DateTime, Text, Enum as SAEnum, ForeignKey, JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base
import enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class LoadStatus(str, enum.Enum):
    available = "available"
    booked = "booked"
    expired = "expired"


class CallOutcome(str, enum.Enum):
    booked = "booked"
    rejected = "rejected"          # FMCSA fail or negotiation dead-end
    no_match = "no_match"          # no loads matched carrier needs
    carrier_declined = "carrier_declined"  # carrier said no to pitched load
    needs_follow_up = "needs_follow_up"


class CallSentiment(str, enum.Enum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"
    hostile = "hostile"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Load(Base):
    __tablename__ = "loads"

    load_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    origin: Mapped[str] = mapped_column(String(100))
    destination: Mapped[str] = mapped_column(String(100))
    pickup_datetime: Mapped[datetime] = mapped_column(DateTime)
    delivery_datetime: Mapped[datetime] = mapped_column(DateTime)
    equipment_type: Mapped[str] = mapped_column(String(50))
    loadboard_rate: Mapped[float] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[float] = mapped_column(Float)
    commodity_type: Mapped[str] = mapped_column(String(100))
    num_of_pieces: Mapped[int] = mapped_column(Integer)
    miles: Mapped[float] = mapped_column(Float)
    dimensions: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        SAEnum(LoadStatus, name="load_status", create_constraint=True),
        default=LoadStatus.available,
    )

    # Relationship
    call_records: Mapped[list["CallRecord"]] = relationship(back_populates="load")


class CallRecord(Base):
    __tablename__ = "call_records"

    call_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    carrier_mc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    carrier_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    carrier_dot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    load_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("loads.load_id"), nullable=True
    )
    outcome: Mapped[str] = mapped_column(
        SAEnum(CallOutcome, name="call_outcome", create_constraint=True)
    )
    sentiment: Mapped[str] = mapped_column(
        SAEnum(CallSentiment, name="call_sentiment", create_constraint=True),
        default=CallSentiment.neutral,
    )
    initial_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    agreed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    counter_offers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    num_rounds: Mapped[int] = mapped_column(Integer, default=0)
    call_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    fmcsa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    fmcsa_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extracted_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationship
    load: Mapped[Load | None] = relationship(back_populates="call_records")


class CarrierPreference(Base):
    """Stores carrier lane/equipment preferences when no loads match.
    Feeds dashboard's 'unmet demand' analytics."""
    __tablename__ = "carrier_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    carrier_mc: Mapped[str] = mapped_column(String(20))
    carrier_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(100), nullable=True)
    equipment_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    min_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BookingConfirmation(Base):
    """Generated automatically when a load is booked.
    Represents the booking confirmation that would be
    sent to dispatch, emailed to carrier, and logged in TMS."""
    __tablename__ = "booking_confirmations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    confirmation_number: Mapped[str] = mapped_column(String(20), unique=True)
    call_id: Mapped[str] = mapped_column(String(50))
    load_id: Mapped[str] = mapped_column(String(20))
    carrier_mc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    carrier_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    carrier_dot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(100), nullable=True)
    agreed_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    loadboard_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    equipment_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pickup_datetime: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivery_datetime: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    negotiation_rounds: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    booked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
