"""
FMCSA QCMobile API integration.

Endpoints used:
  - /carriers/docket-number/{mc}       → lookup by MC/MX number
  - /carriers/{dotNumber}              → lookup by DOT number
  - /carriers/name/{name}              → lookup by company name
  - /carriers/{dotNumber}/authority     → authority history (optional enrichment)

Eligibility logic:
  - allowToOperate == "Y" AND outOfService != "Y" → eligible
  - Everything else → not eligible, with specific reason
"""

import httpx
import logging
from typing import Optional
from config import get_settings
from schemas import CarrierVerification

logger = logging.getLogger(__name__)

FMCSA_TIMEOUT = 10.0  # seconds


async def verify_carrier_by_mc(mc_number: str) -> CarrierVerification:
    """Primary lookup: verify carrier by MC number via FMCSA API."""
    settings = get_settings()

    # Strip common prefixes: "MC-", "MC", leading zeros
    clean_mc = mc_number.upper().replace("MC-", "").replace("MC", "").strip().lstrip("0")

    if not clean_mc:
        return CarrierVerification(
            mc_number=mc_number,
            is_eligible=False,
            eligibility_reason="Invalid MC number format",
            data_source="validation",
        )

    # If no FMCSA key configured, use mock fallback
    if not settings.fmcsa_api_key:
        logger.warning("FMCSA_API_KEY not set — returning mock response")
        return _mock_carrier(mc_number, reason="FMCSA API key not configured")

    url = f"{settings.fmcsa_base_url}/carriers/docket-number/{clean_mc}"
    params = {"webKey": settings.fmcsa_api_key}

    try:
        async with httpx.AsyncClient(timeout=FMCSA_TIMEOUT) as client:
            resp = await client.get(url, params=params)

        if resp.status_code == 404:
            return CarrierVerification(
                mc_number=mc_number,
                is_eligible=False,
                eligibility_reason="MC number not found in FMCSA database",
                data_source="fmcsa_api",
            )

        if resp.status_code == 401:
            logger.error("FMCSA API authentication failed — check your webkey")
            return _mock_carrier(mc_number, reason="FMCSA API auth failed — using fallback")

        resp.raise_for_status()
        data = resp.json()

        return _parse_fmcsa_response(mc_number, data)

    except httpx.TimeoutException:
        logger.warning("FMCSA API timed out — using mock fallback")
        return _mock_carrier(mc_number, reason="FMCSA API timed out")
    except httpx.HTTPError as e:
        logger.warning(f"FMCSA API error: {e} — using mock fallback")
        return _mock_carrier(mc_number, reason=f"FMCSA API error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error calling FMCSA: {e}")
        return _mock_carrier(mc_number, reason="Unexpected FMCSA error")


async def verify_carrier_by_dot(dot_number: str) -> CarrierVerification:
    """Alternative lookup: verify carrier by DOT number."""
    settings = get_settings()
    clean_dot = dot_number.strip().lstrip("0")

    if not settings.fmcsa_api_key:
        return _mock_carrier(f"DOT-{clean_dot}", reason="FMCSA API key not configured")

    url = f"{settings.fmcsa_base_url}/carriers/{clean_dot}"
    params = {"webKey": settings.fmcsa_api_key}

    try:
        async with httpx.AsyncClient(timeout=FMCSA_TIMEOUT) as client:
            resp = await client.get(url, params=params)

        if resp.status_code == 404:
            return CarrierVerification(
                mc_number=f"DOT-{clean_dot}",
                is_eligible=False,
                eligibility_reason="DOT number not found in FMCSA database",
                data_source="fmcsa_api",
            )

        resp.raise_for_status()
        data = resp.json()
        return _parse_fmcsa_response(f"DOT-{clean_dot}", data)

    except Exception as e:
        logger.warning(f"FMCSA DOT lookup error: {e}")
        return _mock_carrier(f"DOT-{clean_dot}", reason=f"FMCSA API error: {str(e)}")


async def search_carrier_by_name(name: str) -> list[dict]:
    """Search carriers by company name — returns list of matches."""
    settings = get_settings()

    if not settings.fmcsa_api_key:
        return []

    url = f"{settings.fmcsa_base_url}/carriers/name/{name}"
    params = {"webKey": settings.fmcsa_api_key}

    try:
        async with httpx.AsyncClient(timeout=FMCSA_TIMEOUT) as client:
            resp = await client.get(url, params=params)

        if resp.status_code != 200:
            return []

        data = resp.json()
        content = data.get("content", [])

        results = []
        for carrier in content[:10]:  # Cap at 10
            c = carrier.get("carrier", carrier)
            results.append({
                "legal_name": c.get("legalName", ""),
                "dba_name": c.get("dbaName", ""),
                "dot_number": str(c.get("dotNumber", "")),
                "mc_number": str(c.get("mcNumber", "")),
                "allow_to_operate": c.get("allowToOperate", ""),
                "city": c.get("phyCity", ""),
                "state": c.get("phyState", ""),
            })
        return results

    except Exception as e:
        logger.warning(f"FMCSA name search error: {e}")
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_fmcsa_response(mc_number: str, data: dict) -> CarrierVerification:
    """Parse FMCSA JSON response into our CarrierVerification schema."""
    # FMCSA wraps single-carrier responses in {"content": [{"carrier": {...}}]}
    content = data.get("content", [])

    if not content:
        return CarrierVerification(
            mc_number=mc_number,
            is_eligible=False,
            eligibility_reason="No carrier data returned from FMCSA",
            data_source="fmcsa_api",
        )

    # Take first match
    carrier_wrapper = content[0]
    carrier = carrier_wrapper.get("carrier", carrier_wrapper)

    allow = carrier.get("allowToOperate", "N")
    oos = carrier.get("outOfService", "N")
    dot = str(carrier.get("dotNumber", ""))
    mc = str(carrier.get("mcNumber", mc_number))
    legal_name = carrier.get("legalName", "")
    dba_name = carrier.get("dbaName", "")
    phone = carrier.get("telephone", "")

    # Build address
    city = carrier.get("phyCity", "")
    state = carrier.get("phyState", "")
    address = f"{city}, {state}" if city else ""

    # Eligibility decision
    is_eligible = allow == "Y" and oos != "Y"

    if oos == "Y":
        reason = "Carrier is out of service"
    elif allow != "Y":
        reason = f"Carrier not authorized to operate (allowToOperate={allow})"
    else:
        reason = "Carrier is authorized and active"

    return CarrierVerification(
        mc_number=mc,
        dot_number=dot,
        legal_name=legal_name,
        dba_name=dba_name if dba_name else None,
        phone=phone if phone else None,
        address=address if address else None,
        allow_to_operate=allow,
        out_of_service=oos,
        is_eligible=is_eligible,
        eligibility_reason=reason,
        data_source="fmcsa_api",
    )


def _mock_carrier(mc_number: str, reason: str = "") -> CarrierVerification:
    """
    Fallback mock when FMCSA API is unavailable.
    Returns eligible=True with a warning so development/demos can proceed.
    """
    return CarrierVerification(
        mc_number=mc_number,
        dot_number="MOCK-000000",
        legal_name="Mock Carrier (FMCSA Unavailable)",
        is_eligible=True,
        eligibility_reason=f"MOCK RESPONSE — {reason}. Treat as eligible for demo purposes.",
        data_source="mock_fallback",
    )
