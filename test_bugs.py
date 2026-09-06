#!/usr/bin/env python3
"""Regression checks for the four bugs fixed on 7 September 2026.

    python test_bugs.py

No server, no database, no network — every one of these is a pure function or a
pure transform, which is exactly why they were worth isolating. Each test names
the symptom a user reported, so a future change that reintroduces one says which
behaviour it broke rather than just failing.
"""
from __future__ import annotations

import sys

from backend.models import ExtractedFact, SearchHit
from backend.pipeline import grounding

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        print(f"       wanted {want!r}, got {got!r}")
        FAILURES.append(name)


# --------------------------------------------------------------------------
# "No message drafted" — a hook from X carries handles nobody writes in prose,
# so every attempt failed the check and the run finished with no message.
# --------------------------------------------------------------------------
HANDLE_HOOK = ExtractedFact(
    text="Pedro Franceschi appeared on the LightconePod podcast to discuss AI.",
    level="person", category="speaking", date="2026-06-10",
    key_entities=["@LightconePod", "@pedroh96"],
    source_url="https://x.com/ycombinator/status/1",
)

check("a draft naming the podcast in prose is accepted",
      grounding.verify_draft(
          "Hi Pedro,\n\nCaught your Lightcone podcast conversation on AI.", HANDLE_HOOK)[0],
      True)

check("the handle written literally is still accepted",
      grounding.verify_draft("Hi Pedro,\n\nYou were on @LightconePod.", HANDLE_HOOK)[0],
      True)

check("a draft that names nothing specific is still rejected",
      grounding.verify_draft(
          "Hi Pedro,\n\nBrex is doing interesting things in spend.", HANDLE_HOOK)[0],
      False)

check("an empty draft is still rejected",
      grounding.verify_draft("", HANDLE_HOOK)[0], False)

# The looser match must never reach source grounding, which is the safeguard
# against invention and has to stay a verbatim check.
check("source grounding stays strict — prose does not satisfy a handle",
      grounding.verify_fact(
          HANDLE_HOOK,
          [SearchHit(title="t", content="a lightcone podcast episode",
                     url="u", query="q")])[0],
      False)

check("source grounding still passes on the literal handles",
      grounding.verify_fact(
          HANDLE_HOOK,
          [SearchHit(title="t", content="@pedroh96 joined @LightconePod today",
                     url="u", query="q")])[0],
      True)

# A short entity must not match by accident once punctuation is squashed away.
check("a very short entity cannot match on squashing alone",
      grounding.named_in_prose("@ai", "we build agents", "webuildagents"),
      False)

if FAILURES:
    print(f"\n{len(FAILURES)} failed: {', '.join(FAILURES)}")
    sys.exit(1)
print("\nall passed")
