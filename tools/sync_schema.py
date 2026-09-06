"""Regenerate supabase/schema.sql from backend/db.py.

The two were meant to be identical and drifted: schema.sql was missing all four
persona tables, so a fresh production database would have come up without them
and every persona call would have failed on first boot. Generating one from the
other removes the possibility rather than restating the rule in a comment.

    python3 tools/sync_schema.py          # rewrite
    python3 tools/sync_schema.py --check  # fail if out of date (used by tests)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "supabase" / "schema.sql"

HEADER = """-- Dossier schema. Run ONCE in the Supabase SQL editor
-- (Dashboard -> SQL Editor -> New query -> paste -> Run).
--
-- GENERATED FROM backend/db.py BY tools/sync_schema.py — DO NOT EDIT BY HAND.
-- Run `python3 tools/sync_schema.py` after changing SCHEMA there. A test fails
-- if the two disagree, because they silently did once: this file was missing
-- every persona table, which a fresh database would only reveal at boot.
--
-- Not applied on application startup: a serverless app cold-starts constantly
-- and concurrently, so running DDL per boot is wasted round trips and a race.
-- Every statement is IF NOT EXISTS, so re-running it is safe.
--
-- RLS is enabled on every table in the deployed database, and the application
-- connects with a role that bypasses it. The anon key is never used and nothing
-- here is reached from a browser. Enabling RLS is not part of this file because
-- it is a one-time operation on the live database, not a schema definition.
"""


def rendered() -> str:
    sys.path.insert(0, str(ROOT))
    from backend.db import SCHEMA
    return HEADER + SCHEMA.rstrip() + "\n"


if __name__ == "__main__":
    want = rendered()
    if "--check" in sys.argv:
        have = TARGET.read_text() if TARGET.exists() else ""
        if have != want:
            sys.exit("supabase/schema.sql is out of date — run tools/sync_schema.py")
        print("schema.sql matches backend/db.py")
    else:
        TARGET.write_text(want)
        print(f"wrote {TARGET.relative_to(ROOT)} ({len(want.splitlines())} lines)")
