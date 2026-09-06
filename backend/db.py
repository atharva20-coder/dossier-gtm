"""Postgres persistence (Supabase).

Runs are persisted per-stage so a browser close or a function timing out leaves
a readable record instead of a row stuck in 'running' forever (SCENARIOS.md
F2/F3). On serverless that per-stage write does double duty: it is also what the
UI reads to show live progress, because a Vercel function cannot hold an SSE
queue in memory between invocations.

CONNECTING FROM SERVERLESS
--------------------------
Every call goes through Supabase's Supavisor pooler in TRANSACTION mode
(port 6543), not a direct connection. A serverless platform opens and discards
connections constantly, and Postgres charges real memory per backend — pointing
hundreds of cold starts at port 5432 exhausts the instance's connection budget
long before it exhausts anything else.

Two consequences follow from transaction mode and both are load-bearing:

  * Prepared statements must be off (`statement_cache_size=0`). Transaction
    mode hands your session a different backend per transaction, so a statement
    prepared on one is missing on the next — the failure is intermittent
    `InvalidSQLStatementNameError`, which is exactly the kind of bug that only
    shows up under production concurrency.

    The price is that every query costs roughly two network round trips instead
    of one, which makes PROXIMITY the dominant performance factor here. A run
    writes ~20 stage rows, so each 100ms of distance between the app and the
    database is ~4 seconds added to every run. Measured against a Sydney project
    from India: 400ms RTT, ~800ms per query, ~16s per run spent waiting. Deploy
    the app in the database's region — `vercel.json` pins one for this reason —
    or move the database to the app's.
  * The pool stays deliberately small. `max_size=4` per instance, and Vercel
    runs many instances: 4 x (concurrent instances) must fit inside the pooler's
    budget with room for migrations and the Supabase dashboard's own sessions.
    Raising this number is how you take an app down at 3am.

One pool per process, created lazily and reused across invocations on a warm
instance. Never build a second pool for "a different code path".
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import asyncpg

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            BIGSERIAL PRIMARY KEY,
    batch_id      TEXT,
    name          TEXT NOT NULL,
    company       TEXT,
    role          TEXT,
    location      TEXT,
    url           TEXT,
    -- Load-bearing, not decorative: the pipeline refuses to draft at all for
    -- customers, open opportunities and competitors, and it reads that decision
    -- from here. Before this column existed the value was only ever held in
    -- memory, so any path that rebuilt the prospect from the database lost it.
    relationship  TEXT,
    -- Where a message would actually go, and the record of it having gone.
    -- `sent_at` is the idempotency key as much as the audit trail: a resend is
    -- only ever deliberate, because the recipient cannot unread the first one.
    email         TEXT,
    sent_at       TIMESTAMPTZ,
    sent_to       TEXT,
    sent_subject  TEXT,
    sent_message_id TEXT,
    status        TEXT NOT NULL,
    identity_confidence INTEGER,
    seniority     TEXT,
    function      TEXT,
    icp_score     INTEGER,
    hook_level    TEXT,
    chosen_hook   TEXT,
    hook_category TEXT,
    hook_date     TEXT,
    hook_source   TEXT,
    draft_subject TEXT,
    draft_body    TEXT,
    -- Who wrote this draft. Without it the interface names whichever identity
    -- is active right now as the author of a message written by a different
    -- one — and there is then no way to tell that switching identity has left
    -- this lead behind. The name is stored alongside the id because an
    -- identity can be deleted and the draft it wrote still has an author.
    persona_id    BIGINT,
    drafted_by    TEXT NOT NULL DEFAULT '',
    -- What the research found that contradicts the row as imported: a new
    -- employer, a new title. CRM rows go stale silently, and a job change is
    -- both the best hook there is and the reason an address stops working.
    job_change    JSONB,
    failure_reason TEXT,
    elapsed_ms    INTEGER,
    -- Which facts the user chose to include or exclude by hand, overriding the
    -- judge. Kept on the run so a reload shows the drafted message and the
    -- selection that produced it, rather than the message alone.
    fact_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run_stages (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    elapsed_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_stages_run_id_idx ON run_stages (run_id, id);

CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS style_examples (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT,
    prospect   TEXT,
    original   TEXT NOT NULL,
    edited     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run_sources (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url        TEXT,
    title      TEXT,
    query      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_sources_run_id_idx ON run_sources (run_id, id);
CREATE INDEX IF NOT EXISTS runs_batch_id_idx ON runs (batch_id, id DESC);

-- Resolved person profiles, cached by whatever identified them.
--
-- The provider is metered in whole calls and the allowance is small, so the
-- expensive part of a run is repeated lookups of the same person: re-running a
-- lead, or ten colleagues at one company. A profile changes over weeks, not
-- minutes, and the cost of a slightly stale one is far below the cost of not
-- being able to research anyone because the quota is gone.
CREATE TABLE IF NOT EXISTS person_profiles (
    cache_key   TEXT PRIMARY KEY,
    profile_id  TEXT,
    text        TEXT NOT NULL,
    note        TEXT,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per paid provider call. The allowance is small enough that "how many
-- have I used" is a question worth being able to answer exactly.
CREATE TABLE IF NOT EXISTS provider_calls (
    id         BIGSERIAL PRIMARY KEY,
    provider   TEXT NOT NULL,
    endpoint   TEXT NOT NULL,
    cache_key  TEXT,
    ok         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS provider_calls_at_idx ON provider_calls (provider, created_at DESC);

-- Competitor outbound.
--
-- One instruction produces a run, the competitors it found, the people at each
-- of them, and one campaign per persona. Persisted rather than held in the
-- request because this is the longest thing the app does: a browser close
-- mid-way must leave a readable record, for the same reason runs are written
-- per stage.
CREATE TABLE IF NOT EXISTS outbound_runs (
    id             BIGSERIAL PRIMARY KEY,
    target_company TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'planned',
    competitors    JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- The plan read out of the instruction, plus what it is allowed to spend
    -- and what it has spent. Counted in searches and contacts rather than
    -- currency: these providers bill in free-tier quota, and a cap in dollars
    -- the app cannot observe would be theatre.
    config         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Receipts. One row per thing the run did, in order.
CREATE TABLE IF NOT EXISTS outbound_stages (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT NOT NULL REFERENCES outbound_runs(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    elapsed_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS outbound_stages_idx ON outbound_stages (run_id, id);

-- The people found, one row each.
--
-- `email_verified` is the whole reason `email_source` exists beside it: an
-- address read off a page and an address calculated from a domain convention
-- are different claims, and a column that held only the address could not tell
-- them apart.
CREATE TABLE IF NOT EXISTS outbound_contacts (
    id             BIGSERIAL PRIMARY KEY,
    run_id         BIGINT NOT NULL REFERENCES outbound_runs(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    first_name     TEXT NOT NULL DEFAULT '',
    last_name      TEXT NOT NULL DEFAULT '',
    company        TEXT NOT NULL DEFAULT '',
    domain         TEXT NOT NULL DEFAULT '',
    role           TEXT NOT NULL DEFAULT '',
    seniority      TEXT NOT NULL DEFAULT '',
    email          TEXT NOT NULL DEFAULT '',
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    email_source   TEXT NOT NULL DEFAULT '',
    linkedin_url   TEXT NOT NULL DEFAULT '',
    persona_segment TEXT NOT NULL DEFAULT '',
    opener_line    TEXT NOT NULL DEFAULT '',
    source_competitor TEXT NOT NULL DEFAULT '',
    -- The record of a message having gone out. `sent_at` is the idempotency
    -- key as much as the audit trail: a second send is only ever deliberate,
    -- because the recipient cannot unread the first one.
    sent_at        TIMESTAMPTZ,
    sent_to        TEXT,
    sent_subject   TEXT,
    sent_message_id TEXT,
    -- The lead this contact was researched as. Set when the run went deep:
    -- the contact then has everything a lead has — sources, the graph, the
    -- grounded hook — and the leads screen opens it unchanged.
    lead_run_id    BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS outbound_contacts_idx ON outbound_contacts (run_id, id);

-- One campaign per persona: the template every contact in that segment gets,
-- with their own opener line in front of it.
CREATE TABLE IF NOT EXISTS outbound_campaigns (
    id               BIGSERIAL PRIMARY KEY,
    run_id           BIGINT NOT NULL REFERENCES outbound_runs(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    persona          TEXT NOT NULL DEFAULT '',
    template_subject TEXT NOT NULL DEFAULT '',
    template_body    TEXT NOT NULL DEFAULT '',
    contact_count    INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'draft',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS outbound_campaigns_idx ON outbound_campaigns (run_id, id);

-- GTM personas: who is writing, and how they write.
--
-- Separate from app_config's single writer because a team is not one voice. The
-- same prospect gets a different message from a founder than from an SDR, and
-- the difference is worth keeping as a saved, named thing rather than something
-- retyped into a settings form each time.
CREATE TABLE IF NOT EXISTS personas (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    character    TEXT NOT NULL DEFAULT '',   -- who they are: role, seniority, remit
    instructions TEXT NOT NULL DEFAULT '',   -- how they write: tone, length, what to avoid
    emoji        TEXT NOT NULL DEFAULT '🙂',

    -- The rest of what a message needs in order to be worth sending. These sit
    -- on the persona rather than in one global setting because a founder and an
    -- SDR selling the same product are not writing the same email: the founder
    -- can say "I built this", the SDR cannot, and they are usually asking for
    -- different things.
    seniority    TEXT NOT NULL DEFAULT '',   -- founder / ceo / cro / vp / ae / sdr
    intent       TEXT NOT NULL DEFAULT '',   -- what this message is for
    product      TEXT NOT NULL DEFAULT '',   -- what they sell, in a sentence
    problem      TEXT NOT NULL DEFAULT '',   -- the pain it removes
    proof        TEXT NOT NULL DEFAULT '',   -- customers, numbers, credibility
    looking_for  TEXT NOT NULL DEFAULT '',   -- the signals that make someone worth writing to

    is_selected  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one persona is selected at a time; the database enforces it rather
-- than trusting every write path to remember.
CREATE UNIQUE INDEX IF NOT EXISTS personas_one_selected
    ON personas ((is_selected)) WHERE is_selected;

-- What the persona has been taught, and what it has not learned from yet.
--
-- Every accepted edit is evidence about how this person actually writes: the
-- draft it produced, the version they were willing to send, and — when the
-- change came through the assistant — what they asked for in words. Kept as
-- rows rather than folded straight into the persona so the learning step can
-- look at several edits together and find the pattern, instead of overreacting
-- to one.
CREATE TABLE IF NOT EXISTS persona_lessons (
    id          BIGSERIAL PRIMARY KEY,
    persona_id  BIGINT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    run_id      BIGINT,
    before      TEXT NOT NULL,
    after       TEXT NOT NULL,
    instruction TEXT NOT NULL DEFAULT '',
    applied     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS persona_lessons_pending_idx
    ON persona_lessons (persona_id, created_at) WHERE NOT applied;

-- Every version the persona's instructions have ever had.
--
-- A prompt that rewrites itself and keeps no history is a tool that quietly
-- becomes something else. Each revision records what changed, why, and how many
-- edits it was drawn from, so the drift is visible and any version can be
-- restored.
CREATE TABLE IF NOT EXISTS persona_revisions (
    id           BIGSERIAL PRIMARY KEY,
    persona_id   BIGINT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    instructions TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    learned_from INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS persona_revisions_idx
    ON persona_revisions (persona_id, id DESC);

-- What the persona has learned, one rule at a time.
--
-- Append-only on purpose. Regenerating the whole instruction set on every
-- lesson meant a model rewriting eight rules to change one, and rules that were
-- never contradicted came back subtly different or not at all. A memory is a
-- single line added beneath the instructions the author wrote, so their words
-- stay theirs and the learned part is always visibly separate.
--
-- Nothing is deleted. A rule that later proves wrong is superseded by the one
-- that replaces it, which keeps the reasoning legible: this is what it thought,
-- this is what changed its mind.
CREATE TABLE IF NOT EXISTS persona_memories (
    id            BIGSERIAL PRIMARY KEY,
    persona_id    BIGINT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    rule          TEXT NOT NULL,
    learned_from  INTEGER NOT NULL DEFAULT 0,
    supersedes    BIGINT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS persona_memories_active_idx
    ON persona_memories (persona_id, id) WHERE active;
"""

