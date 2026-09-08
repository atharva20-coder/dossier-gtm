"""FastAPI app: routes and the static frontend.

Runs execute inside the request that starts them. Progress reaches the
browser by polling `GET /api/runs/{id}`, whose stage rows the pipeline
writes as it goes — see backend/pipeline/runner.py for why a live socket
is not an option on serverless.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, security
from .integrations import llm as llm_client
from .integrations import email_verify, hunter, mailer, reachability
from .integrations import personsignal as person_client
from .integrations import search as search_client
from .models import (ExtractedFact, ICPConfig, ProspectInput,
                     StakeholderProfile, StyleExample, WriterConfig)
from .pipeline import agent, contacts, draft, judge, normalize, outbound, runner
from .pipeline import persona as persona_stage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# One INFO line per outbound HTTP request. A single campaign makes hundreds, so
# at INFO these are the entire log and nothing this app says can be found in it.
# Warnings and errors from them still come through.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("dossier")

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Dossier")


app.middleware("http")(security.gate)


@app.on_event("startup")
async def _startup():
    # No DDL here — the schema is applied once from supabase/schema.sql. See
    # db.init() for why a serverless cold start is the wrong place for it.
    security.warn_if_open()
    missing = config.missing_keys()
    if missing:
        log.warning("Missing API keys: %s — /api/health will report this.", ", ".join(missing))


@app.on_event("shutdown")
async def _shutdown():
    """Hand connections back rather than letting the platform sever them.

    A pooled Postgres connection dropped mid-flight is held open server-side
    until it times out, and the pooler's client budget is small enough that
    leaking a few per deploy is a real ceiling.
    """
    await db.close()


# ------------------------------------------------------------------ access ---
class AccessKey(BaseModel):
    email: str = ""
    password: str = ""
    key: str = ""          # the older shared-key form, still accepted


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """Whether a gate exists, and whether this browser is through it."""
    authenticated = (not security.enabled()
                     or security.valid(request.cookies.get(security.COOKIE)))
    return {"required": security.enabled(),
            "authenticated": authenticated,
            # Only once through the gate. This endpoint is PUBLIC — it has to be,
            # the sign-in form calls it before anyone has signed in — and the
            # sign-in form is the one screen where the address must not be given
            # away. authenticate() is deliberately vague about which half of a
            # wrong guess was wrong precisely so the mailbox cannot be enumerated;
            # printing it here as the field's placeholder handed it to every
            # visitor for free and undid that.
            "mailbox": (config.GMAIL_ADDRESS or None) if authenticated else None}


@app.post("/api/auth")
async def authenticate(request: Request, body: AccessKey):
    if not security.enabled():
        return {"ok": True, "required": False}
    # Either the mailbox credential or, for an install that has not connected
    # Gmail yet, the older shared key.
    ok = (security.credentials_match(body.email, body.password)
          or security.key_matches(body.key or body.password))
    if not ok:
        # Deliberately vague: a wrong guess should say nothing about which half
        # was wrong, or the address becomes enumerable.
        raise HTTPException(401, "That email and app password did not match.")

    r = JSONResponse({"ok": True, "required": True})
    r.set_cookie(
        security.COOKIE, security.issue(),
        max_age=config.SESSION_TTL_S,
        httponly=True,      # unreadable from JavaScript, so XSS cannot lift it
        samesite="lax",     # not sent on cross-site POSTs
        secure=security.is_https(request),   # see is_https for why not hardcoded
        path="/",
    )
    return r


@app.post("/api/auth/logout")
async def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(security.COOKIE, path="/")
    return r


# ---------------------------------------------------------------- health ---
@app.get("/api/ping")
async def ping():
    """Liveness only: is the process up and can it reach its database.

    Deliberately separate from /api/health, which calls Tavily, Gemini and the
    person-signal provider for real. That is the right check before a demo and
    the wrong one for a platform healthcheck — a host that polls it on every
    deploy, restart and periodic probe would spend search credits as a side
    effect of staying alive.
    """
    try:
        p = await db.pool()
        await p.fetchval("SELECT 1")
        return {"ok": True, "db": True}
    except Exception as e:
        raise HTTPException(503, f"database unreachable: {type(e).__name__}")


# Cached health result: (checked_at, payload). See health() for why.
_health_cache: tuple[float, dict] | None = None


@app.get("/api/health")
async def health(force: bool = False):
    """What is configured, and — only when asked — whether it actually works.

    EVERY PROBE SPENDS SOMETHING. Checking Tavily is a real search against a
    1,000/month allowance; checking Gemini is a real model call. So the default
    answer is drawn from configuration alone and costs nothing: it can be
    requested on every page load, every poll, every reload, for free.

    `force=1` is the deliberate check — the button in the Setup dialog — and
    that is the only path that spends anything. The result is cached so holding
    the button down cannot drain a quota either.

    This is the rule for every provider in the app: an allowance is spent when a
    person asks for work, never to draw a badge.
    """
    global _health_cache
    now = time.time()

    if not force:
        # Configuration only — plus the search budget, which is a local count
        # and costs a query against our own table rather than an allowance.
        # This is the one number worth having on every page load: a campaign
        # that dies halfway through is worse than one that never started.
        tav = await db.provider_month("tavily")
        tav_used = tav["month"]
        tav_left = max(config.TAVILY_MONTHLY_BUDGET - tav_used, 0)
        return {
            "ok": bool(config.TAVILY_API_KEY and config.GEMINI_API_KEY),
            "probed": False,
            "tavily": {"ok": bool(config.TAVILY_API_KEY) and tav_left > 0,
                       "enabled": bool(config.TAVILY_API_KEY),
                       "detail": (f"{tav_left} of {config.TAVILY_MONTHLY_BUDGET} "
                                  f"searches left this month"
                                  if config.TAVILY_API_KEY else "no key"),
                       "used": tav_used, "left": tav_left,
                       "budget": config.TAVILY_MONTHLY_BUDGET,
                       "exhausted": bool(config.TAVILY_API_KEY) and tav_left <= 0,
                       "low": bool(config.TAVILY_API_KEY)
                              and 0 < tav_left <= config.TAVILY_RESERVE},
            "gemini": {"ok": bool(config.GEMINI_API_KEY),
                       "detail": "configured" if config.GEMINI_API_KEY else "no key",
                       "models": [config.MODEL_FAST, config.MODEL_SMART]},
            "person_signal": {"ok": person_client.enabled(), "optional": True,
                              "enabled": person_client.enabled(),
                              "detail": "configured" if person_client.enabled() else "no key",
                              "usage": await db.provider_usage("supercarl")},
            "email_finder": {"ok": bool(config.HUNTER_API_KEY), "optional": True,
                             "enabled": bool(config.HUNTER_API_KEY),
                             "detail": "configured" if config.HUNTER_API_KEY else "no key"},
            "email_verifier": {"ok": email_verify.enabled(), "optional": True,
                               "enabled": email_verify.enabled(),
                               "detail": "configured" if email_verify.enabled() else "no key"},
            "checked_at": now,
            "cached": False,
        }

    if _health_cache and now - _health_cache[0] < config.HEALTH_CACHE_S:
        return {**_health_cache[1], "cached": True}

    tav_ok, tav_msg = await search_client.health()
    _probe = await db.provider_month("tavily")
    probe_used = _probe["month"]
    probe_left = max(config.TAVILY_MONTHLY_BUDGET - probe_used, 0)
    tav_msg = f"{tav_msg} · {probe_left} of {config.TAVILY_MONTHLY_BUDGET} left this month"
    llm_ok, llm_msg = await llm_client.health()
    ps_ok, ps_msg = await person_client.health(probe=True)
    # Hunter's /account reports the remaining budget without spending from it.
    hunter_ok, hunter_msg = await hunter.health()
    # The verifier has no free quota endpoint, so this stays configuration-only
    # even under force — proving the key works costs one of the 100 a day.
    qev_ok, qev_msg = await email_verify.health()

    payload = {
        # Required for a run: search + LLM. Everything else is optional and
        # never marks the whole system unhealthy.
        "ok": tav_ok and llm_ok,
        "probed": True,
        "tavily": {"ok": tav_ok, "detail": tav_msg, "enabled": bool(config.TAVILY_API_KEY),
                   "used": probe_used, "left": probe_left,
                   "budget": config.TAVILY_MONTHLY_BUDGET,
                   "exhausted": probe_left <= 0,
                   "low": 0 < probe_left <= config.TAVILY_RESERVE},
        "gemini": {"ok": llm_ok, "detail": llm_msg,
                   "models": [config.MODEL_FAST, config.MODEL_SMART]},
        "person_signal": {"ok": ps_ok, "detail": ps_msg, "optional": True,
                          "enabled": person_client.enabled(),
                          "usage": await db.provider_usage("supercarl")},
        "email_finder": {"ok": hunter_ok, "detail": hunter_msg, "optional": True,
                         "enabled": bool(config.HUNTER_API_KEY)},
        "email_verifier": {"ok": qev_ok, "detail": qev_msg, "optional": True,
                           "enabled": email_verify.enabled()},
        "checked_at": now,
    }
    _health_cache = (now, payload)
    return {**payload, "cached": False}


# ---------------------------------------------------------------- upload ---
@app.post("/api/upload")
async def upload(request: Request, file: UploadFile):
    """Parse an uploaded sheet into prospects.

    Handles the input-hygiene problems that otherwise masquerade as
    'this prospect has no public signal': messy pasted names, header naming
    variants, duplicate rows.
    """
    await security.enforce_body_limit(request)
    try:
        import pandas as pd
    except ImportError:
        raise HTTPException(500, "pandas is required to parse uploads")

    raw = await file.read()
    name = (file.filename or "").lower()

    try:
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(raw))
        else:
            df = pd.read_excel(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"Could not read that file: {e}")

    if df.empty:
        raise HTTPException(400, "That file has no rows.")

    # Header mapping — people name this column many different things.
    aliases = {
        "name": {"name", "full name", "fullname", "prospect", "prospect name",
                 "contact", "contact name", "person"},
        "company": {"company", "company name", "organisation", "organization", "account", "employer"},
        "role": {"role", "title", "job title", "position", "designation"},
        "location": {"location", "city", "country", "region", "geo"},
        "url": {"url", "link", "website", "linkedin", "profile", "linkedin url"},
        "relationship": {"relationship", "status", "account status", "crm status", "stage"},
        "email": {"email", "e-mail", "email address", "work email", "mail"},
    }
    colmap: dict[str, str] = {}
    for col in df.columns:
        # Normalise separators: "LinkedIn_Profile-URL" and "linkedin profile url"
        # are the same header as far as a spreadsheet author is concerned.
        key = re.sub(r"[\s_\-]+", " ", str(col).strip().lower())
        for field, names in aliases.items():
            if key in names and field not in colmap:
                colmap[field] = col
                break

    # Fallback for the URL column specifically, because it is the one field
    # that materially changes research quality: an exact LinkedIn URL resolves
    # the person deterministically, while name+company only fuzzy-matches.
    # Header variants are endless ("LinkedIn Profile URL", "LI link", "Person
    # Linkedin"), so match on substring rather than trying to enumerate them.
    if "url" not in colmap:
        for col in df.columns:
            key = str(col).strip().lower()
            if "linkedin" in key or "link" in key or key.endswith(" url") or key == "url":
                colmap["url"] = col
                break

    # The name is the only column that must be there — everything else is
    # optional, and a file with three columns is as valid as one with thirty.
    if "name" not in colmap:
        raise HTTPException(
            400,
            f"No name column found. Columns in this file: "
            f"{[str(c) for c in df.columns][:12]}. Rename one to 'Name' "
            f"(or Full Name, Contact, Prospect). Every other column is optional.",
        )

    def cell(row, field):
        """One value as clean text, whatever the spreadsheet put there.

        Sheets are messy in predictable ways: a missing column, a blank cell,
        pandas' NaN for both, and whole numbers arriving as "12345.0" because a
        single blank cell made the column a float. All of them should read as
        empty or as what a person typed, never as the word "nan".
        """
        col = colmap.get(field)
        if not col:
            return ""                      # the column simply is not in this file
        v = row.get(col)
        if v is None or (isinstance(v, float) and v != v):    # NaN is not itself
            return ""
        if isinstance(v, float) and v.is_integer():
            return str(int(v))             # 12345.0 -> "12345"
        text = str(v).strip()
        return "" if text.lower() in ("nan", "none", "null", "n/a", "-") else text

    prospects, seen, dupes, skipped = [], set(), 0, 0
    for _, row in df.iterrows():
        raw_name = cell(row, "name")
        clean = normalize.clean_name(raw_name)
        if not clean:
            skipped += 1
            continue
        company = normalize.clean_company(cell(row, "company"))
        key = normalize.dedupe_key(clean, company)
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        prospects.append({
            "name": clean,
            "raw_name": raw_name,
            "normalized": clean != raw_name.strip(),
            "company": company,
            "role": cell(row, "role"),
            "location": cell(row, "location"),
            "url": cell(row, "url"),
            "relationship": cell(row, "relationship").lower(),
            "email": cell(row, "email"),
        })

    # A long list is imported, not rejected. Rows cost nothing — research only
    # happens when someone presses Run, one lead at a time — so refusing a
    # 500-row file outright was a guard against a spend that no longer happens
    # at this step. Anything past the cap is dropped and reported rather than
    # silently lost.
    truncated = 0
    if len(prospects) > config.MAX_ROWS_PER_UPLOAD:
        truncated = len(prospects) - config.MAX_ROWS_PER_UPLOAD
        prospects = prospects[:config.MAX_ROWS_PER_UPLOAD]

    est = len(prospects) * config.SEARCHES_PER_RUN_ESTIMATE
    note = (f"~{est} search credits and ~{len(prospects) * 4} model calls "
            f"if you run all {len(prospects)}.")
    if truncated:
        note = (f"Imported the first {len(prospects)}; {truncated} more were left out "
                f"(the per-upload cap is {config.MAX_ROWS_PER_UPLOAD}). ") + note
    return {
        "prospects": prospects,
        "mapped_columns": {k: str(v) for k, v in colmap.items()},
        "unmapped_columns": [str(c) for c in df.columns if c not in colmap.values()][:20],
        "duplicates_removed": dupes,
        "rows_skipped": skipped,
        "rows_truncated": truncated,
        "estimated_search_credits": est,
        "estimate_note": note,
    }


# ------------------------------------------------------------------ runs ---
class RunRequest(ProspectInput):
    batch_id: str = ""


@app.post("/api/runs")
async def create_run(req: RunRequest):
    missing = config.missing_keys()
    if missing:
        raise HTTPException(400, f"Missing API keys: {', '.join(missing)}. Add them to .env.")

    p = ProspectInput(name=normalize.clean_name(req.name),
                      company=normalize.clean_company(req.company),
                      role=req.role, location=req.location, url=req.url,
                      relationship=req.relationship)
    if not p.name:
        raise HTTPException(400, "A prospect name is required.")

    # Create the row and return immediately. The client needs the id before the
    # work starts, because that id is how it polls for progress while the
    # separate /execute request is still open.
    # Owned by whoever is writing when it is added, so the rail filters to the
    # work that voice is doing.
    owner = ((await db.get_selected_persona()) or {}).get("id")
    run_id = await db.create_run(req.batch_id or str(uuid.uuid4())[:8],
                                 p.model_dump(), owner_persona_id=owner)
    return {"run_id": run_id}


class BulkRuns(BaseModel):
    batch_id: str = ""
    prospects: list[ProspectInput] = []


@app.post("/api/runs/bulk")
async def create_runs(req: BulkRuns):
    """Persist a whole uploaded batch as queued runs, before any of it is run.

    A prospect list that exists only in the browser is lost on refresh, which
    for a 200-row upload means doing the upload again. Writing the rows up front
    makes the database the single source of truth for what is in the batch, so
    reloading the page restores it — and the runs that have already finished
    come back with their hooks and drafts intact.

    Creating a row costs nothing and starts no work: these are `queued` until
    something calls /execute on them.
    """
    if not req.prospects:
        raise HTTPException(400, "No prospects supplied.")
    if len(req.prospects) > config.MAX_ROWS_PER_UPLOAD:
        raise HTTPException(400, f"{len(req.prospects)} rows exceeds the "
                                 f"{config.MAX_ROWS_PER_UPLOAD}-row cap.")

    batch_id = req.batch_id or str(uuid.uuid4())[:8]
    owner = ((await db.get_selected_persona()) or {}).get("id")
    out = []
    for raw in req.prospects:
        p = ProspectInput(name=normalize.clean_name(raw.name),
                          company=normalize.clean_company(raw.company),
                          role=raw.role, location=raw.location, url=raw.url,
                          relationship=raw.relationship)
        if not p.name:
            continue
        out.append({"run_id": await db.create_run(batch_id, p.model_dump(),
                                                  owner_persona_id=owner),
                    **p.model_dump()})
    return {"batch_id": batch_id, "runs": out}


@app.post("/api/runs/{run_id}/execute")
async def execute_run(run_id: int):
    """Run the pipeline for an already-created run, and return when it is done.

    The work happens inside this request rather than in a detached task.
    Serverless gives no guarantee that work outliving its response ever
    finishes, so a fire-and-forget task is a run that silently disappears. A
    full run measures well inside the platform's function ceiling, and the
    client polls GET /api/runs/{id} for stage-by-stage progress meanwhile.
    """
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] == "needs_disambiguation":
        raise HTTPException(409, "That run is waiting for a disambiguation choice.")

    # Claim it before doing anything. Checking the status and then starting
    # leaves a gap that a double-clicked button lands both requests in, and the
    # cost of losing that race is the whole run executed twice.
    was = run["status"]
    if not await db.claim_run(run_id):
        raise HTTPException(409, "That run is already in progress.")

    # Re-running clears what the last attempt produced. One row per prospect is
    # what lets a reloaded list match what is on screen, so a re-run resets the
    # row rather than leaving a duplicate behind.
    if was != "queued":
        await db.reset_run(run_id)
        await db.claim_run(run_id)      # reset_run puts it back to 'queued'

    p = ProspectInput(
        name=run.get("name") or "",
        company=run.get("company") or "",
        role=run.get("role") or "",
        location=run.get("location") or "",
        url=run.get("url") or "",
        relationship=run.get("relationship") or "",
    )
    await runner.run(run_id, p)
    return {"ok": True, "run": await db.get_run(run_id)}


class Choice(BaseModel):
    company: str = ""
    role: str = ""
    location: str = ""


@app.post("/api/runs/{run_id}/resolve")
async def resolve(run_id: int, choice: Choice):
    """Apply a human disambiguation choice and finish the run.

    The original request already returned — nothing is waiting in memory for
    this answer. Restarting the pipeline with the company now known is what
    resumes it: identity short-circuits on a supplied company, so the repeated
    work is one cheap pass, not another research spend.
    """
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] != "needs_disambiguation":
        raise HTTPException(409, "That run is not waiting for a disambiguation choice.")

    p = ProspectInput(
        name=run.get("name", ""),
        company=choice.company,
        role=choice.role or run.get("role") or "",
        location=choice.location or run.get("location") or "",
        url=run.get("url") or "",
        relationship=run.get("relationship") or "",
    )
    await db.update_run(run_id, company=p.company, role=p.role,
                        location=p.location, status="running")
    await runner.run(run_id, p)
    return {"ok": True, "run": await db.get_run(run_id)}


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: int):
    """Remove one lead and everything its run produced."""
    if not await db.delete_run(run_id):
        raise HTTPException(404, "Run not found")
    return {"ok": True}


@app.post("/api/runs/clean")
async def clean_failed():
    """Discard every run that failed without producing anything.

    Interrupted runs accumulate — a redeploy mid-run, a model timeout — and each
    leaves a row that looks like a lead but holds nothing. Runs that produced a
    hook or a draft are never touched, whatever their status.
    """
    return {"deleted": await db.delete_failed_runs()}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: int):
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {**run, "priority": _priority(run)}


@app.get("/api/runs")
async def list_runs(batch_id: str = ""):
    """All runs, or one batch's. The batch form is what the UI reloads into."""
    # The dashboard is the natural place to retire runs that died mid-flight;
    # doing it here costs one UPDATE and keeps the stats honest.
    await db.reap_stale_runs()
    runs = await db.list_runs(batch_id=batch_id) if batch_id else await db.list_runs()
    return {"runs": runs, "stats": await db.stats(), "style": await db.style_stats(
        ((await db.get_selected_persona()) or {}).get("id"))}


