"""Stage 4 — eligibility gates and hook selection.

This is deliberately a RULE ENGINE, not a model call. The model reads; these
rules decide. That split is what makes every decision inspectable and
defensible — "the model felt it was best" is not an answer you can give in a
review, and it's not an answer a customer accepts.

Four gates, applied in order. A fact must pass all four to be eligible:
  1. Grounding      — verified separately, upstream (highest severity)
  2. Category       — hard-excluded categories can never be a hook
  3. Factual recency— nothing older than FACT_RECENCY_MAX_DAYS
  4. Specificity    — must carry a concrete detail, not vague growth-speak

Eligible facts are then SCORED, which is a different question from eligibility:
outreach value decays far faster than factual accuracy. Scoring combines four
things — how recent the fact is, how much buying intent its category carries,
whether it is about the person or merely their employer, and where it came from
(pipeline/provenance.py: a first-party LinkedIn or X post beats a web article
about the same person, and a claim carried by both beats either alone).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from .. import config
from pydantic import BaseModel, Field

from ..models import ExtractedFact, FactVerdict, JudgeResult, StakeholderProfile, WriterConfig
from ..taxonomy import (
    BLOCKED_INTENTS,
    DEFAULT_INTENT_WEIGHTS,
    PERSON_INTENTS,
    PERSON_TIER_BOOST,
    tier_of,
)
from . import provenance
from ..integrations import llm

log = logging.getLogger(__name__)

VAGUE_PATTERNS = [
    r"\bis growing\b", r"\bcontinues to grow\b", r"\bis expanding\b$",
    r"\bdoing well\b", r"\bmarket leader\b", r"\binnovative\b",
    r"\bexciting (?:times|things)\b", r"\bcommitted to excellence\b",
]


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    s = s.strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None
    return None


# A year written into the sentence itself: "in 2014", "since January 2023".
# Extraction leaves the date field empty far more often than the source is
# actually undated, and the year is usually sitting in the text.
_YEAR_IN_TEXT = re.compile(r"\b(?:in|since|during|back in)\s+"
                           r"(?:[A-Z][a-z]+\s+)?(19[89]\d|20[0-4]\d)\b")


def _year_from_text(text: str) -> date | None:
    """The year a sentence dates itself to, if it names one.

    Only with a preposition in front of it — "in 2014", "since 2023". A bare
    number is as likely to be a headcount or a funding figure, and mistaking
    "$2019M raised" for a date would be worse than having no date at all.
    Resolved to mid-year, since the month is unknown and December-vs-January
    guessing would swing the age by a year.
    """
    m = _YEAR_IN_TEXT.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), 7, 1)
    except ValueError:
        return None


def _age_days(fact: ExtractedFact, today: date) -> int | None:
    """Age in days, or None when the date cannot be trusted.

    A date on or after the day the run happens is treated as no date at all.
    Extraction stamps "today" on facts it could not date — 4 of 13 dated hooks
    in this database carry the run's own date — and because today scores maximum
    freshness, those undatable facts were beating everything else. A fabricated
    date is worse than a missing one: missing is honest and scores 0.22, while
    "today" is a confident lie that scores 1.0.

    The cost is that a genuinely same-day fact is treated as undated. That is
    the right trade against a third of dates being wrong in the other direction.
    """
    d = _parse_date(fact.date)
    if not d or d >= today:
        # Nothing usable in the date field — try the sentence.
        d = _year_from_text(fact.text)
    if not d or d >= today:
        return None
    return (today - d).days


def is_specific(fact: ExtractedFact) -> bool:
    """A hook has to carry something concrete enough to write a non-generic
    sentence from. 'They're growing fast' is not a hook."""
    t = (fact.text or "").strip()
    if len(t) < 35:
        return False
    for p in VAGUE_PATTERNS:
        if re.search(p, t, re.I):
            return False
    has_number = bool(re.search(r"\d", t))
    has_named_entity = len(fact.key_entities or []) >= 1
    has_quote = '"' in t or "“" in t
    return has_number or has_named_entity or has_quote


# Language a source uses when it is describing something that just happened.
# Present tense about a state of affairs ("is live", "now offers") counts too:
# it asserts currency even when nobody wrote a date.
_FRESH_LANGUAGE = re.compile(
    r"\b(just|recently|this (?:week|month|quarter)|last (?:week|month)|"
    r"newly|new(?:ly)? (?:launched|appointed|hired|joined)|has (?:just|now)|"
    r"today announced|announced today|now (?:live|available|offers?|leads?)|"
    r"is (?:now|currently)|currently|as of (?:this|last))\b", re.I)

