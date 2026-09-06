"""ICP, stakeholder and intent taxonomies.

The structure here mirrors how a GTM team actually thinks about targeting,
rather than how an engineer would model it:

    Getting to the right person   ->  ICP fit + stakeholder fit
    Getting the right message     ->  writer persona + intent priorities
    Making it sound like you      ->  learned style from edits

Two intent tiers are modelled SEPARATELY and weighted independently, because
"the company raised a round" and "this person just got promoted" are different
kinds of reason to reach out, and different sellers care about different ones.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Stakeholder: seniority
# ---------------------------------------------------------------------------
SENIORITY_ORDER = ["cxo", "vp", "director", "manager", "ic", "unknown"]

SENIORITY_PATTERNS: list[tuple[str, list[str]]] = [
    ("cxo", [r"\bceo\b", r"\bcfo\b", r"\bcoo\b", r"\bcto\b", r"\bcmo\b", r"\bcro\b",
             r"\bciso\b", r"\bcio\b", r"\bcpo\b", r"\bchro\b", r"\bchief\b",
             r"\bfounder\b", r"\bco-?founder\b", r"\bowner\b", r"\bpartner\b",
             r"\bmanaging director\b", r"\bpresident\b", r"\bproprietor\b"]),
    ("vp", [r"\bvp\b", r"\bv\.p\.\b", r"\bvice president\b", r"\bsvp\b", r"\bevp\b",
            r"\bavp\b", r"\bhead of\b", r"\bgeneral manager\b", r"\bgm\b"]),
    ("director", [r"\bdirector\b", r"\bsr\.? director\b", r"\bassociate director\b",
                  r"\bprincipal\b", r"\bgroup lead\b"]),
    ("manager", [r"\bmanager\b", r"\bmgr\b", r"\blead\b", r"\bsupervisor\b",
                 r"\bteam lead\b"]),
    ("ic", [r"\banalyst\b", r"\bassociate\b", r"\bengineer\b", r"\bdeveloper\b",
            r"\bspecialist\b", r"\bexecutive\b", r"\bconsultant\b", r"\bofficer\b",
            r"\brepresentative\b", r"\bcoordinator\b", r"\bintern\b"]),
]

# ---------------------------------------------------------------------------
# Stakeholder: function
# ---------------------------------------------------------------------------
FUNCTION_PATTERNS: list[tuple[str, list[str]]] = [
    ("operations", [r"\boperations?\b", r"\bops\b", r"\bsupply chain\b", r"\blogistics\b",
                    r"\bfulfil?lment\b", r"\bprocurement\b", r"\bbizops\b"]),
    ("finance", [r"\bfinance\b", r"\bfinancial\b", r"\baccounts?\b", r"\baccounting\b",
                 r"\bcontroller\b", r"\btreasur\w*\b", r"\bfp&a\b", r"\bcfo\b", r"\bpayables?\b"]),
    ("collections", [r"\bcollections?\b", r"\brecovery\b", r"\breceivables?\b", r"\bdunning\b"]),
    ("risk", [r"\brisk\b", r"\bcompliance\b", r"\bfraud\b", r"\baml\b", r"\bkyc\b",
              r"\bgovernance\b", r"\baudit\b", r"\bregulatory\b"]),
    ("customer_success", [r"\bcustomer success\b", r"\bcustomer experience\b", r"\bcx\b",
                          r"\bsupport\b", r"\bservice delivery\b", r"\baccount management\b",
                          r"\bclient success\b"]),
    ("sales", [r"\bsales\b", r"\brevenue\b", r"\bbusiness development\b", r"\bbd\b",
               r"\bgtm\b", r"\bgrowth\b", r"\bcro\b"]),
    ("marketing", [r"\bmarketing\b", r"\bbrand\b", r"\bdemand gen\w*\b", r"\bcommunications?\b"]),
    ("technology", [r"\bengineering\b", r"\btechnology\b", r"\btech\b", r"\bit\b", r"\bdata\b",
                    r"\bplatform\b", r"\bsoftware\b", r"\bcto\b", r"\bsecurity\b"]),
    ("product", [r"\bproduct\b", r"\bdesign\b", r"\bux\b"]),
    ("people", [r"\bhr\b", r"\bhuman resources?\b", r"\btalent\b", r"\brecruit\w*\b", r"\bpeople\b"]),
    ("legal", [r"\blegal\b", r"\bcounsel\b", r"\battorney\b"]),
]

# ---------------------------------------------------------------------------
# Intent taxonomies — the core of the person/company split
# ---------------------------------------------------------------------------
COMPANY_INTENTS = {
    "hiring":           "Opening roles, expanding a team, job postings",
    "cost_reduction":   "Efficiency drives, margin pressure, restructuring for cost",
    "ai_transformation":"AI/automation initiatives, digital transformation programmes",
    "fundraise":        "Funding rounds, new investors, capital raised",
    "ipo":              "IPO filing, listing plans, public-market readiness",
    "expansion":        "New markets, geographies, segments",
    "product_launch":   "New products, major releases",
    "partnership":      "Notable partnerships or integrations",
    "award":            "Awards and recognition",
}

PERSON_INTENTS = {
    "promotion":        "Recently promoted into a bigger remit",
    "role_change":      "Joined a new company or moved into a new role",
    "looking_for":      "Publicly asking for something — tooling, advice, hires, vendors",
    "influencer":       "Actively publishing: talks, podcasts, interviews, bylines",
    "personal_award":   "Individual recognition or award",
    "speaking":         "Conference talks, panels, webinars",
}

# Categories that may never become a hook, whatever tier they arrive in.
BLOCKED_INTENTS = {
    "layoff", "lawsuit", "controversy", "personal_life", "bereavement", "health",
}

# Default relative priority. Person-level intents lead by default because a
# person-level hook is what makes outreach feel written FOR someone rather than
# scraped ABOUT their employer — which is precisely the weakness that showed up
# when the first version leaned company-first.
DEFAULT_INTENT_WEIGHTS: dict[str, float] = {
    # person tier
    "promotion": 1.00,
    "role_change": 1.00,
    "looking_for": 1.00,
    "speaking": 0.90,
    "influencer": 0.85,
    "personal_award": 0.55,
    # company tier
    "hiring": 0.80,
    "ai_transformation": 0.78,
    "cost_reduction": 0.75,
    "ipo": 0.70,
    "expansion": 0.68,
    "fundraise": 0.62,      # high intent but the most saturated trigger in outbound
    "product_launch": 0.60,
    "partnership": 0.50,
    "award": 0.22,
    "other": 0.35,
}

# A person-level fact is preferred over a company-level fact of equal score.
PERSON_TIER_BOOST = 1.15


def tier_of(intent: str) -> str:
    if intent in PERSON_INTENTS:
        return "person"
    if intent in COMPANY_INTENTS:
        return "company"
    return "other"


def classify_seniority(title: str) -> str:
    t = f" {(title or '').lower()} "
    for level, patterns in SENIORITY_PATTERNS:
        for p in patterns:
            if re.search(p, t):
                return level
    return "unknown"


def classify_function(title: str) -> str:
    t = f" {(title or '').lower()} "
    for func, patterns in FUNCTION_PATTERNS:
        for p in patterns:
            if re.search(p, t):
                return func
    return "unknown"


# What each seniority level actually cares about — used to angle the draft so a
# CFO and a VP Ops get genuinely different messages off the same research.
SENIORITY_ANGLE = {
    "cxo": "strategic outcome, cost and risk exposure, board-level narrative. Very short, no detail dumps.",
    "vp":  "throughput, team capacity, hitting the number, what it takes to scale the function.",
    "director": "process reliability, reporting burden, cross-team coordination.",
    "manager": "day-to-day workload on the team, tooling friction, time lost to manual work.",
    "ic": "the actual manual work itself and how much of their week it eats.",
    "unknown": "practical operational impact, stated plainly.",
}

FUNCTION_ANGLE = {
    "operations": "manual process load, throughput and exception handling",
    "finance": "cost control, accuracy, close timelines and audit trail",
    "collections": "recovery rates, ageing receivables, contact efficiency",
    "risk": "control coverage, false positives, auditability and regulatory exposure",
    "customer_success": "response times, ticket volume, retention risk",
    "sales": "pipeline coverage, rep productivity, conversion",
    "marketing": "pipeline contribution and attribution",
    "technology": "integration effort, maintenance burden, reliability",
    "product": "roadmap capacity and user-facing friction",
    "people": "hiring load and onboarding time",
    "legal": "contract turnaround and compliance exposure",
    "unknown": "operational efficiency",
}