_pool: asyncpg.Pool | None = None
# The loop the pool's connections were created on. asyncpg binds its sockets and
# timers to one event loop, so a pool outliving its loop fails obscurely
# ("Event loop is closed", "another operation is in progress") rather than
# reconnecting. A warm serverless instance handed a fresh loop would hit exactly
# that, so the pool is rebuilt rather than reused when the loop changes.
_pool_loop: asyncio.AbstractEventLoop | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def pool() -> asyncpg.Pool:
    """The process's single connection pool. See the module docstring."""
    global _pool, _pool_loop

    running = asyncio.get_running_loop()
    if _pool is not None and _pool_loop is not running:
        # Abandon rather than close: closing would itself run on the dead loop.
        _pool, _pool_loop = None, None

    if _pool is None:
        if not config.DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set. Use the Supabase transaction pooler "
                "string (port 6543), not the direct connection."
            )
        async def _codecs(conn: asyncpg.Connection) -> None:
            """Decode JSON/JSONB to Python objects on the way out.

            Without this asyncpg hands back the raw string and every caller has
            to remember to parse it — which is exactly the kind of thing one
            caller eventually forgets, producing a string where a dict was
            expected far from where the mistake was made.
            """
            for typename in ("json", "jsonb"):
                await conn.set_type_codec(
                    typename, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

        _pool = await asyncpg.create_pool(
            config.DATABASE_URL,
            init=_codecs,
            min_size=config.DB_POOL_MIN,
            max_size=config.DB_POOL_MAX,
            statement_cache_size=0,  # required by the transaction pooler
            command_timeout=config.DB_COMMAND_TIMEOUT_S,
            # asyncpg's introspection cache has the same cross-backend problem
            # as the statement cache under transaction pooling.
            max_cached_statement_lifetime=0,
        )
        _pool_loop = running
    return _pool


async def close() -> None:
    global _pool, _pool_loop
    if _pool is not None:
        await _pool.close()
        _pool, _pool_loop = None, None


def _row(r: asyncpg.Record | None) -> dict | None:
    """Records out of asyncpg carry native types; the API returns JSON, so
    timestamps become ISO strings and JSONB comes back already decoded."""
    if r is None:
        return None
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat(timespec="seconds")
    return d


def _json(v) -> dict:
    """Normalise a JSON column to a dict.

    The pool installs a JSON/JSONB codec, so this is already a dict in practice.
    It stays because rows written before that codec existed are still strings,
    and a decode failure should read as an empty payload rather than a 500.
    """
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v or "{}")
    except Exception:
        return {}