def _priority(run: dict) -> str:
    """Which leads to work first, from what the pipeline already scored.

    ICP fit says who is worth writing to; a person-level hook says there is
    something to say. Neither alone orders a list — a perfect-fit lead with no
    angle is not the one to start with, and a great angle at a company you do
    not sell to is a waste of a good sentence.
    """
    score = run.get("icp_score")
    if score is None:
        return ""
    if run.get("job_change"):
        return "hot"          # a fresh move is the strongest reason to write today
    person_hook = run.get("hook_level") == "person"
    if score >= 70 and person_hook:
        return "high"
    if score >= 70 or person_hook:
        return "medium"
    return "low"


@app.get("/api/leads")
async def leads():
    """Every lead, with the run worth showing for each — what the UI loads.

    The browser deliberately remembers nothing between page loads: no
    localStorage, no session state, because two places holding the same truth is
    how they end up disagreeing. The database knows every lead and what each run
    produced, so a reload asks it.

    Scoped to leads rather than to the most recent upload. See
    `db.latest_leads` for why that distinction turned out to matter.
    """
    await db.reap_stale_runs()
    # Summary rows only. Stages carry the facts, verdicts, sources and research
    # graph — hundreds of kilobytes per run — and the list screen shows none of
    # it. Sending it anyway made a page load scale with the total size of every
    # run ever done. The detail view fetches one run in full when opened.
    # `batch_id` already records where a lead came from — campaigns write
    # "outbound-<id>" — so the origin is surfaced rather than stored twice.
    rows = [{**r, "priority": _priority(r),
             "from_campaign": str(r.get("batch_id") or "").startswith("outbound-")}
            for r in await db.latest_leads(
                persona_id=((await db.get_selected_persona()) or {}).get("id"))]
    return {"batch_id": rows[0]["batch_id"] if rows else "", "runs": rows}


