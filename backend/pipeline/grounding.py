"""Grounding verification — the single most important safeguard in the system.

The failure it prevents: the model reads five articles and produces a clean,
confident fact — "Raised $18M Series B led by Accel" — that appears in none of
them. It passes every other guardrail (recent, safe category, specific), gets
drafted into a warm email, and reaches a founder who did not raise $18M.

That is the highest-severity SILENT failure in the pipeline. One occurrence
doesn't just embarrass a rep, it ends their trust in the tool permanently.

The fix is deliberately dumb and deterministic: every fact must carry verbatim
key entities, and those entities must actually appear in the retrieved source
text. No model self-assessment, no "confidence score" — a string check the
model cannot talk its way around.
"""
from __future__ import annotations

import re
import unicodedata

from ..models import ExtractedFact, SearchHit


def _norm(s: str) -> str:
    """Fold case, unicode and punctuation so trivial formatting differences
    don't cause false rejections."""
    s = unicodedata.normalize("NFKD", s or "")
    s = s.lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = re.sub(r"[\s ]+", " ", s)
    return s.strip()


def _money_variants(token: str) -> set[str]:
    """'$12M' should match '$12 million', '12M', 'USD 12 million'."""
    t = _norm(token)
    out = {t}
    m = re.match(r"^\$?\s*([\d.,]+)\s*(m|mn|million|bn|b|billion|k|cr|crore)?\b", t)
    if m:
        num, unit = m.group(1), (m.group(2) or "")
        num_clean = num.rstrip(".")
        out.add(num_clean)
        expand = {
            "m": ["m", "mn", "million"], "mn": ["m", "mn", "million"],
            "million": ["m", "mn", "million"],
            "b": ["b", "bn", "billion"], "bn": ["b", "bn", "billion"],
            "billion": ["b", "bn", "billion"],
            "k": ["k", "thousand"],
            "cr": ["cr", "crore"], "crore": ["cr", "crore"],
        }
        for u in expand.get(unit, [unit] if unit else []):
            out.add(f"{num_clean}{u}")
            out.add(f"{num_clean} {u}")
            out.add(f"${num_clean}{u}")
            out.add(f"${num_clean} {u}")
    return {o for o in out if len(o) >= 2}


def entity_supported(entity: str, corpus: str) -> bool:
    """Is this single entity present in the retrieved text?"""
    e = _norm(entity)
    if len(e) < 3:
        return False
    if e in corpus:
        return True
    # Money/number tolerance
    if any(v in corpus for v in _money_variants(entity)):
        return True
    # Multi-word entities: accept if every significant word appears near-ish.
    words = [w for w in re.split(r"\W+", e) if len(w) > 2]
    if len(words) >= 2 and all(w in corpus for w in words):
        return True
    return False


def verify_fact(
    fact: ExtractedFact,
    hits: list[SearchHit],
    *,
    min_ratio: float = 0.6,
) -> tuple[bool, str]:
    """Return (grounded, reason).

    A fact is grounded when enough of its declared key entities are literally
    present in retrieved source text. Facts with no declared entities are
    rejected — an unfalsifiable claim is exactly the shape a hallucination takes.
    """
    corpus = _norm(" \n ".join(f"{h.title} {h.content}" for h in hits))
    if not corpus:
        return False, "no source text available to verify against"

    entities = [e for e in (fact.key_entities or []) if e and len(e.strip()) >= 3]
    if not entities:
        return False, "no verifiable entities were provided for this fact"

    supported = [e for e in entities if entity_supported(e, corpus)]
    ratio = len(supported) / len(entities)

    if ratio < min_ratio:
        missing = [e for e in entities if e not in supported][:3]
        return False, f"not found in sources: {', '.join(missing)}"

    return True, f"verified {len(supported)}/{len(entities)} entities in source text"


def _squash(s: str) -> str:
    """Letters and digits only — no spaces, punctuation or case."""
    return re.sub(r"[^a-z0-9]", "", _norm(s))


def named_in_prose(entity: str, draft_norm: str, draft_squashed: str) -> bool:
    """Is this entity referred to in a sentence a human would actually write?

    Looser than `entity_supported` on purpose, and only ever used against a
    draft — never against source text. The two checks answer different
    questions. Grounding asks "did the source say this", where a verbatim match
    is the whole safeguard. This asks "does the message carry the specific
    detail", where insisting on the exact string rejects correct drafts.

    The case that forced it: a hook drawn from X carries the handles it was
    written with — `@LightconePod`, `@pedroh96`. Nobody writes an email saying
    "you were on @LightconePod", so every attempt was rejected and the run
    finished with a hook and no message at all. Squashing to letters and digits
    lets "the Lightcone podcast" satisfy `@LightconePod`, while still requiring
    the name itself to be there.
    """
    if entity_supported(entity, draft_norm):
        return True
    squashed = _squash(entity.lstrip("@"))
    return len(squashed) >= 5 and squashed in draft_squashed


def verify_draft(draft: str, fact: ExtractedFact) -> tuple[bool, str]:
    """The draft must actually carry the hook's specific detail.

    Guards two failures at once:
      * a generic email wearing a 'personalised' label
      * hallucination entering at the drafting stage rather than extraction
    """
    d = _norm(draft)
    if not d:
        return False, "empty draft"

    entities = [e for e in (fact.key_entities or []) if e and len(e.strip()) >= 3]
    if not entities:
        return False, "hook has no verifiable detail to carry into the draft"

    squashed = _squash(draft)
    if any(named_in_prose(e, d, squashed) for e in entities):
        return True, "draft carries the hook's specific detail"
    return False, "draft does not mention the hook's specific detail"


GENERIC_OPENERS = [
    "hope this finds you well",
    "hope this email finds you",
    "i hope you're doing well",
    "i hope you are doing well",
    "i wanted to reach out",
    "just reaching out",
    "i'm reaching out because",
    "quick question for you",
]


def generic_opener_used(draft: str) -> str | None:
    d = _norm(draft)
    for phrase in GENERIC_OPENERS:
        if phrase in d:
            return phrase
    return None
