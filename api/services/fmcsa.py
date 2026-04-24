"""
FMCSA QCMobile API integration.

Endpoints used:
  - /carriers/docket-number/{mc}       → lookup by MC/MX number
  - /carriers/{dotNumber}              → lookup by DOT number
  - /carriers/name/{name}              → lookup by company name

Eligibility logic:
  - allowToOperate == "Y" AND outOfService != "Y" → eligible
  - Everything else → not eligible

Safety principle:
  - If FMCSA is unreachable, carrier is NOT approved.
  - A freight brokerage cannot accept unverified carriers.
  - The fallback is always rejection with a clear reason.
"""

import httpx
import logging
import urllib.parse
from config import get_settings
from schemas import CarrierVerification

logger = logging.getLogger(__name__)

FMCSA_TIMEOUT = 15.0  # seconds — FMCSA can be slow


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

    # If no FMCSA key configured → reject
    if not settings.fmcsa_api_key:
        logger.warning("FMCSA_API_KEY not set — cannot verify carrier")
        return _unverified_rejection(mc_number, "FMCSA API key not configured — cannot verify carrier")

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
            logger.error("FMCSA API authentication failed — check your webkey")
            return _unverified_rejection(mc_number, "FMCSA API authentication failed")

        resp.raise_for_status()
        data = resp.json()

        return _parse_fmcsa_response(mc_number, data)

    except httpx.TimeoutException:
        logger.warning(f"FMCSA API timed out for MC {clean_mc}")
        return _unverified_rejection(mc_number, "FMCSA API timed out — unable to verify carrier")
    except httpx.HTTPError as e:
        logger.warning(f"FMCSA API HTTP error for MC {clean_mc}: {e}")
        return _unverified_rejection(mc_number, "FMCSA API error — unable to verify carrier")
    except Exception as e:
        logger.error(f"Unexpected error calling FMCSA for MC {clean_mc}: {e}")
        return _unverified_rejection(mc_number, "FMCSA service unavailable — unable to verify carrier")


async def verify_carrier_by_dot(dot_number: str) -> CarrierVerification:
    """Alternative lookup: verify carrier by DOT number."""
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

        if resp.status_code == 401:
            return _unverified_rejection(f"DOT-{clean_dot}", "FMCSA API authentication failed")

        resp.raise_for_status()
        data = resp.json()

        return _parse_fmcsa_response(f"DOT-{clean_dot}", data)

    except httpx.TimeoutException:
        logger.warning(f"FMCSA API timed out for DOT {clean_dot}")
        return _unverified_rejection(f"DOT-{clean_dot}", "FMCSA API timed out")
    except httpx.HTTPError as e:
        logger.warning(f"FMCSA DOT lookup HTTP error: {e}")
        return _unverified_rejection(f"DOT-{clean_dot}", "FMCSA API error")
    except Exception as e:
        logger.error(f"FMCSA DOT lookup unexpected error: {e}")
        return _unverified_rejection(f"DOT-{clean_dot}", "FMCSA service unavailable")


async def search_carrier_by_name(name: str) -> list[dict]:
    """Search carriers by company name — returns list of matches."""
    settings = get_settings()

    if not settings.fmcsa_api_key:
        return []

    encoded_name = urllib.parse.quote(name.strip())
    url = f"{settings.fmcsa_base_url}/carriers/name/{encoded_name}"
    params = {"webKey": settings.fmcsa_api_key}

    try:
        async with httpx.AsyncClient(timeout=FMCSA_TIMEOUT) as client:
            resp = await client.get(url, params=params)

        logger.info(f"FMCSA name search [{name}]: status={resp.status_code}")

        if resp.status_code != 200:
            logger.warning(f"FMCSA name search failed: {resp.status_code}")
            return []

        data = resp.json()

        # Handle both response formats
        content = data.get("content", [])
        if not content and isinstance(data, list):
            content = data

        results = []
        for carrier_wrapper in content[:10]:
            c = carrier_wrapper.get("carrier", carrier_wrapper)
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
    # FMCSA wraps responses in {"content": [{"carrier": {...}}]}
    # But DOT lookups may return differently
    content = data.get("content", [])

    if not content:
        # Maybe the data IS the carrier directly (DOT lookup format)
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
        # Standard format: content array
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
        reason = "Carrier is out of service — not eligible"
    elif allow != "Y":
        reason = f"Carrier not authorized to operate (status: {allow})"
    else:
        reason = "Carrier is authorized and active — eligible"

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


def _unverified_rejection(mc_number: str, reason: str) -> CarrierVerification:
    """
    Safe fallback when FMCSA API is unavailable.

    ALWAYS returns is_eligible=False.
    A freight brokerage cannot accept unverified carriers —
    if we can't check FMCSA, the carrier does not pass.
    """
    return CarrierVerification(
        mc_number=mc_number,
        is_eligible=False,
        eligibility_reason=f"UNVERIFIED — {reason}. Please try again or contact support.",
        data_source="fmcsa_unavailable",
    )