@app.get("/api/batches/latest")
async def latest_batch():
    """The most recent upload, fully hydrated. Kept for looking at one batch."""
    batch_id = await db.latest_batch_id()
    if not batch_id:
        return {"batch_id": "", "runs": []}
    return await get_batch(batch_id)


@app.get("/api/batches/{batch_id}")
async def get_batch(batch_id: str):
    """One batch, with each run's stages — enough to rebuild the UI after a reload."""
    # Retire runs that died mid-flight before reading the batch back. This is
    # the page-load path, so skipping it here is what leaves a prospect showing
    # "running" forever after a crash or redeploy — the exact state the reaper
    # exists to clear, sitting on the one screen that always shows it.
    await db.reap_stale_runs()
    runs = await db.hydrate_runs(list(reversed(await db.list_runs(batch_id=batch_id))))
    return {"batch_id": batch_id, "runs": runs}


# ---------------------------------------------------------------- config ---
@app.get("/api/config")
async def get_config():
    """Writer persona, ICP and stakeholder targeting."""
    return {
        "writer": (await db.get_config("writer")) or WriterConfig().model_dump(),
        "icp": (await db.get_config("icp")) or ICPConfig().model_dump(),
        "style_examples": await db.style_stats(
            ((await db.get_selected_persona()) or {}).get("id")),
    }


