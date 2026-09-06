"""Company or domain in, decision-makers out.

WHY THIS EXISTS
---------------
Every other stage in this app starts from a named person. That is a fine
assumption when a list arrives from a CRM and a poor one when a GTM team has
only accounts: "here are forty target companies" is the more common starting
point, and it is the one Dossier could not do anything with.

WHAT IT DOES
------------
Searches for the roles you asked for at the company, reads what comes back, and
returns named people with titles, LinkedIn URLs and the source each was found
in. Every person carries the URL they came from, because a name a model produced
without one is a name it invented — the same rule the fact pipeline runs on.

ADDRESSES
---------
`resolve_address` answers "where would a message to this person go", trying each
source in turn and reporting what every one of them said. The lookup itself is
delegated to `pipeline.waterfall`, so the leads screen and a campaign can never
disagree about the same person's address.

A looked-up address and a calculated one are different claims and stay labelled
as such the whole way to the screen. Only a real one is ever filled into the
send field; the calculated ones are offered, graded, for a person to choose.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from .. import config
from ..integrations import llm, search
from ..models import SearchHit

log = logging.getLogger(__name__)

# Titles worth writing to, by the shape of the ask rather than the exact word.
ROLE_GROUPS: dict[str, list[str]] = {
    "sales": ["CRO", "VP Sales", "Head of Sales", "Sales Director"],
    "marketing": ["CMO", "VP Marketing", "Head of Marketing", "Head of Growth"],
    "finance": ["CFO", "VP Finance", "Financial Controller", "Head of Finance"],
    "engineering": ["CTO", "VP Engineering", "Head of Engineering"],
    "product": ["CPO", "VP Product", "Head of Product"],
    "operations": ["COO", "VP Operations", "Head of Operations"],
    "people": ["CHRO", "VP People", "Head of Talent"],
    "founder": ["Founder", "Co-founder", "CEO", "Managing Director"],
}


class Contact(BaseModel):
    name: str = ""
    role: str = ""
    linkedin_url: str = ""
    # Where this person was actually named. A contact with no source is dropped.
    source_url: str = ""
    evidence: str = Field(default="", description="the sentence that named them")


class ContactList(BaseModel):
    contacts: list[Contact] = Field(default_factory=list)


EXTRACT = """From the search results below, list the people who WORK AT {company}
in a decision-making role.

Rules that decide whether a person belongs in the list:
- They must currently work at {company}. Someone who left, or who works at
  another company mentioned in the same article, does not belong.
- Give their title as written in the source, not a normalised version of it.
- `source_url` must be the URL of the result you took them from. A person you
  cannot point at a URL for must be left out entirely.
- `evidence` is the sentence naming them and their role, copied verbatim.
- Include a LinkedIn profile URL only if one appears in the results. Never
  construct one from their name.
- If the results name nobody who works there, return an empty list. An empty
  answer is correct far more often than a guessed one.

Roles being looked for: {roles}

SEARCH RESULTS
{results}
"""


def domain_of(company_or_domain: str) -> str:
    """The domain, whether a domain, a URL or a bare company name was given."""
    text = (company_or_domain or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text).split("/")[0]
    return text if "." in text and " " not in text else ""


def company_label(company_or_domain: str) -> str:
    """A name fit to show, whether a name or a domain was typed.

    "zamp.finance" typed into the box must not become the company name on every
    lead it produces — the drafting prompt reads that field, and addressing
    someone at "zamp.finance" is how a message announces it was automated.
    """
    text = (company_or_domain or "").strip()
    domain = domain_of(text)
    if not domain:
        return text
    root = domain.rsplit(".", 2)[0] if domain.count(".") > 1 else domain.split(".")[0]
    root = root.replace("www.", "").replace("-", " ")
    return " ".join(w.capitalize() for w in root.split() if w) or text


# Sites that describe companies rather than being them. A company's own domain
# is never one of these, and the first search result very often is.
DIRECTORIES = {
    "linkedin.com", "crunchbase.com", "wikipedia.org", "bloomberg.com",
    "zoominfo.com", "glassdoor.com", "glassdoor.co.in", "indeed.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "medium.com", "github.com", "tracxn.com", "pitchbook.com", "owler.com",
    "rocketreach.co", "apollo.io", "leadiq.com", "signalhire.com",
    "ambitionbox.com", "startupintros.com", "kula.ai", "lever.co",
    "greenhouse.io", "notion.site", "producthunt.com", "reddit.com",
}


def _registrable(host: str) -> str:
    """Strip a leading www. and nothing else — enough to compare hosts."""
    return (host or "").lower().removeprefix("www.")


def _slug(text: str) -> str:
    """Letters and digits only, lowercased. "Acme Corp." -> "acmecorp"."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _is_company_domain(host: str, company: str) -> bool:
    """Whether this host is plausibly the company's own.

    The root must BE the company, not merely contain it. Token overlap looked
    reasonable and resolved "Zamp" to zamp-racing.com — a helmet manufacturer —
    which then derived four confident A-grade addresses at the wrong company.
    A wrong domain here is worse than no domain: no domain shows a message,
    a wrong one shows an answer.
    """
    host = _registrable(host)
    if not host or any(host == d or host.endswith("." + d) for d in DIRECTORIES):
        return False
    root = _slug(host.rsplit(".", 2)[0] if host.count(".") > 1 else host.split(".")[0])
    want = _slug(company)
    return bool(want) and root == want


