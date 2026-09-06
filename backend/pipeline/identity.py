"""Stage 0 — identity resolution.

Confidence is a transparent, additive score, not a model's feeling. That matters
because the interview question "why did it ask for clarification on this
prospect but not that one?" needs an answer better than "the model was unsure".

Scoring (max 100):
    company matches evidence   +40
    role/title consistent      +20
    location matches           +20
    supplied URL corroborates  +20

Outcomes:
    one candidate >= 60, and clear of the next by >= 20  -> proceed
    two or more close candidates                         -> STOP, ask the human
    nothing >= 60                                        -> proceed, flagged low confidence

Researching the wrong person is not a small error — it wastes the run and can
badly embarrass the rep. One extra click beats that.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import config
from ..integrations import llm
from ..models import IdentityCandidate, IdentityResult, ProspectInput, SearchHit


class _Candidate(BaseModel):
    company: str = Field(default="", description="Employer of this candidate.")
    role: str = Field(default="", description="Their role/title, if stated.")
    location: str = Field(default="", description="Their location, if stated.")
    evidence: str = Field(default="", description="One sentence: what in the sources supports this.")
    company_match: bool = Field(default=False, description="Matches the company the user supplied.")
    role_match: bool = Field(default=False, description="Consistent with the role the user supplied.")
    location_match: bool = Field(default=False, description="Matches the location the user supplied.")
    url_match: bool = Field(default=False, description="Corroborated by the reference URL supplied.")


class _IdentityRead(BaseModel):
    candidates: list[_Candidate] = Field(default_factory=list)


PROMPT = """You are resolving WHO a sales prospect actually is, from search results.

Prospect as supplied by the user:
  name:     {name}
  company:  {company}
  role:     {role}
  location: {location}
  url:      {url}

Search results:
{sources}

List every DISTINCT real person these results could plausibly be referring to,
who matches the supplied name. One entry per distinct person/employer pairing.

Rules:
- Do not invent people. Only list candidates the sources actually support.
- If all results clearly describe one person, return exactly one candidate.
- Set the *_match booleans strictly against what the user supplied above.
  If the user supplied nothing for a field, that match is false.
- `evidence` must be grounded in the results, not inferred.
"""


def _score(c: _Candidate) -> int:
    return (40 if c.company_match else 0) + (20 if c.role_match else 0) \
         + (20 if c.location_match else 0) + (20 if c.url_match else 0)


async def resolve(p: ProspectInput, hits: list[SearchHit]) -> IdentityResult:
    # If the user gave us a company, identity is already largely pinned. We skip
    # a model call entirely — cheaper, faster, and there is nothing to disambiguate.
    if p.company.strip():
        return IdentityResult(
            resolved=True,
            confidence=100 if p.location or p.url or p.role else 80,
            chosen=IdentityCandidate(
                company=p.company, role=p.role, location=p.location,
                evidence="company supplied directly by the user", score=100,
            ),
            note="company supplied — no disambiguation needed",
        )

    if not hits:
        return IdentityResult(
            resolved=True, confidence=0, chosen=None,
            note="no search results to resolve identity against",
        )

    sources = "\n\n".join(
        f"[{i+1}] {h.title}\n{h.url}\n{h.content[:700]}" for i, h in enumerate(hits[:8])
    )
    read = await llm.structured(
        _IdentityRead,
        PROMPT.format(name=p.name, company=p.company or "(not supplied)",
                      role=p.role or "(not supplied)",
                      location=p.location or "(not supplied)",
                      url=p.url or "(not supplied)", sources=sources),
        model=config.MODEL_FAST,
        system="You resolve identities strictly from evidence. You never invent people.",
    )

    scored = sorted(
        [(c, _score(c)) for c in read.candidates], key=lambda t: t[1], reverse=True
    )
    candidates = [
        IdentityCandidate(company=c.company, role=c.role, location=c.location,
                          evidence=c.evidence, score=s)
        for c, s in scored
    ]

    if not candidates:
        return IdentityResult(resolved=True, confidence=0, chosen=None,
                              note="no identifiable candidate found in sources")

    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None

    # Ambiguous: two plausible people, too close to call. Stop and ask.
    if second and top.score >= config.IDENTITY_AUTO_PROCEED_SCORE and \
       (top.score - second.score) < config.IDENTITY_AMBIGUITY_MARGIN:
        return IdentityResult(
            resolved=False, confidence=top.score, candidates=candidates[:4],
            note="multiple plausible matches — confirm which person before researching",
        )

    # More than one candidate found, none scoring well: still ambiguous, and
    # guessing here is exactly how you research a stranger.
    if top.score < config.IDENTITY_AUTO_PROCEED_SCORE and len(candidates) > 1:
        return IdentityResult(
            resolved=False, confidence=top.score, candidates=candidates[:4],
            note="no high-confidence match — confirm which person is correct",
        )

    return IdentityResult(
        resolved=True, confidence=top.score, chosen=top, candidates=candidates[:4],
        note="single high-confidence match" if top.score >= config.IDENTITY_AUTO_PROCEED_SCORE
             else "low-confidence match — treat the result with caution",
    )