@app.post("/api/config/writer")
async def set_writer(cfg: WriterConfig):
    await db.set_config("writer", cfg.model_dump())
    return {"ok": True}


@app.post("/api/config/icp")
async def set_icp(cfg: ICPConfig):
    await db.set_config("icp", cfg.model_dump())
    return {"ok": True}


# ------------------------------------------------------------- personas ---
class PersonaFromDescription(BaseModel):
    description: str = ""


@app.post("/api/personas/generate")
async def generate_persona(body: PersonaFromDescription):
    """Turn a description of how someone writes into a persona.

    The first thing a new user does. Asking them to fill in "tone" and "length"
    fields gets shrugs; asking them to describe themselves gets paragraphs, and
    the paragraphs are what the instructions should be built from.
    """
    if len(body.description.strip()) < 20:
        raise HTTPException(400, "Tell it a bit more about how you write.")
    try:
        g = await persona_stage.generate(body.description)
    except Exception as e:
        raise HTTPException(502, f"Could not build a persona ({type(e).__name__}).")

    created = await db.create_persona(
        g.name, g.character, g.instructions, g.emoji,
        seniority=g.seniority, intent=g.intent, product=g.product,
        problem=g.problem, proof=g.proof, looking_for=g.looking_for)
    if len(await db.list_personas()) == 1:
        created = await db.select_persona(created["id"])
    # The first version, so the history starts where the persona started.
    await db.add_persona_revision(created["id"], g.instructions,
                                  "Written from your description.", 0)
    return created


