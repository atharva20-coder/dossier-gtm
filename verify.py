#!/usr/bin/env python3
"""Preflight check — run this FIRST, before the app or a demo.

    python verify.py

Validates, in order, and stops at the first thing that's actually broken:
  1. Both API keys are present
  2. Tavily responds and returns results
  3. Gemini responds on both configured models
  4. A full pipeline run works end to end on a real prospect

Purpose: surface setup problems now, not halfway through a live demo.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
ok = lambda m: print(f"{GREEN}  PASS{RESET}  {m}")
bad = lambda m: print(f"{RED}  FAIL{RESET}  {m}")
warn = lambda m: print(f"{YELLOW}  WARN{RESET}  {m}")
step = lambda n, m: print(f"\n{n}. {m}")


async def main() -> int:
    print("\nDossier preflight\n" + "=" * 60)

    # ---- 1. keys ----------------------------------------------------------
    step(1, "API keys")
    from backend import config

    missing = config.missing_keys()
    if missing:
        for m in missing:
            bad(f"{m} is not set")
        print(f"\n{DIM}Add them to the .env file in this folder:{RESET}")
        print(f"{DIM}  TAVILY_API_KEY=tvly-...   (tavily.com){RESET}")
        print(f"{DIM}  GEMINI_API_KEY=...        (aistudio.google.com/apikey){RESET}\n")
        return 1
    ok("both keys present")

    # ---- 2. Tavily --------------------------------------------------------
    step(2, "Tavily search")
    from backend.integrations import search

    good, msg = await search.health()
    if not good:
        bad(f"Tavily: {msg}")
        if "auth" in msg:
            print(f"{DIM}  The key was rejected. Check it at tavily.com.{RESET}")
        elif "quota" in msg or "rate" in msg:
            print(f"{DIM}  Key works but you're out of credits / rate limited.{RESET}")
        else:
            print(f"{DIM}  Looks like a network problem rather than the key.{RESET}")
        return 1
    ok(f"Tavily {msg}")

    # ---- 3. Gemini --------------------------------------------------------
    step(3, "Gemini")
    from backend.integrations import llm

    good, msg = await llm.health()
    if not good:
        bad(f"Gemini: {msg}")
        print(f"{DIM}  Get a free key at aistudio.google.com/apikey{RESET}")
        return 1
    ok(f"Gemini {msg}")

    for model in (config.MODEL_FAST, config.MODEL_SMART):
        try:
            await llm.text("Reply with the single word: ok", model=model, temperature=0)
            ok(f"model available: {model}")
        except Exception as e:
            bad(f"model {model} unavailable: {e}")
            print(f"{DIM}  Set MODEL_FAST / MODEL_SMART in .env to a model your key can use.{RESET}")
            return 1

    # ---- 3b. optional person-signal provider ------------------------------
    step("3b", "Person-level signal provider (optional)")
    from backend.integrations import personsignal

    if not personsignal.enabled():
        warn("SUPERCARL_API_KEY not set — person-level signal will come from web "
             "search only (interviews, podcasts, talks, bylines). Still works.")
    else:
        ps_ok, ps_msg = await personsignal.health()
        if ps_ok:
            ok(f"Super Carl {ps_msg}")
        else:
            warn(f"Super Carl unavailable: {ps_msg}")
            print(f"{DIM}  Not fatal — runs continue on web search alone.{RESET}")

    # ---- 4. full pipeline -------------------------------------------------
    step(4, "Full pipeline (real APIs, one real prospect)")
    from backend import db
    from backend.models import ProspectInput
    from backend.pipeline import runner

    db.init()
    p = ProspectInput(name="Radhika Patil", company="Cradlewise",
                      role="Co-founder & CEO", location="Bengaluru, India")
    run_id = db.create_run("preflight", p.model_dump())

    seen: list[str] = []

    def emit(ev):
        if ev.status in ("done", "failed", "skipped"):
            mark = "OK " if ev.status == "done" else ("-- " if ev.status == "skipped" else "!! ")
            print(f"{DIM}     {mark}{ev.stage:9s} {ev.detail[:88]}{RESET}")
            seen.append(ev.stage)

    print(f"{DIM}     running: {p.name} at {p.company}…{RESET}")
    await runner.run(run_id, p, emit)

    run = db.get_run(run_id)
    status = run["status"]

    print()
    if status == "completed":
        ok(f"pipeline completed in {(run['elapsed_ms'] or 0)/1000:.1f}s")
        print(f"\n{DIM}     hook:   {run['chosen_hook']}{RESET}")
        print(f"{DIM}     source: {run['hook_source']}{RESET}")
        body = (run["draft_body"] or "").strip().replace("\n", "\n             ")
        print(f"\n     draft:  {body[:500]}\n")
    elif status == "no_signal_found":
        warn("pipeline ran, but no fact cleared the eligibility gates.")
        print(f"{DIM}     This is a legitimate result, not a bug — but for a demo,{RESET}")
        print(f"{DIM}     pick a prospect with fresher public news.{RESET}")
    elif status == "research_failed":
        bad(f"research failed: {run['failure_reason']}")
        return 1
    else:
        bad(f"pipeline ended as '{status}': {run.get('failure_reason') or 'unknown'}")
        return 1

    print("=" * 60)
    print(f"{GREEN}Ready.{RESET} Start the app with:\n")
    print("    uvicorn backend.main:app --reload --port 8000\n")
    print("then open http://localhost:8000\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