async def init() -> None:
    """Create the schema. For local development only.

    In production the schema is applied once from supabase/schema.sql, not on
    every cold start: a serverless app starts constantly and concurrently, and
    running DDL on each one is both wasted round trips and a race.
    """
    p = await pool()
    async with p.acquire() as c:
        await c.execute(SCHEMA)


_last_reap = 0.0


async def reap_stale_runs(force: bool = False) -> int:
    """Mark runs that died mid-flight, so nothing sits in 'running' forever.

    On a single long-lived server this was a startup sweep: a process restart
    proved every in-flight run was dead. That reasoning does not survive
    serverless — instances start constantly and concurrently, so a cold start
    proves nothing about runs executing right now in another instance. Sweeping
    on startup there would kill live work.

    Age is the honest signal instead. A run cannot outlive the platform's
    function ceiling, so one that has not written a stage in appreciably longer
    than that is definitively dead, whoever was running it.
    """
    # Two extra round trips on a path the UI hits constantly, to catch something
    # that can only become true once every RUN_STALE_AFTER_S. Rate-limited per
    # process so a page load does not pay for it every time.
    global _last_reap
    now = time.monotonic()
    if not force and now - _last_reap < config.RUN_STALE_AFTER_S:
        return 0
    _last_reap = now

    p = await pool()
    async with p.acquire() as c:
        # A run that produced a draft before it died is finished work, not a
        # failure. Marking it 'error' would throw away a usable result and
        # invite the user to spend credits re-running it.
        await c.execute(
            "UPDATE runs SET status='completed', updated_at=now() "
            "WHERE status IN ('running','queued') AND draft_body IS NOT NULL "
            f"  AND updated_at < now() - interval '{config.RUN_STALE_AFTER_S} seconds'")
        n = await c.execute(
            "UPDATE runs SET status='error', "
            "failure_reason='interrupted (the run did not finish)', updated_at=now() "
            "WHERE status IN ('running','queued') "
            f"  AND updated_at < now() - interval '{config.RUN_STALE_AFTER_S} seconds'")
    return int(n.split()[-1])