@app.post("/api/personas/{persona_id}/rebuild")
async def rebuild_persona(persona_id: int, body: PersonaFromDescription):
    """Rewrite an existing persona from a fresh description of how they write.

    Separate from generate, which creates. Describing yourself again is almost
    never a request for a second identity — it is the same person saying it
    better — and creating one each time left people with a rail full of near
    duplicates and no idea which was writing.

    What it has learned is kept. Those rules came from real edits and are still
    true of the author; they go back to being applied on top of the new brief,
    and get written into it again the next time something is learned.
    """
    persona = await db.get_persona(persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    if len(body.description.strip()) < 20:
        raise HTTPException(400, "Tell it a bit more about how you write.")
    try:
        g = await persona_stage.generate(body.description)
    except Exception as e:
        raise HTTPException(502, f"Could not rebuild the persona ({type(e).__name__}).")

    fields = {"name": g.name, "character": g.character, "emoji": g.emoji,
              "instructions": g.instructions}
    # Only overwrite the commercial fields the new description actually spoke
    # to. It refuses to invent them, and silently blanking what was there
    # because this description happened not to mention it would lose real work.
    for key, value in (("seniority", g.seniority), ("intent", g.intent),
                       ("product", g.product), ("problem", g.problem),
                       ("proof", g.proof), ("looking_for", g.looking_for)):
        if (value or "").strip():
            fields[key] = value

    # Snapshot what is being replaced BEFORE replacing it. Recording only the
    # new version leaves a rebuild recoverable purely by luck — whether some
    # earlier revision happened to exist — and this overwrites work someone may
    # have spent a while on.
    if (persona.get("instructions") or "").strip():
        await db.add_persona_revision(
            persona_id, persona["instructions"],
            f"Replaced when {persona.get('name') or 'this persona'} was rebuilt.", 0)
    updated = await db.update_persona(persona_id, **fields)
    await db.unfold_memories(persona_id)
    await db.add_persona_revision(persona_id, g.instructions,
                                  "Rebuilt from a new description.", 0)
    return updated


@app.get("/api/personas/{persona_id}/prompt")
async def persona_prompt(persona_id: int):
    """The exact brief this persona hands the writer, assembled as it really is.

    Learning here does not rewrite the instructions someone typed — it appends
    rules, in a block marked as overriding everything above it. So "did the
    prompt actually change" is a question about text, and the honest way to
    answer it is to show the text rather than a summary of it.

    Built by calling the same function the drafting stage calls. A second
    renderer written for display is a renderer that can disagree with the one
    that matters, which would make this reassuring and wrong.
    """
    persona = await db.get_persona(persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    persona["memories"] = await db.active_memories(persona_id)
    # Two different things, and reporting them as one list was misleading: a
    # folded rule is IN the instructions above, and listing it again as
    # "appended" implies the model is told it twice.
    folded = [m["rule"] for m in persona["memories"] if m.get("folded")]
    appended = [m["rule"] for m in persona["memories"] if not m.get("folded")]
    return {
        "prompt": draft._persona_block(persona),
        "learned_rules": appended,
        "folded_rules": folded,
        "authored_instructions": persona.get("instructions") or "",
        "note": ("The instructions are yours, except where it has written a "
                 "learned rule into them — those are listed under Learned with "
                 "the edits behind them. Anything still in the LEARNED block "
                 "below is applied on top and overrides what contradicts it."),
    }


@app.get("/api/personas/{persona_id}/history")
async def persona_history(persona_id: int):
    """What this persona has learned, and how much evidence is still pending."""
    if not await db.get_persona(persona_id):
        raise HTTPException(404, "Persona not found")
    return {"memories": await db.all_memories(persona_id),
            "revisions": await db.persona_revisions(persona_id),
            "lessons": await db.lesson_counts(persona_id)}


@app.delete("/api/personas/{persona_id}/memories/{memory_id}")
async def forget(persona_id: int, memory_id: int):
    """Retire one learned rule the persona got wrong.

    The row is kept and only deactivated: what it believed, and when it stopped,
    is part of how the persona got here.
    """
    if not await db.forget_memory(memory_id):
        raise HTTPException(404, "No such learned rule")
    return {"ok": True}


class PersonaIn(BaseModel):
    name: str = ""
    character: str = ""
    instructions: str = ""
    emoji: str = "🙂"
    seniority: str = ""
    intent: str = ""
    product: str = ""
    problem: str = ""
    proof: str = ""
    looking_for: str = ""
    # The whole brief in the sender's own words, and a message they wrote. Both
    # optional: someone who fills in the fields never sees them.
    brief: str = ""
    sample: str = ""


@app.get("/api/personas")
async def list_personas():
    """Every saved persona, the selected one first."""
    return {"personas": await db.list_personas()}


@app.post("/api/personas")
async def create_persona(body: PersonaIn):
    if not body.name.strip():
        raise HTTPException(400, "A persona needs a name.")
    persona = await db.create_persona(
        body.name.strip(), body.character.strip(), body.instructions.strip(),
        (body.emoji or "🙂")[:8],
        seniority=body.seniority.strip(), intent=body.intent.strip(),
        product=body.product.strip(), problem=body.problem.strip(),
        proof=body.proof.strip(), looking_for=body.looking_for.strip())
    # The first persona becomes the active voice: a saved persona nobody
    # selected would silently do nothing, which reads as the feature not working.
    if len(await db.list_personas()) == 1:
        persona = await db.select_persona(persona["id"])
    return persona


@app.patch("/api/personas/{persona_id}")
async def edit_persona(persona_id: int, body: PersonaIn):
    """Update only the fields the caller actually sent.

    `exclude_unset` is load-bearing. Without it every unsent field arrives as
    its model default and overwrites what was there — a request changing only
    the instructions silently reset the avatar to the default emoji, which is
    exactly the kind of edit nobody thinks to check for.
    """
    fields = {k: v.strip() if isinstance(v, str) else v
              for k, v in body.model_dump(exclude_unset=True).items()
              if v is not None}
    if not fields:
        raise HTTPException(400, "Nothing to change.")
    persona = await db.update_persona(persona_id, **fields)
    if not persona:
        raise HTTPException(404, "Persona not found")
    return persona


@app.post("/api/personas/{persona_id}/select")
async def choose_persona(persona_id: int):
    persona = await db.select_persona(persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    return persona


@app.delete("/api/personas/{persona_id}")
async def remove_persona(persona_id: int):
    if not await db.delete_persona(persona_id):
        raise HTTPException(404, "Persona not found")
    return {"ok": True}


# --------------------------------------------------------------- sending ---
class SendRequest(BaseModel):
    to: str = ""
    resend: bool = False


@app.get("/api/send/status")
async def send_status():
    """Whether sending is available at all, so the UI can say why not."""
    return {"configured": mailer.configured(), "from": config.GMAIL_ADDRESS or None}


@app.post("/api/runs/{run_id}/email")
async def set_email(run_id: int, body: SendRequest):
    """Store the address for a lead, without sending anything."""
    if body.to and not mailer.valid_address(body.to):
        raise HTTPException(400, f"{body.to!r} does not look like an email address.")
    run = await db.set_email(run_id, body.to)
    if not run:
        raise HTTPException(404, "Run not found")
    return {"ok": True, "run": run}


@app.get("/api/runs/{run_id}/email/grade")
async def grade_email(run_id: int, address: str = ""):
    """How reachable an address looks, before anything is sent to it.

    Graded against the person we researched, so the answer can say more than
    "well-formed": whether it is a shared inbox, a personal account rather than
    their work one, and whether the domain accepts mail at all. Read-only, free,
    and it never contacts the recipient.
    """
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return await asyncio.to_thread(
        reachability.grade, address or run.get("email") or "",
        name=run.get("name") or "", company=run.get("company") or "")


@app.post("/api/runs/{run_id}/send")
async def send_message(run_id: int, body: SendRequest):
    """Send the drafted message. The only irreversible thing this app does.

    Every guard lives in `mailer.check` rather than here, so the same rules
    apply however this is reached, and the UI can ask for the reason a send
    would be refused without performing one.
    """
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    try:
        to = await mailer.check_async(run, body.to, resend=body.resend)
    except mailer.SendRefused as e:
        raise HTTPException(409, str(e))

    subject = run.get("draft_subject") or f"Quick note, {run.get('name', '')}".strip()
    try:
        message_id = await mailer.send(to, subject, run["draft_body"])
    except mailer.SendFailed as e:
        raise HTTPException(502, str(e))

    # Delivered. Record it before anything else can fail.
    updated = await db.mark_sent(run_id, to, subject, message_id)
    if body.to and body.to != (run.get("email") or ""):
        updated = await db.set_email(run_id, to)
    log.info("sent run %s to %s (%s)", run_id, to, message_id)
    return {"ok": True, "run": await db.get_run(run_id)}


# ------------------------------------------------------------ assistant ---
class ChatMessage(BaseModel):
    message: str = ""


@app.get("/api/runs/{run_id}/chat")
async def chat_history(run_id: int):
    """Everything said about this lead so far.

    Read on open rather than kept in the browser, so the thread survives a
    refresh, a different tab and a different machine — the same reason every
    other part of a run is read back from the database rather than remembered.
    """
    return {"turns": await db.chat_turns(run_id)}


@app.post("/api/runs/{run_id}/chat")
async def chat(run_id: int, body: ChatMessage):
    """Talk to the assistant about one lead — and let it act.

    It can read the facts and sources, include or exclude them, force a
    different hook, and rewrite the message. It cannot add information: new
    facts only ever enter through the pipeline, where they are grounded against
    a real source. See pipeline/agent.py.
    """
    if not body.message.strip():
        raise HTTPException(400, "Say something for it to act on.")
    try:
        turn = await agent.run_turn(run_id, body.message, _stage_payload)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e).lower() else 400, str(e))
    # Stored after the turn succeeds, so a failed call leaves no half-exchange
    # in the thread. A model that could not be reached is not a thing that was
    # said.
    await db.add_chat_turn(run_id, body.message.strip(),
                           turn.get("reply") or "", turn.get("actions") or [])
    return turn


# --------------------------------------------------- overriding the judge ---
class HookChoice(BaseModel):
    excluded: list[str] = []      # fact ids the user does not want used
    chosen: str = ""              # a specific fact id to use, or "" for the best


@app.post("/api/runs/{run_id}/regenerate")
async def regenerate(run_id: int, choice: HookChoice):
    """Re-pick the hook and rewrite the draft from facts already gathered.

    The judge is a rule engine, and rules are wrong sometimes — a hook can be
    accurate, recent and still the one a rep would never send. This lets them
    exclude facts or name the one to use, and rewrites from there.

    Deliberately NO new research. Everything needed was stored during the run,
    so overriding a judgment costs one drafting call rather than a fresh round
    of search credits, and the evidence stays identical to what was reviewed.
    """
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    verdicts = _stage_payload(run, "judge").get("verdicts") or []
    if not verdicts:
        raise HTTPException(409, "That run has no judged facts to choose from.")

    facts = [ExtractedFact(**v["fact"]) for v in verdicts]
    excluded = set(choice.excluded)
    kept = [f for f in facts if judge.fact_id(f) not in excluded]
    if not kept:
        raise HTTPException(400, "Every fact was excluded — nothing left to write from.")

    # Everything the user decided here is evidence, and only what CHANGED is.
    # Re-recording the same exclusions on every rewrite would let one opinion
    # held twice look like two opinions.
    was = run.get("fact_overrides") or {}
    before_excluded = set(was.get("excluded") or [])
    before_chosen = was.get("chosen") or ""
    by_id = {judge.fact_id(f): f for f in facts}
    for fid in excluded - before_excluded:
        if fid in by_id:
            await db.record_fact_feedback(
                run_id, "excluded", {"fact_id": fid, **by_id[fid].model_dump()},
                "findings", persona_id=pid)
    for fid in before_excluded - excluded:
        if fid in by_id:
            await db.record_fact_feedback(
                run_id, "included", {"fact_id": fid, **by_id[fid].model_dump()},
                "findings", persona_id=pid)
    if choice.chosen and choice.chosen != before_chosen and choice.chosen in by_id:
        await db.record_fact_feedback(
            run_id, "chose",
            {"fact_id": choice.chosen, **by_id[choice.chosen].model_dump()},
            "findings", persona_id=pid)

    persona_now = await db.get_selected_persona()
    pid = (persona_now or {}).get("id")
    writer = WriterConfig(**(await db.get_config("writer") or {}))
    stakeholder = StakeholderProfile(
        **(_stage_payload(run, "profile").get("stakeholder") or {}))
    p = ProspectInput(name=run.get("name") or "", company=run.get("company") or "",
                      role=run.get("role") or "", location=run.get("location") or "",
                      url=run.get("url") or "")

    if choice.chosen:
        if choice.chosen in excluded:
            raise HTTPException(400, "That fact was both chosen and excluded.")
        hook = next((f for f in kept if judge.fact_id(f) == choice.chosen), None)
        if hook is None:
            raise HTTPException(400, "That fact was not found among this run's facts.")
        reason = "chosen by hand, overriding the ranking"
    else:
        jr = await judge.judge(kept, target_company=p.company, writer=writer,
                               name=p.name, role=p.role,
                         stakeholder=stakeholder,
                         persona=await db.get_selected_persona(),
                         learned=judge.learned_weights(
                             await db.hook_outcomes(pid),
                             await db.fact_feedback_counts(pid)))
        hook, reason = jr.chosen, jr.chosen_reason

    style = [StyleExample(original=e["original"], edited=e["edited"],
                          prospect=e.get("prospect", ""), created_at=e.get("created_at", ""))
             for e in await db.recent_style_examples(3, pid)]
    # Read fresh at the top of this request rather than cached anywhere: an
    # edit made seconds ago has to be in force on the very next draft.
    persona = persona_now
    d = await draft.write(p, hook, writer=writer, stakeholder=stakeholder,
                          style_examples=style, persona=persona)

    await db.add_draft_revision(
        run_id, run.get("draft_body") or "", d.body,
        subject=d.subject, source="rewrite",
        instruction=reason, hook=hook.text if hook else "",
        persona_id=(persona or {}).get("id"))
    await db.update_run(
        run_id,
        status="completed" if hook else "no_signal_found",
        chosen_hook=hook.text if hook else None,
        hook_level=hook.level if hook else None,
        hook_category=hook.category if hook else None,
        hook_date=hook.date if hook else None,
        hook_source=hook.source_url if hook else None,
        draft_subject=d.subject, draft_body=d.body, **db.authored(persona),
        # Same reasoning as the runner: an empty draft has to say why it is
        # empty, or a rewrite that produced nothing looks like a broken button.
        failure_reason=("every remaining fact was excluded" if not hook
                        else None if d.body else d.note),
        fact_overrides={"excluded": sorted(excluded), "chosen": choice.chosen},
    )
    await db.add_stage(run_id, "draft", "done",
                       f"Rewritten by hand — {reason}"
                       + (f"; {len(excluded)} fact(s) excluded" if excluded else ""),
                       {"draft": d.model_dump(), "manual": True})
    return {"ok": True, "run": await db.get_run(run_id)}


def _chosen_fact(run: dict) -> ExtractedFact | None:
    """The hook as the judge recorded it, entities included.

    The run's columns keep the hook's text and source for display, but not its
    `key_entities` — and those are exactly what the grounding check needs to
    confirm a rewrite still carries the specific detail. Rebuilding the fact
    from the columns alone produced a hook with nothing to verify, so every
    revision was reported as ungrounded no matter how faithful it was.
    """
    payload = _stage_payload(run, "judge")
    chosen = payload.get("chosen")
    if chosen:
        return ExtractedFact(**chosen)
    if not run.get("chosen_hook"):
        return None
    # Older runs stored no chosen payload; match the text against the verdicts.
    for v in payload.get("verdicts") or []:
        if (v.get("fact") or {}).get("text") == run["chosen_hook"]:
            return ExtractedFact(**v["fact"])
    return ExtractedFact(
        text=run["chosen_hook"], level=run.get("hook_level") or "company",
        category=run.get("hook_category") or "", date=run.get("hook_date") or "",
        key_entities=[], source_url=run.get("hook_source") or "")


def _stage_payload(run: dict, stage: str) -> dict:
    """The payload of a stage's terminal row, or an empty dict."""
    for s in reversed(run.get("stages") or []):
        if s["stage"] == stage and s["status"] == "done":
            return s.get("payload") or {}
    return {}


# ------------------------------------------------------- learning from edits ---
class DraftEdit(BaseModel):
    edited: str


@app.post("/api/runs/{run_id}/draft")
async def save_draft_edit(run_id: int, edit: DraftEdit):
    """Persist a rewritten draft and learn from the change.

    Two things happen here, and they matter for different reasons:
      * the edit is saved, so the rep's work survives navigation
      * the (original, edited) pair becomes a style example, so future drafts
        converge on how this person actually writes
    """
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    original = run.get("draft_body") or ""
    edited = edit.edited or ""
    learned = original.strip() != edited.strip() and bool(edited.strip())

    persona_changes: list = []
    if learned:
        # Filed against the voice that wrote it. Global examples meant a formal
        # persona learned from edits made to a blunt one.
        await db.add_style_example(run_id, run.get("name", ""), original, edited,
                                   persona_id=run.get("persona_id"))
        # The same edit is evidence about how this person writes.
        persona_changes = await persona_stage.note_edit(run_id, original, edited)
        await db.add_draft_revision(
            run_id, original, edited, subject=run.get("draft_subject") or "",
            source="edit", hook=run.get("chosen_hook") or "",
            persona_id=run.get("persona_id"))
    await db.update_run(run_id, draft_body=edited)

    return {"ok": True, "learned": learned,
            "style_examples": (await db.style_stats(run.get("persona_id")))["examples"],
            "persona_changes": persona_changes}


@app.get("/api/learned/feed")
async def learning_feed(limit: int = 60):
    """What the app has learned, newest first, for the notifications drawer.

    Read from the same rows the ranking and the persona actually use, so the
    panel cannot congratulate itself on a change that did not happen.
    """
    return {"events": await db.learning_feed(min(max(limit, 1), 200))}


@app.get("/api/learned/triggers")
async def learned_triggers():
    """Which kinds of hook this sender actually acts on.

    Shown rather than applied silently: a ranking that quietly reshapes itself
    is one nobody can argue with. Every row says what was learned and from how
    much evidence, so it can be disagreed with — and overridden outright by
    setting an explicit weight, which always wins.
    """
    # Shown for whoever is writing. A rail of personas that all learn the same
    # thing is one persona with several avatars.
    active = await db.get_selected_persona()
    outcomes = await db.hook_outcomes((active or {}).get("id"))
    feedback = await db.fact_feedback_counts((active or {}).get("id"))
    learned = judge.learned_weights(outcomes, feedback)

    # Every category anyone has acted on, in either direction. Built from both
    # sides because a category that has only ever been rejected has no row in
    # `outcomes` at all, and it is exactly the one worth showing.
    blank = {"drafted": 0, "sent": 0, "hand_picked": 0, "examples": []}
    rows = []
    for category in sorted(set(outcomes) | set(feedback)):
        o = {**blank, **outcomes.get(category, {})}
        fb = feedback.get(category, {})
        rows.append({
            "category": category,
            "drafted": o["drafted"],
            "sent": o["sent"],
            "hand_picked": o["hand_picked"],
            "excluded": fb.get("excluded", 0),
            "restored": fb.get("included", 0),
            "dropped_examples": fb.get("dropped_examples") or [],
            # Their own words. A weight nobody can explain is a weight nobody
            # can argue with, which is the opposite of the point.
            "reasons": fb.get("reasons") or [],
            "weight": learned.get(category, 1.0),
            "learned": category in learned,
            "examples": o.get("examples") or [],
        })
    rows.sort(key=lambda r: (-abs(r["weight"] - 1.0),
                             -(r["sent"] * 2 + r["hand_picked"] + r["excluded"])))
    return {"triggers": rows,
            "persona": f"{(active or {}).get('emoji') or ''} "
                       f"{(active or {}).get('name') or ''}".strip(),
            "min_evidence": config.LEARN_MIN_EVIDENCE,
            "note": ("A send counts double a hand-pick, and dropping a fact by hand "
                     "counts against its category. Leaving a draft alone teaches "
                     "nothing — not acting is not a preference. Where you said why, "
                     "that reason is kept with it.")}


# ------------------------------------------------------- outbound campaigns ---
class OutboundRequest(BaseModel):
    target_company: str = ""
    config: dict = {}

@app.post("/api/outbound/runs")
async def create_outbound_run_api(req: OutboundRequest):
    if not req.target_company:
        raise HTTPException(400, "Target company is required.")
    
    # We load standard ICP for default targets if none are provided
    config_dict = req.config
    if not config_dict.get("target_titles"):
        icp = await db.get_config("icp") or {}
        config_dict["target_titles"] = icp.get("seniorities", ["VP", "Director"])
        
    from . import outbound_db
    # Owned by whoever is writing, like a lead. The campaign's messages come out
    # in this voice and everything it learns belongs to it.
    run_id = await outbound_db.create_outbound_run(
        req.target_company, config_dict,
        persona_id=((await db.get_selected_persona()) or {}).get("id"))
    return {"run_id": run_id}

@app.get("/api/outbound/runs")
async def list_outbound_runs_api():
    from . import outbound_db
    runs = await outbound_db.list_outbound_runs(
        persona_id=((await db.get_selected_persona()) or {}).get("id"))
    return {"runs": runs}

@app.get("/api/outbound/runs/{run_id}")
async def get_outbound_run_api(run_id: int):
    from . import outbound_db
    run = await outbound_db.get_outbound_run(run_id)
    if not run:
        raise HTTPException(404, "Outbound run not found")
    stages = await outbound_db.get_outbound_stages(run_id)
    return {"run": run, "stages": stages}

@app.post("/api/outbound/runs/{run_id}/execute")
async def execute_outbound_run_api(run_id: int):
    from . import outbound_db
    run = await outbound_db.get_outbound_run(run_id)
    if not run:
        raise HTTPException(404, "Outbound run not found")
    
    if run.get("status") in ["running", "completed"]:
        raise HTTPException(409, f"Run is already {run['status']}")
        
    await outbound_db.update_outbound_run(run_id, status="running")
    
    from .pipeline import outbound as outbound_pipeline
    await outbound_pipeline.run_outbound_pipeline(run_id)
    
    return {"ok": True}

@app.get("/api/outbound/runs/{run_id}/contacts")
async def get_outbound_contacts_api(run_id: int):
    """Every contact, with the message they would actually receive.

    Assembled here rather than in the browser so the screen and the CSV export
    render from the same function — two substitution paths is how a rep
    approves one message and a sequencer sends another.
    """
    from . import outbound_db
    from .pipeline import outbound as outbound_pipeline

    contacts = await outbound_db.get_outbound_contacts(run_id)
    campaigns = {c["persona"]: c
                 for c in await outbound_db.get_outbound_campaigns(run_id)}
    sender = (await db.get_config("writer") or {}).get("sender_name", "")
    # The hook a deeply-researched contact was written from, so the screen can
    # show what the opener rests on rather than just the sentence it produced.
    lead_ids = [c["lead_run_id"] for c in contacts if c.get("lead_run_id")]
    hooks: dict[int, dict] = {}
    if lead_ids:
        pool = await db.pool()
        for row in await pool.fetch(
                "SELECT id, chosen_hook, hook_level, hook_source, "
                "(SELECT count(*) FROM run_sources s WHERE s.run_id = r.id) AS sources "
                "FROM runs r WHERE id = ANY($1::bigint[])", lead_ids):
            hooks[row["id"]] = dict(row)

    for c in contacts:
        subject, body = outbound_pipeline.render_message(
            c, campaigns.get(c.get("persona_segment") or ""), sender)
        c["subject"] = subject
        c["message"] = body
        c["research"] = hooks.get(c.get("lead_run_id") or 0)
    return {"contacts": contacts}

class OutboundSend(BaseModel):
    to: str = ""
    resend: bool = False


@app.post("/api/outbound/runs/{run_id}/contacts/{contact_id}/send")
async def send_outbound_message(run_id: int, contact_id: int, body: OutboundSend):
    """Send one campaign message, over the same Gmail account everything else uses.

    ONE CONTACT PER REQUEST, ON PURPOSE. There is no bulk send here and there
    will not be: a campaign that can mail forty strangers on one click is a
    different product with a different blast radius. Every guard the lead
    screen enforces applies unchanged — `mailer.check_async` is the single
    gate, so a blocked address or an unreachable domain is refused identically
    whichever screen asked.
    """
    from . import outbound_db
    from .pipeline import outbound as outbound_pipeline

    contacts_rows = await outbound_db.get_outbound_contacts(run_id)
    contact = next((c for c in contacts_rows if c["id"] == contact_id), None)
    if not contact:
        raise HTTPException(404, "Contact not found in this run")

    campaigns = {c["persona"]: c
                 for c in await outbound_db.get_outbound_campaigns(run_id)}
    sender = (await db.get_config("writer") or {}).get("sender_name", "")
    subject, message = outbound_pipeline.render_message(
        contact, campaigns.get(contact.get("persona_segment") or ""), sender)
    if not message.strip():
        raise HTTPException(400, "There is no message to send for this contact.")

    # The mailer speaks in runs. Present the contact as one rather than
    # teaching it a second shape — the guards must not diverge by caller.
    as_run = {
        "name": contact.get("name") or "",
        "company": contact.get("company") or "",
        "email": contact.get("email") or "",
        "relationship": "",
        "draft_body": message,
        "sent_at": contact.get("sent_at"),
        "sent_to": contact.get("sent_to"),
    }
    try:
        to = await mailer.check_async(as_run, body.to, resend=body.resend)
    except mailer.SendRefused as e:
        raise HTTPException(409, str(e))

    try:
        message_id = await mailer.send(to, subject, message)
    except mailer.SendFailed as e:
        raise HTTPException(502, str(e))

    pool = await db.pool()
    await pool.execute(
        "UPDATE outbound_contacts SET sent_at=now(), sent_to=$2, sent_subject=$3, "
        "sent_message_id=$4 WHERE id=$1",
        contact_id, to, subject, message_id)
    log.info("outbound message sent to %s (contact %s)", to, contact_id)
    return {"ok": True, "sent_to": to}


@app.get("/api/outbound/runs/{run_id}/export")
async def export_outbound(run_id: int, campaign_id: int = 0):
    """The contacts as a sequencer imports them.

    Columns are the intersection Instantly, Smartlead and Lemlist all accept,
    so one file works in any of them. `email_source` and `email_verified`
    travel beside the address on purpose: an address this app calculated must
    not arrive in a sequencer looking like one somebody verified, and whoever
    imports it is the last person who can notice.
    """
    import csv
    import io as _io

    from . import outbound_db

    run = await outbound_db.get_outbound_run(run_id)
    if not run:
        raise HTTPException(404, "Outbound run not found")

    rows = await outbound_db.get_outbound_contacts(run_id)
    campaigns = {c["persona"]: c for c in await outbound_db.get_outbound_campaigns(run_id)}
    if campaign_id:
        chosen = next((c for c in campaigns.values() if c["id"] == campaign_id), None)
        if not chosen:
            raise HTTPException(404, "Campaign not found in this run")
        rows = [r for r in rows if r.get("persona_segment") == chosen["persona"]]

    sender_name = (await db.get_config("writer") or {}).get("sender_name", "")
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "email_source", "email_verified", "first_name", "last_name",
                "company", "domain", "title", "linkedin_url", "segment",
                "source_competitor", "subject", "body"])
    for c in rows:
        template = campaigns.get(c.get("persona_segment") or "") or {}
        subject, body = outbound.render_message(c, template, sender_name)
        w.writerow([
            c.get("email", ""), c.get("email_source", ""),
            "true" if c.get("email_verified") else "false",
            c.get("first_name", ""), c.get("last_name", ""), c.get("company", ""),
            c.get("domain", ""), c.get("role", ""), c.get("linkedin_url", ""),
            c.get("persona_segment", ""), c.get("source_competitor", ""),
            subject, (body or "").replace("\n", " "),
        ])

    name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"outbound_{run_id}_{run['target_company']}")
    return StreamingResponse(
        _io.BytesIO(buf.getvalue().encode()), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}.csv"})


