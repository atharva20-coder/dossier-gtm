#!/usr/bin/env python3
"""Prove one persona's learning never reaches another.

    python tools/check_persona_isolation.py

Everything the app learns is scoped to the voice that learned it — edits, hook
preferences, rejected facts, and which leads belong to whom. This checks that
claim against the database rather than against the code, because the scoping is
spread across half a dozen queries and any one of them forgetting its filter is
a silent leak: two personas slowly converging on a voice neither person writes
in, and nobody able to see why.

Read-only. It asserts, it never writes.

Rows recorded before scoping existed carry no persona, and are deliberately
shared rather than hidden — that is history, not a leak, and it is reported
separately so the two are never confused.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(ENV):
    for line in open(ENV):
        if line.startswith("DATABASE_URL="):
            os.environ.setdefault(
                "DATABASE_URL", line.split("=", 1)[1].strip().strip('"').strip("'"))

from backend import db                       # noqa: E402
from backend.pipeline import draft           # noqa: E402

LEAKS: list[str] = []
SHARED: list[str] = []


def check(name: str, leaked: list) -> None:
    if leaked:
        LEAKS.append(name)
        print(f"LEAK  {name}")
        for row in leaked[:5]:
            print(f"        {row}")
    else:
        print(f"ok    {name}")


async def main() -> int:
    p = await db.pool()
    personas = await p.fetch("SELECT id, name, emoji FROM personas ORDER BY id")
    if len(personas) < 2:
        print("Only one persona — nothing to isolate. Create a second to test this.")
        return 0

    print(f"{len(personas)} personas\n")

    # ---- leads --------------------------------------------------------
    bad = []
    for a in personas:
        rows = await db.latest_leads(persona_id=a["id"])
        for r in rows:
            owner = r.get("owner_persona_id")
            if owner is not None and owner != a["id"]:
                bad.append(f"{a['name']} sees lead {r['id']} ({r['name']}) "
                           f"owned by persona {owner}")
    check("leads are only ever the owner's, or unowned", bad)

    # ---- hook preferences ---------------------------------------------
    bad = []
    for a in personas:
        counts = await db.hook_outcomes(a["id"])
        got = sum(c["drafted"] for c in counts.values())
        mine = await p.fetchval(
            "SELECT count(*) FROM runs WHERE chosen_hook IS NOT NULL "
            "AND coalesce(hook_category,'') <> '' "
            "AND (persona_id = $1 OR persona_id IS NULL)", a["id"])
        if got != mine:
            bad.append(f"{a['name']}: counted {got} hooks, owns {mine}")
    check("hook preferences count only this persona's runs", bad)

    # ---- rejected facts ------------------------------------------------
    bad = []
    for a in personas:
        counts = await db.fact_feedback_counts(a["id"])
        got = sum(c["chose"] + c["excluded"] + c["included"] for c in counts.values())
        mine = await p.fetchval(
            "SELECT count(*) FROM fact_feedback WHERE coalesce(category,'') <> '' "
            "AND (persona_id = $1 OR persona_id IS NULL)", a["id"])
        if got != mine:
            bad.append(f"{a['name']}: counted {got} judgements, owns {mine}")
    check("rejected and chosen facts stay with the voice that judged them", bad)

    # ---- edits ----------------------------------------------------------
    bad = []
    for a in personas:
        for e in await db.recent_style_examples(50, a["id"]):
            if e.get("persona_id") not in (None, a["id"]):
                bad.append(f"{a['name']} offered an edit from persona {e['persona_id']}")
    check("edits are offered only to the voice that was edited", bad)

    # ---- the prompt itself ----------------------------------------------
    #
    # The end of the chain, and the one that matters: whatever the queries do,
    # no persona may be handed another's rules in the text it writes from.
    bad = []
    rules = {a["id"]: {m["rule"] for m in await db.active_memories(a["id"])}
             for a in personas}
    for a in personas:
        row = await db.get_persona(a["id"])
        row["memories"] = await db.active_memories(a["id"])
        block = draft._persona_block(row)
        for b in personas:
            if b["id"] == a["id"]:
                continue
            for rule in rules[b["id"]] - rules[a["id"]]:
                if rule and rule in block:
                    bad.append(f"{a['name']}'s prompt contains {b['name']}'s rule: "
                               f"{rule[:60]}")
    check("no persona's prompt contains another's learned rule", bad)

    # ---- campaigns -------------------------------------------------------
    #
    # An outbound run is one persona's work in the same way a lead is: it
    # writes in that voice, the leads it creates are owned by it, and what it
    # teaches belongs to it.
    from backend import outbound_db          # noqa: PLC0415
    bad = []
    for a in personas:
        for r in await outbound_db.list_outbound_runs(a["id"]):
            owner = r.get("persona_id")
            if owner is not None and owner != a["id"]:
                bad.append(f"{a['name']} sees campaign {r['id']} "
                           f"({r['target_company']}) owned by persona {owner}")
    check("campaigns are only ever the owner's, or unowned", bad)

    # And the leads a campaign creates must belong to the same voice it does.
    bad = [f"campaign {r['run_id']} (persona {r['cpid']}) made lead {r['lead']} "
           f"owned by persona {r['lpid']}"
           for r in await p.fetch("""
               SELECT c.run_id, o.persona_id AS cpid,
                      r.id AS lead, r.owner_persona_id AS lpid
               FROM outbound_contacts c
               JOIN outbound_runs o ON o.id = c.run_id
               JOIN runs r ON r.id = c.lead_run_id
               WHERE o.persona_id IS NOT NULL
                 AND r.owner_persona_id IS NOT NULL
                 AND r.owner_persona_id <> o.persona_id""")]
    check("a campaign's leads belong to the campaign's persona", bad)

    # ---- what is shared on purpose ---------------------------------------
    for table, label in (("style_examples", "edits"),
                         ("fact_feedback", "fact judgements")):
        n = await p.fetchval(f"SELECT count(*) FROM {table} WHERE persona_id IS NULL")
        if n:
            SHARED.append(f"{n} {label} recorded before scoping existed — shared by all")
    n = await p.fetchval("SELECT count(*) FROM runs WHERE owner_persona_id IS NULL")
    if n:
        SHARED.append(f"{n} leads with no owner — visible to all")
    n = await p.fetchval("SELECT count(*) FROM outbound_runs WHERE persona_id IS NULL")
    if n:
        SHARED.append(f"{n} campaigns with no owner — visible to all")

    print()
    for line in SHARED:
        print(f"note  {line}")

    await db.close()
    if LEAKS:
        print(f"\n{len(LEAKS)} leak(s): {', '.join(LEAKS)}")
        return 1
    print("\nno leakage between personas")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
