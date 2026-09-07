"""Person-level signal provider (Super Carl).

WHY THIS EXISTS
---------------
Web search reaches what is *written about* someone — interviews, podcasts,
conference programmes, press quotes. It does not reach what someone *says
themselves*, because LinkedIn and X block that: LinkedIn has no self-serve API,
and Proxycurl, the main compliant LinkedIn data API, shut down entirely.

That matters because the strongest outreach hook is usually a person publicly
describing a problem they have — and those posts are precisely the content the
open web does not index.

Super Carl exposes this through an API rather than by driving a personal
LinkedIn session, which is the important distinction: tools like PhantomBuster
require your own `li_at` session cookie and carry a documented account-ban risk
to the operator. An API key does not put the user's own account on the line.

FLOW (two calls, both documented at supercarl.ai/docs/endpoints.json)
---------------------------------------------------------------------
    1. POST /api/v1/search/people      -> resolve the person, get a profile id
       accepts linkedin_profile_url | linkedin_username | description
    2. GET  /api/v1/profiles/{id}/text -> their profile text, including posts

DESIGN STANCE
-------------
This is an ENHANCEMENT, never a dependency. No key, a failure, a timeout, or an
empty result all degrade silently back to web-search-only research. The run must
never fail because an optional provider was unavailable.

Everything it returns still passes through the same extraction, grounding and
judgment stages as web results — including grounding verification. Signal from a
paid provider gets no more trust than signal from a news article.
"""
from __future__ import annotations

import json
import logging

import httpx

from .. import config
from ..models import SearchHit

log = logging.getLogger(__name__)

BASE = "https://api.supercarl.ai"

# Hits carrying this prefix were resolved to a specific profile id, so they are
# identity-verified by construction. The extract stage relies on that to tell
# this person's facts apart from a namesake's.
PROFILE_URL_PREFIX = "https://supercarl.ai/profile/"


class PersonSignalUnavailable(Exception):
    """Provider could not be used. Never fatal — research continues without it."""


def enabled() -> bool:
    return bool(config.SUPERCARL_API_KEY)


def _headers() -> dict[str, str]:
    return {
        "X-API-Key": config.SUPERCARL_API_KEY,
        "Content-Type": "application/json",
    }


def _flatten_text(payload: object, depth: int = 0) -> str:
    """Collapse the ProfileText object into plain text.

    The exact shape of ProfileText isn't published beyond "ProfileText object",
    so rather than assume a field layout we walk whatever comes back and collect
    the strings. Resilient to their schema changing, and to it differing from
    what we expect.
    """
    if depth > 6:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (int, float)):
        return ""
    if isinstance(payload, list):
        return "\n".join(t for t in (_flatten_text(v, depth + 1) for v in payload) if t)
    if isinstance(payload, dict):
        parts = []
        for k, v in payload.items():
            if k in ("id", "user_id", "picture", "url", "created_at", "updated_at"):
                continue
            t = _flatten_text(v, depth + 1)
            if t:
                parts.append(f"{k}: {t}" if isinstance(v, str) and len(t) < 200 else t)
        return "\n".join(parts)
    return ""


async def _record(endpoint: str, ok: bool) -> None:
    """Log one paid call. Never raises: accounting must not break research."""
    try:
        from .. import db
        await db.record_provider_call("supercarl", endpoint, ok=ok)
    except Exception:
        log.debug("could not record provider call", exc_info=True)


def _cache_key(name: str, company: str, linkedin_url: str) -> str:
    """What identifies this person for caching.

    A LinkedIn URL identifies someone exactly, so it is preferred. Falling back
    to name plus company matches how the provider is queried when no URL is
    given, which keeps the cache aligned with what was actually asked.
    """
    url = (linkedin_url or "").strip().lower().rstrip("/")
    if "linkedin.com/in/" in url:
        return "li:" + url.split("linkedin.com/in/")[-1].split("?")[0]
    return f"nc:{name.strip().lower()}|{company.strip().lower()}"


