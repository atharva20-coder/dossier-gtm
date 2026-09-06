"""Grading an address before anything is sent to it.

WHY
---
The send path was built to be careful about everything except the one thing it
cannot take back: where the message goes. `mailer.valid_address` answers "is
this shaped like an email", which passes `info@gmial.com` — a shared inbox at a
domain that does not exist. Both mistakes are silent. SMTP accepts the message,
Gmail reports success, and the bounce arrives later in a mailbox nobody reads.

So the address gets graded the way a GTM team grades one: can it receive mail at
all, is it a person or a shared inbox, is it their work account or a personal
one, and does it plausibly belong to the person we researched.

WHAT THIS IS NOT
----------------
Not verification. Confirming an address exists means asking the receiving server
about a specific mailbox, which is a probe most providers refuse, rate-limit, or
quietly answer "yes" to for everything. Every check here is either local or one
DNS lookup, so grading is free, instant, and never touches the recipient.

The grade informs; it blocks only at F, where the address provably cannot
receive mail. A C is often correct to send to.
"""
from __future__ import annotations

import re
import secrets
import socket
import struct
import unicodedata

# Addresses at these domains are the sender's own consumer mail, not a company
# account. Writing to one is not wrong, but it is a different message.
FREE_PROVIDERS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "proton.me", "protonmail.com", "gmx.com", "mail.com", "zoho.com",
    "rediffmail.com", "yandex.com",
}

# Mail sent here is read by a rota, or by nobody.
ROLE_ACCOUNTS = {
    "info", "sales", "support", "admin", "hello", "contact", "help", "team",
    "office", "enquiries", "inquiries", "careers", "jobs", "hr", "billing",
    "accounts", "finance", "legal", "press", "media", "marketing", "noreply",
    "no-reply", "donotreply", "postmaster", "webmaster", "abuse", "privacy",
}

DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "sharklasers.com",
    "getnada.com", "temp-mail.org", "dispostable.com", "maildrop.cc",
}

ADDRESS = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$")

# Grades in order, worst last. A grade is the worst finding, not an average:
# one fatal problem is not offset by three things being fine.
ORDER = ["A", "B", "C", "D", "F"]


def _worst(a: str, b: str) -> str:
    return a if ORDER.index(a) >= ORDER.index(b) else b


def _tokens(text: str) -> set[str]:
    """Lowercase word-ish pieces of a name or company, minus the noise words."""
    stop = {"the", "and", "inc", "ltd", "llc", "pvt", "private", "limited",
            "corp", "co", "technologies", "technology", "labs", "group"}
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 1} - stop


def _ascii(text: str) -> str:
    """Strip accents to their base letters.

    Mail systems address "Iso-Järvenpää" as "iso-jarvenpaa". Splitting on
    [^A-Za-z] instead threw away every accented character AND everything
    between them, turning that surname into "rvenp" — a confidently wrong
    address for a real person.
    """
    return "".join(c for c in unicodedata.normalize("NFKD", text or "")
                   if not unicodedata.combining(c))


def _domain_matches(domain: str, company: str) -> bool:
    """Whether the domain is the company's.

    Compared as slugs — letters and digits only — so "north-wind.com" matches
    "Northwind" and "acme-corp.co.uk" matches "Acme Corp". Token overlap was
    both too loose (zamp-racing.com matched "Zamp") and too strict (a hyphen
    split a one-word company in two), which had the grader contradicting the
    contact finder about the same domain.
    """
    host = (domain or "").lower().removeprefix("www.")
    root = host.rsplit(".", 2)[0] if host.count(".") > 1 else host.split(".")[0]
    slug = lambda t: re.sub(r"[^a-z0-9]", "", (t or "").lower())   # noqa: E731
    want, got = slug(company), slug(root)
    if not want or not got:
        return False
    # Equal, or the company is the leading part of a longer domain
    # ("zampfinance.com" for "Zamp"), which a suffix match would not catch.
    return got == want or got.startswith(want) or want.startswith(got)


