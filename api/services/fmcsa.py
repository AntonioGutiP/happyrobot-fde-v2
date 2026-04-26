"""
FMCSA QCMobile API integration.

Eligibility: allowedToOperate == "Y" → eligible.
This field confirms the carrier's USDOT is active and they are
legally permitted to operate. All other data (authority status,
insurance, safety rating) is returned for reference.

IMPORTANT: The FMCSA docs say "allowToOperate" but the actual
API returns "allowedToOperate" (with "ed").
"""

import httpx
import logging
import urllib.parse
from config import get_settings
from schemas import CarrierVerification

logger = logging.getLogger(__name__)

FMCSA_TIMEOUT = 15.0


async def verify_carrier_by_mc(mc_number: str) -> CarrierVerification:
    """Verify carrier by MC (docket) number."""
    settings = get_settings()

    clean_mc = mc_number.upper().replace("MC-", "").replace("MC", "").strip().lstrip("0")

    if not clean_mc:
        return CarrierVerification(
            mc_number=mc_number, is_eligible=False,
            eligibility_reason="Invalid MC number format",
            data_source="validation",
        )

    if not settings.fmcsa_api_key:
        return _unverified_rejection(mc_number, "FMCSA API key not configured")

    url = f"{settings.fmcsa_base_url}/carriers/docket-number/{clean_mc}"
    params = {"webKey": settings.fmcsa_api_key}

    try:
        async with httpx.AsyncClient(timeout=FMCSA_TIMEOUT) as client:
            resp = await client.get(url, params=params)

        logger.info(f"FMCSA MC lookup [{clean_mc}]: status={resp.status_code}")

        if resp.status_code == 404:
            return CarrierVerification(
                mc_number=mc_number, is_eligible=False,
                eligibility_reason="MC number not found in FMCSA database",
                data_source="fmcsa_api",
            )

        if resp.status_code == 401:
            return _unverified_rejection(mc_number, "FMCSA API authentication failed")

        resp.raise_for_status()
        return _parse_response(mc_number, resp.json())

    except httpx.TimeoutException:
        logger.warning(f"FMCSA timeout for MC {clean_mc}")
        return _unverified_rejection(mc_number, "FMCSA API timed out")
    except httpx.HTTPError as e:
        logger.warning(f"FMCSA HTTP error for MC {clean_mc}: {e}")
        return _unverified_rejection(mc_number, "FMCSA API error")
    except Exception as e:
        logger.error(f"FMCSA unexpected error for MC {clean_mc}: {e}")
        return _unverified_rejection(mc_number, "FMCSA service unavailable")


async def verify_carrier_by_dot(dot_number: str) -> CarrierVerification:
    """Verify carrier by DOT number."""
    settings = get_settings()
    clean_dot = dot_number.strip().lstrip("0")

    if not clean_dot or not clean_dot.isdigit():
        return CarrierVerification(
            mc_number=f"DOT-{dot_number}", is_eligible=False,
            eligibility_reason="Invalid DOT number format",
            data_source="validation",
        )

    if not settings.fmcsa_api_key:
        return _unverified_rejection(f"DOT-{clean_dot}", "FMCSA API key not configured")

    url = f"{settings.fmcsa_base_url}/carriers/{clean_dot}"
    params = {"webKey": settings.fmcsa_api_key}

    try:
        async with httpx.AsyncClient(timeout=FMCSA_TIMEOUT) as client:
            resp = await client.get(url, params=params)

        logger.info(f"FMCSA DOT lookup [{clean_dot}]: status={resp.status_code}")

        if resp.status_code in (404, 403):
            return CarrierVerification(
                mc_number=f"DOT-{clean_dot}", is_eligible=False,
                eligibility_reason="DOT number not found or access denied",
                data_source="fmcsa_api",
            )

        resp.raise_for_status()
        return _parse_response(f"DOT-{clean_dot}", resp.json())

    except Exception as e:
        logger.warning(f"FMCSA DOT lookup error: {e}")
        return _unverified_rejection(f"DOT-{clean_dot}", "FMCSA service unavailable")


async def search_carrier_by_name(name: str) -> list[dict]:
    """Search carriers by company name."""
    settings = get_settings()

    if not settings.fmcsa_api_key:
        return []

    encoded_name = urllib.parse.quote(name.strip())
    url = f"{settings.fmcsa_base_url}/carriers/name/{encoded_name}"
    params = {"webKey": settings.fmcsa_api_key}

    try:
        async with httpx.AsyncClient(timeout=FMCSA_TIMEOUT) as client:
            resp = await client.get(url, params=params)

        if resp.status_code != 200:
            return []

        data = resp.json()
        content = data.get("content", [])

        results = []
        for item in content[:10]:
            c = item.get("carrier", item)
            results.append({
                "legal_name": c.get("legalName", ""),
                "dba_name": c.get("dbaName", ""),
                "dot_number": str(c.get("dotNumber", "")),
                "allowed_to_operate": c.get("allowedToOperate", ""),
                "common_authority": c.get("commonAuthorityStatus", ""),
                "city": c.get("phyCity", ""),
                "state": c.get("phyState", ""),
            })
        return results

    except Exception as e:
        logger.warning(f"FMCSA name search error: {e}")
        return []


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_response(mc_number: str, data: dict) -> CarrierVerification:
    """Parse FMCSA response. Eligibility = allowedToOperate == 'Y'."""
    content = data.get("content", [])

    if not content:
        if "dotNumber" in data or "legalName" in data:
            carrier = data
        elif isinstance(data, list) and len(data) > 0:
            carrier = data[0].get("carrier", data[0])
        else:
            return CarrierVerification(
                mc_number=mc_number, is_eligible=False,
                eligibility_reason="No carrier data returned from FMCSA",
                data_source="fmcsa_api",
            )
    else:
        carrier = content[0].get("carrier", content[0])

    # --- Extract using CORRECT field name: allowedToOperate (with "ed") ---
    allowed = carrier.get("allowedToOperate", "N")

    dot = str(carrier.get("dotNumber", ""))
    legal_name = carrier.get("legalName", "")
    dba_name = carrier.get("dbaName")
    oos_date = carrier.get("oosDate")

    city = carrier.get("phyCity", "")
    state = carrier.get("phyState", "")
    street = carrier.get("phyStreet", "")
    address = f"{street}, {city}, {state}" if street else (f"{city}, {state}" if city else "")

    # --- Single eligibility check ---
    is_eligible = allowed == "Y"

    if is_eligible:
        reason = "Carrier is authorized and active — eligible"
    else:
        reason = "Carrier is not authorized to operate"

    return CarrierVerification(
        mc_number=mc_number,
        dot_number=dot,
        legal_name=legal_name,
        dba_name=dba_name if dba_name else None,
        phone=None,
        address=address if address else None,
        allow_to_operate=allowed,
        out_of_service="Y" if oos_date else "N",
        is_eligible=is_eligible,
        eligibility_reason=reason,
        data_source="fmcsa_api",
    )


def _unverified_rejection(mc_number: str, reason: str) -> CarrierVerification:
    """Fallback — always rejects unverified carriers."""
    return CarrierVerification(
        mc_number=mc_number, is_eligible=False,
        eligibility_reason=f"UNVERIFIED — {reason}. Please try again or contact support.",
        data_source="fmcsa_unavailable",
    )