# Language that points forward rather than back. "will be turning a year old
# soon" is not evidence that anything has happened.
_FUTURE_LANGUAGE = re.compile(
    r"\b(will|soon|upcoming|plans to|expected to|scheduled to|is set to|"
    r"next (?:week|month|quarter|year))\b", re.I)


def content_recency(fact: ExtractedFact) -> float:
    """What the text itself says about how current it is.

    Only consulted when there is no usable date, which is most of the time: a
    LinkedIn post rarely carries one an extractor can parse, and the alternative
    is treating every undated fact as equally lukewarm. The source saying "just
    launched" is real evidence of recency — weaker than a date, and far better
    than nothing.

    Forward-looking language earns nothing. "Will be turning a year old soon"
    describes something that has not happened, and it was exactly the sentence
    that beat a live product announcement.
    """
    text = fact.text or ""
    if _FUTURE_LANGUAGE.search(text):
        return config.FUTURE_TALK
    if _FRESH_LANGUAGE.search(text):
        return config.FRESH_TALK
    return 1.0


def outreach_value(age: int | None) -> float:
    """Outreach value is NOT the same clock as factual accuracy.

    A five-month-old funding round is still perfectly true and already useless:
    everyone congratulated them months ago, so referencing it now reads as late
    rather than well-researched. Value peaks inside ~3 weeks, decays after.
    """
    if age is None:
        # Usable, never preferred. Low enough that any fact carrying a real
        # recent date beats it — an absent date is missing information, not
        # freshness, and treating it as freshness is how an undated fact
        # outranks the dated version of the same event.
        return config.UNDATED_VALUE
    if age < 0:
        return 0.5                       # future-dated: suspicious metadata
    if age <= config.OUTREACH_PEAK_DAYS:
        return 1.0
    if age <= config.OUTREACH_DECAY_DAYS:
        span = config.OUTREACH_DECAY_DAYS - config.OUTREACH_PEAK_DAYS
        return 1.0 - 0.5 * ((age - config.OUTREACH_PEAK_DAYS) / max(span, 1))
    if age <= config.FACT_RECENCY_MAX_DAYS:
        span = config.FACT_RECENCY_MAX_DAYS - config.OUTREACH_DECAY_DAYS
        return 0.5 - 0.4 * ((age - config.OUTREACH_DECAY_DAYS) / max(span, 1))
    # Past the window it keeps falling rather than dropping to zero, so an old
    # fact stays a candidate and loses to anything fresher instead of vanishing.
    # Halves every year beyond, and never quite reaches nothing.
    years_over = (age - config.FACT_RECENCY_MAX_DAYS) / 365.0
    return max(config.STALE_FLOOR, 0.10 * (0.5 ** years_over))


# Words that appear in every offer and every fact, and so separate nothing.
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "their",
    "our", "are", "was", "were", "has", "have", "had", "not", "but", "all", "any",
    "who", "how", "why", "what", "when", "which", "them", "they", "you", "its",
    "company", "companies", "business", "team", "teams", "people", "new", "using",
    "used", "use", "help", "helps", "make", "makes", "more", "most", "than",
}


# Facts that describe a career rather than an event. These age out of being a
# hook long before they stop being useful for knowing who someone is.
CAREER_CATEGORIES = {"role_change", "promotion", "looking_for"}


def _same_company(a: str, b: str) -> bool:
    from .normalize import company_key
    ka, kb = company_key(a), company_key(b)
    return bool(ka) and bool(kb) and (ka == kb or ka in kb or kb in ka)