def domain_from_sources(company: str, urls: list[str]) -> str:
    """The company's domain, taken from pages the research already read.

    Preferred over a search because these pages were retrieved while confirming
    this specific person at this specific company — they are anchored to the
    right company in a way a name search is not.
    """
    for url in urls:
        try:
            host = url.split("//", 1)[-1].split("/")[0]
        except Exception:      # noqa: BLE001
            continue
        if _is_company_domain(host, company):
            return _registrable(host)
    return ""


async def company_domain(company: str) -> str:
    """The company's own web domain, found by search. Last resort.

    A company NAME does not imply a domain, and every address convention is a
    function of the domain. Directory sites are excluded outright — the top
    result for a company name is very often its LinkedIn page, and deriving
    addresses from that gives addresses at linkedin.com.
    """
    company = (company or "").strip()
    if not company:
        return ""
    if (known := domain_of(company)):
        return known

    hits, _ = await search.search_many([(f'"{company}" official website', "general")])
    return domain_from_sources(company, [h.url for h in hits])


def _queries(company: str, roles: list[str]) -> list[tuple[str, str]]:
    """Anchored to the company name in every query.

    An unanchored role search returns the most famous CRO on the internet. The
    same lesson the research traversal learned: the anchor is the whole point.
    """
    site = 'site:linkedin.com/in'
    joined = " OR ".join(f'"{r}"' for r in roles[:4])
    return [
        (f'{site} "{company}" ({joined})', "general"),
        (f'"{company}" ({joined}) leadership team', "general"),
        (f'"{company}" appoints OR names OR promotes ({joined})', "news"),
    ]


def _render(hits: list[SearchHit], limit: int = 12) -> str:
    out = []
    for h in hits[:limit]:
        body = (h.content or "")[:1200]
        out.append(f"URL: {h.url}\nTITLE: {h.title}\n{body}\n---")
    return "\n".join(out)


async def find(company: str, groups: list[str] | None = None) -> dict:
    """Decision-makers at one company. Returns contacts and what was searched."""
    company = (company or "").strip()
    if not company:
        return {"contacts": [], "sources": [], "note": "No company given."}

    roles: list[str] = []
    for g in (groups or ["founder", "sales"]):
        roles.extend(ROLE_GROUPS.get(g, [g]))
    roles = list(dict.fromkeys(roles))

    label = company_label(company)
    hits, errors = await search.search_many(_queries(company, roles))
    if not hits:
        note = ("Search failed — this is not the same as nobody being there."
                if errors else f"Nothing public names a decision-maker at {company}.")
        return {"contacts": [], "sources": [], "note": note}

    try:
        result = await llm.structured(
            ContactList,
            EXTRACT.format(company=company, roles=", ".join(roles), results=_render(hits)),
            model=config.MODEL_FAST,
        )
    except llm.LLMFailure as e:
        return {"contacts": [], "sources": [], "note": f"Could not read the results ({e.reason})."}

    seen: set[str] = set()
    contacts: list[dict] = []
    for c in result.contacts:
        key = c.name.strip().lower()
        # No name, no source, or a duplicate: all dropped. A contact whose
        # source URL was not among the results is one the model made up.
        if not key or key in seen or not c.source_url:
            continue
        if not any(c.source_url.rstrip("/") == h.url.rstrip("/") for h in hits):
            log.warning("dropped %s — source_url not among the search results", c.name)
            continue
        seen.add(key)
        contacts.append({**c.model_dump(), "company": label})

    return {
        "contacts": contacts,
        "sources": [{"title": h.title, "url": h.url, "query": h.query} for h in hits[:12]],
        "note": (f"{len(contacts)} decision-maker(s) found at {company}."
                 if contacts else f"No decision-maker at {company} was named in the results."),
    }