async def create_run(batch_id: str, p_: dict) -> int:
    p = await pool()
    return int(await p.fetchval(
        """INSERT INTO runs (batch_id, name, company, role, location, url,
                            relationship, email, status)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'queued') RETURNING id""",
        batch_id, p_.get("name", ""), p_.get("company", ""), p_.get("role", ""),
        p_.get("location", ""), p_.get("url", ""), p_.get("relationship", ""),
        p_.get("email", ""),
    ))


def authored(persona: dict | None) -> dict:
    """The author fields to store next to any draft.

    Every path that writes a draft must pass these through, or the interface
    ends up naming whichever identity happens to be active as the author of a
    message a different one wrote — which is exactly the state that makes
    "switch identity" quietly do nothing to leads already drafted.
    """
    pd = persona or {}
    name = f"{pd.get('emoji') or ''} {pd.get('name') or ''}".strip()
    return {"persona_id": pd.get("id"), "drafted_by": name}


async def update_run(run_id: int, **fields) -> None:
    if not fields:
        return
    # $1 is the run id, so the field placeholders start at $2.
    sets = ", ".join(f"{k}=${i}" for i, k in enumerate(fields, start=2))
    p = await pool()
    await p.execute(
        f"UPDATE runs SET {sets}, updated_at=now() WHERE id=$1",
        run_id, *fields.values(),
    )


async def add_stage(run_id: int, stage: str, status: str, detail: str = "",
                    payload: dict | None = None, elapsed_ms: int = 0) -> None:
    p = await pool()
    await p.execute(
        """INSERT INTO run_stages (run_id, stage, status, detail, payload, elapsed_ms)
           VALUES ($1,$2,$3,$4,$5,$6)""",
        run_id, stage, status, detail, payload or {}, elapsed_ms,
    )


async def add_sources(run_id: int, sources: list[dict]) -> None:
    if not sources:
        return
    p = await pool()
    await p.executemany(
        "INSERT INTO run_sources (run_id, url, title, query) VALUES ($1,$2,$3,$4)",
        [(run_id, s.get("url", ""), s.get("title", ""), s.get("query", "")) for s in sources],
    )


async def claim_run(run_id: int) -> bool:
    """Atomically mark a run as running. False if something already had it.

    The guard has to be the write itself. Reading the status and then updating
    it leaves a window between the two, and a double-clicked button lands both
    requests inside it — so both see "not running", both proceed, and the run
    executes twice, spending two rounds of search and model credits. One
    conditional UPDATE closes it: the database decides who won.
    """
    p = await pool()
    got = await p.fetchval(
        "UPDATE runs SET status='running', updated_at=now() "
        "WHERE id=$1 AND status <> 'running' RETURNING id", run_id)
    return got is not None


async def delete_run(run_id: int) -> bool:
    """Remove a run and everything it produced. Returns whether it existed.

    Stages and sources go with it via ON DELETE CASCADE. Deleting is the only
    way to clear a run that failed and produced nothing, and without it the only
    remedy is SQL — which means the person who needs it cannot do it.
    """
    p = await pool()
    result = await p.execute("DELETE FROM runs WHERE id=$1", run_id)
    return result.split()[-1] != "0"


