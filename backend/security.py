"""Access control and hardening.

WHY THIS EXISTS
---------------
Every endpoint in this app either costs money or exposes data. `/api/runs`
spends Tavily and Gemini credits on each call, `/api/export` returns every
prospect and draft as a CSV, and `/api/config` holds the sender's pitch. On a
public URL with no gate, all of that belongs to whoever finds the link — the
first symptom being an exhausted search quota, and the second being someone
else's prospect list in a stranger's hands.

THE GATE
--------
One shared access key, set as `APP_ACCESS_KEY`. A correct key exchanges for a
signed, HttpOnly session cookie; every API request after that carries the
cookie. It is deliberately modest — one team, one key — because real accounts
would mean user tables, password resets and email, none of which this tool has
any use for yet.

If `APP_ACCESS_KEY` is unset the gate is OPEN, which is what makes local
development bearable. That is a real footgun, so it is logged loudly at
startup rather than left silent: an app that is accidentally public should say
so every time it boots.

WHAT THIS IS NOT
----------------
Not multi-tenant. Everyone holding the key sees the same prospects, because
there is one database and no owner column. Adding accounts later means adding
that column first — the gate is not what would need rewriting.
"""
from __future__ import annotations

import hmac
import logging
import secrets
import time
from hashlib import sha256

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from . import config

log = logging.getLogger(__name__)

COOKIE = "dossier_session"

# Paths reachable without a session: the gate itself, the liveness probe, and
# the static frontend (which must load in order to show the key prompt).
PUBLIC_PATHS = {"/api/auth", "/api/auth/status", "/api/ping"}


def enabled() -> bool:
    """Whether a gate exists at all.

    Either credential turns it on. The Gmail app password is preferred because
    it ties the person signing in to the mailbox messages are sent from — you
    cannot log in as someone whose mail you cannot send.
    """
    return bool(config.GMAIL_APP_PASSWORD or config.APP_ACCESS_KEY)


def _sign(expires: int) -> str:
    msg = str(expires).encode()
    mac = hmac.new(config.SESSION_SECRET.encode(), msg, sha256).hexdigest()
    return f"{expires}.{mac}"


def issue() -> str:
    """A session token valid for SESSION_TTL_S."""
    return _sign(int(time.time()) + config.SESSION_TTL_S)


def valid(token: str | None) -> bool:
    """Constant-time check of a session cookie.

    The signature covers the expiry, so a client cannot extend its own session
    by editing the cookie — the usual mistake with hand-rolled tokens.
    """
    if not token or "." not in token:
        return False
    expires, _, _mac = token.partition(".")
    try:
        if int(expires) < time.time():
            return False
    except ValueError:
        return False
    return hmac.compare_digest(token, _sign(int(expires)))


def key_matches(supplied: str) -> bool:
    """Compare against the configured key without leaking length via timing."""
    return bool(supplied) and bool(config.APP_ACCESS_KEY) and \
        hmac.compare_digest(supplied, config.APP_ACCESS_KEY)


def credentials_match(email: str, password: str) -> bool:
    """Check a sign-in against the Gmail account this app sends from.

    Signing in with the sending mailbox's own credential means there is one
    identity rather than two: whoever is logged in is demonstrably the person
    the messages will come from. Gmail app passwords are shown with spaces for
    readability, so they are stripped before comparison — a paste that keeps
    them should not be rejected as wrong.

    Both halves are compared in constant time. Comparing the address with `==`
    would leak, through timing, how much of a guessed address was right.
    """
    if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD):
        return False
    supplied_email = (email or "").strip().lower()
    supplied_pw = (password or "").replace(" ", "")
    ok_email = hmac.compare_digest(supplied_email, config.GMAIL_ADDRESS.strip().lower())
    ok_pw = hmac.compare_digest(supplied_pw, config.GMAIL_APP_PASSWORD)
    # Both are evaluated before returning, so a wrong address and a wrong
    # password take the same time.
    return ok_email and ok_pw


def is_https(request: Request) -> bool:
    """Whether the ORIGINAL request reached the user over HTTPS.

    Vercel and Railway terminate TLS upstream and forward plain HTTP, so
    `request.url.scheme` reports "http" for a request the browser made over
    HTTPS. The forwarded header is what carries the truth, and marking the
    session cookie `Secure` depends on getting this right in both directions:
    omit it on real HTTPS and the cookie can leak over a downgraded connection;
    set it on plain HTTP and the browser silently never sends it back, which
    presents as a login that appears to succeed and changes nothing.
    """
    forwarded = request.headers.get("x-forwarded-proto", "")
    if forwarded:
        return forwarded.split(",")[0].strip() == "https"
    return request.url.scheme == "https"


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    # The frontend itself is public; the data behind it is not.
    return not path.startswith("/api/")


