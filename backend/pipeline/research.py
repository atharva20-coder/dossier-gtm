"""Stages 1-2 — query generation and parallel retrieval.

THE KEY DESIGN POINT, and the thing the first version got wrong:

Research runs in two SEPARATE tiers, and the person tier goes first.

    PERSON tier   — the individual: promotions, role changes, talks, podcasts,
                    interviews, bylines, quotes, things they publicly said they
                    need. This is what makes a message feel written FOR someone.

    COMPANY tier  — their employer: hiring, fundraise, IPO, cost/AI programmes,
                    expansion. Real signal, but it is about the company, not
                    them, and everyone else in their inbox is using it too.

The first version generated four company queries against one person query, so
it unsurprisingly returned company news and attributed it to the person. Both
tiers are now generated, retrieved and scored independently.

LinkedIn and X have no usable self-serve API (Proxycurl, the main compliant
LinkedIn data API, shut down entirely), so their content is reached two other
ways: the person-signal provider for the prospect's own profile and posts, and
site-scoped WEB queries against linkedin.com and x.com, which search the public
index rather than the platforms. Everything else comes from where people are
actually written about — podcasts, conference programmes, interviews, press
quotes, bylines, personal sites.

THEN THE TRAVERSAL
------------------
Everything above is decided before a single result is read, so it can only ever
be generic. Once identity IS confirmed — the provider resolved the prospect from
their LinkedIn URL and returned their real employment history — the system knows
things it can search with, and `_traverse` walks outward from there: prior
employers, then whatever those results turn up, wave after wave until the depth
limit or the query budget stops it. See pipeline/graph.py.

The point is not volume, it is aim. A generic `"Shubham Verma" interview` query
returns strangers; `"Shubham Verma" "Boston Consulting Group"` returns this one.
Anchoring every query to the prospect is also what keeps the traversal from
re-importing the namesake problem the rest of the pipeline works to prevent.
"""
from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from .. import config
from ..integrations import personsignal, search
from . import graph
from ..integrations.search import SearchFailure
from ..models import ProspectInput, SearchHit


class _Queries(BaseModel):
    person: list[str] = Field(
        default_factory=list,
        description=("3-4 queries about the INDIVIDUAL: interviews, podcasts, conference "
                     "talks, quotes, articles they wrote, promotions or a new role."),
    )
    company_episodic: list[str] = Field(
        default_factory=list,
        description="2-3 queries about the COMPANY's recent events: funding, IPO, expansion, launches.",
    )
    company_always_on: list[str] = Field(
        default_factory=list,
        description="2 queries about durable company signal: open roles/hiring, what the company does.",
    )


QUERY_PROMPT = """Generate web search queries to research a sales prospect.

Prospect:
  name:     {name}
  company:  {company}
  role:     {role}
  location: {location}
  profile:  {url}

Return THREE groups.

PERSON (3-4 queries) — about the INDIVIDUAL, not their employer. Target places
people are actually written about:
  - interviews and podcast appearances featuring them
  - conference talks, panels, webinars they spoke at
  - articles or blog posts they personally wrote
  - direct quotes from them in press coverage
  - their own promotion, appointment or move to this role
Always include their full name in these. Add the company name only when the
name is common enough to need it for disambiguation.

COMPANY_EPISODIC (2-3 queries) — time-sensitive company events:
  funding, IPO plans, market expansion, major product launches, cost or AI
  transformation programmes.

COMPANY_ALWAYS_ON (2 queries) — signal almost any company has:
  open roles they are hiring for, and what the company actually does.

Rules:
- Plain search queries. No quotes, no boolean operators, no site: filters.
- Do not include the words "news" or "latest" — recency is handled elsewhere.
- Do not write site-scoped queries for LinkedIn or X. Those are added separately
  and would only duplicate them here.
- NEVER invent a profession, industry, employer or descriptor that is not given
  above. If the role and company are unknown, the queries use the name and
  nothing else. Guessing what someone does turns a search for this person into a
  search for a famous stranger who shares their name — which is the single most
  expensive mistake available here, because every result that comes back is
  about the wrong human being and looks entirely plausible.
- If only a name is known, prefer FEWER queries over padding the list out. Two
  honest queries beat four invented ones.
"""


