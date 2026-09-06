"""Sending the message for real, over Gmail SMTP.

THIS IS THE ONE IRREVERSIBLE THING THE APP DOES
------------------------------------------------
Everything else can be re-run, re-judged or rewritten. A sent email cannot be
unsent, and it lands in front of a real person who did not ask for it. So the
rules here are stricter than anywhere else in the codebase, and they are
enforced in the send path rather than left to the UI:

  * Nothing is ever sent automatically. There is no scheduler, no batch send,
    no "send all". Every message is one deliberate act.
  * A prospect flagged customer, open opportunity, competitor, already
    contacted or do-not-contact is refused outright — the same relationships
    that stop research from running stop a message from going out.
  * A run that has already been sent is refused unless the caller explicitly
    says it is a resend, because the recipient cannot unread the first one.
  * An address that does not parse is refused before the connection opens.

WHY SMTP AND AN APP PASSWORD
----------------------------
Gmail's API would mean an OAuth consent flow, token storage and refresh — real
work for the same outcome at this stage. An app password over SMTP with STARTTLS
is a few lines of standard library and no new dependency. The trade is that the
credential is long-lived and equivalent to mail-send access on that account, so
it lives in the environment and never in the database.

`smtplib` is blocking, so it runs in a thread. A synchronous socket in the event
loop would stall every other request for the duration of the send.
"""
from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from .. import config
from . import reachability

log = logging.getLogger(__name__)

# Relationships that must never receive a cold message. The same list the
# pipeline uses to refuse research, applied again at the door.
BLOCKED_RELATIONSHIPS = {
    "customer": "already a customer — a cold message would damage the account",
    "open_opp": "an opportunity is already open — this would cut across it",
    "competitor": "competitor — this would hand them the pitch",
    "contacted": "contacted recently — a second cold touch reads as spam",
    "do_not_contact": "on the do-not-contact list",
}

# Deliberately permissive: address validation is for catching typos and empty
# fields, not for adjudicating the RFC. The server rejects what it dislikes.
_ADDRESS = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$")


class SendRefused(Exception):
    """The message must not be sent. Carries a reason fit to show the user."""


class SendFailed(Exception):
    """The message could not be sent. Nothing reached the recipient."""


def configured() -> bool:
    return bool(config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD)


def valid_address(address: str) -> bool:
    _, addr = parseaddr((address or "").strip())
    return bool(addr) and bool(_ADDRESS.match(addr))


def check(run: dict, to: str, resend: bool = False) -> str:
    """Everything that must be true before a connection is opened.

    Returns the address to send to. Raises SendRefused with a reason the user
    can act on. Kept apart from the sending itself so it can be called to show
    the user why a button is disabled, without side effects.
    """
    if not configured():
        raise SendRefused(
            "Gmail is not connected. Add GMAIL_ADDRESS and GMAIL_APP_PASSWORD.")

    address = (to or run.get("email") or "").strip()
    if not address:
        raise SendRefused("No email address for this person.")
    if not valid_address(address):
        raise SendRefused(f"{address!r} does not look like an email address.")

    # Local grading only. The DNS half of it is a blocking socket read, and
    # this function is called from request handlers to explain a disabled
    # button — see `check_async` for the full check the send path uses.
    verdict = reachability.grade(address, name=run.get("name") or "",
                                 company=run.get("company") or "", check_dns=False)
    if not verdict["sendable"]:
        raise SendRefused(f"{verdict['summary']} — {' '.join(verdict['reasons'])}")

    relationship = (run.get("relationship") or "").strip().lower()
    if relationship in BLOCKED_RELATIONSHIPS:
        raise SendRefused(f"Not sending — {BLOCKED_RELATIONSHIPS[relationship]}.")

    if not (run.get("draft_body") or "").strip():
        raise SendRefused("There is no message to send.")

    if run.get("sent_at") and not resend:
        raise SendRefused(
            f"Already sent to {run.get('sent_to')}. Confirm again to send another.")

    return address


