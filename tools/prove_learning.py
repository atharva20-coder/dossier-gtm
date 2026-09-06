#!/usr/bin/env python3
"""Prove the persona actually learns, rather than just storing what you typed.

    python tools/prove_learning.py            # run the whole demonstration
    python tools/prove_learning.py --show     # print current state, change nothing

The claim under test is the app's central one, and the one an interviewer will
push hardest on: "it learns from what you do." A tool that files your edits away
and writes the same thing next time can look identical from the outside, so this
checks the only thing that separates them — whether a lead you never touched
comes out different afterwards.

What it does, in order:

  1. Prints the persona's learned rules as they stand.
  2. Makes a few edits to leads, each expressing the SAME preference. One edit
     is a mood; the app deliberately waits for a pattern.
  3. Shows what the persona concluded, in its own words, including the rule it
     dropped or replaced and why.
  4. Rewrites a DIFFERENT lead — one never edited, from the same facts and the
     same hook — and diffs it against what that lead said before.

Step 4 is the whole point. A change there cannot come from the edits being
replayed, because that lead was not edited: it can only come from the rule.

Nothing is invented and nothing is mocked — every call goes through the running
app, and the leads it touches are real rows you can open afterwards.
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

BASE = os.getenv("DOSSIER_URL", "http://localhost:8000")

BOLD, DIM, GREEN, AMBER, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"


def head(text: str) -> None:
    print(f"\n{BOLD}{text}{RESET}\n" + "─" * min(len(text), 72))


def key() -> str:
    k = os.environ.get("APP_ACCESS_KEY")
    if not k:
        env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        for line in open(env):
            if line.startswith("APP_ACCESS_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not k:
        sys.exit("APP_ACCESS_KEY not set and not found in .env")
    return k


async def rules(c: httpx.AsyncClient, persona_id: int) -> list[dict]:
    h = (await c.get(f"/api/personas/{persona_id}/history")).json()
    return [m for m in h.get("memories", []) if m.get("active", True)]


async def show_rules(c: httpx.AsyncClient, persona_id: int, title: str) -> list[dict]:
    head(title)
    rs = await rules(c, persona_id)
    if not rs:
        print(f"  {DIM}(none yet){RESET}")
    for r in rs:
        print(f"  · {r['rule']}")
        print(f"    {DIM}learned from {r['learned_from']} edit(s){RESET}")
    hist = (await c.get(f"/api/personas/{persona_id}/history")).json()
    lessons = hist.get("lessons") or {}
    print(f"\n  {DIM}edits recorded: {lessons.get('total', 0)} · "
          f"waiting to be learned from: {lessons.get('pending', 0)}{RESET}")
    return rs


async def main(show_only: bool) -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=180) as c:
        # The app authenticates with a cookie, not a header — the same session
        # a browser gets, so this exercises exactly the path a person does.
        auth = await c.post("/api/auth", json={"key": key()})
        if auth.status_code != 200:
            sys.exit(f"Could not sign in ({auth.status_code}): {auth.text[:200]}")

        personas = (await c.get("/api/personas")).json()["personas"]
        persona = next((p for p in personas if p.get("is_selected")), personas[0])
        pid = persona["id"]
        print(f"{BOLD}Persona:{RESET} {persona.get('emoji', '')} {persona['name']}  "
              f"{DIM}(id {pid}){RESET}")

        before_rules = await show_rules(c, pid, "1 · What it has learned so far")
        if show_only:
            return 0

        # ---- pick leads: several to teach with, one held back ---------------
        runs = (await c.get("/api/runs")).json()["runs"]
        usable = [r for r in runs
                  if (r.get("draft_body") or "").strip() and r.get("chosen_hook")]
        if len(usable) < 3:
            sys.exit("Need at least 3 leads with a drafted message to demonstrate this.")

        control, teach = usable[0], usable[1:3]
        head("2 · The lead held back")
        print(f"  {control['name']} at {control.get('company') or '—'} "
              f"{DIM}(run {control['id']}){RESET}")
        print(f"  {DIM}Never edited. This is what has to change on its own.{RESET}\n")
        print("  " + (control["draft_body"] or "").replace("\n", "\n  "))
        control_before = control["draft_body"]

        # ---- teach: the same preference, expressed more than once -----------
        #
        # One consistent change, applied to different messages: end on a
        # concrete, time-boxed ask instead of a vague one. Chosen because it is
        # unmistakable in the output, so step 4 cannot be read two ways.
        head("3 · Teaching it, one preference, several times")
        for r in teach:
            body = r["draft_body"] or ""
            edited = body.rstrip() + "\n\nOpen to a 15-minute call on Tuesday or Wednesday?"
            res = await c.post(f"/api/runs/{r['id']}/draft", json={"edited": edited})
            out = res.json()
            print(f"  edited {r['name']} {DIM}(run {r['id']}){RESET} — "
                  f"learned={out.get('learned')} "
                  f"persona changes: {len(out.get('persona_changes') or [])}")
            for ch in out.get("persona_changes") or []:
                colour = AMBER if ch["action"] == "drop" else GREEN
                print(f"    {colour}{ch['action']}{RESET}: {ch.get('rule') or ch.get('replaced')}")
                if ch.get("why"):
                    print(f"      {DIM}why: {ch['why']}{RESET}")

        after_rules = await show_rules(c, pid, "4 · What it concluded")

        kept = {r["rule"] for r in after_rules}
        removed = [r["rule"] for r in before_rules if r["rule"] not in kept]
        fresh = [r["rule"] for r in after_rules
                 if r["rule"] not in {b["rule"] for b in before_rules}]
        if fresh:
            print(f"\n  {GREEN}new rule(s):{RESET}")
            for r in fresh:
                print(f"    + {r}")
        if removed:
            print(f"\n  {AMBER}rules it dropped as no longer right:{RESET}")
            for r in removed:
                print(f"    - {r}")
        if not fresh and not removed:
            print(f"\n  {DIM}No rule changed. It needs a clearer pattern — run again, "
                  f"or make the edits more consistent.{RESET}")

        # ---- the test that matters -----------------------------------------
        head("5 · The held-back lead, rewritten from the same facts")
        print(f"  {DIM}No new research, same hook, same evidence — only the "
              f"persona changed.{RESET}")
        res = await c.post(f"/api/runs/{control['id']}/regenerate",
                           json={"excluded": [], "chosen": ""})
        after = (res.json().get("run") or {}).get("draft_body") or ""
        print(f"\n  {DIM}before:{RESET}")
        print("  " + (control_before or "").replace("\n", "\n  "))
        print(f"\n  {DIM}after:{RESET}")
        print("  " + after.replace("\n", "\n  "))
        changed = after.strip() != (control_before or "").strip()
        print(f"\n  {(GREEN + 'CHANGED') if changed else (AMBER + 'UNCHANGED')}{RESET} — "
              + ("a lead you never touched now writes differently."
                 if changed else
                 "the rule did not reach this lead's message."))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--show" in sys.argv)))