@app.delete("/api/outbound/runs/{run_id}")
async def delete_outbound_run(run_id: int):
    """Remove a run and everything it produced."""
    pool = await db.pool()
    got = await pool.fetchval("DELETE FROM outbound_runs WHERE id=$1 RETURNING id", run_id)
    if got is None:
        raise HTTPException(404, "Outbound run not found")
    return {"ok": True}


@app.get("/api/outbound/runs/{run_id}/campaigns")
async def get_outbound_campaigns_api(run_id: int):
    from . import outbound_db
    campaigns = await outbound_db.get_outbound_campaigns(run_id)
    return {"campaigns": campaigns}

# ---------------------------------------------------------------- export ---
# ------------------------------------------------ accounts to contacts ---
class CompanyLookup(BaseModel):
    company: str = ""                     # a name or a domain
    roles: list[str] = []                 # role groups; see contacts.ROLE_GROUPS


@app.post("/api/contacts/find")
async def find_contacts(body: CompanyLookup):
    """Decision-makers at a company, from a name or a domain.

    The entry point for a team that has accounts rather than people. Every
    contact returned carries the URL that named them; one that does not is
    dropped rather than shown, because a name with no source is a name the
    model produced from nothing.
    """
    company = body.company.strip()
    if not company:
        raise HTTPException(400, "Give a company name or a domain.")
    return await contacts.find(company, body.roles or None)