async def check_async(run: dict, to: str, resend: bool = False) -> str:
    """`check`, plus the one lookup that needs the network.

    An F here is a domain that publishes no mail servers, which is nearly always
    a typo in a hand-typed address — the failure that otherwise surfaces as a
    bounce hours later. The lookup runs in a thread because it is a blocking
    socket read, and this is called from the request path.
    """
    address = check(run, to, resend)
    verdict = await asyncio.to_thread(
        reachability.grade, address,
        name=run.get("name") or "", company=run.get("company") or "")
    if not verdict["sendable"]:
        raise SendRefused(f"{verdict['summary']} — {' '.join(verdict['reasons'])}")
    return address


def _send_blocking(to: str, subject: str, body: str, reply_to: str = "") -> str:
    """Open the connection and hand the message over. Returns its Message-ID."""
    msg = EmailMessage()
    msg["From"] = formataddr((config.GMAIL_SENDER_NAME or "", config.GMAIL_ADDRESS))
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    if reply_to:
        msg["Reply-To"] = reply_to
    message_id = make_msgid()
    msg["Message-ID"] = message_id
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT,
                      timeout=config.SMTP_TIMEOUT_S) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        smtp.send_message(msg)
    return message_id


async def send(to: str, subject: str, body: str, reply_to: str = "") -> str:
    """Send one message. Returns its Message-ID.

    Raises SendFailed with a reason that never contains the credential —
    smtplib puts the login in some exception strings, and a failure here is
    shown to the user and written to a log.
    """
    try:
        return await asyncio.to_thread(_send_blocking, to, subject, body, reply_to)
    except smtplib.SMTPAuthenticationError:
        raise SendFailed(
            "Gmail rejected the credentials. An app password is required — a "
            "normal account password will not work, and the account must have "
            "2-step verification on.")
    except smtplib.SMTPRecipientsRefused:
        raise SendFailed(f"Gmail would not accept {to} as a recipient.")
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        log.exception("send failed")
        raise SendFailed(f"Could not reach Gmail ({type(e).__name__}).")


def _demo() -> None:
    """ponytail: `python -m backend.integrations.mailer`. Sends nothing.

    The credential is stubbed here rather than in `__main__` so the checks hold
    under any caller. With it outside, the guards only ran when Gmail happened
    to be configured — which on a machine with no credential is never, and the
    checks silently covered nothing.
    """
    real = (config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
    config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD = "x@gmail.com", "secret"
    try:
        _checks()
    finally:
        config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD = real
    print("mailer checks passed")


def _checks() -> None:
    assert valid_address("a.b+tag@example.co.uk")
    assert valid_address(" Ada <ada@example.com> ")
    assert not valid_address("")
    assert not valid_address("not-an-address")
    assert not valid_address("a@b")                 # no TLD
    assert not valid_address("a@b,c@d.com")         # smuggled second recipient

    base = {"draft_body": "hello", "relationship": "", "email": "a@b.com",
            "name": "", "company": ""}
    assert check(base, "") == "a@b.com"
    assert check(base, "override@c.com") == "override@c.com"

    # A disposable domain is refused without a lookup, so this stays offline.
    try:
        check({**base, "email": "x@mailinator.com"}, "")
        raise AssertionError("a disposable address should have been refused")
    except SendRefused:
        pass

    for rel, _ in BLOCKED_RELATIONSHIPS.items():
        try:
            check({**base, "relationship": rel}, "")
            raise AssertionError(f"{rel} should have been refused")
        except SendRefused:
            pass

    for bad, why in (({**base, "draft_body": "  "}, "empty draft"),
                     ({**base, "email": ""}, "no address"),
                     ({**base, "email": "nope"}, "malformed address"),
                     ({**base, "sent_at": "2026-01-01"}, "already sent")):
        try:
            check(bad, "")
            raise AssertionError(f"{why} should have been refused")
        except SendRefused:
            pass

    # A resend is allowed, but only when asked for explicitly.
    assert check({**base, "sent_at": "2026-01-01"}, "", resend=True) == "a@b.com"

    # Unconfigured, everything is refused — including a resend.
    config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD = "", ""
    try:
        check(base, "")
        raise AssertionError("an unconfigured mailer should refuse")
    except SendRefused:
        pass
    config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD = "x@gmail.com", "secret"


if __name__ == "__main__":
    _demo()
