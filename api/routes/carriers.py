from fastapi import APIRouter, Query
from typing import Optional
from schemas import CarrierVerification
from services.fmcsa import verify_carrier_by_mc, verify_carrier_by_dot, search_carrier_by_name

router = APIRouter(prefix="/carriers", tags=["Carriers"])


@router.get("/verify/{mc_number}", response_model=CarrierVerification)
async def verify_carrier(mc_number: str):
    """
    Verify carrier eligibility via FMCSA QCMobile API.

    Used by HappyRobot agent as a webhook tool during calls.
    The agent collects the MC number from the carrier and calls this
    endpoint to determine if the carrier is authorized to operate.

    Returns eligibility status with detailed reasoning.
    Falls back to mock data if FMCSA API is unavailable.
    """
    return await verify_carrier_by_mc(mc_number)


@router.get("/verify-dot/{dot_number}", response_model=CarrierVerification)
async def verify_carrier_dot(dot_number: str):
    """
    Alternative verification by DOT number.
    Used when carrier doesn't know their MC number but has DOT#.
    """
    return await verify_carrier_by_dot(dot_number)


@router.get("/search-name")
async def search_by_name(name: str = Query(..., min_length=2, description="Company name to search")):
    """
    Search FMCSA by carrier company name.
    Used when carrier knows neither MC nor DOT number.
    Returns up to 10 matching carriers.
    """
    results = await search_carrier_by_name(name)

    if not results:
        return {"matches": [], "message": "No carriers found matching that name"}

    return {"matches": results, "count": len(results)}
