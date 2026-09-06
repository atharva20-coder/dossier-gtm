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
        return 0.35                      # unknown date: usable, never preferred
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
    return 0.0


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
) -> float:
    score = outreach_value(age) * intent_weight(fact.category, writer)

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
) -> JudgeResult:
    today = today or date.today()
    verdicts: list[FactVerdict] = []

    for f in facts:
        age = _age_days(f, today)

        # Gate 1 — category. Hard exclusion, never a preference the model can
        # override. Referencing layoffs as a sales hook is actively harmful.
        if f.category in config.BLOCKED_CATEGORIES or f.category in BLOCKED_INTENTS:
            verdicts.append(FactVerdict(
                fact=f, eligible=False,
                reason=f"excluded — '{f.category}' is a reputationally sensitive category",
                score=0.0))
            continue

        # Gate 2 — factual recency.
        if age is not None and age > config.FACT_RECENCY_MAX_DAYS:
            months = round(age / 30)
            verdicts.append(FactVerdict(
                fact=f, eligible=False,
                reason=f"excluded — stale, roughly {months} months old",
                score=0.0))
            continue

        # Gate 3 — specificity.
        if not is_specific(f):
            verdicts.append(FactVerdict(
                fact=f, eligible=False,
                reason="excluded — too vague to write a specific line from",
                score=0.0))
            continue

        score = score_fact(f, age, writer)

        # Gate 4 — outreach value floor. Technically true, practically dead.
        if score <= 0.05:
            verdicts.append(FactVerdict(
                fact=f, eligible=False,
                reason="excluded — still accurate but past its useful outreach window",
                score=score))
            continue

        tier = f.level if f.level in ("person", "company") else tier_of(f.category)
        age_txt = "date unknown" if age is None else f"{age}d old"
        reason = (f"eligible — {tier}-level {f.category}, {age_txt}, specific and safe; "
                  f"{provenance.describe(tier, fact_sources(f))}")

        # Not a gate: a company milestone referenced at a junior prospect should
        # be framed as context, not as their personal achievement.
        if stakeholder:
            from .profile import hook_fits_role
            fits, note = hook_fits_role(f.category, stakeholder)
            if not fits:
                reason += f" (note: {note})"

        verdicts.append(FactVerdict(fact=f, eligible=True, reason=reason, score=score))

    eligible = sorted([v for v in verdicts if v.eligible], key=lambda v: v.score, reverse=True)
    if not eligible:
        return JudgeResult(verdicts=verdicts, chosen=None,
                           chosen_reason="no candidate cleared the eligibility gates")

    best = eligible[0]
    runners = eligible[1:]
    best_tier = best.fact.level if best.fact.level in ("person", "company") else tier_of(best.fact.category)
    reason = f"highest score ({best.score}) — {best_tier}-level {best.fact.category}"
    if best_tier == "person":
        reason += ", preferred because it is about them rather than their employer"
    reason += f"; {provenance.describe(best_tier, fact_sources(best.fact))}"
    if runners:
        reason += f"; {len(runners)} runner-up hook(s) available"
    return JudgeResult(verdicts=verdicts, chosen=best.fact, chosen_reason=reason)
