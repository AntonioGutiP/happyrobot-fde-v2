"""
FMCSA QCMobile API integration.

CRITICAL: The FMCSA docs say "allowToOperate" but the actual API
returns "allowedToOperate" (with "ed"). This was discovered by
inspecting the raw API response for Schneider National (MC-133655).

Actual response fields used for eligibility:
  - allowedToOperate: "Y" or "N"
  - commonAuthorityStatus: "A" (active) or other
  - contractAuthorityStatus: "A" (active) or other
  - brokerAuthorityStatus: "A" (active) or other
  - statusCode: "A" (active) or other
  - oosDate: null (no OOS order) or date string
  - safetyRating: "S" (satisfactory), "U" (unsatisfactory), etc.
  - bipdInsuranceOnFile: insurance amount on file

Eligibility logic:
  - allowedToOperate == "Y"
  - At least one active authority (common or contract)
  - No out-of-service order (oosDate is null)
  All three must be true → eligible
"""

import httpx
import logging
import urllib.parse
from config import get_settings
from schemas import CarrierVerification

logger = logging.getLogger(__name__)

FMCSA_TIMEOUT = 15.0


async def verify_carrier_by_mc(mc_number: str) -> CarrierVerification:
    """Primary lookup: verify carrier by MC (docket) number."""
    settings = get_settings()

    clean_mc = mc_number.upper().replace("MC-", "").replace("MC", "").strip().lstrip("0")

    if not clean_mc:
        return CarrierVerification(
            mc_number=mc_number,
            is_eligible=False,
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
                mc_number=mc_number,
                is_eligible=False,
                eligibility_reason="MC number not found in FMCSA database",
                data_source="fmcsa_api",
            )

        if resp.status_code == 401:
            return _unverified_rejection(mc_number, "FMCSA API authentication failed")

        resp.raise_for_status()
        data = resp.json()
        return _parse_fmcsa_response(mc_number, data)

    except httpx.TimeoutException:
        logger.warning(f"FMCSA API timed out for MC {clean_mc}")
        return _unverified_rejection(mc_number, "FMCSA API timed out — unable to verify")
    except httpx.HTTPError as e:
        logger.warning(f"FMCSA API error for MC {clean_mc}: {e}")
        return _unverified_rejection(mc_number, "FMCSA API error — unable to verify")
    except Exception as e:
        logger.error(f"Unexpected FMCSA error for MC {clean_mc}: {e}")
        return _unverified_rejection(mc_number, "FMCSA service unavailable")


async def verify_carrier_by_dot(dot_number: str) -> CarrierVerification:
    """Alternative lookup by DOT number."""
    settings = get_settings()
    clean_dot = dot_number.strip().lstrip("0")

    if not clean_dot or not clean_dot.isdigit():
        return CarrierVerification(
            mc_number=f"DOT-{dot_number}",
            is_eligible=False,
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

        if resp.status_code == 404:
            return CarrierVerification(
                mc_number=f"DOT-{clean_dot}",
                is_eligible=False,
                eligibility_reason="DOT number not found in FMCSA database",
                data_source="fmcsa_api",
            )

        if resp.status_code in (401, 403):
            return _unverified_rejection(f"DOT-{clean_dot}", "FMCSA API access denied for DOT lookup")

        resp.raise_for_status()
        data = resp.json()
        return _parse_fmcsa_response(f"DOT-{clean_dot}", data)

    except httpx.TimeoutException:
        return _unverified_rejection(f"DOT-{clean_dot}", "FMCSA API timed out")
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

def _parse_fmcsa_response(mc_number: str, data: dict) -> CarrierVerification:
    """
    Parse FMCSA response using the ACTUAL field names from the API.

    The API wraps responses in {"content": [{"carrier": {...}}]}.
    """
    content = data.get("content", [])

    if not content:
        if "dotNumber" in data or "legalName" in data:
            carrier = data
        elif isinstance(data, list) and len(data) > 0:
            carrier = data[0].get("carrier", data[0])
        else:
            return CarrierVerification(
                mc_number=mc_number,
                is_eligible=False,
                eligibility_reason="No carrier data returned from FMCSA",
                data_source="fmcsa_api",
            )
    else:
        carrier_wrapper = content[0]
        carrier = carrier_wrapper.get("carrier", carrier_wrapper)

    # --- Extract fields using CORRECT names from actual API ---
    allowed = carrier.get("allowedToOperate", "N")       # "Y" or "N"
    common_auth = carrier.get("commonAuthorityStatus", "")  # "A" = Active
    contract_auth = carrier.get("contractAuthorityStatus", "")
    broker_auth = carrier.get("brokerAuthorityStatus", "")
    status_code = carrier.get("statusCode", "")           # "A" = Active
    oos_date = carrier.get("oosDate")                     # null = no OOS
    safety_rating = carrier.get("safetyRating", "")       # "S" = Satisfactory

    dot = str(carrier.get("dotNumber", ""))
    legal_name = carrier.get("legalName", "")
    dba_name = carrier.get("dbaName", "")

    # Build address
    city = carrier.get("phyCity", "")
    state = carrier.get("phyState", "")
    street = carrier.get("phyStreet", "")
    address = f"{street}, {city}, {state}" if street else (f"{city}, {state}" if city else "")

    # Insurance info
    bipd_on_file = carrier.get("bipdInsuranceOnFile", "0")

    # Fleet info
    total_drivers = carrier.get("totalDrivers", 0)
    total_power_units = carrier.get("totalPowerUnits", 0)

    # --- Eligibility decision ---
    has_active_authority = common_auth == "A" or contract_auth == "A"
    is_allowed = allowed == "Y"
    is_not_oos = oos_date is None

    is_eligible = is_allowed and has_active_authority and is_not_oos

    # Build detailed reason
    if not is_allowed:
        reason = "Carrier is not allowed to operate (allowedToOperate=N)"
    elif not has_active_authority:
        reason = f"No active operating authority (common={common_auth}, contract={contract_auth})"
    elif not is_not_oos:
        reason = f"Carrier has an out-of-service order (date: {oos_date})"
    else:
        reason = "Carrier is authorized and active — eligible"

    # Build authority summary for the response
    auth_summary = f"common={common_auth}, contract={contract_auth}, broker={broker_auth}"

    return CarrierVerification(
        mc_number=mc_number,
        dot_number=dot,
        legal_name=legal_name,
        dba_name=dba_name if dba_name else None,
        phone=None,  # Not returned by docket-number endpoint
        address=address if address else None,
        allow_to_operate=allowed,
        out_of_service="Y" if oos_date else "N",
        is_eligible=is_eligible,
        eligibility_reason=reason,
        data_source="fmcsa_api",
    )


def _unverified_rejection(mc_number: str, reason: str) -> CarrierVerification:
    """
    Safe fallback — ALWAYS rejects.
    A freight brokerage cannot accept unverified carriers.
    """
    return CarrierVerification(
        mc_number=mc_number,
        is_eligible=False,
        eligibility_reason=f"UNVERIFIED — {reason}. Please try again or contact support.",
        data_source="fmcsa_unavailable",
    )