def has_mx(domain: str, timeout: float = 2.0) -> bool | None:
    """Whether the domain publishes mail servers.

    A raw DNS query rather than a dependency: this is one packet out, one in,
    and the alternative is adding dnspython to the deployment for forty lines of
    struct parsing. Returns None — not False — when the lookup itself fails, so
    a blocked UDP port or a slow resolver never grades a good address as dead.
    """
    if not domain:
        return None
    try:
        qname = b"".join(bytes([len(p)]) + p for p in domain.encode("idna").split(b".")) + b"\0"
        txid = secrets.randbits(16)
        # Standard query, recursion desired, one question, MX (type 15), IN (1).
        query = struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 15, 1)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(query, ("1.1.1.1", 53))
            data, _ = sock.recvfrom(2048)
        finally:
            sock.close()

        if len(data) < 12 or struct.unpack("!H", data[:2])[0] != txid:
            return None
        rcode = struct.unpack("!H", data[2:4])[0] & 0xF
        if rcode == 3:            # NXDOMAIN — the domain does not exist
            return False
        if rcode != 0:
            return None
        return struct.unpack("!H", data[6:8])[0] > 0     # answer count
    except Exception:             # noqa: BLE001 — any lookup failure is "unknown"
        return None


def grade(address: str, *, name: str = "", company: str = "",
          check_dns: bool = True) -> dict:
    """Grade one address, with the reason for every mark against it."""
    address = (address or "").strip()
    if not address:
        return {"grade": "F", "sendable": False, "reasons": ["No address yet."],
                "summary": "No address"}
    if not ADDRESS.match(address):
        return {"grade": "F", "sendable": False,
                "reasons": ["Not a valid email address."], "summary": "Malformed"}

    local, _, domain = address.lower().rpartition("@")
    reasons: list[str] = []
    g = "A"

    if domain in DISPOSABLE:
        return {"grade": "F", "sendable": False, "summary": "Disposable address",
                "reasons": ["A disposable-mail domain — nobody reads it."]}

    mx = has_mx(domain) if check_dns else None
    if mx is False:
        return {"grade": "F", "sendable": False, "summary": "Cannot receive mail",
                "reasons": [f"{domain} publishes no mail servers — check for a typo."]}
    if mx is None and check_dns:
        reasons.append("Could not confirm the domain accepts mail.")

    # Strip the +tag before matching a person's name against the local part.
    bare = local.split("+")[0]
    if bare in ROLE_ACCOUNTS or bare.split(".")[0] in ROLE_ACCOUNTS:
        g = _worst(g, "D")
        reasons.append("A shared inbox, not a person.")

    free = domain in FREE_PROVIDERS
    if free and company:
        g = _worst(g, "C")
        reasons.append(f"A personal account, not their address at {company}.")

    name_hit = bool(_tokens(name) & _tokens(bare)) if name else False
    domain_hit = _domain_matches(domain, company) if company else False

    if not name_hit and g == "A":
        g = _worst(g, "B")
        reasons.append("Nothing in the address matches their name.")
    if name_hit:
        reasons.append("The address carries their name.")
    if domain_hit:
        reasons.append(f"The domain matches {company}.")
    elif company and not free and g == "A":
        g = _worst(g, "B")
        reasons.append(f"The domain is not obviously {company}.")

    summary = {"A": "Looks like them", "B": "Plausible", "C": "Personal account",
               "D": "Shared inbox", "F": "Do not send"}[g]
    return {"grade": g, "sendable": g != "F", "reasons": reasons, "summary": summary}


# The conventions a company domain almost always follows, most common first.
PATTERNS = [
    ("{first}.{last}", "first.last"),
    ("{first}", "first"),
    ("{f}{last}", "flast"),
    ("{first}{last}", "firstlast"),
    ("{f}.{last}", "f.last"),
    ("{first}_{last}", "first_last"),
    ("{last}.{first}", "last.first"),
]


def candidates(name: str, domain: str, *, company: str = "",
               check_dns: bool = True, limit: int = 4) -> list[dict]:
    """The addresses a domain's convention implies for this person.

    THESE ARE DERIVED, NOT FOUND. Nothing here consulted a database of real
    addresses, because this app has none — every one of these is the arithmetic
    of "their name at their company domain", which is right often enough to be
    a lead and wrong often enough that presenting it as a fact would be a lie.
    So each carries `derived: True`, and the caller is expected to say so.

    The domain is checked for mail servers once, not once per pattern: they all
    share it, and the answer cannot differ between them.
    """
    domain = (domain or "").strip().lower().lstrip("@")
    parts = [p for p in re.split(r"[^a-z0-9]+", _ascii(name).lower()) if p]
    if not domain or "." not in domain or len(parts) < 1:
        return []

    # A domain that cannot receive mail makes every pattern on it worthless.
    if check_dns and has_mx(domain) is False:
        return []

    first, last = parts[0], (parts[-1] if len(parts) > 1 else "")
    out: list[dict] = []
    seen: set[str] = set()
    for template, label in PATTERNS:
        if not last and "{last}" in template:
            continue
        local = template.format(first=first, last=last, f=first[:1])
        address = f"{local}@{domain}"
        if address in seen:
            continue
        seen.add(address)
        # DNS was settled above; grading each one again would repeat the lookup.
        verdict = grade(address, name=name, company=company, check_dns=False)
        out.append({**verdict, "address": address, "pattern": label, "derived": True})
        if len(out) >= limit:
            break
    return out