async def gate(request: Request, call_next):
    """Reject unauthenticated API calls, and set baseline response headers."""
    if enabled() and not is_public(request.url.path):
        if not valid(request.cookies.get(COOKIE)):
            return JSONResponse({"detail": "Not authorised."}, status_code=401)

    response = await call_next(request)

    # Caching, which the frontend build makes simple: asset filenames contain a
    # content hash, so they can be cached forever and a new build produces new
    # names. index.html must NOT be cached — it is the only file that names
    # those hashes, and a stale copy keeps loading a bundle that has been
    # replaced or deleted, which presents as the app ignoring recent changes.
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers.setdefault(
            "Cache-Control", "public, max-age=31536000, immutable")
    elif path.startswith("/api/"):
        # No-store, not merely no-cache. An API response with no Cache-Control
        # at all is heuristically cacheable, and browsers do cache it: a run
        # re-fetched right after the assistant rewrote its draft came back as
        # the copy from before the rewrite, so the new message never appeared
        # and a reload "lost" it. Nothing this API returns is worth re-serving.
        response.headers.setdefault("Cache-Control", "no-store")
    else:
        response.headers.setdefault("Cache-Control", "no-cache, must-revalidate")

    # Baseline hardening. The app serves its own frontend from the same origin
    # and loads nothing from anywhere else, so a strict policy costs nothing.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    )
    return response


async def enforce_body_limit(request: Request) -> None:
    """Reject oversized uploads before they are read into memory.

    `MAX_ROWS_PER_UPLOAD` caps rows, but that check happens after pandas has
    already parsed the file — a 200MB spreadsheet would exhaust the function's
    memory before ever reaching it.
    """
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"That file is larger than the "
                 f"{config.MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit.")


def safe_reason(e: BaseException) -> str:
    """A failure message safe to store on the run and show in the UI.

    Exception text from a database or HTTP client routinely contains the
    connection string, which means the password. Runs are displayed, exported
    to CSV and kept, so the type is recorded and the detail goes to the log
    where the operator can see it and the user cannot.
    """
    log.exception("run failure: %s", e)
    return type(e).__name__


def warn_if_open() -> None:
    if not enabled():
        log.warning(
            "APP_ACCESS_KEY is not set — the API is OPEN. Anyone who can reach "
            "this URL can spend your search and model credits and read every "
            "prospect. Set APP_ACCESS_KEY before exposing this publicly.")
    if config.SESSION_SECRET == config.SESSION_SECRET_FALLBACK:
        log.warning(
            "SESSION_SECRET is not set — sessions are signed with a key that "
            "changes on restart, so everyone is logged out on every deploy.")


def _demo() -> None:
    """ponytail: run with `python -m backend.security`."""
    config.SESSION_SECRET = "test-secret"
    t = issue()
    assert valid(t)
    assert not valid(None) and not valid("") and not valid("garbage")
    assert not valid("nonsense.deadbeef"), "unparseable expiry"

    # An expired token is refused even though its signature is genuine.
    assert not valid(_sign(int(time.time()) - 1))

    # The expiry is signed, so it cannot be extended by editing the cookie.
    expires, _, mac = t.partition(".")
    assert not valid(f"{int(expires) + 99999}.{mac}")

    # A different secret cannot mint a valid token.
    real, config.SESSION_SECRET = t, "other-secret"
    assert not valid(real)
    config.SESSION_SECRET = "test-secret"

    config.APP_ACCESS_KEY = "s3cret"
    assert key_matches("s3cret")
    assert not key_matches("wrong") and not key_matches("")

    config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD = "Me@Gmail.com", "abcdefghijklmnop"
    assert credentials_match("me@gmail.com", "abcdefghijklmnop"), "address is case-insensitive"
    assert credentials_match(" me@gmail.com ", "abcd efgh ijkl mnop"), \
        "app passwords are displayed with spaces; a paste keeping them must work"
    assert not credentials_match("me@gmail.com", "wrong-password")
    assert not credentials_match("someone@else.com", "abcdefghijklmnop")
    assert not credentials_match("", "")
    config.GMAIL_ADDRESS = config.GMAIL_APP_PASSWORD = ""
    assert not credentials_match("me@gmail.com", "abcdefghijklmnop"), \
        "no configured mailbox means no sign-in"

    class _Req:
        def __init__(self, headers, scheme):
            self.headers, self.url = headers, type("U", (), {"scheme": scheme})()

    assert is_https(_Req({"x-forwarded-proto": "https"}, "http")), "proxy-terminated TLS"
    assert is_https(_Req({"x-forwarded-proto": "https,http"}, "http")), "first hop wins"
    assert not is_https(_Req({"x-forwarded-proto": "http"}, "http"))
    assert is_https(_Req({}, "https")), "direct TLS"
    assert not is_https(_Req({}, "http")), "plain local http"

    assert is_public("/") and is_public("/assets/app.js") and is_public("/api/ping")
    assert not is_public("/api/runs") and not is_public("/api/export")

    assert safe_reason(ValueError("password=hunter2 in dsn")) == "ValueError", \
        "detail must not reach the stored reason"
    assert secrets.compare_digest("a", "a")
    print("security checks passed")


if __name__ == "__main__":
    _demo()
