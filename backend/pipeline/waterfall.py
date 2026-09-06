"""Email Enrichment Waterfall.

Finds an email for a contact, cheapest source first.
1. Hunter email-finder (1 search credit) — a real, looked-up address
2. Domain convention (free) — arithmetic, never a lookup
Then verification, which can clear an address it says would bounce.
"""

# `X | None` in an annotation is a syntax error before Python 3.10, and this
# app runs on 3.9. The future import defers annotation evaluation so the
# modern spelling works on both.
from __future__ import annotations
import asyncio
import logging
import re
from typing import Any

from .. import config
from ..integrations import email_verify, hunter, provider, reachability

log = logging.getLogger(__name__)


async def enrich_email(name: str, company: str, domain: str) -> dict[str, Any]:
    """Find and verify an email for a person, trying multiple methods."""
    result = {
        "email": "",
        "source": "",
        "verified": False,
        "verification_result": {},
        # Set when the domain accepts every address: "valid" is meaningless
        # there, and no amount of verifying will change that.
        "accept_all": False,
        # What each provider actually did. "No key", "rejected the key" and
        # "no match" all end with the next provider being tried, but they are
        # different problems and a run that cannot tell them apart teaches
        # nobody why every address came back derived.
        "attempts": [],
    }

    def step(name: str, outcome: str) -> None:
        result["attempts"].append({"provider": name, "outcome": outcome})

    if not domain:
        step("all", "no company domain to work from")
        return result

    first_name, last_name = _split_name(name)
    if not first_name or not last_name:
        step("all", "no usable first and last name")
        return result

    # 1. Hunter.io (primary, 1 credit)
    if not config.HUNTER_API_KEY:
        step("Hunter", "no key set")
    try:
        h_data = await hunter.find_email(domain, first_name, last_name)
        if h_data and h_data.get("email"):
            result["email"] = h_data["email"]
            result["source"] = "hunter"

            # The status lives under `verification`, not at the top level.
            # Reading it from the top level made this always None, so Hunter's
            # own "valid" was discarded and a paid, correct, verified address
            # fell through to a calculated guess.
            score = h_data.get("score") or 0
            verification = h_data.get("verification") or {}
            status = verification.get("status") or ""

            # `accept_all` means the server accepts every address at the
            # domain, so "valid" there says nothing about this mailbox.
            accept_all = bool(h_data.get("accept_all"))

            # Hunter also returns the person's title and profile. Free with the
            # lookup already paid for, and better than what search inferred.
            result["role"] = h_data.get("position") or ""
            result["linkedin_url"] = h_data.get("linkedin_url") or ""

            if not accept_all and (status == "valid" or score > 90):
                result["verified"] = True
                result["verification_result"] = {
                    "status": status or "valid", "score": score, "from": "hunter"}
                step("Hunter", f"found and verified ({score}/100)")
                return result
            if accept_all:
                # The server accepts every address at this domain, so no
                # verifier can say anything about this one. Spending a
                # verification credit here buys nothing.
                result["accept_all"] = True
                step("Hunter", "found, but the domain accepts all mail — "
                               "no verifier can confirm it")
            else:
                step("Hunter", f"found, unverified — {status or 'no status'}")
            return result
        if config.HUNTER_API_KEY and not result["email"]:
            step("Hunter", "no match")
    except provider.Refused as e:
        # The provider would not answer. That is the user's to fix, and it must
        # not be reported as "no match".
        step("Hunter", str(e))
    except Exception as e:
        log.error("Hunter enrichment error: %s", e)
        step("Hunter", provider.reason_for_error("Hunter", e))

    # 3. Derived Patterns (if API lookups failed)
    #
    # The domain is already known here, so the candidates are computed directly
    # rather than through contacts.resolve_address: that helper re-discovers the
    # domain, which costs a web search per contact — 40 searches to answer a
    # question already answered. The MX check runs in a thread because it is a
    # blocking socket read on the request path.
    if not result["email"]:
        try:
            reachable = await asyncio.to_thread(reachability.has_mx, domain)
            if reachable is False:
                result["source"] = f"{domain} accepts no mail"
                step("Domain convention", f"{domain} accepts no mail")
            else:
                picks = reachability.candidates(name, domain, company=company,
                                                check_dns=False)
                if picks:
                    result["email"] = picks[0]["address"]
                    # Left unverified on purpose: this is the arithmetic of a
                    # name against a domain, and the flag beside it has to keep
                    # saying so all the way to the CSV.
                    result["source"] = f"derived:{picks[0]['pattern']}"
                    result["verification_result"] = {
                        "status": "unverified", "from": "pattern",
                        "grade": picks[0]["grade"], "why": picks[0]["reasons"]}
                    step("Domain convention", f"derived {picks[0]['pattern']}")
                else:
                    step("Domain convention", "no usable name")
        except Exception as e:
            log.error(f"Derived enrichment error: {e}")

    # 4. Verification
    # If we found an email but it wasn't pre-verified (like a high-score Hunter hit)
    if result["email"] and not result["verified"] and not result.get("accept_all") \
            and not email_verify.enabled() and config.HUNTER_API_KEY:
        # Hunter meters verifications separately from searches (100/month vs
        # 50), so this costs nothing from the finder's budget.
        try:
            v = await hunter.verify_email(result["email"])
            status = (v or {}).get("status") or ""
            if status == "valid" and not (v or {}).get("accept_all"):
                result["verified"] = True
                result["verification_result"] = {"status": status, "from": "hunter"}
                step("Verification", "confirmed by Hunter")
            elif status:
                step("Verification", f"Hunter says {status}")
                if status == "invalid":
                    # It would bounce. Better no address than a bad one.
                    result["email"] = ""
                    result["source"] = "Hunter says the address is invalid"
        except provider.Refused as e:
            step("Verification", str(e))
        except Exception as e:
            log.error("Hunter verification error: %s", e)
            step("Verification", provider.reason_for_error("Hunter", e))
    elif not email_verify.enabled() and not config.HUNTER_API_KEY:
        step("Verification", "no key set — the address stays unverified")

    if result["email"] and not result["verified"] and email_verify.enabled():
        try:
            v_data = await email_verify.verify(result["email"])
            if v_data:
                result["verification_result"] = v_data
                status = v_data.get("result", "")
                # Every field comes back as the STRING "true"/"false". Testing
                # them for truthiness would make "false" true — the classic way
                # this API is misread.
                accept_all = v_data.get("accept_all") == "true"
                safe = v_data.get("safe_to_send") == "true"

                if status == "invalid":
                    # It would bounce. Better no address than a bad one.
                    hint = v_data.get("did_you_mean") or ""
                    result["email"] = ""
                    result["source"] = ("looks like a typo for " + hint if hint
                                        else "the verifier says it would bounce")
                    step("Verification", v_data.get("reason") or "invalid"
                         + (f" — did you mean {hint}?" if hint else ""))
                elif status == "valid" and safe and not accept_all:
                    result["verified"] = True
                    step("Verification", "confirmed deliverable")
                elif accept_all:
                    # The domain accepts everything, so "valid" here says
                    # nothing about this mailbox. Marking it verified would
                    # certify an address nobody has confirmed exists — this
                    # API returns "valid" for a name that was invented.
                    result["accept_all"] = True
                    step("Verification", "the domain accepts all mail — "
                                         "cannot be confirmed either way")
                else:
                    step("Verification", f"{status or 'unknown'}"
                         + ("" if safe else ", not safe to send"))
        except provider.Refused as e:
            step("Verification", str(e))
        except Exception as e:
            # A verifier that errored has said nothing about this address.
            # Treating silence as "invalid" would delete a good address every
            # time the key expired.
            log.error("QEV verification error: %s", e)
            step("Verification", provider.reason_for_error("Email verification", e))

    return result


def _split_name(name: str) -> tuple[str, str]:
    """Split a full name into first and last, handling basic edge cases."""
    clean = re.sub(r'\(.*?\)', '', name).strip() # Remove (she/her) etc
    parts = clean.split()
    if len(parts) < 2:
        return clean, ""
    return parts[0], " ".join(parts[1:])
