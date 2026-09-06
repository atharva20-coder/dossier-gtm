"""Stage 3 — fact extraction, plus syndication collapsing.

Two things happen here that matter:

1. Every fact must declare `key_entities` — verbatim strings from the source
   that prove it. Those are what the grounding stage checks. Requiring them up
   front is what makes hallucination detectable at all.

2. Syndication collapsing. Most funding news is one press release redistributed
   across a dozen aggregators. Five hits is not five confirmations, it is one
   fact wearing five outfits. Treating source COUNT as confidence means being
   fooled by duplication.
"""
from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher

from .. import config
from ..integrations import llm
from ..models import ExtractedFact, ExtractionResult, ProspectInput, SearchHit

PROMPT = """Extract factual, referenceable events about this prospect and their company.

Prospect: {name}
Company:  {company}
Today's date: {today}

SOURCES:
{sources}

Extract each distinct, concrete fact you can find. For each one:

- text: one sentence stating the fact plainly.

- level: 'person' or 'company'.
    'person'  = about {name} THEMSELVES — they were promoted, changed roles,
                spoke at something, appeared on a podcast, wrote an article,
                were quoted directly, won something personally, or publicly
                said they need something.
    'company' = about their employer as an organisation.
    Be strict. "Their company raised $12M" is COMPANY level even though it
    mentions them. Only mark 'person' when the individual is the subject.

- category: exactly one of —
    PERSON-level: promotion, role_change, looking_for, influencer,
                  personal_award, speaking
    COMPANY-level: hiring, cost_reduction, ai_transformation, fundraise, ipo,
                   expansion, product_launch, partnership, award
    EXCLUDED-but-still-report: layoff, lawsuit, controversy, personal_life
    Fallback: other
- date: the ISO date (YYYY-MM-DD) the EVENT happened. If the source only gives a
  publication date, use that. If genuinely unknown, return an empty string.
  NEVER guess a date to make something look recent.
- key_entities: 2-4 VERBATIM strings copied exactly from the source text that
  prove this fact — amounts ("$12M"), investor names, role titles, product names.
  These are machine-checked against the source. Do not paraphrase them.
  Do not include an entity unless it literally appears in the source text.
- source_url: the URL of the source you took it from.
- subject_company: which company the fact is about.
- subject_person: the full name of the individual the fact is ABOUT, written
  exactly as the source writes it, or "" if the fact is about an organisation.
  A source often names several people — an author, someone quoted, someone
  congratulated. Name only the one the fact is actually about.
    "Priya Nair was promoted to CTO"        -> "Priya Nair"
    "Acme raised $12M"                      -> ""
    "Ravi Shah interned at Acme building X" -> "Ravi Shah"   (NOT "")
  This is machine-checked against the prospect, so getting it wrong causes a
  correct fact about the wrong person to be attributed to {name}.

CRITICAL RULES:
- Extract ONLY what the sources actually say. Never infer, never embellish,
  never fill gaps with plausible detail.
- If a source discusses several companies, set subject_company correctly. Do not
  attribute a competitor's news to {company}.
- If a source discusses several PEOPLE, set subject_person correctly. A post
  about a colleague's or an intern's achievement at {company} is a fact about
  THAT person, not about {name} and not about {company}.
- Include negative facts (layoffs, lawsuits) if present — they are filtered
  later by policy, but hiding them from the record would be wrong.
- If the sources contain no concrete facts, return an empty list.
"""