def _as_hits(name: str, pid: str, text: str) -> list[SearchHit]:
    words = len(text.split())
    return [SearchHit(
        title=f"{name} — profile and recent posts",
        url=f"{PROFILE_URL_PREFIX}{pid}",
        content=text[:12000],
        score=1.0,
        query="person profile (Super Carl)",
    )] if words else []


async def _resolve_person(
    client: httpx.AsyncClient, name: str, company: str, linkedin_url: str
) -> tuple[str | None, str]:
    """Find the profile id. Returns (profile_id, note)."""
    body: dict[str, object] = {"limit": 3, "include_evidence_text": True}

    if linkedin_url and "linkedin.com/in/" in linkedin_url:
        body["linkedin_profile_url"] = linkedin_url
        how = "LinkedIn URL"
    else:
        body["description"] = f"{name} at {company}".strip() if company else name
        how = "name + company"

    r = await client.post(f"{BASE}/api/v1/search/people", headers=_headers(), json=body)
    await _record("search/people", r.status_code < 400)

    if r.status_code in (401, 403):
        raise PersonSignalUnavailable("Super Carl rejected the API key")
    if r.status_code in (402, 429):
        raise PersonSignalUnavailable(
            "Super Carl allowance exhausted — research continues on web search alone")
    if r.status_code >= 400:
        raise PersonSignalUnavailable(f"Super Carl search returned HTTP {r.status_code}")

    data = r.json()
    users = data.get("users") or []
    if not users:
        reason = data.get("no_results_reason") or "no matching profile"
        # Measured: resolving by URL takes about a second and returns the right
        # person; resolving by description takes ten and routinely returns
        # nobody. When the slow path found nothing, say what would have worked,
        # because the fix is a field the user can fill in.
        hint = "" if how == "LinkedIn URL" else (
            " — their LinkedIn URL resolves them directly, which is both faster "
            "and exact")
        return None, f"no person-level profile found ({reason}){hint}"

    top = users[0]
    pid = top.get("id")
    if not pid:
        return None, "profile matched but carried no id"

    # Guard against confidently matching the wrong person. If we resolved by
    # name alone and the returned profile names a different employer, say so
    # rather than silently attaching a stranger's posts to this prospect.
    note = f"matched by {how}"
    if company and how == "name + company":
        bio = f"{top.get('bio') or ''} {top.get('location') or ''}".lower()
        if company.lower().split()[0] not in bio:
            note += " (company not confirmed in profile — treat with caution)"
    return pid, note


def _text_failure(status: int, body: str) -> str:
    """Explain why the text call failed, in terms someone can act on.

    The provider answers 404 with "Profile text not available yet" for a profile
    it has just resolved successfully: the person IS known, their text has not
    finished being ingested. That is a WAIT, not a failure. Reporting it as
    "returned HTTP 404" sent people looking for a broken key or a wrong URL when
    the answer was to run it again later.
    """
    detail = ""
    try:
        detail = str((json.loads(body) or {}).get("error") or "").strip()
    except (ValueError, AttributeError):
        pass
    if status == 404 and "not available yet" in detail.lower():
        return ("profile found, but the provider has not finished preparing its "
                "text — research continues on web search, and this profile "
                "should resolve on a later run")
    return (f"Super Carl profile text returned HTTP {status}"
            + (f": {detail}" if detail else ""))


async def _profile_text(client: httpx.AsyncClient, pid: str) -> str:
    r = await client.get(
        f"{BASE}/api/v1/profiles/{pid}/text",
        headers=_headers(),
        params={"mode": "full", "posts_limit": config.SUPERCARL_POSTS_LIMIT},
    )
    await _record("profiles/text", r.status_code < 400)
    if r.status_code >= 400:
        raise PersonSignalUnavailable(_text_failure(r.status_code, r.text))
    data = r.json()
    return _flatten_text(data.get("text") or data)