def _fallback_queries(p: ProspectInput) -> _Queries:
    """Used if query generation fails — the run degrades, it does not die.

    With no company, the company tiers are dropped rather than aimed at the
    person's own name: `"Atharva" funding round` is not a question about anyone,
    and every credit it spends buys a result about someone else.
    """
    n, c = p.name, p.company
    person = [f"{n} {c} interview" if c else f"{n} interview",
              f"{n} podcast", f"{n} conference talk"]
    if p.role:
        person.append(f"{n} appointed {p.role}")
    if not c:
        return _Queries(person=person)
    return _Queries(
        person=person,
        company_episodic=[f"{c} funding round", f"{c} expansion plans"],
        company_always_on=[f"{c} careers open roles", f"{c} what the company does"],
    )


async def build_queries(p: ProspectInput) -> _Queries:
    from ..integrations import llm
    try:
        q = await llm.structured(
            _Queries,
            QUERY_PROMPT.format(name=p.name, company=p.company or "(unknown)",
                                role=p.role or "(unknown)",
                                location=p.location or "(unknown)",
                                url=p.url or "(none)"),
            model=config.MODEL_FAST,
            system="You write precise, literal web search queries.",
        )
        if q.person or q.company_episodic:
            return q
    except Exception:
        pass
    return _fallback_queries(p)


# Domains asked directly for person-level signal, strongest first.
SOCIAL_SITES = ("linkedin.com", "x.com")


def _pinned_profile(p: ProspectInput) -> bool:
    """Whether identity is pinned by a profile URL the provider can resolve.

    Both halves matter. The URL alone is not a pin if nothing will fetch it, and
    the provider alone cannot pin anyone without an address to fetch.
    """
    return "linkedin.com/in/" in (p.url or "").lower() and personsignal.enabled()


def _social_queries(p: ProspectInput) -> list[tuple[str, str]]:
    """Site-scoped queries for the prospect's own posts.

    Skipped when there is no company to disambiguate with: `site:linkedin.com
    "Shubham Verma"` on a common name returns a wall of strangers, which is the
    namesake problem the extract stage then has to clean up. Better not to ask.

    LinkedIn is skipped for the same reason, more sharply, once the prospect's
    own profile URL is supplied and the provider is there to fetch it. This
    query searches BY NAME, so it cannot return the one profile we were already
    handed — everything it does return is someone else with that name. The
    provider resolves the exact profile from the URL, so asking anyway spends a
    credit to reintroduce the ambiguity the URL was given to remove.

    The provider has to be enabled for that to hold. Without a key nothing
    fetches the URL, and this query is the only thing reaching LinkedIn at all —
    so it stays, namesakes and all, rather than dropping the tier entirely.
    """
    if not p.name.strip() or not p.company.strip():
        return []
    sites = [s for s in SOCIAL_SITES
             if not (s == "linkedin.com" and _pinned_profile(p))]
    return [(f'site:{site} "{p.name}" "{p.company}"', "general") for site in sites]


# How many anchored follow-up queries the second pass may spend. Each one is a
# search credit, so this is a real cost knob, not a formality.
DEEP_PASS_MAX_QUERIES = 4

# Organisations named in a resolved profile, e.g. "Project Leader at Boston
# Consulting Group (BCG)" or "Founder S Office at Zamp".
_ORG_AT = re.compile(r"\bat ([A-Z][\w&.,'\- ]{2,60})")

# Field labels that follow a value in profile text. An employer name absorbs the
# label of whatever field comes next unless it is cut off here.
_FIELD_MARKER = re.compile(r"\b(description|dates?|location|skills|title|company)\b", re.I)

# Fragments that are a place or a date rather than an employer. Matched as whole
# words: a substring test here quietly discarded every Indian institution,
# because "india" is inside "Indian Institute of Technology".
_NOT_AN_ORG = re.compile(r"\b(area|region|remote|present|greater)\b", re.I)