async def delete_failed_runs() -> int:
    """Delete runs that failed and produced nothing at all.

    Only the ones with no hook and no draft: a run that produced something is a
    result, however it ended, and is never swept up by a bulk clean-up.
    """
    p = await pool()
    result = await p.execute(
        "DELETE FROM runs WHERE status IN ('error','research_failed') "
        "AND chosen_hook IS NULL AND draft_body IS NULL")
    return int(result.split()[-1])


async def mark_sent(run_id: int, to: str, subject: str, message_id: str) -> dict | None:
    """Record that a message actually went out.

    Written immediately after the SMTP handoff succeeds and never rolled back:
    if the record fails to save the message is still delivered, and the worst
    outcome is a second send, so the write is kept as small and as soon as
    possible.
    """
    p = await pool()
    return _row(await p.fetchrow(
        "UPDATE runs SET sent_at=now(), sent_to=$2, sent_subject=$3, "
        "sent_message_id=$4, updated_at=now() WHERE id=$1 RETURNING *",
        run_id, to, subject, message_id))


async def set_email(run_id: int, email: str) -> dict | None:
    p = await pool()
    return _row(await p.fetchrow(
        "UPDATE runs SET email=$2, updated_at=now() WHERE id=$1 RETURNING *",
        run_id, email.strip()))


async def reset_run(run_id: int) -> None:
    """Clear everything a previous attempt produced, keeping the prospect.

    Re-running reuses the row so that one prospect stays one row — the batch
    the UI reloads has to match the batch on screen. Old stages and sources are
    deleted rather than appended to, because a pipeline view showing two runs'
    stages interleaved is worse than showing none.
    """
    p = await pool()
    async with p.acquire() as c:
        async with c.transaction():
            await c.execute("DELETE FROM run_stages WHERE run_id=$1", run_id)
            await c.execute("DELETE FROM run_sources WHERE run_id=$1", run_id)
            await c.execute(
                # Deliberately does NOT clear email/sent_at/sent_to: re-running
                # the research must never erase the record that a message was
                # already sent to this person.
                "UPDATE runs SET status='queued', identity_confidence=NULL, "
                "fact_overrides='{}'::jsonb, "
                "seniority=NULL, function=NULL, icp_score=NULL, hook_level=NULL, "
                "chosen_hook=NULL, hook_category=NULL, hook_date=NULL, "
                "hook_source=NULL, draft_subject=NULL, draft_body=NULL, "
                # The author goes with the draft it wrote.
                "persona_id=NULL, drafted_by='', job_change=NULL, "
                "failure_reason=NULL, elapsed_ms=NULL, updated_at=now() "
                "WHERE id=$1", run_id)


def _add_fact_ids(stage: dict) -> None:
    """Ensure every judged fact carries its id, computing it if absent.

    The id is a hash of the fact's text, so it can always be derived rather than
    stored. Deriving it on read means runs recorded before the field existed are
    still selectable in the UI — otherwise the include/exclude controls would be
    silently dead on exactly the runs a user already has.
    """
    if stage.get("stage") != "judge":
        return
    # Late import: the pipeline imports db, so importing it at module level here
    # would close the loop.
    from .pipeline.judge import fact_id_for_text

    for v in stage.get("payload", {}).get("verdicts") or []:
        if not v.get("fact_id"):
            v["fact_id"] = fact_id_for_text((v.get("fact") or {}).get("text", ""))


async def get_run(run_id: int) -> dict | None:
    p = await pool()
    async with p.acquire() as c:
        run = _row(await c.fetchrow("SELECT * FROM runs WHERE id=$1", run_id))
        if not run:
            return None
        run["stages"] = [_row(r) for r in await c.fetch(
            "SELECT * FROM run_stages WHERE run_id=$1 ORDER BY id", run_id)]
        for s in run["stages"]:
            s["payload"] = _json(s["payload"])
            _add_fact_ids(s)
        run["sources"] = [_row(r) for r in await c.fetch(
            "SELECT * FROM run_sources WHERE run_id=$1 ORDER BY id", run_id)]
        return run


async def list_runs(limit: int = 200, batch_id: str = "") -> list[dict]:
    p = await pool()
    if batch_id:
        return [_row(r) for r in await p.fetch(
            "SELECT * FROM runs WHERE batch_id=$1 ORDER BY id DESC LIMIT $2",
            batch_id, limit)]
    return [_row(r) for r in await p.fetch(
        "SELECT * FROM runs ORDER BY id DESC LIMIT $1", limit)]


# ------------------------------------------------------------- personas ---
async def list_personas() -> list[dict]:
    p = await pool()
    return [_row(r) for r in await p.fetch(
        "SELECT * FROM personas ORDER BY is_selected DESC, id")]


async def get_selected_persona() -> dict | None:
    """The active persona, with everything it has learned attached.

    The memories ride along rather than being fetched separately, because every
    caller that drafts needs them and one that forgets would quietly write with
    an out-of-date voice.
    """
    p = await pool()
    row = _row(await p.fetchrow("SELECT * FROM personas WHERE is_selected LIMIT 1"))
    if row:
        row["memories"] = await active_memories(row["id"])
    return row