def _norm_text(s: str) -> str:
    s = re.sub(r"\W+", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def collapse_syndicated(facts: list[ExtractedFact], threshold: float = 0.82) -> tuple[list[ExtractedFact], int]:
    """Collapse near-duplicate facts that came from syndicated copies of one story.

    Returns (kept, collapsed_count). The kept copy kicks the earliest date and
    the union of entities so nothing verifiable is lost.
    """
    kept: list[ExtractedFact] = []
    collapsed = 0

    for f in facts:
        if f.source_url and f.source_url not in f.corroborating_urls:
            f.corroborating_urls.append(f.source_url)

        fn = _norm_text(f.text)
        match = None
        for k in kept:
            if SequenceMatcher(None, fn, _norm_text(k.text)).ratio() >= threshold:
                match = k
                break
        if match is None:
            kept.append(f)
            continue

        collapsed += 1
        # Prefer the earlier date (closer to the original announcement).
        if f.date and (not match.date or f.date < match.date):
            match.date = f.date
        for e in f.key_entities:
            if e not in match.key_entities:
                match.key_entities.append(e)
        # Keep every source that carried the story. Collapsing exists so that
        # ten aggregators are not counted as ten confirmations, but WHICH
        # sources carried it still matters: the same claim on a first-party post
        # and in independent reporting is genuinely corroborated, and only this
        # list can tell the two cases apart. See pipeline/provenance.py.
        for u in f.corroborating_urls:
            if u not in match.corroborating_urls:
                match.corroborating_urls.append(u)

    return kept, collapsed


def drop_wrong_company(facts: list[ExtractedFact], company: str,
                       person: str = "") -> tuple[list[ExtractedFact], list[ExtractedFact]]:
    """Discard facts about a DIFFERENT company that appeared in the same article.

    The classic competitor-comparison trap — an article about the prospect's
    employer that also describes a rival, whose news then gets attributed here.

    A PERSON-LEVEL fact about the prospect is exempt, however different the
    company on it. "Dana Rao was VP of Finance Operations at Adobe" carries
    Adobe as its subject company and is still a fact about the person being
    researched: it is their career history, and it is what tells you a new SVP
    has done the job before. Dropping it here deleted every former employer
    before anything downstream could use them — the person axis is guarded by
    `drop_third_party_people` and `drop_wrong_person`, so this gate does not
    need to police it too.
    """
    from .normalize import company_key
    from .extract import _name_key  # noqa: F401 — same module, kept explicit

    if not company:
        return facts, []
    target = company_key(company)
    who = _name_key(person) if person else ""
    keep, dropped = [], []
    for f in facts:
        # Their own history, at whatever company it happened.
        if f.level == "person" and who and _name_key(f.subject_person or "") == who:
            keep.append(f)
            continue
        subj = company_key(f.subject_company or "")
        if not subj or subj == target or subj in target or target in subj:
            keep.append(f)
        else:
            dropped.append(f)
    return keep, dropped


def _url_key(u: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", (u or "").strip().lower()).rstrip("/")


def _name_key(name: str) -> str:
    """Comparison key for a personal name: case, punctuation and honorifics out."""
    s = re.sub(r"[^\w\s]", " ", (name or "").lower())
    drop = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "shri", "smt", "jr", "sr"}
    return " ".join(t for t in s.split() if t and t not in drop)


def same_person(a: str, b: str) -> bool:
    """Whether two written names plausibly denote the same individual.

    Deliberately lenient about form and strict about identity: "Dr. Priya Nair"
    and "Priya Nair" are one person, "Priya Nair" and "Rahul Nair" are not.
    A middle name or initial on one side only is common in press coverage, so
    one name being a subsequence of the other counts as a match.
    """
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ta, tb = ka.split(), kb.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # Every part of the shorter name must appear in the longer, in order, and
    # the family name (last token) must match outright.
    if short[-1] != long_[-1]:
        return False
    it = iter(long_)
    return all(any(t == l for l in it) for t in short)


def drop_third_party_people(
    p: ProspectInput, facts: list[ExtractedFact]
) -> tuple[list[ExtractedFact], list[ExtractedFact]]:
    """Discard facts whose subject is a DIFFERENT named individual.

    `drop_wrong_company` guards the company axis and `drop_wrong_person` guards
    the namesake axis, and a real hook slipped between them: a LinkedIn post
    congratulating an intern on the AI work they did at the prospect's employer.
    Every existing check passed it. Its subject_company was correct, so the
    company gate kept it. The extractor labelled it company-level — the employer
    is where the work happened — so the namesake gate never looked at it. It was
    recent, specific, grounded, and carried by a first-party LinkedIn post, so it
    won on score. The draft then congratulated the prospect on a third party's
    internship.

    The lesson generalises past that one case: a fact can be true, correctly
    attributed to the right company, and still be ABOUT someone who is not the
    prospect. Colleagues, interns, co-founders, predecessors in a role and
    quoted customers all produce facts like this, and none of them are a hook
    for this person. So the subject is checked at every level, not just at
    person level.

    Facts about an organisation (empty subject_person) are untouched.
    """
    if not p.name.strip():
        return facts, []
    keep, dropped = [], []
    for f in facts:
        subject = (f.subject_person or "").strip()
        if subject and not same_person(subject, p.name):
            dropped.append(f)
        else:
            keep.append(f)
    return keep, dropped


def drop_wrong_person(
    p: ProspectInput, facts: list[ExtractedFact], hits: list[SearchHit]
) -> tuple[list[ExtractedFact], list[ExtractedFact]]:
    """Discard person-level facts that belong to somebody who merely shares the
    prospect's name.

    `drop_wrong_company` guards the company axis; this guards the person axis,
    and nothing else in the pipeline does. Grounding cannot: it verifies that a
    fact's entities appear in the source, which a namesake's article satisfies
    perfectly — the podcast really does exist, it is just a different Shubham
    Verma. A common name plus a category the judge rewards (person-level
    `speaking`) is enough to outrank the genuine hook, so the wrong person's
    podcast ends up in an email addressed to a fintech founder.

    The test is attribution, not truth: a fact about the PERSON must come from a
    source that also ties that person to something we independently know about
    them. Two ways to pass:

      * the source is a resolved profile — matched by LinkedIn URL, so it is
        about this individual by construction, or
      * the source text names their employer.

    Company-level facts are untouched; `drop_wrong_company` already owns them.
    Without a known company there is no anchor to test against, so the gate
    stays off rather than guessing.
    """
    from ..integrations.personsignal import PROFILE_URL_PREFIX
    from .normalize import company_key

    anchor = company_key(p.company)
    if not anchor:
        return facts, []

    # Longest token of the company name — "Zamp" from "Zamp", "cradlewise" from
    # "Cradlewise Pvt Ltd". Short tokens are dropped: a two-letter fragment
    # matches almost any text and would wave everything through.
    tokens = [t for t in anchor.split() if len(t) >= 4] or [max(anchor.split(), key=len)]

    by_url = {_url_key(h.url): h for h in hits}

    keep: list[ExtractedFact] = []
    dropped: list[ExtractedFact] = []
    for f in facts:
        if f.level != "person":
            keep.append(f)
            continue
        if (f.source_url or "").startswith(PROFILE_URL_PREFIX):
            keep.append(f)
            continue

        hit = by_url.get(_url_key(f.source_url))
        # A source we cannot locate cannot be checked, and an unattributable
        # person-level claim is exactly the failure this gate exists to stop.
        text = _norm_text(f"{hit.title} {hit.content}") if hit else ""
        if text and any(t in text for t in tokens):
            keep.append(f)
        else:
            dropped.append(f)

    return keep, dropped


# Characters of each source shown to the model. The facts worth extracting are
# near the top of a page — headline, lede, quotes — while the tail is navigation
# and boilerplate that costs tokens and time to read.
SOURCE_CHARS = 3500


async def _extract_batch(p: ProspectInput, batch: list[SearchHit], today: str) -> list[ExtractedFact]:
    sources = "\n\n".join(
        f"[{i+1}] {h.title}\nURL: {h.url}\n{h.content[:SOURCE_CHARS]}"
        for i, h in enumerate(batch)
    )
    result = await llm.structured(
        ExtractionResult,
        PROMPT.format(name=p.name, company=p.company or "(unknown)", today=today, sources=sources),
        model=config.MODEL_FAST,
        system=("You extract facts strictly from provided text. You never invent "
                "details and never guess dates. Fabricating a fact is the worst "
                "possible failure."),
        temperature=0.1,
    )
    return result.facts


async def extract(p: ProspectInput, hits: list[SearchHit], today: str) -> tuple[list[ExtractedFact], dict]:
    """Read the retrieved sources and pull out concrete facts.

    Extraction runs in CONCURRENT BATCHES rather than as one large call, for two
    reasons that arrived together. Research now returns 40-50 sources, and a
    single call could only ever be shown the first ten of them — everything the
    deep pass worked to find was being retrieved and then thrown away unread.
    Meanwhile that one call had grown to ~35k characters and was taking most of
    a minute, which under concurrent load tipped over the model timeout and
    failed whole runs.

    Smaller batches fix both: more sources get read in total, and the slowest
    call is a fraction of what one combined call cost. Overlap between batches
    is not a problem — `collapse_syndicated` already exists to merge the same
    fact arriving from several places, which is exactly what this produces.
    """
    if not hits:
        return [], {"collapsed": 0, "wrong_company": 0, "wrong_person": 0, "third_party": 0}

    considered = hits[:config.EXTRACT_MAX_SOURCES]
    size = max(1, config.EXTRACT_SOURCES_PER_CALL)
    batches = [considered[i:i + size] for i in range(0, len(considered), size)]

    results = await asyncio.gather(
        *[_extract_batch(p, b, today) for b in batches],
        return_exceptions=True,
    )

    facts: list[ExtractedFact] = []
    failures = [r for r in results if isinstance(r, BaseException)]
    for r in results:
        if not isinstance(r, BaseException):
            facts.extend(r)

    # One batch failing loses a slice of the sources, not the run. Every batch
    # failing is a real infrastructure failure and must surface as one, because
    # reporting it as "no facts found" would be a lie about the prospect.
    if failures and not facts:
        raise failures[0]

    facts, dropped = drop_wrong_company(facts, p.company, p.name)
    facts, third_party = drop_third_party_people(p, facts)
    facts, wrong_person = drop_wrong_person(p, facts, hits)
    facts, collapsed = collapse_syndicated(facts)

    return facts, {
        "collapsed": collapsed,
        "wrong_company": len(dropped),
        "wrong_person": len(wrong_person),
        "third_party": len(third_party),
        "sources_read": len(considered),
        "batches": len(batches),
        "batches_failed": len(failures),
    }
