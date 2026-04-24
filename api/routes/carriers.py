from fastapi import APIRouter, Query
from typing import Optional
from schemas import CarrierVerification
from services.fmcsa import verify_carrier_by_mc, verify_carrier_by_dot, search_carrier_by_name
import httpx
from config import get_settings

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


@router.get("/debug-raw/{mc_number}")
async def debug_raw_fmcsa(mc_number: str):
    """
    TEMPORARY: Returns the raw FMCSA API response for debugging.
    Remove before production.
    """
    settings = get_settings()
    clean_mc = mc_number.upper().replace("MC-", "").replace("MC", "").strip().lstrip("0")

    # Call both endpoints and return raw JSON
    results = {}

    # 1. Docket number lookup
    try:
        url = f"{settings.fmcsa_base_url}/carriers/docket-number/{clean_mc}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params={"webKey": settings.fmcsa_api_key})
        results["docket_lookup"] = {
            "status_code": resp.status_code,
            "url": str(resp.url),
            "raw_json": resp.json() if resp.status_code == 200 else resp.text[:500],
        }
        # If we got a DOT number, also try the DOT endpoint
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("content", [])
            if content:
                carrier = content[0].get("carrier", content[0])
                dot = carrier.get("dotNumber")
                # List ALL fields returned
                results["all_carrier_fields"] = list(carrier.keys())
                results["carrier_raw"] = carrier

                if dot:
                    # 2. DOT number lookup
                    url2 = f"{settings.fmcsa_base_url}/carriers/{dot}"
                    resp2 = await client.get(url2, params={"webKey": settings.fmcsa_api_key})
                    results["dot_lookup"] = {
                        "status_code": resp2.status_code,
                        "raw_json": resp2.json() if resp2.status_code == 200 else resp2.text[:500],
                    }
    except Exception as e:
        results["error"] = str(e)

    return results