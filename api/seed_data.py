"""
Seed 18 realistic loads across diverse US lanes.

Run on startup: if loads table is empty, inserts seed data.
Also seeds 8 sample call records so the dashboard has data from day 1.
"""

import logging
from datetime import datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from models import Load, CallRecord

logger = logging.getLogger(__name__)

# Base date: today
_TODAY = datetime.utcnow().replace(hour=8, minute=0, second=0, microsecond=0)


def _dt(days_offset: int, hour: int = 8) -> datetime:
    return _TODAY + timedelta(days=days_offset, hours=hour - 8)


SEED_LOADS = [
    # --- Dry Van loads ---
    dict(load_id="LD-1001", origin="Dallas, TX", destination="Atlanta, GA",
         pickup_datetime=_dt(1, 6), delivery_datetime=_dt(2, 14),
         equipment_type="Dry Van", loadboard_rate=2200.00, weight=38000,
         commodity_type="Electronics", num_of_pieces=24, miles=781,
         dimensions="48x96x102", notes="No-touch freight, dock-to-dock",
         status="available"),

    dict(load_id="LD-1002", origin="Chicago, IL", destination="Miami, FL",
         pickup_datetime=_dt(1, 8), delivery_datetime=_dt(3, 10),
         equipment_type="Dry Van", loadboard_rate=3100.00, weight=42000,
         commodity_type="Consumer Goods", num_of_pieces=30, miles=1381,
         dimensions="48x96x102", notes="Residential delivery, lift gate required",
         status="available"),

    dict(load_id="LD-1003", origin="Los Angeles, CA", destination="Phoenix, AZ",
         pickup_datetime=_dt(0, 14), delivery_datetime=_dt(1, 8),
         equipment_type="Dry Van", loadboard_rate=950.00, weight=28000,
         commodity_type="Furniture", num_of_pieces=18, miles=373,
         dimensions="48x96x102", notes="Blanket wrap, pad wrapped",
         status="available"),

    dict(load_id="LD-1004", origin="Nashville, TN", destination="Charlotte, NC",
         pickup_datetime=_dt(2, 7), delivery_datetime=_dt(2, 18),
         equipment_type="Dry Van", loadboard_rate=1100.00, weight=33000,
         commodity_type="Auto Parts", num_of_pieces=40, miles=410,
         dimensions="48x96x102", notes="Palletized, shrink-wrapped",
         status="available"),

    dict(load_id="LD-1005", origin="Houston, TX", destination="Memphis, TN",
         pickup_datetime=_dt(3, 6), delivery_datetime=_dt(4, 12),
         equipment_type="Dry Van", loadboard_rate=1650.00, weight=40000,
         commodity_type="Paper Products", num_of_pieces=22, miles=586,
         dimensions="48x96x102", notes="Floor loaded, driver assist unload",
         status="available"),

    dict(load_id="LD-1006", origin="Indianapolis, IN", destination="Columbus, OH",
         pickup_datetime=_dt(1, 10), delivery_datetime=_dt(1, 18),
         equipment_type="Dry Van", loadboard_rate=800.00, weight=25000,
         commodity_type="Beverages", num_of_pieces=28, miles=176,
         dimensions="48x96x102", notes="Pallet jack required",
         status="available"),

    # --- Reefer loads ---
    dict(load_id="LD-2001", origin="Fresno, CA", destination="Denver, CO",
         pickup_datetime=_dt(0, 5), delivery_datetime=_dt(1, 16),
         equipment_type="Reefer", loadboard_rate=3400.00, weight=44000,
         commodity_type="Produce", num_of_pieces=20, miles=1086,
         dimensions="48x96x102", notes="Temp 34°F, continuous monitoring",
         status="available"),

    dict(load_id="LD-2002", origin="Omaha, NE", destination="Dallas, TX",
         pickup_datetime=_dt(2, 4), delivery_datetime=_dt(3, 8),
         equipment_type="Reefer", loadboard_rate=2800.00, weight=43000,
         commodity_type="Frozen Meat", num_of_pieces=18, miles=661,
         dimensions="48x96x102", notes="Temp 0°F, USDA inspected",
         status="available"),

    dict(load_id="LD-2003", origin="Seattle, WA", destination="Portland, OR",
         pickup_datetime=_dt(1, 3), delivery_datetime=_dt(1, 12),
         equipment_type="Reefer", loadboard_rate=850.00, weight=36000,
         commodity_type="Dairy Products", num_of_pieces=32, miles=174,
         dimensions="48x96x102", notes="Temp 38°F, no layover",
         status="available"),

    dict(load_id="LD-2004", origin="Miami, FL", destination="Atlanta, GA",
         pickup_datetime=_dt(3, 2), delivery_datetime=_dt(3, 18),
         equipment_type="Reefer", loadboard_rate=1900.00, weight=39000,
         commodity_type="Seafood", num_of_pieces=15, miles=662,
         dimensions="48x96x102", notes="Temp 28°F, time-critical",
         status="available"),

    # --- Flatbed loads ---
    dict(load_id="LD-3001", origin="Pittsburgh, PA", destination="Detroit, MI",
         pickup_datetime=_dt(2, 7), delivery_datetime=_dt(3, 10),
         equipment_type="Flatbed", loadboard_rate=1800.00, weight=46000,
         commodity_type="Steel Coils", num_of_pieces=4, miles=288,
         dimensions="Various", notes="Tarps required, chains and binders",
         status="available"),

    dict(load_id="LD-3002", origin="Houston, TX", destination="Oklahoma City, OK",
         pickup_datetime=_dt(1, 6), delivery_datetime=_dt(2, 14),
         equipment_type="Flatbed", loadboard_rate=1950.00, weight=48000,
         commodity_type="Pipe & Tubing", num_of_pieces=8, miles=441,
         dimensions="40ft lengths", notes="Oversize, escort may be needed",
         status="available"),

    dict(load_id="LD-3003", origin="Sacramento, CA", destination="Las Vegas, NV",
         pickup_datetime=_dt(3, 8), delivery_datetime=_dt(4, 12),
         equipment_type="Flatbed", loadboard_rate=1500.00, weight=35000,
         commodity_type="Lumber", num_of_pieces=6, miles=563,
         dimensions="24ft bundles", notes="Standard tarps, 4ft stakes",
         status="available"),

    dict(load_id="LD-3004", origin="Kansas City, MO", destination="St. Louis, MO",
         pickup_datetime=_dt(0, 10), delivery_datetime=_dt(0, 20),
         equipment_type="Flatbed", loadboard_rate=900.00, weight=30000,
         commodity_type="Construction Materials", num_of_pieces=10, miles=248,
         dimensions="Various", notes="Jobsite delivery, no dock",
         status="available"),

    # --- Higher-value loads ---
    dict(load_id="LD-4001", origin="New York, NY", destination="Chicago, IL",
         pickup_datetime=_dt(1, 4), delivery_datetime=_dt(2, 16),
         equipment_type="Dry Van", loadboard_rate=3800.00, weight=41000,
         commodity_type="Pharmaceuticals", num_of_pieces=50, miles=790,
         dimensions="48x96x102", notes="Temperature-sensitive, team drivers preferred",
         status="available"),

    dict(load_id="LD-4002", origin="San Francisco, CA", destination="Seattle, WA",
         pickup_datetime=_dt(2, 6), delivery_datetime=_dt(3, 10),
         equipment_type="Dry Van", loadboard_rate=2400.00, weight=30000,
         commodity_type="Tech Equipment", num_of_pieces=35, miles=808,
         dimensions="48x96x102", notes="High-value, white-glove handling",
         status="available"),

    dict(load_id="LD-4003", origin="Boston, MA", destination="Philadelphia, PA",
         pickup_datetime=_dt(1, 7), delivery_datetime=_dt(1, 16),
         equipment_type="Dry Van", loadboard_rate=1050.00, weight=22000,
         commodity_type="Medical Supplies", num_of_pieces=42, miles=308,
         dimensions="48x96x102", notes="Priority freight, appointment delivery",
         status="available"),

    dict(load_id="LD-4004", origin="Denver, CO", destination="Salt Lake City, UT",
         pickup_datetime=_dt(4, 5), delivery_datetime=_dt(5, 10),
         equipment_type="Reefer", loadboard_rate=2100.00, weight=37000,
         commodity_type="Organic Produce", num_of_pieces=26, miles=525,
         dimensions="48x96x102", notes="Temp 36°F, organic certified trailer required",
         status="available"),
]


