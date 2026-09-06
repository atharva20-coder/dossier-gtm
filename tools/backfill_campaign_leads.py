#!/usr/bin/env python3
"""Give already-created campaign leads the message their campaign wrote.

    python tools/backfill_campaign_leads.py --check     # report only
    python tools/backfill_campaign_leads.py             # write

Why this exists: outbound created a lead row for every contact, but only wrote
the message onto the ones it researched in full. A contact the search budget
stopped short of got a finished message on the outbound screen and an empty
lead — same person, two screens, one of them blank.

The pipeline now fills the lead as part of creating the campaign. This carries
the same fix back to leads created before it, rendering through
`outbound.render_message` so the backfill cannot disagree with the screen.

Only ever fills a lead whose draft is empty. A lead that was researched has its
own, better message and is left alone.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(ENV):
    for line in open(ENV):
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            os.environ.setdefault(
                "DATABASE_URL", line.split("=", 1)[1].strip().strip('"').strip("'"))

from backend import db, outbound_db                      # noqa: E402
from backend.pipeline import outbound                    # noqa: E402

REASON = ("Written from the competitor angle — this lead was not researched "
          "individually. Research it to find a hook specific to them.")


async def backfill_receipts(check: bool) -> int:
    """Put the findings onto stage rows recorded before they carried any.

    The Receipts tab used to show only what each stage did — "Found 2 contacts"
    — with the people themselves nowhere on screen. Stages now record what they
    found, and this reconstructs it for runs that finished earlier, from the
    contacts table those stages wrote to.

    Only the stages that can be rebuilt honestly. Nothing is invented: if the
    contact rows cannot answer it, the row is left as it was.
    """
    pool = await db.pool()
    runs = await pool.fetch("SELECT id, target_company, competitors FROM outbound_runs")
    touched = 0
    for r in runs:
        contacts = await outbound_db.get_outbound_contacts(r["id"])
        if not contacts:
            continue
        campaigns = await outbound_db.get_outbound_campaigns(r["id"])
        comps = r["competitors"]
        if isinstance(comps, str):
            import json as _json
            try:
                comps = _json.loads(comps or "[]")
            except Exception:
                comps = []
        rebuilt = {
            "competitor_discovery": [{"name": c.get("name", ""),
                                      "detail": c.get("domain", ""),
                                      "url": (f"https://{c['domain']}"
                                              if c.get("domain") else "")}
                                     for c in (comps or [])],
            "contact_search": [{"name": c["name"], "role": c.get("role") or "",
                                "company": c.get("company") or "",
                                "url": c.get("linkedin_url") or ""}
                               for c in contacts],
            "email_enrichment": [{"name": c["name"], "email": c.get("email") or "",
                                  "source": c.get("email_source") or "",
                                  "verified": bool(c.get("email_verified"))}
                                 for c in contacts if c.get("email")],
            "drafting": [{"name": c["name"],
                          "opener": (c.get("opener_line") or "")[:140],
                          "lead_run_id": c.get("lead_run_id")}
                         for c in contacts],
            "campaign_creation": [{"name": c.get("name") or "",
                                   "count": c.get("contact_count") or 0}
                                  for c in campaigns],
        }
        for stage, found in rebuilt.items():
            if not found:
                continue
            rows = await pool.fetch(
                "SELECT id, payload FROM outbound_stages "
                "WHERE run_id=$1 AND stage=$2 AND status='done' ORDER BY id DESC LIMIT 1",
                r["id"], stage)
            if not rows:
                continue
            payload = db._json(rows[0]["payload"])
            if payload.get("found"):
                continue
            payload["found"] = found
            print(f"  {'would add' if check else 'added'} {len(found):>2} finding(s) "
                  f"to {stage} on outbound run {r['id']}")
            if not check:
                await pool.execute("UPDATE outbound_stages SET payload=$1 WHERE id=$2",
                                   payload, rows[0]["id"])
            touched += 1
    print(f"{touched} stage row(s) {'to update' if check else 'updated'}")
    return touched


async def main(check: bool) -> int:
    sender = (await db.get_config("writer") or {}).get("sender_name", "")
    persona = await db.get_selected_persona()
    pool = await db.pool()
    runs = await pool.fetch("SELECT id, target_company FROM outbound_runs ORDER BY id")

    filled = skipped = empty = 0
    for r in runs:
        contacts = await outbound_db.get_outbound_contacts(r["id"])
        campaigns = {c["persona"]: c
                     for c in await outbound_db.get_outbound_campaigns(r["id"])}
        for c in contacts:
            lead_id = c.get("lead_run_id")
            if not lead_id:
                continue
            lead = await db.get_run(lead_id)
            if not lead:
                continue
            if (lead.get("draft_body") or "").strip():
                skipped += 1
                continue
            subject, body = outbound.render_message(
                c, campaigns.get(c.get("persona_segment") or ""), sender)
            if not body.strip():
                empty += 1
                print(f"  - lead {lead_id} {c['name']}: nothing to write "
                      f"(no opener, no template)")
                continue
            print(f"  {'would fill' if check else 'filled'} lead {lead_id} "
                  f"{c['name']} at {c['company']} — {len(body)} chars")
            if not check:
                await db.update_run(lead_id, status="completed",
                                    draft_subject=subject, draft_body=body,
                                    failure_reason=REASON, **db.authored(persona))
                await db.add_stage(
                    lead_id, "draft", "done",
                    f"Message written for the {c.get('persona_segment') or 'campaign'} "
                    f"segment of the {r['target_company']} competitor campaign.",
                    {"draft": {"subject": subject, "body": body, "grounded": False,
                               "note": "from the competitor campaign"},
                     "from_campaign": True})
            filled += 1

    print(f"\n{filled} lead(s) {'to fill' if check else 'filled'} · "
          f"{skipped} already had a message · {empty} had nothing to write\n")
    await backfill_receipts(check)
    await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--check" in sys.argv)))
