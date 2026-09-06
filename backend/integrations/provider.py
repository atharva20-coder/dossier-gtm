"""Shared rules for the optional enrichment providers.

WHY THIS EXISTS
---------------
Hunter and the verifier are both optional: the app must behave the
same whether a key is absent, wrong, expired, or simply out of quota. Each of
those is a different sentence to show a user and the same behaviour underneath —
skip this provider, carry on with the next.

They were each catching bare `Exception` and logging "failed", which satisfies
"does not crash" and nothing else. Two things were missing and both are the
difference between degrading and limping:

  * A TIMEOUT. aiohttp's default is five minutes. A provider that accepts the
    connection and never answers would stall the pipeline for five minutes per
    contact — forty contacts is three hours of a run that looks alive. An
    expired key fails fast; a black-holed network does not.
  * A REASON. "Hunter returned nothing" and "Hunter rejected the key" lead to
    different actions, and a run that cannot tell them apart teaches the user
    nothing about why every address came back derived.
"""
from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger(__name__)

# Generous enough for a slow lookup, short enough that a dead provider costs
# seconds per contact rather than minutes.
TIMEOUT = aiohttp.ClientTimeout(total=12, connect=5)


def session() -> aiohttp.ClientSession:
    """A session that cannot hang."""
    return aiohttp.ClientSession(timeout=TIMEOUT)


class Refused(Exception):
    """The provider would not answer — bad key, no quota, or an outage.

    Distinct from returning nothing, which means the provider answered and had
    no match. Both end with the next step being tried, but only one of them is
    the user's to fix, and a caller that cannot tell them apart reports "no
    match" for an expired key.
    """


def reason_for_status(name: str, status: int) -> str:
    """What an HTTP status from a provider means, in words worth showing.

    Kept coarse on purpose: the user's next action is the same for 401 and 403
    (check the key) and the same for 402 and 429 (wait or upgrade), and a
    literal status code teaches nobody anything.
    """
    if status in (401, 403):
        return f"{name} rejected the key — check it has not expired"
    if status in (402, 429):
        return f"{name} is out of quota for now"
    if status in BAD_INPUT:
        return f"{name} had no match"
    if 500 <= status < 600:
        return f"{name} is having trouble at their end"
    return f"{name} refused the request ({status})"


def reason_for_error(name: str, exc: BaseException) -> str:
    """The same, for a failure that never produced a status."""
    import asyncio

    if isinstance(exc, (asyncio.TimeoutError, aiohttp.ServerTimeoutError)):
        return f"{name} did not answer in time"
    if isinstance(exc, aiohttp.ClientConnectorError):
        return f"{name} could not be reached"
    return f"{name} failed ({type(exc).__name__})"


# Statuses that mean "this request was no good", not "this provider is no
# good". Nothing the user can fix with a key, so they must not be reported as
# refusals — a nonexistent domain returns 400, and calling that a key problem
# sends someone to check a key that is fine.
BAD_INPUT = {400, 404, 422}


def raise_for(name: str, status: int) -> None:
    """Refuse on anything that is not the request's own fault."""
    if status in BAD_INPUT:
        return
    raise Refused(reason_for_status(name, status))


def _demo() -> None:
    """ponytail: `python -m backend.integrations.provider`. No network."""
    import asyncio

    assert "expired" in reason_for_status("Hunter", 401)
    assert "expired" in reason_for_status("Hunter", 403)
    assert "quota" in reason_for_status("Hunter", 429)
    assert "quota" in reason_for_status("Hunter", 402)
    assert "no match" in reason_for_status("Hunter", 404)
    assert "their end" in reason_for_status("Hunter", 503)
    assert "418" in reason_for_status("Hunter", 418)
    print("ok  a status becomes an action, not a code")

    assert "in time" in reason_for_error("Hunter", asyncio.TimeoutError())
    assert "TypeError" in reason_for_error("Hunter", TypeError("x"))
    print("ok  a failure with no status still names itself")

    # The timeout is the point of this module: without it a provider that
    # accepts the connection and never answers stalls every contact.
    assert TIMEOUT.total and TIMEOUT.total <= 20
    assert TIMEOUT.connect and TIMEOUT.connect <= 10
    print("ok  no request can outlive the timeout")

    # A 404 is an answer; everything else is a refusal the caller must be able
    # to report as such.
    # A bad request is the request's fault, not the provider's: a nonexistent
    # domain returns 400, and reporting that as a key problem sends someone to
    # check a key that is fine.
    for ok in (400, 404, 422):
        raise_for("Hunter", ok)
    for status in (401, 403, 402, 429, 500):
        try:
            raise_for("Hunter", status)
            raise AssertionError(f"{status} should have refused")
        except Refused:
            pass
    print("ok  a refusal is distinguishable from an empty answer")

    print("all provider checks passed")


if __name__ == "__main__":
    _demo()