SEED_CALLS = [
    # Booked calls
    dict(carrier_mc="MC-382635", carrier_name="Swift Transport LLC", carrier_dot="1234567",
         load_id="LD-1001", outcome="booked", sentiment="positive",
         initial_rate=2200.00, agreed_price=2050.00,
         counter_offers=[{"round": 1, "carrier_offer": 1800, "our_counter": 2100},
                         {"round": 2, "carrier_offer": 2050, "our_counter": 2050}],
         num_rounds=2, call_duration=245.0, fmcsa_verified=True,
         fmcsa_status="Authorized", extracted_data={"equipment": "Dry Van", "lane": "Dallas-Atlanta"}),

    dict(carrier_mc="MC-491022", carrier_name="Heartland Carriers Inc", carrier_dot="2345678",
         load_id="LD-2001", outcome="booked", sentiment="positive",
         initial_rate=3400.00, agreed_price=3200.00,
         counter_offers=[{"round": 1, "carrier_offer": 2900, "our_counter": 3300},
                         {"round": 2, "carrier_offer": 3200, "our_counter": 3200}],
         num_rounds=2, call_duration=310.0, fmcsa_verified=True,
         fmcsa_status="Authorized", extracted_data={"equipment": "Reefer", "lane": "Fresno-Denver"}),

    dict(carrier_mc="MC-558190", carrier_name="Pacific Freight Lines", carrier_dot="3456789",
         load_id="LD-1003", outcome="booked", sentiment="neutral",
         initial_rate=950.00, agreed_price=950.00,
         counter_offers=[], num_rounds=0, call_duration=120.0,
         fmcsa_verified=True, fmcsa_status="Authorized",
         extracted_data={"equipment": "Dry Van", "lane": "LA-Phoenix", "note": "Accepted immediately"}),

    # Rejected - FMCSA
    dict(carrier_mc="MC-112233", carrier_name="Shady Haulers LLC", carrier_dot="9999999",
         load_id=None, outcome="rejected", sentiment="neutral",
         initial_rate=None, agreed_price=None, counter_offers=None,
         num_rounds=0, call_duration=85.0, fmcsa_verified=False,
         fmcsa_status="Not Authorized",
         extracted_data={"reason": "Operating authority revoked"}),

    # Carrier declined
    dict(carrier_mc="MC-667788", carrier_name="Midwest Express", carrier_dot="4567890",
         load_id="LD-1002", outcome="carrier_declined", sentiment="neutral",
         initial_rate=3100.00, agreed_price=None,
         counter_offers=[], num_rounds=0, call_duration=180.0,
         fmcsa_verified=True, fmcsa_status="Authorized",
         extracted_data={"reason": "Rate too low, wants $3800+"}),

    # No match
    dict(carrier_mc="MC-778899", carrier_name="Desert Sun Transport", carrier_dot="5678901",
         load_id=None, outcome="no_match", sentiment="neutral",
         initial_rate=None, agreed_price=None, counter_offers=None,
         num_rounds=0, call_duration=95.0, fmcsa_verified=True,
         fmcsa_status="Authorized",
         extracted_data={"wanted_origin": "Tucson, AZ", "wanted_dest": "El Paso, TX",
                         "equipment": "Flatbed"}),

    # Negotiation failed (3 rounds)
    dict(carrier_mc="MC-334455", carrier_name="Lone Star Logistics", carrier_dot="6789012",
         load_id="LD-3001", outcome="rejected", sentiment="negative",
         initial_rate=1800.00, agreed_price=None,
         counter_offers=[{"round": 1, "carrier_offer": 1200, "our_counter": 1700},
                         {"round": 2, "carrier_offer": 1350, "our_counter": 1600},
                         {"round": 3, "carrier_offer": 1400, "our_counter": 1530}],
         num_rounds=3, call_duration=420.0, fmcsa_verified=True,
         fmcsa_status="Authorized",
         extracted_data={"reason": "Price gap too wide", "equipment": "Flatbed",
                         "lane": "Pittsburgh-Detroit"}),

    # Needs follow-up
    dict(carrier_mc="MC-990011", carrier_name="Eagle Transport Co", carrier_dot="7890123",
         load_id="LD-4001", outcome="needs_follow_up", sentiment="positive",
         initial_rate=3800.00, agreed_price=None,
         counter_offers=[], num_rounds=0, call_duration=200.0,
         fmcsa_verified=True, fmcsa_status="Authorized",
         extracted_data={"reason": "Wants to check driver availability",
                         "callback_requested": True, "equipment": "Dry Van",
                         "lane": "NYC-Chicago"}),
]


async def seed_database(db: AsyncSession):
    """Insert seed data if tables are empty."""
    # Check if loads exist
    count = await db.execute(select(func.count(Load.load_id)))
    if count.scalar() > 0:
        logger.info("Database already seeded — skipping")
        return

    logger.info("Seeding database with %d loads and %d call records...", len(SEED_LOADS), len(SEED_CALLS))

    # Insert loads
    for load_data in SEED_LOADS:
        db.add(Load(**load_data))

    await db.flush()

    # Insert call records
    for call_data in SEED_CALLS:
        db.add(CallRecord(**call_data))

    await db.commit()
    logger.info("Seed data inserted successfully")