def _norm(text: str) -> str:
    """Collapse whitespace and case so two phrasings of one event can be compared."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _terms(text: str) -> set[str]:
    """Meaningful words, lowercased and de-suffixed enough to match loosely."""
    # Two characters, not three: "AI" is the single most load-bearing term in
    # a great many offers, and a three-character floor silently drops it.
    words = re.findall(r"[a-z][a-z0-9]+", (text or "").lower())
    out = set()
    for w in words:
        if w in _STOPWORDS:
            continue
        # Crude stemming: "hiring"/"hires"/"hire" should meet.
        for suffix in ("ing", "ers", "er", "es", "s"):
            if len(w) > 5 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.add(w)
    return out


def offer_terms(writer: WriterConfig | None, persona: dict | None = None) -> set[str]:
    """What the sender actually sells, as words a fact can be matched against.

    Assembled from both the sender config and the active persona because they
    are filled in at different times — the persona on day one, the writer
    config later — and a judgment that reads only one of them is blind half
    the time.
    """
    parts: list[str] = []
    if writer:
        parts += [writer.product, writer.problem_solved, writer.proof]
    if persona:
        parts += [str(persona.get(k) or "") for k in
                  ("product", "problem", "proof", "looking_for")]
    return _terms(" ".join(p for p in parts if p))


def relevance(fact: ExtractedFact, offer: set[str]) -> tuple[float, int]:
    """How much this fact connects to what the sender sells.

    THE POINT OF THE WHOLE RANKING. Without it the system optimises for the most
    interesting fact about a person rather than the one that gives the sender a
    reason to write — so a decade-old research paper beats "they just moved
    their whole product onto AI agents" for a vendor selling AI research tooling.

    Deliberately word overlap rather than a model call: it is deterministic,
    free, explainable in the reason string, and it cannot hallucinate a
    connection that is not there. Its weakness is synonyms, which is a smaller
    problem than a confident invented rationale.
    """
    if not offer:
        return 1.0, 0                    # nothing configured: stay neutral
    hits = len(_terms(fact.text) & offer)
    if hits == 0:
        return config.RELEVANCE_MISS, 0
    return min(1.0 + config.RELEVANCE_STEP * hits, config.RELEVANCE_MAX), hits


# What a person themselves said or did recently, as opposed to something that
# happened to them or to their employer.
ACTIVITY_CATEGORIES = {"influencer", "speaking", "looking_for"}


def activity_boost(fact: ExtractedFact, age: int | None) -> float:
    """Recent activity is the best hook there is, and only while it is recent.

    "I saw what you posted last week" is a different message from "I saw that
    you joined three years ago" — one proves someone read something, the other
    proves someone read a profile. So a post or a talk is worth more than a
    static role fact WHEN IT IS FRESH, and worth no more than anything else once
    it is not. The boost is on the freshness, not on the category.
    """
    if fact.category not in ACTIVITY_CATEGORIES or age is None:
        return 1.0
    if age <= config.OUTREACH_PEAK_DAYS:
        return config.ACTIVITY_BOOST
    if age <= config.OUTREACH_DECAY_DAYS:
        return 1.0 + (config.ACTIVITY_BOOST - 1.0) * 0.4
    return 1.0


def learned_weights(outcomes: dict[str, dict] | None,
                    feedback: dict[str, dict] | None = None) -> dict[str, float]:
    """Multipliers learned from what the user actually sent and hand-picked.

    The taxonomy's defaults are one team's opinion about which triggers matter.
    They are a reasonable starting point and a poor permanent answer: a
    recruiter, an investor and an AP-automation vendor want different things,
    and none of them should have to edit a weights table to say so.

    Only costly acts count. Sending is worth most — a real message to a real
    person. Hand-picking a hook is next, because overruling the ranking is
    deliberate. A hook the user simply left alone is worth nothing: not acting
    is not a preference, and counting it would just re-learn the defaults.

    It never demotes on ABSENCE of evidence — a quiet week is not an opinion.
    It does demote on PRESENCE of a negative one: a fact the user read and
    dropped by hand is the only explicit "not this" the app ever receives, and
    it costs them exactly as much as a hand-pick. Ignoring it meant the system
    could be told no fifty times and learn nothing.

    `feedback` is optional so the ranking still works with no history at all.
    """
    if not outcomes and not feedback:
        return {}
    learned: dict[str, float] = {}
    for category, o in (outcomes or {}).items():
        evidence = o.get("sent", 0) * 2 + o.get("hand_picked", 0)
        if evidence < config.LEARN_MIN_EVIDENCE:
            continue
        capped = min(evidence, config.LEARN_EVIDENCE_CAP)
        learned[category] = round(1.0 + config.LEARN_STEP * capped, 3)

    for category, fb in (feedback or {}).items():
        # Putting a fact back cancels having dropped it. Someone who excluded
        # something and then changed their mind has not rejected the category.
        against = fb.get("excluded", 0) - fb.get("included", 0)
        if against < config.LEARN_MIN_EVIDENCE:
            continue
        capped = min(against, config.LEARN_EVIDENCE_CAP)
        # Multiplicative, so a category that is both often sent and often
        # dropped ends up near where it started rather than at an extreme.
        # Floored: this can make a category unlikely, never unreachable, because
        # a hook nothing else can beat should still be offered.
        base = learned.get(category, 1.0)
        learned[category] = round(
            max(config.LEARN_FLOOR, base * (1.0 - config.LEARN_STEP * capped)), 3)
    return learned


def intent_weight(category: str, writer: WriterConfig | None,
                  learned: dict[str, float] | None = None) -> float:
    """Weight for an intent category.

    Precedence: what the writer explicitly configured, then the taxonomy
    default adjusted by what they have actually sent. An explicit setting is a
    statement and always wins; the learned multiplier is an inference and only
    ever nudges.
    """
    if writer and writer.intent_weights and category in writer.intent_weights:
        return float(writer.intent_weights[category])
    base = DEFAULT_INTENT_WEIGHTS.get(category, DEFAULT_INTENT_WEIGHTS["other"])
    return base * (learned or {}).get(category, 1.0)


def fact_id_for_text(text: str) -> str:
    """A stable handle for a fact, derived from its text.

    Derived rather than stored, and from the text rather than a position: the
    fact list is rebuilt on every judgment, so an index would silently point at
    a different fact the moment anything upstream changed. Deriving it also means
    runs recorded before the field existed can still be addressed.
    """
    import hashlib

    normalised = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalised.encode()).hexdigest()[:12]


def fact_id(fact: ExtractedFact) -> str:
    return fact_id_for_text(fact.text)


def fact_sources(fact: ExtractedFact) -> list[str]:
    """Every URL known to carry this fact, newest evidence model first."""
    urls = list(fact.corroborating_urls or [])
    if fact.source_url and fact.source_url not in urls:
        urls.append(fact.source_url)
    return urls


def priority_band(fact: ExtractedFact, target_company: str) -> int:
    """Which band a fact sits in. Lower is better, and bands beat scores.

    Weights were the wrong instrument for this. Four rounds of tuning kept
    producing a different weak hook, because a multiplier lets a well-evidenced
    trivial fact climb past a plainly better one. An explicit ordering says the
    thing directly:

      0  what they themselves said or did — posts, reposts, talks
      1  their current job, and what that company is doing now
      2  anything else about them
      3  a role at a company that is not their current one

    Within a band, the score still decides. Across bands, it does not get a
    vote — which is the point.
    """
    same_company = (not fact.subject_company or not target_company
                    or _same_company(fact.subject_company, target_company))

    if fact.level == "person" and fact.category in ACTIVITY_CATEGORIES:
        return 0
    if same_company:
        return 1
    if fact.level == "person" and fact.category in CAREER_CATEGORIES:
        # A former employer. Real, and never the opening line.
        return 3
    return 2


def score_fact(
    fact: ExtractedFact,
    age: int | None,
    writer: WriterConfig | None = None,
    offer: set[str] | None = None,
    learned: dict[str, float] | None = None,
    target_company: str = "",
) -> float:
    score = outreach_value(age) * intent_weight(fact.category, writer, learned)

    # Relevance to the offer, applied before every other multiplier so that a
    # fact with nothing to do with what the sender sells cannot win on charm.
    score *= relevance(fact, offer or set())[0]

    # Something they said or did in the last few weeks.
    score *= activity_boost(fact, age)

    # With no date, the wording is the only thing left that speaks to currency.
    if age is None:
        score *= content_recency(fact)

    # An undated fact about them at a DIFFERENT company is career history far
    # more often than news. Keeping such facts is right — they say who someone
    # is — but with no date there is nothing separating "just moved to F2A"
    # from "was a Principal at F2A years ago", and opening a message with the
    # wrong employer is the one mistake that proves nobody read anything. It
    # stays a candidate; it stops winning on provenance alone.
    if (age is None and target_company and fact.level == "person"
            and fact.subject_company
            and not _same_company(fact.subject_company, target_company)):
        score *= config.OTHER_EMPLOYER_UNDATED

    # A person-level hook ("you were on that podcast", "you just moved into this
    # role") is what makes a message feel written FOR someone. A company-level
    # hook is about their employer, and every other rep in their inbox is using
    # the same one. Prefer the person tier when scores are otherwise close.
    tier = fact.level if fact.level in ("person", "company") else tier_of(fact.category)

    # The boost is for a person-level TRIGGER, not for any sentence that happens
    # to be about a person. "other" is the catch-all — it means the fact could
    # not be classified as a trigger at all — and letting it collect the tier
    # boost put "will be turning a year old at Zamp soon" above "Zamp
    # transitioned completely towards AI agents". An unclassified fact is the
    # absence of a reason to write, and it should only win when nothing
    # classified exists.
    if (tier == "person" and fact.category in PERSON_INTENTS
            and (writer is None or writer.prefer_person_signal)):
        score *= PERSON_TIER_BOOST

    # Where the fact came from. A first-party LinkedIn or X post outranks a web
    # article about the same person, and a claim carried by BOTH a post and
    # independent reporting outranks either alone. See pipeline/provenance.py
    # for why the ordering differs between person- and company-level facts.
    score *= provenance.multiplier(tier, fact_sources(fact))

    return round(score, 4)


class Pick(BaseModel):
    """The collective call's answer."""
    index: int = Field(default=-1, description="which candidate to open with, or -1 for none")
    why: str = Field(default="", description="one line, in the words a rep would use")