async def fetch(
    name: str, company: str = "", linkedin_url: str = ""
) -> tuple[list[SearchHit], str]:
    """Fetch person-level signal.

    Returns (hits, note). Never raises: every failure path degrades to an empty
    result with an explanatory note, because an optional provider must not be
    able to fail a run.
    """
    if not enabled():
        return [], ""

    from .. import db

    # Cache first, always. Two paid calls per lookup against an allowance of
    # tens means re-running one lead, or researching several colleagues at one
    # company, is where the quota actually goes.
    key = _cache_key(name, company, linkedin_url)
    try:
        cached = await db.get_person_profile(key, config.SUPERCARL_CACHE_DAYS)
    except Exception:
        cached = None          # a cache that is down must not stop research
    if cached and (cached.get("text") or "").strip():
        return _as_hits(name, cached["profile_id"] or "", cached["text"]), (
            f"{cached.get('note') or 'matched'} (from cache)")

    try:
        async with httpx.AsyncClient(timeout=config.SUPERCARL_TIMEOUT_S) as client:
            pid, note = await _resolve_person(client, name, company, linkedin_url)
            if not pid:
                return [], note

            text = await _profile_text(client, pid)
            if not text.strip():
                return [], "profile resolved but returned no readable text"

            try:
                await db.put_person_profile(key, pid, text, note)
            except Exception:
                pass           # research succeeded; failing to cache is not fatal

            hit = SearchHit(
                title=f"{name} — profile and recent posts",
                url=f"{PROFILE_URL_PREFIX}{pid}",
                content=text[:12000],
                score=1.0,
                query="person profile (Super Carl)",
            )
            words = len(text.split())
            return [hit], f"{note}; {words} words of profile text and posts"

    except PersonSignalUnavailable as e:
        log.info("person signal unavailable: %s", e)
        return [], str(e)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        log.info("person signal network error: %s", type(e).__name__)
        return [], f"provider unreachable ({type(e).__name__})"
    except Exception as e:  # noqa: BLE001 — never let an optional provider break a run
        log.warning("person signal unexpected error: %s", e)
        return [], f"provider error ({type(e).__name__})"


async def health(probe: bool = False) -> tuple[bool, str]:
    """Report provider readiness. Costs nothing unless `probe` is set.

    The allowance here is metered in whole calls and is small — tens, not
    thousands. A health check that makes a real search spends one of them, and
    this is shown in the header on every page load, so it would quietly consume
    the entire quota without a single prospect being researched. Configuration
    is checked instead, and an actual probe is something the operator asks for.
    """
    if not enabled():
        return False, "SUPERCARL_API_KEY not set (optional — web search still works)"
    if not probe:
        return True, "key configured (not probed — each check costs an API call)"
    try:
        async with httpx.AsyncClient(timeout=config.SUPERCARL_TIMEOUT_S) as client:
            r = await client.post(
                f"{BASE}/api/v1/search/people",
                headers=_headers(),
                json={"description": "chief operating officer", "limit": 1},
            )
        # A probe is a real search against a small allowance, so it is recorded
        # like any other call. Left uncounted, the usage figure the UI shows
        # drifts below the truth every time someone presses "Check now" — and a
        # budget that under-reports is the one that runs out by surprise.
        await _record("search/people", r.status_code < 400)
        if r.status_code in (401, 403):
            return False, "API key rejected"
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}"
        n = len((r.json().get("users") or []))
        return True, f"ok ({n} result{'' if n == 1 else 's'})"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _demo() -> None:
    """Self-check: 'not ready yet' must not read like 'broken'."""
    waiting = _text_failure(404, '{"error":"Profile text not available yet"}')
    assert "later run" in waiting and "HTTP 404" not in waiting, waiting

    # A genuine failure still names the status, and carries the provider's words.
    gone = _text_failure(404, '{"error":"No such profile"}')
    assert "HTTP 404" in gone and "No such profile" in gone, gone
    assert "HTTP 500" in _text_failure(500, "upstream exploded")
    assert "HTTP 502" in _text_failure(502, "")          # empty body is fine
    assert "HTTP 503" in _text_failure(503, "<html>no json here</html>")
    print("person-signal checks passed")


if __name__ == "__main__":
    _demo()
