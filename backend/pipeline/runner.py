"""Pipeline orchestration.

Records an event per stage so the UI shows real progress from the real pipeline
— not an animation on a timer. Every stage records what it did and why, because
"the AI decided" is not an explanation anyone can act on.

Progress reaches the browser through the database, not through a live socket.
Each stage is written the moment it happens and the UI polls for it. That is a
deliberate choice for serverless: a Vercel function cannot hold an SSE queue in
memory between invocations, and the stage rows had to be written for durability
anyway (SCENARIOS.md F2/F3). One mechanism, two jobs.

Also holds the company-level research cache: ten contacts at one company should
cost one research pass, not ten. That is both a large cost saving and a
correctness win — colleagues get consistent facts.
"""
from __future__ import annotations

import logging
import time
from datetime import date

from .. import db, security
from ..integrations.llm import LLMFailure
from ..integrations.search import SearchFailure
from ..models import (
    ICPConfig,
    ProspectInput,
    SearchHit,
    StyleExample,
    WriterConfig,
)
from . import draft as draft_stage
from . import extract as extract_stage
from . import grounding, identity, judge, normalize, profile, research

log = logging.getLogger(__name__)

# company_key -> (hits, timestamp).
#
# Process-local, which on serverless means per warm instance rather than global.
# A miss costs one extra research pass and nothing else, so this stays a plain
# dict: a shared cache table would add a round trip to every run to save a
# fraction of them, and correctness never depends on a hit.
_research_cache: dict[str, tuple[list[SearchHit], float]] = {}
CACHE_TTL_S = 60 * 30


def _cache_get(key: str) -> list[SearchHit] | None:
    if not key:
        return None
    entry = _research_cache.get(key)
    if not entry:
        return None
    hits, ts = entry
    if time.time() - ts > CACHE_TTL_S:
        _research_cache.pop(key, None)
        return None
    return hits


def _job_change(p: ProspectInput, ident) -> dict | None:
    """What the research found that contradicts the row as imported.

    Only reported when identity actually resolved and confidence is high: a
    low-confidence match reporting a new employer is far more likely to be the
    wrong person than a real move, and acting on it would overwrite a correct
    CRM row with a stranger's job.
    """
    chosen = getattr(ident, "chosen", None)
    if not chosen or not ident.resolved or ident.confidence < 75:
        return None

    moved = bool(p.company and chosen.company
                 and normalize.company_key(chosen.company) != normalize.company_key(p.company))
    retitled = bool(p.role and chosen.role
                    and normalize.role_key(chosen.role) != normalize.role_key(p.role))
    if not (moved or retitled):
        return None

    parts = []
    if moved:
        parts.append(f"now at {chosen.company}, not {p.company}")
    if retitled:
        parts.append(f"title is {chosen.role}, not {p.role}")
    return {
        "moved": moved, "retitled": retitled,
        "from_company": p.company if moved else "",
        "to_company": chosen.company if moved else "",
        "from_role": p.role if retitled else "",
        "to_role": chosen.role if retitled else "",
        "confidence": ident.confidence,
        "summary": " and ".join(parts),
    }


