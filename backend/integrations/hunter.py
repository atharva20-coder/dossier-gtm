"""Hunter.io Integration for email finding and domain search.

Provides 50 free searches/verifications per month on the free tier.
"""

# `X | None` in an annotation is a syntax error before Python 3.10, and this
# app runs on 3.9. The future import defers annotation evaluation so the
# modern spelling works on both.
from __future__ import annotations
import logging
from typing import Any

import aiohttp
from .. import config
from . import provider

log = logging.getLogger(__name__)

BASE_URL = "https://api.hunter.io/v2"


class HunterError(Exception):
    pass


async def _request(session: aiohttp.ClientSession, endpoint: str, params: dict) -> dict[str, Any]:
    url = f"{BASE_URL}{endpoint}"
    params["api_key"] = config.HUNTER_API_KEY
    async with session.get(url, params=params) as response:
        data = await response.json()
        if not response.ok:
            # A refusal is re-raised as such; only a 404 falls through as
            # "Hunter answered and had no match".
            provider.raise_for("Hunter", response.status)
            errors = data.get("errors", [])
            msg = errors[0].get("details", str(errors)) if errors else "Unknown error"
            raise HunterError(f"Hunter API error {response.status}: {msg}")
        return data.get("data", {})


async def find_email(domain: str, first_name: str, last_name: str) -> dict[str, Any] | None:
    """Find the most likely email for a person at a company."""
    if not config.HUNTER_API_KEY:
        return None
        
    params = {
        "domain": domain,
        "first_name": first_name,
        "last_name": last_name
    }
    
    async with provider.session() as session:
        try:
            data = await _request(session, "/email-finder", params)
            return data
        except provider.Refused:
            raise
        except HunterError as e:
            log.warning("Hunter had no match: %s", e)
            return None


async def verify_email(email: str) -> dict[str, Any] | None:
    """Verify deliverability of an email address."""
    if not config.HUNTER_API_KEY:
        return None
        
    params = {"email": email}
    
    async with provider.session() as session:
        try:
            return await _request(session, "/email-verifier", params)
        except Exception as e:
            log.error(f"Hunter verify email failed: {e}")
            return None


async def health() -> tuple[bool, str]:
    """Whether the key works, and how much of the month is left.

    `/account` costs nothing, so the answer names the remaining budget rather
    than just "ok" — running out mid-campaign is the failure worth seeing
    coming, and it is the difference between a run that degrades and a run you
    knew would degrade.
    """
    if not config.HUNTER_API_KEY:
        return False, "Not configured (no key)"
    try:
        async with provider.session() as session:
            acct = await _request(session, "/account", {})
        req = acct.get("requests") or {}
        parts = []
        for name in ("searches", "verifications"):
            row = req.get(name) or {}
            used, avail = row.get("used"), row.get("available")
            if avail is not None:
                parts.append(f"{int(avail) - int(used or 0)} {name} left")
        return True, ", ".join(parts) or "ok"
    except provider.Refused as e:
        return False, str(e)
    except Exception as e:
        return False, provider.reason_for_error("Hunter", e)