async def create_persona(name: str, character: str, instructions: str, emoji: str,
                         **extra) -> dict:
    """Create a persona. `extra` carries the GTM brief fields."""
    cols = ["name", "character", "instructions", "emoji"]
    vals = [name, character, instructions, emoji]
    allowed = {"seniority", "intent", "product", "problem", "proof", "looking_for"}
    for k, v in extra.items():
        if k in allowed and v:
            cols.append(k)
            vals.append(v)
    placeholders = ",".join(f"${i}" for i in range(1, len(vals) + 1))
    p = await pool()
    return _row(await p.fetchrow(
        f"INSERT INTO personas ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
        *vals))


async def update_persona(persona_id: int, **fields) -> dict | None:
    if not fields:
        return await get_persona(persona_id)
    sets = ", ".join(f"{k}=${i}" for i, k in enumerate(fields, start=2))
    p = await pool()
    return _row(await p.fetchrow(
        f"UPDATE personas SET {sets}, updated_at=now() WHERE id=$1 RETURNING *",
        persona_id, *fields.values()))


async def get_persona(persona_id: int) -> dict | None:
    p = await pool()
    return _row(await p.fetchrow("SELECT * FROM personas WHERE id=$1", persona_id))


async def select_persona(persona_id: int) -> dict | None:
    """Make one persona the active voice, in a single transaction.

    Clearing first and setting second would briefly leave none selected, and a
    request landing in that window would draft with no persona at all.
    """
    p = await pool()
    async with p.acquire() as c:
        async with c.transaction():
            await c.execute("UPDATE personas SET is_selected=FALSE WHERE is_selected")
            row = await c.fetchrow(
                "UPDATE personas SET is_selected=TRUE, updated_at=now() "
                "WHERE id=$1 RETURNING *", persona_id)
    return _row(row)


async def record_lesson(persona_id: int, run_id: int | None, before: str,
                        after: str, instruction: str = "") -> None:
    """Remember one accepted edit. Silently ignores no-ops."""
    if not after.strip() or before.strip() == after.strip():
        return
    p = await pool()
    await p.execute(
        "INSERT INTO persona_lessons (persona_id, run_id, before, after, instruction) "
        "VALUES ($1,$2,$3,$4,$5)", persona_id, run_id, before, after, instruction)


async def pending_lessons(persona_id: int, limit: int = 12) -> list[dict]:
    p = await pool()
    return [_row(r) for r in await p.fetch(
        "SELECT * FROM persona_lessons WHERE persona_id=$1 AND NOT applied "
        "ORDER BY id LIMIT $2", persona_id, limit)]


async def mark_lessons_applied(ids: list[int]) -> None:
    if not ids:
        return
    p = await pool()
    await p.execute("UPDATE persona_lessons SET applied=TRUE WHERE id = ANY($1::bigint[])", ids)


async def add_memory(persona_id: int, rule: str, learned_from: int,
                     supersedes: int | None = None) -> dict:
    """Append one learned rule, retiring the rule it replaces.

    Both happen together: a memory that supersedes another while the other stays
    active would leave the persona holding two contradictory rules.
    """
    p = await pool()
    async with p.acquire() as c:
        async with c.transaction():
            if supersedes:
                await c.execute(
                    "UPDATE persona_memories SET active=FALSE "
                    "WHERE id=$1 AND persona_id=$2", supersedes, persona_id)
            row = await c.fetchrow(
                "INSERT INTO persona_memories (persona_id, rule, learned_from, supersedes) "
                "VALUES ($1,$2,$3,$4) RETURNING *",
                persona_id, rule, learned_from, supersedes)
    return _row(row)


async def active_memories(persona_id: int) -> list[dict]:
    p = await pool()
    return [_row(r) for r in await p.fetch(
        "SELECT * FROM persona_memories WHERE persona_id=$1 AND active ORDER BY id",
        persona_id)]


async def all_memories(persona_id: int, limit: int = 50) -> list[dict]:
    p = await pool()
    return [_row(r) for r in await p.fetch(
        "SELECT * FROM persona_memories WHERE persona_id=$1 ORDER BY id DESC LIMIT $2",
        persona_id, limit)]


async def forget_memory(memory_id: int) -> bool:
    """Retire one learned rule. The row stays; only the persona stops using it."""
    p = await pool()
    r = await p.execute("UPDATE persona_memories SET active=FALSE WHERE id=$1", memory_id)
    return r.split()[-1] != "0"


async def add_persona_revision(persona_id: int, instructions: str, summary: str,
                               learned_from: int) -> dict:
    """Save a new version of the instructions and make it the live one."""
    p = await pool()
    async with p.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "INSERT INTO persona_revisions (persona_id, instructions, summary, learned_from) "
                "VALUES ($1,$2,$3,$4) RETURNING *",
                persona_id, instructions, summary, learned_from)
            await c.execute(
                "UPDATE personas SET instructions=$2, updated_at=now() WHERE id=$1",
                persona_id, instructions)
    return _row(row)