COLLECTIVE_PROMPT = """Pick the ONE fact to open a cold email to this person with.

WHO
{name}{role_part}, currently at {company}.

WHAT THE SENDER SELLS
{offer}

CANDIDATES
{candidates}

HOW TO CHOOSE
- Something they said or did themselves beats something that happened to them,
  and both beat something their company announced.
- Recent beats old, and a dated fact beats an undated one of similar quality:
  a date is evidence, its absence is not freshness.
- A fact connected to what the sender sells beats a more interesting one that
  is not — the message needs a reason to exist, not a fun opening line.
- NEVER pick a role at a company that is not {company}. Opening on a former
  employer is wrong and provably so in one click.
- Reject an award, a qualification or an anniversary unless nothing else
  remains: nobody replies to being congratulated on a decade-old exam result.
- If none of them is worth opening with, answer -1. A generic message is better
  than a strange one.

`why` is one line explaining the choice to the rep who has to send it.
"""


async def collective_pick(candidates: list[FactVerdict], *, name: str, role: str,
                          company: str, offer: str, today: date) -> tuple[int, str]:
    """One judgment over the whole shortlist, instead of arithmetic per fact.

    The scores narrow the field; this decides among what is left. Ranking each
    fact in isolation and taking the maximum cannot express "of these eight,
    this is the one worth opening with" — every multiplier tried on that problem
    let some well-evidenced trivial fact climb over a plainly better one, and no
    amount of retuning fixed it because the comparison is between facts, not
    within them.

    Returns (index, reason), or (-1, "") when the model declines or fails. The
    deterministic order is always there to fall back to, so a run never depends
    on this working.
    """
    if not candidates:
        return -1, ""

    lines = []
    for i, v in enumerate(candidates):
        age = _age_days(v.fact, today)
        when = "undated" if age is None else f"{round(age / 30)} months old"
        lines.append(
            f"[{i}] {v.fact.text}\n"
            f"     {v.fact.level}-level {v.fact.category} · {when} · "
            f"about {v.fact.subject_company or 'unknown'} · "
            f"{provenance.describe(v.fact.level, fact_sources(v.fact))}")

    try:
        pick = await llm.structured(
            Pick,
            COLLECTIVE_PROMPT.format(
                name=name, role_part=f", {role}" if role else "",
                company=company or "an unknown company",
                offer=offer or "(not configured)",
                candidates="\n".join(lines)),
            model=config.MODEL_FAST)
    except Exception as e:                       # noqa: BLE001 — never fail a run
        log.warning("collective pick failed, using the ranked order: %s", e)
        return -1, ""

    if not (0 <= pick.index < len(candidates)):
        return -1, pick.why
    return pick.index, pick.why.strip()


