"""Copy every table from one Supabase project to another.

Used to move the database closer to where the app runs. The transaction pooler
forbids prepared statements, so each query costs about two network round trips
and a run writes ~20 rows — distance between app and database is the single
largest thing in a page load. Moving from Sydney to Mumbai took a measured
400ms round trip to roughly 30ms.

    python scripts/migrate_region.py <OLD_DATABASE_URL> <NEW_DATABASE_URL>

Apply supabase/schema.sql to the NEW project first. Copies rows only; it never
drops or alters anything, and it is safe to re-run because every insert is
ON CONFLICT DO NOTHING. Sequences are reset afterwards so new ids continue past
the copied ones rather than colliding with them.
"""
from __future__ import annotations

import asyncio
import sys

import asyncpg

# Parents before children: run_stages and run_sources reference runs, and the
# three persona tables reference personas. Copying a child first fails on the
# foreign key, so this order is load-bearing rather than cosmetic.
TABLES = ["runs", "run_stages", "run_sources", "app_config",
          "style_examples", "person_profiles", "provider_calls",
          "personas", "persona_lessons", "persona_memories", "persona_revisions"]

SEQUENCES = {
    "runs": "id", "run_stages": "id", "run_sources": "id",
    "style_examples": "id", "provider_calls": "id", "personas": "id",
    "persona_lessons": "id", "persona_memories": "id", "persona_revisions": "id",
}


async def copy_table(src: asyncpg.Connection, dst: asyncpg.Connection, table: str) -> int:
    rows = await src.fetch(f"SELECT * FROM {table}")
    if not rows:
        print(f"  {table:<16} empty")
        return 0

    cols = list(rows[0].keys())
    collist = ", ".join(f'"{c}"' for c in cols)
    params = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
    # Re-runnable: a partially copied table finishes rather than erroring.
    sql = f"INSERT INTO {table} ({collist}) VALUES ({params}) ON CONFLICT DO NOTHING"

    async with dst.transaction():
        await dst.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    print(f"  {table:<16} {len(rows)} rows")
    return len(rows)


async def main(old_url: str, new_url: str) -> None:
    # JSONB must round-trip as text, or it is decoded on read and re-encoded on
    # write as a JSON *string* — the values survive but the types do not.
    src = await asyncpg.connect(old_url, statement_cache_size=0)
    dst = await asyncpg.connect(new_url, statement_cache_size=0)
    try:
        total = 0
        for table in TABLES:
            try:
                total += await copy_table(src, dst, table)
            except asyncpg.UndefinedTableError:
                print(f"  {table:<16} not present in source, skipped")

        for table, col in SEQUENCES.items():
            await dst.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                f"COALESCE((SELECT MAX({col}) FROM {table}), 1))")
        print(f"\n{total} rows copied. Sequences reset.")
        print("Now point DATABASE_URL at the new project and restart.")
    finally:
        await src.close()
        await dst.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