# --------------------------------------------------------------- waterfall ---
# Addresses in a real page, as opposed to derived ones.
_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Addresses that appear on a profile but belong to nobody in particular.
_NOT_A_PERSON = re.compile(
    r"^(no-?reply|do-?not-?reply|support|info|hello|contact|press|help|admin)@",
    re.I)


def addresses_in_text(text: str, *, domain: str = "") -> list[str]:
    """Real addresses written in a page, deduplicated and filtered.

    Not a guess: these were typed by someone. Shared inboxes are dropped
    because they are the ones most likely to appear in a footer, and a footer
    address is not the person the message is for.
    """
    out: list[str] = []
    for hit in _IN_TEXT.findall(text or ""):
        low = hit.lower().strip(".,;:)")
        if _NOT_A_PERSON.match(low) or low in out:
            continue
        if domain and not low.endswith("@" + domain):
            continue
        out.append(low)
    return out


async def resolve_address(run: dict) -> dict:
    """Find an address for a lead by trying each source in turn.

    First match wins, and every step reports whether it hit, missed, or could
    not run — because "we found nothing" and "we never looked" are different
    answers, and a pipeline that conflates them cannot be debugged or trusted.

    The order is by confidence, not by convenience: an address someone actually
    wrote down beats one this app calculated, always. Only the last step is
    derived, and it is labelled as such the whole way to the screen.
    """
    from .. import db
    from ..integrations import personsignal, reachability

    name = (run.get("name") or "").strip()
    company = (run.get("company") or "").strip()
    steps: list[dict] = []

    def step(source: str, status: str, detail: str) -> None:
        steps.append({"source": source, "status": status, "detail": detail})

    # 1 — already on the record. Free, and the user's own answer outranks ours.
    if (existing := (run.get("email") or "").strip()):
        step("On the record", "hit", "Imported with the lead, or typed in.")
        return {"address": existing, "derived": False, "source": "On the record",
                "steps": steps, "candidates": []}
    step("On the record", "miss", "No address arrived with this lead.")

    # 2 — the company's own domain, from pages this research already read.
    urls = [x.get("url") or "" for x in (run.get("sources") or [])]
    if run.get("hook_source"):
        urls.insert(0, run["hook_source"])
    domain = domain_from_sources(company, urls)
    if domain:
        step("Company domain", "hit", f"{domain}, from a page this research read.")
    else:
        domain = await company_domain(company)
        if domain:
            step("Company domain", "hit", f"{domain}, from a search for the company.")
        else:
            step("Company domain", "miss",
                 f"Could not identify {company or 'their company'}'s domain.")

    # 3 — an address written on the cached profile. Never fetched here: the
    # provider allowance is small, and spending one to look for a field it does
    # not promise is not a trade worth making.
    cached = await db.get_person_profile(
        personsignal._cache_key(name, company, run.get("url") or ""),
        config.SUPERCARL_CACHE_DAYS)
    if not cached:
        step("Public profile", "skipped",
             "No cached profile — the provider allowance is not spent on a guess.")
    else:
        found = addresses_in_text(cached.get("text") or "", domain=domain)
        if found:
            step("Public profile", "hit", "Written on their profile.")
            return {"address": found[0], "derived": False, "source": "Public profile",
                    "steps": steps, "candidates": []}
        step("Public profile", "miss", "Their profile states no address.")

    # 4 — the email-finder, then arithmetic, then verification.
    #
    # Delegated to the outbound waterfall rather than reimplemented: the leads
    # screen and a campaign must not disagree about the same person's address,
    # and two copies of this logic would drift the first time one was touched.
    if not domain:
        step("Domain convention", "skipped", "No domain to apply a convention to.")
        return {"address": "", "derived": False, "source": "", "steps": steps,
                "candidates": []}

    from . import waterfall
    found = await waterfall.enrich_email(name, company, domain)
    for attempt in found.get("attempts", []):
        status = ("hit" if "found" in attempt["outcome"] or "confirmed" in attempt["outcome"]
                  else "skipped" if "no key" in attempt["outcome"] else "miss")
        step(attempt["provider"], status, attempt["outcome"])

    if found.get("email") and not str(found.get("source", "")).startswith("derived"):
        # A real lookup. Not a guess, and the screen may say so.
        return {"address": found["email"], "derived": False,
                "source": "Email finder", "steps": steps, "candidates": [],
                "verified": bool(found.get("verified"))}

    candidates = reachability.candidates(name, domain, company=company)
    if not candidates:
        step("Domain convention", "miss", f"{domain} accepts no mail.")
        return {"address": "", "derived": False, "source": "", "steps": steps,
                "candidates": []}

    step("Domain convention", "derived",
         f"{len(candidates)} pattern(s) at {domain} — calculated, not looked up.")
    return {"address": "", "derived": True, "source": "Domain convention",
            "steps": steps, "candidates": candidates}


