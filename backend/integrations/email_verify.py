"""QuickEmailVerification API for email validation.

Generous free tier (100 credits/day) for verifying email deliverability.
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

BASE_URL = "https://api.quickemailverification.com/v1"


class QEVError(Exception):
    pass


def enabled() -> bool:
    """Whether verification can run at all.

    Checked by the caller before the result is acted on: an unconfigured
    verifier returns nothing, and treating nothing as "invalid" would silently
    discard every address the waterfall found.
    """
    return bool(config.QEV_API_KEY)


async def verify(email: str) -> dict[str, Any] | None:
    """Verify an email address.
    
    Returns a dict with 'result' (valid, invalid, unknown) and 'reason'.
    """
    if not config.QEV_API_KEY:
        return None
        
    url = f"{BASE_URL}/verify"
    params = {
        "email": email,
        "apikey": config.QEV_API_KEY
    }
    
    async with provider.session() as session:
        try:
            async with session.get(url, params=params) as response:
                data = await response.json()
                if not response.ok:
                    msg = data.get("message", str(data))
                    provider.raise_for("Email verification", response.status)
                    raise QEVError(f"QEV API error {response.status}: {msg}")
                return data
        except provider.Refused:
            raise
        except Exception as e:
            log.error("QEV verify error: %s", e)
            return None


async def health() -> tuple[bool, str]:
    """Whether a key is configured. Deliberately does NOT call the API.

    There is no free quota endpoint — `/v1/quota` returns 404 — so the only way
    to prove the key works is to spend one of the 100 daily verifications. A
    health badge redrawn on every page load would eat the day's allowance to
    say "ok", so this reports configuration and lets the first real
    verification report the truth.
    """
    if not config.QEV_API_KEY:
        return False, "Not configured (no key)"
    return True, "configured — 100 verifications/day"