@app.get("/api/runs/{run_id}/addresses")
async def address_candidates(run_id: int):
    """Find an address for this lead, trying each source in turn.

    First match wins, and every step reports hit, miss, or skipped — "found
    nothing" and "never looked" are different answers, and a pipeline that
    conflates them cannot be trusted or debugged.

    Only the last step derives anything. Those are the arithmetic of a name
    against a domain, never a lookup, and they are labelled derived the whole
    way to the screen so nothing can present one as a verified address.
    """
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if not (run.get("name") or "").strip():
        return {"address": "", "derived": False, "source": "", "steps": [],
                "candidates": []}
    return await contacts.resolve_address(run)


@app.get("/api/export")
async def export_runs(batch_id: str = ""):
    """CSV of every run, ready to bulk-import into a sequencer.

    Without this the tool is a dead end: reps send from Outreach/Salesloft/
    Gmail, and hand-copying drafts one at a time is worse than not having it.
    """
    import csv
    import io as _io

    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "first_name", "company", "role", "email", "email_grade",
                "linkedin_url", "seniority", "function", "icp_score", "priority",
                "job_change", "status", "hook_level", "hook_category", "hook",
                "hook_date", "source", "subject", "draft", "written_as",
                "sent_at", "sent_to"])
    for r in await db.list_runs(1000, batch_id=batch_id):
        address = r.get("email") or ""
        # Grading is local only here: one CSV can hold a thousand rows, and a
        # DNS lookup each would turn an export into a minutes-long request.
        rating = reachability.grade(address, name=r.get("name") or "",
                                    company=r.get("company") or "",
                                    check_dns=False) if address else None
        change = r.get("job_change") or {}
        w.writerow([
            r.get("name", ""), (r.get("name") or "").split(" ")[0],
            r.get("company", ""), r.get("role", ""),
            address, rating["grade"] if rating else "",
            r.get("url", ""),
            r.get("seniority", ""), r.get("function", ""), r.get("icp_score", ""),
            _priority(r), change.get("summary", ""),
            r.get("status", ""), r.get("hook_level", ""), r.get("hook_category", ""),
            r.get("chosen_hook", ""), r.get("hook_date", ""), r.get("hook_source", ""),
            r.get("draft_subject", ""),
            (r.get("draft_body") or "").replace("\n", " "),
            r.get("drafted_by", ""), r.get("sent_at", "") or "", r.get("sent_to", "") or "",
        ])
    return StreamingResponse(
        _io.BytesIO(buf.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=dossier_export.csv"},
    )


# -------------------------------------------------------------- frontend ---
FRONTEND = ROOT / "frontend"
# The marketing page. One file, copied into the image on its own — the rest of
# docs/ is deliberately not shipped, because it is interview preparation and
# nobody outside this machine should be able to request it.
LANDING = ROOT / "landing" / "index.html"

if (FRONTEND / "assets").exists():
    # Vite emits hashed files under /assets — mount that path exactly.
    app.mount("/assets", StaticFiles(directory=str(FRONTEND / "assets")), name="assets")


def _spa() -> FileResponse:
    idx = FRONTEND / "index.html"
    if not idx.exists():
        raise HTTPException(
            503,
            "Frontend is not built. Run:  cd ui && npm install && npm run build",
        )
    return FileResponse(str(idx))


@app.get("/")
def index():
    """The landing page, for someone who has not seen this before.

    The app itself lives at /app. Splitting them means a first-time visitor gets
    an explanation rather than a login box, and the login box is one click away
    for anyone who already knows what this is. If the landing page is missing —
    a local checkout that never copied it — fall through to the app rather than
    erroring, so development is unaffected.
    """
    if LANDING.exists():
        return FileResponse(str(LANDING))
    return _spa()


@app.get("/app")
def app_root():
    """The application. React Router is mounted with basename="/app"."""
    return _spa()


@app.get("/{path:path}")
def spa_fallback(path: str):
    """Serve any other static file, else fall back to index.html.

    Keeps deep links working without a separate dev server during a demo. A
    deep link into the app — /app/outbound, /app/leads/41 — lands here too:
    there is no such file, so it returns the SPA and React Router resolves the
    rest of the path against its basename.
    """
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    candidate = FRONTEND / path
    if path and candidate.is_file():
        return FileResponse(str(candidate))

    # A link made before the app moved under /app — /leads/41, /outbound —
    # would otherwise return the SPA at a URL outside its basename, and React
    # Router would match nothing and render a blank page. Send it to the same
    # path under /app instead, so old links keep working.
    if path and not path.startswith("app"):
        return RedirectResponse(f"/app/{path}", status_code=308)

    return _spa()
