"""Database operations for the outbound pipeline."""

# `X | None` in an annotation is a syntax error before Python 3.10, and this
# app runs on 3.9. The future import defers annotation evaluation so the
# modern spelling works on both.
from __future__ import annotations
import json
from typing import Any

from . import db

async def create_outbound_run(target_company: str, config: dict) -> int:
    pool = await db.pool()
    query = """
        INSERT INTO outbound_runs (target_company, config)
        VALUES ($1, $2)
        RETURNING id
    """
    return await pool.fetchval(query, target_company, json.dumps(config))

async def get_outbound_run(run_id: int) -> dict[str, Any] | None:
    pool = await db.pool()
    row = await pool.fetchrow("SELECT * FROM outbound_runs WHERE id = $1", run_id)
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("competitors"), str):
        d["competitors"] = json.loads(d["competitors"])
    if isinstance(d.get("config"), str):
        d["config"] = json.loads(d["config"])
    return d

async def list_outbound_runs() -> list[dict[str, Any]]:
    pool = await db.pool()
    # The counts come from the same query rather than a request per row: the
    # list pane shows them for every run, and fetching them separately is one
    # round trip per run on a page that already has the data one join away.
    rows = await pool.fetch("""
        SELECT r.*,
               (SELECT count(*) FROM outbound_contacts c WHERE c.run_id = r.id)
                   AS contact_count,
               (SELECT count(*) FROM outbound_campaigns k WHERE k.run_id = r.id)
                   AS campaign_count
        FROM outbound_runs r ORDER BY r.id DESC LIMIT 50""")
    out = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("competitors"), str):
            d["competitors"] = json.loads(d["competitors"])
        if isinstance(d.get("config"), str):
            d["config"] = json.loads(d["config"])
        out.append(d)
    return out

async def update_outbound_run(run_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = []
    args = [run_id]
    for i, (k, v) in enumerate(fields.items(), start=2):
        sets.append(f"{k} = ${i}")
        if isinstance(v, (dict, list)):
            args.append(json.dumps(v))
        else:
            args.append(v)
    query = f"UPDATE outbound_runs SET {', '.join(sets)}, updated_at = now() WHERE id = $1"
    pool = await db.pool()
    await pool.execute(query, *args)

async def add_outbound_stage(run_id: int, stage: str, status: str, detail: str = "", payload: dict | None = None) -> None:
    pool = await db.pool()
    query = """
        INSERT INTO outbound_stages (run_id, stage, status, detail, payload)
        VALUES ($1, $2, $3, $4, $5)
    """
    await pool.execute(query, run_id, stage, status, detail, json.dumps(payload or {}))

async def get_outbound_stages(run_id: int) -> list[dict[str, Any]]:
    pool = await db.pool()
    rows = await pool.fetch("SELECT * FROM outbound_stages WHERE run_id = $1 ORDER BY id ASC", run_id)
    out = []
    for r in rows:
        d = dict(r)
        # asyncpg hands JSONB back as text unless a codec is installed, and the
        # UI types this as an object. Decoded here so every caller gets the
        # same shape rather than each one guessing.
        if isinstance(d.get("payload"), str):
            d["payload"] = json.loads(d["payload"] or "{}")
        out.append(d)
    return out

async def create_outbound_contact(run_id: int, contact: dict) -> int:
    pool = await db.pool()
    query = """
        INSERT INTO outbound_contacts (
            run_id, name, first_name, last_name, company, domain, role,
            seniority, email, email_verified, email_source, linkedin_url,
            persona_segment, opener_line, source_competitor
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        RETURNING id
    """
    return await pool.fetchval(
        query,
        run_id,
        contact.get("name", ""),
        contact.get("first_name", ""),
        contact.get("last_name", ""),
        contact.get("company", ""),
        contact.get("domain", ""),
        contact.get("role", ""),
        contact.get("seniority", ""),
        contact.get("email", ""),
        contact.get("email_verified", False),
        contact.get("email_source", ""),
        contact.get("linkedin_url", ""),
        contact.get("persona_segment", ""),
        contact.get("opener_line", ""),
        contact.get("source_competitor", "")
    )

async def get_outbound_contacts(run_id: int) -> list[dict[str, Any]]:
    pool = await db.pool()
    # Ordered, so the list does not reshuffle between polls while a run is
    # still writing rows into it.
    rows = await pool.fetch(
        "SELECT * FROM outbound_contacts WHERE run_id = $1 "
        "ORDER BY persona_segment, id", run_id)
    return [dict(r) for r in rows]

async def create_outbound_campaign(run_id: int, name: str, persona: str, subject: str, body: str, contact_count: int) -> int:
    pool = await db.pool()
    query = """
        INSERT INTO outbound_campaigns (
            run_id, name, persona, template_subject, template_body, contact_count
        ) VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
    """
    return await pool.fetchval(query, run_id, name, persona, subject, body, contact_count)

async def get_outbound_campaigns(run_id: int) -> list[dict[str, Any]]:
    pool = await db.pool()
    rows = await pool.fetch("SELECT * FROM outbound_campaigns WHERE run_id = $1", run_id)
    return [dict(r) for r in rows]
