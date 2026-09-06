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

import re
from datetime import date, datetime

from .. import config
from ..models import ExtractedFact, FactVerdict, JudgeResult, StakeholderProfile, WriterConfig
from ..taxonomy import (
    BLOCKED_INTENTS,
    DEFAULT_INTENT_WEIGHTS,
    PERSON_TIER_BOOST,
    tier_of,
)
from . import provenance

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


def _age_days(fact: ExtractedFact, today: date) -> int | None:
    d = _parse_date(fact.date)
    return None if not d else (today - d).days


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


def intent_weight(category: str, writer: WriterConfig | None) -> float:
    """Weight for an intent category.

    Order of precedence: what the writer explicitly configured, then the
    taxonomy default. This is what lets an investor treat 'fundraise' as their
    top signal while an AP-automation vendor treats 'hiring' as theirs, with no
    code change.
    """
    if writer and writer.intent_weights and category in writer.intent_weights:
        return float(writer.intent_weights[category])
    return DEFAULT_INTENT_WEIGHTS.get(category, DEFAULT_INTENT_WEIGHTS["other"])


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


def score_fact(
    fact: ExtractedFact,
    age: int | None,
    writer: WriterConfig | None = None,
    offer: set[str] | None = None,
) -> float:
    score = outreach_value(age) * intent_weight(fact.category, writer)

    # Relevance to the offer, applied before every other multiplier so that a
    # fact with nothing to do with what the sender sells cannot win on charm.
    score *= relevance(fact, offer or set())[0]

    # Something they said or did in the last few weeks.
    score *= activity_boost(fact, age)

    # A person-level hook ("you were on that podcast", "you just moved into this
    # role") is what makes a message feel written FOR someone. A company-level
    # hook is about their employer, and every other rep in their inbox is using
    # the same one. Prefer the person tier when scores are otherwise close.
    tier = fact.level if fact.level in ("person", "company") else tier_of(fact.category)
    if tier == "person" and (writer is None or writer.prefer_person_signal):
        score *= PERSON_TIER_BOOST

    # Where the fact came from. A first-party LinkedIn or X post outranks a web
    # article about the same person, and a claim carried by BOTH a post and
    # independent reporting outranks either alone. See pipeline/provenance.py
    # for why the ordering differs between person- and company-level facts.
    score *= provenance.multiplier(tier, fact_sources(fact))

    return round(score, 4)


def judge(
    facts: list[ExtractedFact],
    *,
    today: date | None = None,
    target_company: str = "",
    writer: WriterConfig | None = None,
    stakeholder: StakeholderProfile | None = None,
    persona: dict | None = None,
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

        score = score_fact(f, age, writer, offer)
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
        age_txt = ("date unknown" if age is None
                   else f"{months} months old — too old to lead with" if stale
                   else f"{age}d old")
        fresh_txt = ("; recent activity of theirs"
                     if activity_boost(f, age) > 1.0 else "")
        fit_txt = ("" if not offer
                   else f"; {hits} word(s) in common with what you sell" if hits
                   else "; nothing in common with what you sell")
        reason = (f"eligible — {tier}-level {f.category}, {age_txt}, specific and safe"
                  f"{fresh_txt}{fit_txt}; {provenance.describe(tier, fact_sources(f))}")

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

    eligible = sorted([v for v in verdicts if v.eligible], key=lambda v: v.score, reverse=True)

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

    best = eligible[0]
    runners = eligible[1:]
    best_tier = best.fact.level if best.fact.level in ("person", "company") else tier_of(best.fact.category)
    reason = f"highest score ({best.score}) — {best_tier}-level {best.fact.category}"
    if best_tier == "person":
        reason += ", preferred because it is about them rather than their employer"
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