async def persona_revisions(persona_id: int, limit: int = 20) -> list[dict]:
    p = await pool()
    return [_row(r) for r in await p.fetch(
        "SELECT * FROM persona_revisions WHERE persona_id=$1 ORDER BY id DESC LIMIT $2",
        persona_id, limit)]


async def lesson_counts(persona_id: int) -> dict:
    p = await pool()
    r = await p.fetchrow(
        "SELECT count(*) AS total, count(*) FILTER (WHERE NOT applied) AS pending "
        "FROM persona_lessons WHERE persona_id=$1", persona_id)
    return {"total": int(r["total"]), "pending": int(r["pending"])}


async def delete_persona(persona_id: int) -> bool:
    """Remove a persona, promoting another if this one was the active voice.

    Deleting the selected persona would otherwise leave the app with no voice at
    all: drafts would fall back to generic prose with no indication anything was
    wrong. Whatever remains is a better default than nothing.
    """
    p = await pool()
    async with p.acquire() as c:
        async with c.transaction():
            was_selected = await c.fetchval(
                "DELETE FROM personas WHERE id=$1 RETURNING is_selected", persona_id)
            if was_selected is None:
                return False
            if was_selected:
                await c.execute(
                    "UPDATE personas SET is_selected=TRUE WHERE id = "
                    "(SELECT id FROM personas ORDER BY id LIMIT 1)")
    return True


# --------------------------------------------------- provider cache/usage ---
async def get_person_profile(cache_key: str, max_age_days: int) -> dict | None:
    """A cached profile, if one was fetched recently enough."""
    p = await pool()
    row = await p.fetchrow(
        "SELECT * FROM person_profiles WHERE cache_key=$1 "
        f"  AND fetched_at > now() - interval '{int(max_age_days)} days'", cache_key)
    return _row(row)


async def put_person_profile(cache_key: str, profile_id: str, text: str, note: str) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO person_profiles (cache_key, profile_id, text, note) "
        "VALUES ($1,$2,$3,$4) ON CONFLICT (cache_key) DO UPDATE SET "
        "profile_id=excluded.profile_id, text=excluded.text, note=excluded.note, "
        "fetched_at=now()",
        cache_key, profile_id, text, note)


async def record_provider_call(provider: str, endpoint: str,
                               cache_key: str = "", ok: bool = True) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO provider_calls (provider, endpoint, cache_key, ok) VALUES ($1,$2,$3,$4)",
        provider, endpoint, cache_key, ok)


async def provider_usage(provider: str) -> dict:
    """How many paid calls have been made, so the allowance can be seen."""
    p = await pool()
    r = await p.fetchrow(
        "SELECT count(*) AS total, "
        "       count(*) FILTER (WHERE created_at > now() - interval '1 day') AS today, "
        "       max(created_at) AS last_call "
        "FROM provider_calls WHERE provider=$1", provider)
    return {"total": int(r["total"]), "today": int(r["today"]),
            "last_call": r["last_call"].isoformat(timespec="seconds") if r["last_call"] else None}


async def provider_month(provider: str) -> dict:
    """Calls made in the current calendar month, and today.

    Tavily and Hunter both meter monthly and neither exposes a cheap "how much
    is left" for search, so the app counts its own calls. Counting is only ever
    an under-estimate — a call made outside this app is invisible — which is the
    right direction to be wrong in for a budget.
    """
    p = await pool()
    r = await p.fetchrow(
        "SELECT count(*) FILTER (WHERE created_at >= date_trunc('month', now())) AS month, "
        "       count(*) FILTER (WHERE created_at >= date_trunc('day', now()))   AS today "
        "FROM provider_calls WHERE provider=$1 AND ok", provider)
    return {"month": int(r["month"]), "today": int(r["today"])}


async def hook_outcomes() -> dict[str, dict]:
    """What the user has actually done with each kind of hook.

    Only acts that cost the user something count as evidence. Sending is the
    strongest — a real message to a real person. Hand-picking is next: they
    overruled the ranking on purpose. A hook the judge chose and the user simply
    left alone says nothing, because not acting is not a preference.
    """
    p = await pool()
    rows = await p.fetch("""
        SELECT hook_category AS category,
               count(*)                                        AS drafted,
               count(*) FILTER (WHERE sent_at IS NOT NULL)      AS sent,
               count(*) FILTER (WHERE coalesce(fact_overrides->>'chosen','') <> '')
                                                                AS hand_picked
        FROM runs
        WHERE chosen_hook IS NOT NULL AND coalesce(hook_category,'') <> ''
        GROUP BY hook_category""")
    return {r["category"]: {"drafted": int(r["drafted"]), "sent": int(r["sent"]),
                            "hand_picked": int(r["hand_picked"])} for r in rows}