# Lowercase words that genuinely appear inside organisation names. Anything
# else in lowercase means the match has run past the name into prose — "at Dunzo
# from Product, Engineering and Analytics" is a sentence, not an employer.
_NAME_CONNECTORS = {"of", "and", "the", "for", "de", "du", "van", "von"}


def _trim_to_name(text: str) -> str:
    """Cut a match down to the organisation name it starts with."""
    out: list[str] = []
    for word in text.split():
        bare = word.strip(",.&").strip()
        if not bare:
            continue
        if bare.islower() and bare.lower() not in _NAME_CONNECTORS:
            break
        out.append(word)
    # A trailing connector means the name was cut mid-phrase; drop it.
    while out and out[-1].strip(",.").lower() in _NAME_CONNECTORS:
        out.pop()
    return " ".join(out).strip(" ,.")


# "Dates: May 2015 - Jul 2015" / "Dates: Sep 2022 - Present", written on the
# line after the role in a resolved profile.
_DATES = re.compile(r"^Dates:\s*([A-Za-z]{3,9})?\s*(\d{4})", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _started(lines: list[str], at: int) -> tuple[int, int]:
    """When the role on line `at` began, as (year, month). (0, 0) if unstated.

    Read from the `Dates:` line that follows the role, which is where a resolved
    profile puts it. Used only for ordering, so an unparseable date sorting last
    is the right failure.
    """
    for ln in lines[at + 1: at + 4]:
        m = _DATES.match(ln)
        if m:
            month = _MONTHS.get((m.group(1) or "").lower()[:3], 0)
            return int(m.group(2)), month
    return 0, 0


def profile_orgs_with_evidence(profile_text: str, exclude: str) -> list[tuple[str, str]]:
    """Employers from the profile, newest first, each with the line that named it.

    Walks the profile line by line rather than pattern-matching the whole text,
    because each role's dates live on the line after it. Searching the text for
    a name instead collapses repeat employers onto whichever stint appears
    first — someone who left a company and returned would be shown at the older
    role, dated years before they actually went back.

    Ordered by when the role started, because the graph reads as a career and a
    career has a direction. Provider order cannot be trusted for that.

    The evidence line is carried rather than discarded because the first
    question anyone asks of a surprising research thread is "where did that come
    from?". Without it, answering means reading the database by hand.
    """
    from .normalize import company_key

    skip = company_key(exclude)
    lines = [ln.strip() for ln in (profile_text or "").split("\n") if ln.strip()]
    found: list[tuple[tuple[int, int], str, str]] = []

    for i, line in enumerate(lines):
        m = _ORG_AT.search(line)
        if not m:
            continue
        name = _FIELD_MARKER.split(m.group(1))[0].strip(" .,:-")
        name = re.sub(r"\s*\([^)]*\)$", "", name).strip()    # drop a trailing "(BCG)"
        name = _trim_to_name(name)
        if len(name) < 3 or _NOT_AN_ORG.search(name):
            continue
        key = company_key(name)
        if not key or key == skip:
            continue

        started = _started(lines, i)
        prior = next((j for j, (_, o, _) in enumerate(found)
                      if company_key(o) == key), None)
        if prior is not None:
            # People rejoin employers. Keep the most recent stint.
            if started > found[prior][0]:
                found[prior] = (started, name, line[:300])
            continue
        found.append((started, name, line[:300]))

    found.sort(key=lambda f: f[0], reverse=True)   # undated sorts last
    return [(name, evidence) for _, name, evidence in found]


def _profile_orgs(profile_text: str, exclude: str) -> list[str]:
    """Employer names alone, for callers that do not need the evidence."""
    return [name for name, _ in profile_orgs_with_evidence(profile_text, exclude)]


def deep_person_queries(p: ProspectInput, provider_hits: list[SearchHit]) -> list[tuple[str, str]]:
    """Follow-up queries aimed at THIS person, once we know who they are.

    Returns nothing unless the identity was actually confirmed by the provider.
    Running these on an unconfirmed name would be the opposite of what they are
    for: more queries about a name we have not pinned down means more of a
    namesake's material, not more of the prospect's.
    """
    if not provider_hits or not p.name.strip() or not p.company.strip():
        return []

    profile_text = "\n".join(h.content for h in provider_hits)
    name, company = p.name.strip(), p.company.strip()

    queries = [
        # What they said, in their own words, at their current employer.
        f'"{name}" "{company}" (interview OR podcast OR keynote OR panel)',
        # What they have written under their own byline.
        f'"{name}" "{company}" (wrote OR author OR blog OR "op-ed")',
    ]
    # Prior employers are the sharpest disambiguator a profile gives us.
    for org in _profile_orgs(profile_text, exclude=company)[:2]:
        queries.append(f'"{name}" "{org}"')

    return [(q, "general") for q in queries[:DEEP_PASS_MAX_QUERIES]]


async def identity_probe(p: ProspectInput) -> tuple[list[SearchHit], list[str]]:
    """Cheap first pass to establish WHO this is, before spending the full
    research budget researching the wrong person."""
    q = f"{p.name} {p.company}".strip() if p.company else p.name
    return await search.search_many([(q, "general"), (f"{q} profile role", "general")])


async def _traverse(
    p: ProspectInput,
    provider_hits: list[SearchHit],
    seen_hits: list[SearchHit],
    errors: list[str],
    on_wave: "Callable[[dict], Awaitable[None]] | None" = None,
) -> tuple[list[tuple[str, str]], list[SearchHit], list[dict], dict]:
    """Walk the entity graph outward from the prospect, one wave per depth.

    Returns (queries_run, new_hits, trail). Never raises: the traversal is an
    enhancement on top of research that already succeeded, so a failure here
    costs extra sources, never the run.

    Runs only when identity was actually confirmed by the provider. Expanding a
    graph around an unconfirmed name is the namesake problem with a bigger
    budget: every wave would pull in more material about whoever else shares it.
    """
    if not provider_hits or not p.name.strip() or not p.company.strip():
        return [], [], [], {}

    profile_text = "\n".join(h.content for h in provider_hits)
    # Every employer becomes a node; the budget decides how many get SEARCHED.
    # Showing a role and spending a credit on it are different things, and
    # truncating the list hid real history — a reader seeing four of six jobs
    # has no way to know the other two exist.
    orgs = profile_orgs_with_evidence(profile_text, exclude=p.company)
    g = graph.seed(p, orgs, provider_hits[0].url if provider_hits else "")

    all_q: list[tuple[str, str]] = []
    new_hits: list[SearchHit] = []
    known_urls = {h.url for h in seen_hits}

    # Questions worth asking of anyone once you know who they are, independent
    # of what their profile names.
    extra = [(q, "general") for q in (
        f'"{p.name}" "{p.company}" (interview OR podcast OR keynote OR panel)',
        f'"{p.name}" "{p.company}" (wrote OR author OR blog OR "op-ed")',
    )]

    read_for_proposals = list(provider_hits)
    if on_wave:
        await on_wave(g.snapshot())

    for depth in range(1, config.GRAPH_MAX_DEPTH + 1):
        frontier = g.frontier(depth)
        wave = graph.queries_for(g, frontier) + (extra if depth == 1 else [])

        remaining = config.GRAPH_QUERY_BUDGET - g.queries_spent
        if remaining <= 0:
            break

        # Ration the budget across the remaining depths instead of letting the
        # first wave eat it. Depth 1 is always the widest — every employer in
        # the profile is a candidate — so spending greedily there leaves nothing
        # for the waves that make this a traversal rather than a fan-out, and
        # the whole graph comes out one level deep.
        waves_left = config.GRAPH_MAX_DEPTH - depth + 1
        allowance = max(1, remaining // max(waves_left, 1))
        wave = wave[:allowance]
        if not wave:
            break

        # Which query belongs to which node. Keeping this mapping is what lets
        # the next wave record where it discovered something, instead of hanging
        # every finding off the prospect and flattening the traversal.
        node_of_query = {q: n.key() for (q, _), n in zip(wave, frontier)}

        hits, errs = await search.search_many(wave)
        g.queries_spent += len(wave)
        # Only nodes that actually got a query are explored. Marking the whole
        # frontier would claim credit for threads the budget cut off, and the
        # trail is meant to record what was done, not what was considered.
        g.explored.update(k for k in node_of_query.values())
        errors += errs

        fresh = [h for h in hits if h.url and h.url not in known_urls]
        known_urls.update(h.url for h in fresh)
        new_hits += fresh
        all_q += wave
        read_for_proposals = fresh or read_for_proposals

        hits_by_node: dict[str, list[SearchHit]] = {}
        for h in fresh:
            key = node_of_query.get(h.query)
            if key:
                hits_by_node.setdefault(key, []).append(h)

        # Credit each explored node with the sources its own query returned, so
        # the UI can show which thread actually paid off rather than an average.
        for key in node_of_query.values():
            g.sources_found[key] = len(hits_by_node.get(key, []))

        # Decide the next wave from what this one actually returned.
        if depth < config.GRAPH_MAX_DEPTH:
            await graph.propose(g, read_for_proposals, depth + 1, hits_by_node)
        if on_wave:
            await on_wave(g.snapshot())

    return all_q, new_hits, g.trail(), g.snapshot()


async def gather(
    p: ProspectInput,
    on_wave: "Callable[[dict], Awaitable[None]] | None" = None,
) -> tuple[list[SearchHit], list[str], dict]:
    """Run research across both tiers.

    Returns (hits, errors, breakdown) where breakdown reports how many sources
    came from each tier — which is what lets the UI show, honestly, whether a
    hook is grounded in something about the PERSON or merely about their employer.

    Raises SearchFailure only when every query failed for infrastructure
    reasons, because "we ran out of credits" must never be presented to the
    user as "this prospect has no public signal".
    """
    # Start the provider immediately. It does not depend on the generated
    # queries, and waiting for a model call before beginning a network fetch
    # puts one latency in front of the other for no reason.
    provider_task = asyncio.ensure_future(
        personsignal.fetch(p.name, p.company, p.url))

    q = await build_queries(p)

    person_q = [(s, "general") for s in q.person[:4] if s.strip()]
    # Ask the social platforms directly rather than hoping a general query
    # surfaces them. What someone posts themselves is the strongest person-level
    # signal there is, and it is exactly what a generic web query buries under
    # directory pages and namesakes — a site-scoped query is the difference
    # between reaching their own words and reaching an article about them.
    # LinkedIn first, X second: see pipeline/provenance.py for the same ordering
    # applied when the resulting facts are scored.
    person_q = _social_queries(p) + person_q
    epi_q = [(s, "news") for s in q.company_episodic[:3] if s.strip()]
    always_q = [(s, "general") for s in q.company_always_on[:2] if s.strip()]

    all_q = person_q + epi_q + always_q

    # Web search and the person-signal provider run concurrently — the provider
    # must never add latency to the critical path.
    (hits, errors), (provider_hits, provider_note) = await asyncio.gather(
        search.search_many(all_q),
        provider_task,
    )

    if not hits and not provider_hits and errors and len(errors) == len(all_q):
        raise SearchFailure("; ".join(errors[:3]))

    # The LinkedIn query was skipped on the promise that the provider would
    # fetch the supplied profile instead. When the provider comes back with
    # nothing, that promise is broken and the URL — the one piece of certain
    # identity in the whole run — is being used for nothing at all, while the
    # remaining queries carry only a name and go looking for whoever famous
    # shares it.
    #
    # So ask the index for the profile URL itself. This is not the query that
    # was skipped: that one searched by NAME and returned strangers. This one
    # searches for the exact profile address, so what comes back is that page
    # and pages citing it — no namesake can match it.
    if not provider_hits and _pinned_profile(p):
        slug = p.url.split("linkedin.com/in/", 1)[1].strip("/").split("?")[0]
        recovery = [(f'"linkedin.com/in/{slug}"', "general")]
        rec_hits, rec_errs = await search.search_many(recovery)
        hits += rec_hits
        errors += rec_errs
        all_q += recovery
        person_q += recovery
        provider_note = (f"{provider_note}; recovered by searching the profile URL"
                         if provider_note else "provider returned nothing")

    # Everything above was decided before anything was read. What follows is the
    # part that behaves like a person: walk outward from the prospect, following
    # what each wave turns up. See pipeline/graph.py.
    deep_q, deep_hits, trail, snapshot = await _traverse(
        p, provider_hits, hits, errors, on_wave)
    if deep_q:
        hits += deep_hits
        person_q += deep_q

    person_queries = {s for s, _ in person_q}
    person_hits = sum(1 for h in hits if h.query in person_queries)

    # Provider content is person-level by definition: it is what they wrote.
    all_hits = provider_hits + hits
    person_total = person_hits + len(provider_hits)

    breakdown = {
        "person_queries": len(person_q),
        "company_queries": len(epi_q) + len(always_q),
        "person_sources": person_total,
        "company_sources": len(all_hits) - person_total,
        "queries_run": [s for s, _ in all_q + deep_q],
        "provider_used": bool(provider_hits),
        "provider_note": provider_note,
        "deep_pass_queries": [s for s, _ in deep_q],
        "deep_pass_sources": len(deep_hits),
        "graph_trail": trail,
        "graph": snapshot,
    }
    return all_hits, errors, breakdown


def _demo() -> None:
    """Self-check: a supplied profile URL must not buy a namesake search."""
    p = ProspectInput(name="Atharva Joshi", company="Zamp",
                      url="https://www.linkedin.com/in/atharva20/")
    key = config.SUPERCARL_API_KEY
    try:
        config.SUPERCARL_API_KEY = "test-key"
        qs = [q for q, _ in _social_queries(p)]
        assert not any("linkedin.com" in q for q in qs), qs
        assert any("x.com" in q for q in qs), "x.com has no URL to pin it, so it stays"

        # No URL supplied: this query is how the prospect's own posts get reached.
        assert any("linkedin.com" in q for q, _ in
                   _social_queries(p.model_copy(update={"url": ""})))

        # A company page is not a person's profile and pins nobody.
        assert any("linkedin.com" in q for q, _ in _social_queries(
            p.model_copy(update={"url": "https://linkedin.com/company/zamp"})))

        # Without the provider, nothing else fetches the URL — keep the query.
        config.SUPERCARL_API_KEY = ""
        assert any("linkedin.com" in q for q, _ in _social_queries(p))
    finally:
        config.SUPERCARL_API_KEY = key

    # Unchanged: no company means no disambiguator, so neither site is asked.
    assert _social_queries(ProspectInput(name="Atharva Joshi")) == []

    # A bare name must not buy company queries aimed at the person's own name.
    bare = _fallback_queries(ProspectInput(name="Atharva"))
    assert bare.company_episodic == [] and bare.company_always_on == [], bare
    assert all("Atharva" in q for q in bare.person)
    assert not any("appointed" in q for q in bare.person), "no role, no role query"

    withco = _fallback_queries(ProspectInput(name="Atharva", company="Zamp",
                                             role="Founder"))
    assert withco.company_episodic and any("appointed Founder" in q
                                           for q in withco.person)

    # The URL is a pin only when something can actually resolve it.
    key = config.SUPERCARL_API_KEY
    try:
        config.SUPERCARL_API_KEY = "test-key"
        assert _pinned_profile(p)
        assert not _pinned_profile(p.model_copy(update={"url": ""}))
        config.SUPERCARL_API_KEY = ""
        assert not _pinned_profile(p), "no provider, no pin"
    finally:
        config.SUPERCARL_API_KEY = key

    print("research checks passed")


if __name__ == "__main__":
    _demo()