async def judge(
    facts: list[ExtractedFact],
    *,
    today: date | None = None,
    target_company: str = "",
    writer: WriterConfig | None = None,
    stakeholder: StakeholderProfile | None = None,
    persona: dict | None = None,
    learned: dict[str, float] | None = None,
    name: str = "",
    role: str = "",
) -> JudgeResult:
    today = today or date.today()
    verdicts: list[FactVerdict] = []
    background: list[ExtractedFact] = []
    offer = offer_terms(writer, persona)

    # Two facts can describe the same event with only one of them dated —
    # "in the Founder's Office since January 2023" and "joined the Founder's
    # Office". The dated one is gated out as stale and the undated one sails
    # through, so the system rewards the version that says less. An undated
    # fact inherits the age of a stale near-twin.
    stale_twins = [(f.text, _age_days(f, today)) for f in facts
                   if (a := _age_days(f, today)) is not None
                   and a > config.FACT_RECENCY_MAX_DAYS]

    def inherited_age(f: ExtractedFact) -> int | None:
        """The age of a stale fact describing the same event, if there is one.

        Compared on content words rather than character ratio. Two phrasings of
        one event — "joined the Founder's Office" and "has been in the
        Founder's Office since January 2023" — scored 0.618 on characters
        against a 0.62 threshold, which is not a distinction anyone could tune
        reliably. Shared words are what actually make them the same event.
        """
        mine = _terms(f.text)
        if not mine:
            return None
        for text, twin_age in stale_twins:
            theirs = _terms(text)
            # Containment, not Jaccard: the dated version carries extra words
            # ("since January 2023") that inflate the union and hide the fact
            # that one restates the other. What matters is how much of the
            # shorter fact is already in the longer one.
            shared = len(mine & theirs) / max(min(len(mine), len(theirs)), 1)
            if shared >= config.TWIN_SIMILARITY:
                return twin_age
        return None

    for f in facts:
        age = _age_days(f, today)
        borrowed = False
        if age is None and (twin := inherited_age(f)) is not None:
            age, borrowed = twin, True

        # Gate 1 — category. Hard exclusion, never a preference the model can
        # override. Referencing layoffs as a sales hook is actively harmful.
        if f.category in config.BLOCKED_CATEGORIES or f.category in BLOCKED_INTENTS:
            verdicts.append(FactVerdict(
                fact=f, eligible=False,
                reason=f"never used — '{f.category}' is a reputationally sensitive category",
                score=0.0))
            continue

        # Age is a penalty, not a gate.
        #
        # It used to be a hard exclusion, and that threw away real signal: an
        # old fact is often the only thing that says who someone is, and a run
        # with nothing left to say falls back to a generic message — which is
        # worse than an honest older reference. Everything stays a candidate;
        # recency decides the ranking, and a stale fact only wins when nothing
        # fresher exists.
        stale = age is not None and age > config.FACT_RECENCY_MAX_DAYS
        if stale and f.category in CAREER_CATEGORIES and f.level == "person":
            background.append(f)

        # Gate 3 — specificity.
        if not is_specific(f):
            verdicts.append(FactVerdict(
                fact=f, eligible=False,
                reason="ranked last — too vague to write a specific line from",
                score=0.0))
            continue

        score = score_fact(f, age, writer, offer, learned, target_company)
        fit, hits = relevance(f, offer)

        # Not a gate on truth — a gate on being the OPENING LINE. The fact stays
        # in the list with its score and its reason; it just loses. Nothing is
        # thrown away, because an old fact is often the only thing that says who
        # someone is, and it still reaches the draft as background.
        if score <= 0.05:
            verdicts.append(FactVerdict(
                fact=f, eligible=False,
                reason="not the hook — still accurate, but outranked by anything more recent",
                score=score))
            continue

        tier = f.level if f.level in ("person", "company") else tier_of(f.category)
        months = None if age is None else round(age / 30)
        dated_today = bool(_parse_date(f.date)) and age is None
        age_txt = (("dated today, which extraction does when it cannot find a real "
                    "date — treated as undated") if dated_today
                   else "date unknown" if age is None
                   else f"{months} months old — too old to lead with" if stale
                   else f"{age}d old")
        fresh_txt = ("; recent activity of theirs"
                     if activity_boost(f, age) > 1.0 else "")
        if (age is None and target_company and f.level == "person" and f.subject_company
                and not _same_company(f.subject_company, target_company)):
            fresh_txt += (f"; undated and about them at {f.subject_company}, "
                          f"not {target_company} — likely career history")
        if age is None:
            cr = content_recency(f)
            if cr > 1.0:
                fresh_txt += "; the source describes it as current"
            elif cr < 1.0:
                fresh_txt += "; it describes something that has not happened yet"
        learned_txt = ("" if not (learned or {}).get(f.category)
                       else f"; you act on {f.category.replace('_', ' ')} hooks")
        fit_txt = ("" if not offer
                   else f"; {hits} word(s) in common with what you sell" if hits
                   else "; nothing in common with what you sell")
        reason = (f"eligible — {tier}-level {f.category}, {age_txt}, specific and safe"
                  f"{fresh_txt}{learned_txt}{fit_txt}; "
                  f"{provenance.describe(tier, fact_sources(f))}")

        # Not a gate: a company milestone referenced at a junior prospect should
        # be framed as context, not as their personal achievement.
        if stakeholder:
            from .profile import hook_fits_role
            fits, note = hook_fits_role(f.category, stakeholder)
            if not fits:
                reason += f" (note: {note})"

        verdicts.append(FactVerdict(fact=f, eligible=True, reason=reason, score=score))

    # Oldest first, so a trajectory reads as one.
    background.sort(key=lambda f: f.date or "")

    # Band first, score second. What they said outranks what happened to them,
    # and their current company outranks a former one however well evidenced.
    eligible = sorted(
        [v for v in verdicts if v.eligible],
        key=lambda v: (priority_band(v.fact, target_company), -v.score))

    if not eligible:
        # Last resort. Nothing cleared the bar, and the alternative is a message
        # with no reason to exist — so the best surviving fact is used, provided
        # it is merely old rather than ancient. A reference to something from
        # last year reads as thin; one from a decade ago reads as automated, and
        # that line is where this stops.
        limit = config.FACT_RECENCY_MAX_DAYS * config.LAST_RESORT_MULTIPLE
        salvage = [v for v in verdicts
                   if v.score > 0
                   and v.fact.category not in config.BLOCKED_CATEGORIES
                   and (a := _age_days(v.fact, today)) is not None and a <= limit]
        if salvage:
            pick = max(salvage, key=lambda v: v.score)
            months = round(_age_days(pick.fact, today) / 30)
            return JudgeResult(
                verdicts=verdicts, chosen=pick.fact, background=background,
                chosen_reason=(f"nothing recent cleared the bar, so the best of what "
                               f"remains was used — {months} months old. Read it before "
                               f"sending: an old reference can read as automated"))
        return JudgeResult(verdicts=verdicts, chosen=None, background=background,
                           chosen_reason="no candidate cleared the eligibility gates")

    # The rules narrow; one call decides. A shortlist rather than everything,
    # so the model is choosing between real contenders instead of re-deriving
    # the gates — and capped, because a long list is where attention goes.
    shortlist = eligible[:config.SHORTLIST_SIZE]
    picked_reason = ""
    if len(shortlist) > 1 and config.COLLECTIVE_PICK:
        index, why = await collective_pick(
            shortlist, name=name, role=role, company=target_company,
            offer=", ".join(sorted(offer)[:24]), today=today)
        if index >= 0:
            # Move the chosen one to the front, keeping the rest in rank order.
            chosen_v = shortlist[index]
            eligible = [chosen_v] + [v for v in eligible if v is not chosen_v]
            picked_reason = why

    best = eligible[0]
    runners = eligible[1:]
    best_tier = best.fact.level if best.fact.level in ("person", "company") else tier_of(best.fact.category)
    band = priority_band(best.fact, target_company)
    band_txt = {0: "something they posted or said themselves",
                1: "about them or their current company",
                2: "about them",
                3: "a former employer — nothing more current was found"}[band]
    reason = (f"{band_txt}; best of that group at {best.score} — "
              f"{best_tier}-level {best.fact.category}")
    if picked_reason:
        reason = f"{picked_reason} (chosen across {len(shortlist)} candidates); {reason}"
    if best_tier == "person":
        reason += ", preferred because it is about them rather than their employer"
    if (boost := (learned or {}).get(best.fact.category)):
        reason += (f", and it is a hook type you act on "
                   f"(weighted x{boost} from what you have sent)")
    if (hits := relevance(best.fact, offer)[1]):
        reason += f", and it touches what you sell ({hits} term(s) in common)"
    reason += f"; {provenance.describe(best_tier, fact_sources(best.fact))}"
    if runners:
        reason += f"; {len(runners)} runner-up hook(s) available"
    best_age = _age_days(best.fact, today)
    if best_age is not None and best_age > config.FACT_RECENCY_MAX_DAYS:
        reason += (f" — NOTE: {round(best_age / 30)} months old, chosen only "
                   f"because nothing more recent cleared the bar")
    if background:
        reason += f"; {len(background)} career fact(s) also kept as background"
    return JudgeResult(verdicts=verdicts, chosen=best.fact, chosen_reason=reason,
                       background=background)
