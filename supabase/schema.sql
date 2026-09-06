-- Dossier schema. Run ONCE in the Supabase SQL editor
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
    -- Which persona this lead BELONGS to, as distinct from persona_id, which
    -- names whoever wrote the draft that is on it now.
    --
    -- The same person is worth writing to for different reasons by different
    -- voices: a founder opening a conversation and a recruiter approaching the
    -- same engineer are two pieces of work, and merging them into one lead
    -- means one of them overwrites the other's message. So the rail is a
    -- filter as well as a selector.
    owner_persona_id BIGINT,
    persona_id    BIGINT,
    drafted_by    TEXT NOT NULL DEFAULT '',
    -- Which version of that persona's brief wrote this draft, so a message
    -- written before the brief changed can say so rather than looking current.
    persona_version TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS run_chat (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    you        TEXT NOT NULL,
    reply      TEXT NOT NULL DEFAULT '',
    actions    JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_chat_run_id_idx ON run_chat (run_id, id);

CREATE TABLE IF NOT EXISTS fact_feedback (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    fact_id    TEXT NOT NULL,
    -- Which voice was writing when this judgement was made. What counts as a
    -- good reason to write differs by who is asking: a recruiter and an
    -- investor reject opposite things.
    persona_id BIGINT,
    action     TEXT NOT NULL,          -- chose | excluded | included
    category   TEXT NOT NULL DEFAULT '',
    level      TEXT NOT NULL DEFAULT '',
    fact_text  TEXT NOT NULL DEFAULT '',
    via        TEXT NOT NULL DEFAULT '',   -- findings | assistant
    -- Why the user did it, in their words, when they said. The act tells you
    -- what happened; only the reason tells you whether it generalises. "Awards
    -- say nothing about need" is a rule about awards. "That one is four years
    -- old" is a rule about age, and treating the second as the first teaches
    -- the wrong thing.
    reason     TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fact_feedback_cat_idx ON fact_feedback (category, action);

CREATE TABLE IF NOT EXISTS draft_revisions (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    before_body TEXT NOT NULL DEFAULT '',
    after_body  TEXT NOT NULL DEFAULT '',
    subject     TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',   -- edit | assistant | rewrite | campaign
    instruction TEXT NOT NULL DEFAULT '',
    hook        TEXT NOT NULL DEFAULT '',
    persona_id  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS draft_revisions_run_idx ON draft_revisions (run_id, id);

CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Edits are evidence about ONE voice, not about writing in general.
--
-- These were global, so a formal CRO persona learned from edits made to a
-- blunt founder one and the two slowly converged on something neither person
-- would send. Scoped to the persona that wrote the draft being edited.
CREATE TABLE IF NOT EXISTS style_examples (
    id         BIGSERIAL PRIMARY KEY,
    persona_id BIGINT,
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
    -- Which voice this campaign belongs to. A campaign is a piece of one
    -- persona's work in exactly the way a lead is: the leads it creates are
    -- owned by that persona, its messages are written in that voice, and what
    -- it teaches belongs to it. Listing every persona's campaigns together
    -- would make the rail a selector everywhere except the one screen that
    -- generates the most work.
    persona_id     BIGINT,
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

    -- The whole brief, written by hand, used verbatim.
    --
    -- The fields above are a good scaffold and a ceiling: someone who knows
    -- exactly what they want their agent to do should be able to say it in
    -- their own words and have those words be the instruction, not an input to
    -- one this app assembles. When this is set it REPLACES the assembled brief
    -- rather than sitting alongside it, because two briefs in one prompt is
    -- two sets of orders.
    brief        TEXT NOT NULL DEFAULT '',
    -- A message they wrote themselves, as the anchor for voice. Worth more
    -- than any description of a voice, because it IS the voice.
    sample       TEXT NOT NULL DEFAULT '',

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
    -- Whether this rule has been written into the persona's own instructions.
    -- Once it has, the prompt must stop appending it separately or the model
    -- is told the same thing twice, in two voices, one of them claiming to
    -- override the other.
    folded        BOOLEAN NOT NULL DEFAULT FALSE,
    -- When it stopped being used. `active` alone says a rule was retired but
    -- not when, and a timeline cannot show an event with no time.
    retired_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS persona_memories_active_idx
    ON persona_memories (persona_id, id) WHERE active;