async def run(run_id: int, p: ProspectInput) -> None:
    """Execute the full pipeline for one prospect.

    Returns when the run reaches a terminal state OR when it needs a human to
    disambiguate. It never blocks waiting for that answer: the choice arrives as
    a separate HTTP request, in a different process, possibly minutes later.
    `/api/runs/{id}/resolve` restarts this function with the chosen company
    filled in, which makes identity resolve immediately and costs one cheap pass
    rather than a resume state machine nobody would maintain.
    """
    t_run = time.perf_counter()
    today = date.today()

    async def stage(name, status, detail="", payload=None, t0=None):
        ms = int((time.perf_counter() - t0) * 1000) if t0 else 0
        await db.add_stage(run_id, name, status, detail, payload, ms)

    try:
        await db.update_run(run_id, status="running")

        writer = WriterConfig(**(await db.get_config("writer") or {}))
        persona = await db.get_selected_persona()
        icp = ICPConfig(**(await db.get_config("icp") or {}))
        style_examples = [
            StyleExample(original=e["original"], edited=e["edited"],
                         prospect=e.get("prospect", ""), created_at=e.get("created_at", ""))
            for e in await db.recent_style_examples(3)
        ]

        # ---------- Targeting: who is this, and are they worth researching? --
        t0 = time.perf_counter()
        stakeholder = profile.profile_stakeholder(p)
        icp_score, for_, against = profile.score_icp_fit(p, stakeholder, icp)
        await db.update_run(run_id, seniority=stakeholder.seniority,
                            function=stakeholder.function, icp_score=icp_score)

        # Relationship state is checked BEFORE any research spend. Cold-pitching
        # an existing customer is the worst output this system can produce, and
        # every stage would otherwise work perfectly while producing it.
        rel = (p.relationship or "").strip().lower()
        SKIP = {
            "customer": "already a customer — cold outreach would damage the account",
            "open_opp": "an opportunity is already open — an SDR touch would cut across it",
            "competitor": "competitor — drafting would hand them our pitch",
            "contacted": "contacted recently — a second cold touch reads as spam",
            "do_not_contact": "on the do-not-contact list",
        }
        if rel in SKIP:
            await db.update_run(run_id, status="error", failure_reason=f"skipped: {SKIP[rel]}",
                                elapsed_ms=int((time.perf_counter() - t_run) * 1000))
            await stage("identity", "skipped", f"Skipped before research — {SKIP[rel]}",
                        {"relationship": rel}, t0=t0)
            return

        fit_detail = (f"{stakeholder.seniority}/{stakeholder.function}"
                      f" · ICP fit {icp_score}/100")
        if against:
            fit_detail += f" — {against[0]}"
        await stage("profile", "done", fit_detail,
                    {"stakeholder": stakeholder.model_dump(), "icp_score": icp_score,
                     "reasons_for": for_, "reasons_against": against}, t0=t0)

        # ---------- Stage 0: identity ------------------------------------
        t0 = time.perf_counter()
        await stage("identity", "started", f"Resolving who {p.name} is")

        probe_hits: list[SearchHit] = []
        if not p.company:
            try:
                probe_hits, _ = await research.identity_probe(p)
            except SearchFailure as e:
                await db.update_run(run_id, status="research_failed", failure_reason=e.reason)
                await stage("identity", "failed", f"Search unavailable: {e.reason}", t0=t0)
                return

        ident = await identity.resolve(p, probe_hits)

        if not ident.resolved and ident.candidates:
            # Two plausible people. Stop and ask — guessing here means
            # researching a stranger and emailing the wrong person.
            cands = [c.model_dump() for c in ident.candidates]
            await db.update_run(run_id, status="needs_disambiguation",
                                identity_confidence=ident.confidence)
            await stage("identity", "done",
                        f"{len(cands)} plausible matches — waiting for confirmation",
                        {"needs_choice": True, "candidates": cands}, t0=t0)

            # Stop here. The run is parked in `needs_disambiguation` with the
            # candidates on the stage row, and /resolve picks it up from there.
            return
        else:
            if ident.chosen and not p.company:
                p = ProspectInput(name=p.name, company=ident.chosen.company,
                                  role=p.role or ident.chosen.role,
                                  location=p.location or ident.chosen.location,
                                  url=p.url, relationship=p.relationship)
                await db.update_run(run_id, company=p.company)

            # A row that arrived with a company and title already on it is the
            # one where the resolver's answer matters most: that is a CRM
            # export, and CRM rows go stale without saying so. Comparing the
            # two is free — the answer was fetched either way and was
            # previously discarded whenever the row already had values.
            change = _job_change(p, ident)
            if change:
                await db.update_run(run_id, job_change=change,
                                    company=change.get("to_company") or p.company,
                                    role=change.get("to_role") or p.role)
                p = ProspectInput(name=p.name,
                                  company=change.get("to_company") or p.company,
                                  role=change.get("to_role") or p.role,
                                  location=p.location, url=p.url,
                                  relationship=p.relationship)

            await db.update_run(run_id, identity_confidence=ident.confidence)
            await stage("identity", "done",
                        f"{ident.note} (confidence {ident.confidence}/100)"
                        + (f" — {change['summary']}" if change else ""),
                        {"confidence": ident.confidence, "note": ident.note,
                         "job_change": change}, t0=t0)

        # ---------- Stages 1-2: research ---------------------------------
        t0 = time.perf_counter()
        ckey = normalize.company_key(p.company)
        cached = _cache_get(ckey)

        if cached is not None:
            hits, errors = cached, []
            await stage("research", "done",
                        f"Reused cached research for {p.company} ({len(hits)} sources) — "
                        f"colleagues at one company cost one research pass, not many",
                        {"cached": True, "sources": len(hits)}, t0=t0)
        else:
            await stage("research", "started", "Running searches in parallel")

            async def on_wave(snapshot: dict) -> None:
                """Publish the research graph after every wave.

                The traversal is the part of a run with the most to show and the
                longest to wait, so it reports itself as it goes rather than
                only in hindsight. Each snapshot overwrites the last, which is
                what makes the UI animate: the client polls, diffs the node set
                against what it is already drawing, and grows the picture.
                """
                await db.add_stage(
                    run_id, "research", "progress",
                    f"Following {len(snapshot['nodes']) - 1} threads about {p.name}",
                    {"graph": snapshot}, 0)

            try:
                hits, errors, breakdown = await research.gather(p, on_wave=on_wave)
            except SearchFailure as e:
                # Infrastructure failure — must NEVER be reported as
                # "this prospect has no public signal".
                await db.update_run(run_id, status="research_failed", failure_reason=e.reason)
                await stage("research", "failed",
                            f"Research could not run: {e.reason}. This is an infrastructure "
                            f"failure, not a finding about the prospect.", t0=t0)
                return

            if ckey:
                _research_cache[ckey] = (hits, time.time())

            await db.add_sources(run_id, [{"url": h.url, "title": h.title, "query": h.query} for h in hits])
            detail = (f"{len(hits)} sources — {breakdown['person_sources']} person-level, "
                      f"{breakdown['company_sources']} company-level")
            if breakdown.get("provider_used"):
                detail += f" (incl. their own profile and posts: {breakdown['provider_note']})"
            if breakdown.get("deep_pass_queries"):
                followed = ", ".join(t["value"] for t in breakdown.get("graph_trail", [])[:3])
                detail += (f"; identity confirmed, so research followed "
                           f"{len(breakdown['deep_pass_queries'])} further threads about them "
                           f"(+{breakdown['deep_pass_sources']} sources)")
                if followed:
                    detail += f" — via {followed}"
            elif breakdown.get("provider_note"):
                detail += f" · person-signal provider: {breakdown['provider_note']}"
            if errors:
                detail += f" ({len(errors)} queries failed, continued with the rest)"
            await stage("research", "done", detail,
                        {"sources": [{"title": h.title, "url": h.url, "query": h.query} for h in hits[:12]],
                         "errors": errors[:5], **breakdown}, t0=t0)

        if not hits:
            await db.update_run(run_id, status="no_signal_found",
                                elapsed_ms=int((time.perf_counter() - t_run) * 1000))
            await stage("extract", "skipped", "No sources found to extract from")
            d = await draft_stage.write(p, None, writer=writer, stakeholder=stakeholder,
                                        style_examples=style_examples, persona=persona)
            await db.update_run(run_id, draft_subject=d.subject, draft_body=d.body,
                                **db.authored(persona))
            await stage("draft", "done", d.note, {"draft": d.model_dump()})
            return

        # ---------- Stage 3: extraction ----------------------------------
        t0 = time.perf_counter()
        await stage("extract", "started", "Reading sources and pulling out concrete facts")
        try:
            facts, meta = await extract_stage.extract(p, hits, today.isoformat())
        except LLMFailure as e:
            await db.update_run(run_id, status="error", failure_reason=e.reason)
            await stage("extract", "failed", f"Extraction failed: {e.reason}", t0=t0)
            return

        detail = f"{len(facts)} candidate facts"
        if meta["collapsed"]:
            detail += (f"; {meta['collapsed']} syndicated duplicate(s) collapsed — "
                       f"the same press release on several sites is one fact, not several")
        if meta["wrong_company"]:
            detail += f"; {meta['wrong_company']} fact(s) about a different company discarded"
        if meta["third_party"]:
            detail += (f"; {meta['third_party']} fact(s) about a different person at "
                       f"{p.company or 'the company'} discarded")
        if meta["wrong_person"]:
            detail += (f"; {meta['wrong_person']} fact(s) about a namesake discarded — "
                       f"the source never ties them to {p.company}")
        await stage("extract", "done", detail,
                    {"facts": [f.model_dump() for f in facts], **meta}, t0=t0)

        # ---------- Grounding verification -------------------------------
        t0 = time.perf_counter()
        await stage("ground", "started", "Verifying every fact against the source text")
        grounded, rejected = [], []
        for f in facts:
            ok, why = grounding.verify_fact(f, hits)
            (grounded if ok else rejected).append({"fact": f.model_dump(), "reason": why})
            if ok:
                f.__dict__["_grounding"] = why
        kept = [f for f in facts if any(g["fact"]["text"] == f.text for g in grounded)]

        await stage("ground", "done",
                    (f"{len(kept)}/{len(facts)} facts verified against source text"
                     + (f"; {len(rejected)} dropped as unverifiable" if rejected else "")),
                    {"verified": grounded, "rejected": rejected}, t0=t0)

        # ---------- Stage 4: judgment ------------------------------------
        t0 = time.perf_counter()
        await stage("judge", "started", "Applying eligibility rules")
        jr = judge.judge(kept, today=today, target_company=p.company,
                         writer=writer, stakeholder=stakeholder)
        # Each verdict carries the fact's stable id so the UI can include or
        # exclude it by hand later without re-running any research.
        await stage("judge", "done", jr.chosen_reason,
                    {"verdicts": [{**v.model_dump(), "fact_id": judge.fact_id(v.fact)}
                                  for v in jr.verdicts],
                     "chosen": jr.chosen.model_dump() if jr.chosen else None,
                     "chosen_id": judge.fact_id(jr.chosen) if jr.chosen else None}, t0=t0)

        # ---------- Stage 5: draft ---------------------------------------
        t0 = time.perf_counter()
        await stage("draft", "started", "Writing the draft")
        d = await draft_stage.write(p, jr.chosen, writer=writer, stakeholder=stakeholder,
                                    style_examples=style_examples, persona=persona,
                                    background=jr.background)
        note = d.note + (f" · matched to your voice from {len(style_examples)} edited draft(s)"
                         if style_examples else "")
        await stage("draft", "done" if d.body else "failed", note, {"draft": d.model_dump()}, t0=t0)

        elapsed = int((time.perf_counter() - t_run) * 1000)
        if jr.chosen:
            await db.update_run(run_id, status="completed", chosen_hook=jr.chosen.text,
                                hook_level=jr.chosen.level,
                                hook_category=jr.chosen.category, hook_date=jr.chosen.date,
                                hook_source=jr.chosen.source_url, draft_subject=d.subject,
                                draft_body=d.body, elapsed_ms=elapsed,
                                **db.authored(persona))
        else:
            await db.update_run(run_id, status="no_signal_found", draft_subject=d.subject,
                                draft_body=d.body, elapsed_ms=elapsed,
                                failure_reason="no fact cleared the eligibility gates",
                                **db.authored(persona))

    except Exception as e:  # noqa: BLE001 — last-resort guard
        # Exception text from a driver or HTTP client routinely contains the
        # connection string, and this reason is stored, displayed and exported.
        # The detail goes to the log; the user gets the type.
        reason = security.safe_reason(e)
        await db.update_run(run_id, status="error", failure_reason=reason)
        await stage("draft", "failed", f"Unexpected error ({reason})")
