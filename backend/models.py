"""Pydantic schemas. These double as the structured-output contracts sent to Gemini."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Run status vocabulary.
#
# The distinction between `no_signal_found` and `research_failed` is deliberate
# and load-bearing: one is a real finding about the prospect, the other is an
# infrastructure problem. Collapsing them makes every downstream metric lie.
# --------------------------------------------------------------------------
RunStatus = Literal[
    "queued",
    "running",
    "needs_disambiguation",
    "completed",
    "no_signal_found",
    "research_failed",
    "error",
]

StageName = Literal["profile", "identity", "research", "extract", "ground", "judge", "draft"]


class ProspectInput(BaseModel):
    name: str
    company: str = ""
    role: str = ""
    location: str = ""
    url: str = ""
    relationship: str = ""   # customer / open_opp / competitor / contacted / blank
    email: str = ""          # where a drafted message would actually be sent


# --------------------------------------------------------------------------
# "Getting to the right person" — ICP and stakeholder targeting
# --------------------------------------------------------------------------
class ICPConfig(BaseModel):
    """Who you actually sell to. Empty fields mean 'no preference'."""
    industries: list[str] = Field(default_factory=list)
    geographies: list[str] = Field(default_factory=list)
    headcount_min: int | None = None
    headcount_max: int | None = None
    revenue_note: str = ""
    seniorities: list[str] = Field(default_factory=list)   # cxo / vp / director / manager / ic
    functions: list[str] = Field(default_factory=list)     # operations / finance / risk / ...


class StakeholderProfile(BaseModel):
    seniority: str = "unknown"
    function: str = "unknown"
    angle_seniority: str = ""
    angle_function: str = ""
    title_known: bool = False


class ICPFit(BaseModel):
    score: int = 100
    reasons_for: list[str] = Field(default_factory=list)
    reasons_against: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# "Getting the right message" — who is writing, and why
# --------------------------------------------------------------------------
class WriterConfig(BaseModel):
    """The sender's identity and offer.

    Without this the system optimises for 'most interesting fact about the
    prospect' rather than 'fact that best connects them to what I sell' —
    which is the wrong objective.
    """
    sender_name: str = ""
    sender_role: str = ""          # CEO / CRO / Sales / Founder ...
    sender_company: str = ""
    product: str = ""              # what you sell
    problem_solved: str = ""       # the pain you remove
    proof: str = ""                # customers, results, credibility
    intent: str = "book_meeting"   # book_meeting / intro / partnership / hiring / research
    tone: str = "direct, peer-to-peer, no corporate filler"

    # Relative priority across intent categories; overrides taxonomy defaults.
    intent_weights: dict[str, float] = Field(default_factory=dict)
    prefer_person_signal: bool = True

    def configured(self) -> bool:
        return bool(self.product or self.problem_solved or self.sender_company)


class StyleExample(BaseModel):
    """A draft the user edited — used to teach the system their voice."""
    original: str
    edited: str
    prospect: str = ""
    created_at: str = ""


class IdentityCandidate(BaseModel):
    company: str = ""
    role: str = ""
    location: str = ""
    evidence: str = ""
    score: int = 0


class IdentityResult(BaseModel):
    resolved: bool
    confidence: int
    candidates: list[IdentityCandidate] = Field(default_factory=list)
    chosen: Optional[IdentityCandidate] = None
    note: str = ""


class SearchHit(BaseModel):
    title: str
    url: str
    content: str
    score: float = 0.0
    query: str = ""


class ExtractedFact(BaseModel):
    """One candidate fact pulled out of retrieved source text."""
    text: str = Field(description="The fact, stated in one sentence.")
    level: str = Field(
        default="company",
        description=(
            "'person' if this is about the individual themselves (their promotion, "
            "role change, talk, podcast, byline, quote, personal award, or something "
            "they publicly said they need). 'company' if it is about their employer."
        ),
    )
    category: str = Field(
        description=(
            "PERSON-level: promotion, role_change, looking_for, influencer, "
            "personal_award, speaking. "
            "COMPANY-level: hiring, cost_reduction, ai_transformation, fundraise, "
            "ipo, expansion, product_launch, partnership, award. "
            "EXCLUDED: layoff, lawsuit, controversy, personal_life. "
            "Otherwise: other"
        )
    )
    date: str = Field(description="ISO date YYYY-MM-DD of the EVENT, or empty if unknown.")
    key_entities: list[str] = Field(
        default_factory=list,
        description=(
            "Verbatim strings from the source that prove this fact — amounts, "
            "company names, investor names, role titles. Used for grounding checks."
        ),
    )
    source_url: str = ""
    subject_company: str = Field(default="", description="Which company this fact is about.")
    subject_person: str = Field(
        default="",
        description=(
            "The full name of the individual this fact is ABOUT, exactly as the "
            "source writes it, or an empty string if the fact is about an "
            "organisation rather than a person. A fact can mention several "
            "people — name only the one the fact is about. For 'Priya Nair was "
            "promoted to CTO' this is 'Priya Nair'. For 'Acme raised $12M' it is "
            "empty even if a founder is quoted."
        ),
    )

    # Filled in by the pipeline, never by the model — see pipeline/provenance.py.
    # When syndication collapsing merges near-identical facts, every source that
    # carried the story is recorded here. That is what distinguishes a claim
    # confirmed by two independent parties from one press release repeated ten
    # times, which is a distinction the hook score depends on.
    corroborating_urls: list[str] = Field(
        default_factory=list, json_schema_extra={"llm": False},
    )


class ExtractionResult(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)


class FactVerdict(BaseModel):
    fact: ExtractedFact
    eligible: bool
    reason: str
    score: float = 0.0


class JudgeResult(BaseModel):
    verdicts: list[FactVerdict] = Field(default_factory=list)
    chosen: Optional[ExtractedFact] = None
    chosen_reason: str = ""
    # Career history: where they have worked and in what roles. Too old to open
    # a message with, and exactly what tells you who you are writing to — a VP
    # at one company a decade ago who is now an SVP somewhere else is a
    # different person to write to than someone in their first such role.
    background: list[ExtractedFact] = Field(default_factory=list)


class DraftResult(BaseModel):
    subject: str = ""
    body: str = ""
    grounded: bool = True
    note: str = ""


class StageEvent(BaseModel):
    stage: StageName
    status: Literal["started", "done", "failed", "skipped"]
    detail: str = ""
    payload: dict = Field(default_factory=dict)
    elapsed_ms: int = 0