def _demo() -> None:
    """Offline checks. DNS is skipped so these never depend on a network."""
    def gr(a, **kw):
        return grade(a, check_dns=False, **kw)

    assert gr("")["grade"] == "F"
    assert gr("not-an-email")["grade"] == "F"
    assert gr("x@mailinator.com")["grade"] == "F"
    assert not gr("x@mailinator.com")["sendable"]
    print("ok  malformed and disposable addresses are refused outright")

    assert gr("priya.nair@zamp.finance", name="Priya Nair",
              company="Zamp")["grade"] == "A"
    print("ok  their name at their company grades A")

    assert gr("info@zamp.finance", name="Priya Nair", company="Zamp")["grade"] == "D"
    assert gr("sales+eu@zamp.finance", company="Zamp")["grade"] == "D"
    print("ok  a shared inbox is a D however well the domain matches")

    c = gr("priya.nair@gmail.com", name="Priya Nair", company="Zamp")
    assert c["grade"] == "C" and c["sendable"]
    print("ok  a personal account is a C, and still sendable")

    # A name match must not rescue a worse finding, and the grade is the worst
    # mark rather than an average of them.
    assert gr("info@gmail.com", name="Info Person", company="Zamp")["grade"] == "D"
    print("ok  the grade is the worst finding, not an average")

    assert gr("q7x@some-other-co.com", name="Priya Nair", company="Zamp")["grade"] == "B"
    print("ok  an unrecognisable address at an unrelated domain is a B")

    # No company known: a personal address is all anyone has, so it is not held
    # against them.
    assert gr("priya.nair@gmail.com", name="Priya Nair")["grade"] == "A"
    print("ok  with no company known, a personal address is not penalised")

    # The grader and the contact finder must agree about a domain, or the UI
    # accepts a domain on one screen and disowns it on the next.
    assert _domain_matches("north-wind.com", "Northwind")
    assert _domain_matches("acme-corp.co.uk", "Acme Corp")
    assert _domain_matches("zampfinance.com", "Zamp")
    assert not _domain_matches("some-other-co.com", "Zamp")
    assert not _domain_matches("", "Zamp")
    print("ok  a hyphenated domain still matches the company it belongs to")

    assert has_mx("") is None
    print("ok  an empty domain is unknown, never a failure")

    # --- derived candidates ----------------------------------------------
    cands = candidates("Priya Nair", "zamp.finance", company="Zamp", check_dns=False)
    assert [c["address"] for c in cands][:2] == [
        "priya.nair@zamp.finance", "priya@zamp.finance"]
    assert all(c["derived"] for c in cands), "every candidate must say it was derived"
    assert cands[0]["grade"] == "A"
    print("ok  candidates follow the common conventions, most likely first")

    assert candidates("Priya Nair", "", check_dns=False) == []
    assert candidates("", "zamp.finance", check_dns=False) == []
    assert candidates("Priya Nair", "not-a-domain", check_dns=False) == []
    print("ok  no name or no usable domain yields nothing, never a guess")

    # One name still produces the patterns that do not need a surname.
    one = candidates("Cher", "example.com", check_dns=False)
    assert [c["address"] for c in one] == ["cher@example.com"]
    print("ok  a single name yields only the patterns it can fill")

    # An accented name must transliterate, not lose its letters. Splitting on
    # [^A-Za-z] turned "Jaakko Iso-Järvenpää" into jaakko.rvenp@ — a confident
    # address for a person who does not have it.
    acc = candidates("Jaakko Iso-Järvenpää", "airbase.com", check_dns=False)
    assert acc[0]["address"] == "jaakko.jarvenpaa@airbase.com", acc[0]["address"]
    assert candidates("José Álvarez", "acme.com", check_dns=False)[0]["address"] \
        == "jose.alvarez@acme.com"
    print("ok  an accented name transliterates instead of losing letters")

    assert len(candidates("Priya Nair", "zamp.finance", check_dns=False, limit=2)) == 2
    print("ok  the list is capped")

    print("all reachability checks passed")


if __name__ == "__main__":
    _demo()