def _demo() -> None:
    """ponytail: `python -m backend.pipeline.contacts`. No network."""
    assert domain_of("https://zamp.finance/careers") == "zamp.finance"
    assert domain_of("zamp.finance") == "zamp.finance"
    assert domain_of("Zamp") == ""
    assert domain_of("") == ""
    print("ok  a domain is recognised whether given as a URL, a domain or a name")

    qs = _queries("Zamp", ["CRO", "VP Sales"])
    assert all('"Zamp"' in q for q, _ in qs), "every query must anchor to the company"
    assert any("linkedin.com/in" in q for q, _ in qs)
    print("ok  every query stays anchored to the company")

    # Role groups expand, and an unknown group is passed through as a title
    # rather than silently dropping the search.
    assert "CRO" in ROLE_GROUPS["sales"]
    print("ok  role groups expand to real titles")

    # A domain typed into the box must not become the company name on the lead:
    # the drafting prompt reads that field.
    assert company_label("zamp.finance") == "Zamp"
    assert company_label("https://www.acme-corp.co.uk/about") == "Acme Corp"
    assert company_label("Zamp") == "Zamp"
    assert company_label("") == ""
    print("ok  a domain becomes a company name fit to address someone at")

    # A company's LinkedIn page is not its domain, and deriving addresses from
    # one would produce addresses at linkedin.com.
    assert "linkedin.com" in DIRECTORIES and "crunchbase.com" in DIRECTORIES
    assert _registrable("www.Zamp.Finance") == "zamp.finance"
    assert not _is_company_domain("linkedin.com", "LinkedIn")
    assert not _is_company_domain("in.linkedin.com", "Zamp")
    print("ok  directory sites can never be mistaken for a company domain")

    # The root must BE the company. Token overlap resolved "Zamp" to
    # zamp-racing.com — a helmet manufacturer — and then derived four
    # confident A-grade addresses at the wrong company.
    assert _is_company_domain("zamp.finance", "Zamp")
    assert _is_company_domain("www.zamp.finance", "Zamp")
    assert not _is_company_domain("zamp-racing.com", "Zamp")
    assert not _is_company_domain("zampier.io", "Zamp")
    assert _is_company_domain("acmecorp.co.uk", "Acme Corp")
    print("ok  a near-miss domain is rejected — a wrong one is worse than none")

    assert domain_from_sources("Zamp", [
        "https://in.linkedin.com/in/x", "https://zamp-racing.com/shop",
        "https://zamp.finance/about"]) == "zamp.finance"
    assert domain_from_sources("Zamp", ["https://crunchbase.com/zamp"]) == ""
    print("ok  the domain is taken from pages the research already read")

    # --- addresses written in a page, as opposed to calculated ------------
    text = ("Reach me at priya.nair@zamp.finance or via info@zamp.finance. "
            "Old one: noreply@zamp.finance")
    assert addresses_in_text(text) == ["priya.nair@zamp.finance"]
    print("ok  a shared inbox in a page footer is not the person")

    assert addresses_in_text(text, domain="other.com") == []
    assert addresses_in_text("") == []
    print("ok  an address at the wrong domain is not this person's either")

    print("all contact-discovery checks passed")


if __name__ == "__main__":
    _demo()
