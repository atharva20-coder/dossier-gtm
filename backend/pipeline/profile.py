"""Stakeholder profiling and ICP fit scoring.

Two jobs, both about "getting to the right person" before spending research
budget on them:

  1. Classify the stakeholder — seniority and function, from their title.
     Used to angle the message (a CFO and a VP Ops get different messages off
     the same research) and to sanity-check the hook against their role.

  2. Score ICP fit — does this prospect's company match who you actually sell
     to? An out-of-ICP prospect is research spend you shouldn't make.

Important scoping note: in Sales Navigator, ICP filters FIND people. This tool
receives a list, so ICP here is a QUALIFIER, not a sourcing mechanism. Sourcing
would need a data provider; qualifying needs nothing.
"""
from __future__ import annotations

import re

from ..models import ICPConfig, ProspectInput, StakeholderProfile
from ..taxonomy import (
    FUNCTION_ANGLE,
    SENIORITY_ANGLE,
    SENIORITY_ORDER,
    classify_function,
    classify_seniority,
)


def profile_stakeholder(p: ProspectInput) -> StakeholderProfile:
    title = p.role or ""
    seniority = classify_seniority(title)
    function = classify_function(title)
    return StakeholderProfile(
        seniority=seniority,
        function=function,
        angle_seniority=SENIORITY_ANGLE.get(seniority, SENIORITY_ANGLE["unknown"]),
        angle_function=FUNCTION_ANGLE.get(function, FUNCTION_ANGLE["unknown"]),
        title_known=bool(title.strip()),
    )


def _parse_headcount(text: str) -> int | None:
    """Pull an employee count out of free text like '11-50 employees'."""
    if not text:
        return None
    t = text.lower().replace(",", "")
    m = re.search(r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:\+)?\s*employees?", t)
    if m:
        return (int(m.group(1)) + int(m.group(2))) // 2
    m = re.search(r"(\d+)\s*\+?\s*employees?", t)
    if m:
        return int(m.group(1))
    return None


def score_icp_fit(
    p: ProspectInput,
    stakeholder: StakeholderProfile,
    icp: ICPConfig,
) -> tuple[int, list[str], list[str]]:
    """Return (score 0-100, reasons_for, reasons_against).

    Anything the config leaves empty is treated as "no preference" and simply
    doesn't participate — an unconfigured ICP must never silently disqualify
    every prospect.
    """
    checks: list[tuple[bool, str, str]] = []   # (passed, weight_label, explanation)
    for_, against = [], []

    # -- seniority ---------------------------------------------------------
    if icp.seniorities:
        if stakeholder.seniority in icp.seniorities:
            for_.append(f"seniority '{stakeholder.seniority}' is in target")
            checks.append((True, "seniority", ""))
        elif stakeholder.seniority == "unknown":
            against.append("seniority could not be determined from the title")
            checks.append((False, "seniority", ""))
        else:
            # Adjacent seniority is a partial miss, not a hard one.
            try:
                gap = min(abs(SENIORITY_ORDER.index(stakeholder.seniority)
                              - SENIORITY_ORDER.index(s)) for s in icp.seniorities)
            except ValueError:
                gap = 3
            if gap <= 1:
                for_.append(f"seniority '{stakeholder.seniority}' is adjacent to target")
                checks.append((True, "seniority", ""))
            else:
                against.append(f"seniority '{stakeholder.seniority}' is outside target")
                checks.append((False, "seniority", ""))

    # -- function ----------------------------------------------------------
    if icp.functions:
        if stakeholder.function in icp.functions:
            for_.append(f"function '{stakeholder.function}' is in target")
            checks.append((True, "function", ""))
        elif stakeholder.seniority == "cxo":
            # A CEO/founder spans every function. Penalising them for having no
            # single one would push exactly the wrong people down the list.
            for_.append("CXO-level — function scope covers the whole business")
            checks.append((True, "function", ""))
        elif stakeholder.function == "unknown":
            against.append("function could not be determined from the title")
            checks.append((False, "function", ""))
        else:
            against.append(f"function '{stakeholder.function}' is outside target")
            checks.append((False, "function", ""))

    # -- geography ---------------------------------------------------------
    if icp.geographies:
        loc = (p.location or "").lower()
        if not loc:
            against.append("no location supplied")
            checks.append((False, "geography", ""))
        elif any(g.lower().strip() in loc for g in icp.geographies if g.strip()):
            for_.append(f"location '{p.location}' matches target geography")
            checks.append((True, "geography", ""))
        else:
            against.append(f"location '{p.location}' is outside target geography")
            checks.append((False, "geography", ""))

    # -- industry / headcount / revenue ------------------------------------
    # These describe the COMPANY and are not present on an uploaded row. They
    # are filled in later from research when available, so they are scored as
    # "unknown" rather than failed — penalising a prospect for data we simply
    # have not fetched yet would be wrong.
    unknown_dims = []
    if icp.industries:
        unknown_dims.append("industry")
    if icp.headcount_min or icp.headcount_max:
        unknown_dims.append("headcount")
    if icp.revenue_note:
        unknown_dims.append("revenue")

    if not checks:
        return 100, ["no ICP filters configured — every prospect qualifies"], []

    passed = sum(1 for ok, _, _ in checks if ok)
    score = round(100 * passed / len(checks))

    if unknown_dims:
        against.append(
            f"not assessed from the uploaded row: {', '.join(unknown_dims)} "
            f"(company-level data, only knowable after research)"
        )

    return score, for_, against


def hook_fits_role(intent: str, stakeholder: StakeholderProfile) -> tuple[bool, str]:
    """Sanity-check a company-level hook against the prospect's seniority.

    Congratulating a junior IC on 'your funding round' is odd — they didn't
    raise it. This does not block the hook; it flags it so the draft can angle
    the message correctly rather than credit someone for something that wasn't
    theirs.
    """
    credit_intents = {"fundraise", "ipo", "expansion"}
    if intent in credit_intents and stakeholder.seniority in ("ic", "manager"):
        return False, (
            f"'{intent}' is a company milestone and this prospect is {stakeholder.seniority}-level "
            f"— reference it as company context, not as their personal achievement"
        )
    return True, ""