async def latest_leads(limit: int = 200) -> list[dict]:
    """One row per lead — the run worth showing — across every batch.

    A "batch" is just which upload a prospect arrived in. Nobody thinks in
    batches; they think in leads. Scoping the screen to the newest batch meant
    re-adding one prospect hid every other lead, and a re-run that failed for
    infrastructure reasons hid the good result from the attempt before it.

    So: group by person, and prefer the most recent run that actually produced
    something over a later one that produced nothing. A failed attempt is still
    visible on the lead itself; it just does not erase work already done.
    """
    p = await pool()
    rows = await p.fetch(
        """
        SELECT DISTINCT ON (lower(name), lower(coalesce(company, '')))
               *
        FROM runs
        ORDER BY lower(name), lower(coalesce(company, '')),
                 (chosen_hook IS NOT NULL OR draft_body IS NOT NULL) DESC,
                 id DESC
        """)
    ordered = sorted((_row(r) for r in rows), key=lambda r: r["id"], reverse=True)
    return ordered[:limit]


async def hydrate_runs(runs: list[dict]) -> list[dict]:
    """Attach stages and sources to many runs in a fixed number of queries.

    Calling get_run() per row is three round trips each, and the transaction
    pooler forbids prepared statements so every one of those costs about two
    network hops. Against a database in another region that turned a page load
    into seconds of waiting that grew with the number of leads. This is three
    queries total, no matter how many runs.
    """
    if not runs:
        return []
    ids = [r["id"] for r in runs]
    p = await pool()
    async with p.acquire() as c:
        stage_rows = await c.fetch(
            "SELECT * FROM run_stages WHERE run_id = ANY($1::bigint[]) ORDER BY id", ids)
        source_rows = await c.fetch(
            "SELECT * FROM run_sources WHERE run_id = ANY($1::bigint[]) ORDER BY id", ids)

    stages: dict[int, list[dict]] = {i: [] for i in ids}
    for row in stage_rows:
        st = _row(row)
        st["payload"] = _json(st["payload"])
        _add_fact_ids(st)
        stages[st["run_id"]].append(st)

    sources: dict[int, list[dict]] = {i: [] for i in ids}
    for row in source_rows:
        src = _row(row)
        sources[src["run_id"]].append(src)

    for r in runs:
        r["stages"] = stages.get(r["id"], [])
        r["sources"] = sources.get(r["id"], [])
    return runs


async def latest_batch_id() -> str:
    """The batch of the most recently created run — what the UI reopens into."""
    p = await pool()
    return await p.fetchval(
        "SELECT batch_id FROM runs WHERE batch_id <> '' ORDER BY id DESC LIMIT 1") or ""


async def stats() -> dict:
    # One scan, one round trip. Six separate COUNT queries against the same
    # table is six times the load for an answer a single FILTER pass gives.
    p = await pool()
    r = await p.fetchrow("""
        SELECT
          count(*) FILTER (WHERE status IN
            ('completed','no_signal_found','research_failed','error'))        AS finished,
          count(*) FILTER (WHERE status = 'completed')                        AS completed,
          count(*) FILTER (WHERE status = 'no_signal_found')                  AS no_signal,
          count(*) FILTER (WHERE status = 'research_failed')                  AS research_failed,
          count(*) FILTER (WHERE status = 'needs_disambiguation')             AS needs_disambiguation,
          avg(elapsed_ms) FILTER (WHERE elapsed_ms > 0 AND status IN
            ('completed','no_signal_found','research_failed','error'))        AS avg_ms
        FROM runs
    """)
    finished, completed = int(r["finished"]), int(r["completed"])
    return {
        "total_runs": finished,
        "completed": completed,
        "no_signal": int(r["no_signal"]),
        "research_failed": int(r["research_failed"]),
        "needs_disambiguation": int(r["needs_disambiguation"]),
        "hook_rate": round(100 * completed / finished) if finished else 0,
        "avg_ms": round(r["avg_ms"]) if r["avg_ms"] else 0,
    }


# --------------------------------------------------------------------- config
async def set_config(key: str, value: dict) -> None:
    p = await pool()
    await p.execute(
        "INSERT INTO app_config (key, value) VALUES ($1,$2) "
        "ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=now()",
        key, value,
    )


async def get_config(key: str) -> dict:
    p = await pool()
    v = await p.fetchval("SELECT value FROM app_config WHERE key=$1", key)
    return _json(v) if v is not None else {}


# -------------------------------------------------------------- style learning
async def add_style_example(run_id: int | None, prospect: str,
                            original: str, edited: str) -> None:
    """Record a draft the user rewrote.

    Rep edit distance is the highest-signal free quality metric available, and
    these same records are what teach the system the sender's voice.
    """
    if not edited.strip() or original.strip() == edited.strip():
        return
    p = await pool()
    await p.execute(
        "INSERT INTO style_examples (run_id, prospect, original, edited) "
        "VALUES ($1,$2,$3,$4)",
        run_id, prospect, original, edited,
    )


async def recent_style_examples(limit: int = 3) -> list[dict]:
    p = await pool()
    return [_row(r) for r in await p.fetch(
        "SELECT * FROM style_examples ORDER BY id DESC LIMIT $1", limit)]


async def style_stats() -> dict:
    p = await pool()
    return {"examples": int(await p.fetchval("SELECT count(*) FROM style_examples"))}
