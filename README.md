# Dossier

Takes a list of sales prospects, researches each one against live public sources, decides whether there's a genuine reason to reach out, and drafts a grounded outreach email — showing its reasoning at every step.

Built for the Zamp AI Solutions Associate case study (PS-3).

---

## Setup (about 3 minutes)

```bash
pip install -r requirements.txt
cp .env.example .env          # paste your keys and DATABASE_URL in
cd ui && npm install && npm run build && cd ..   # builds the React frontend
python verify.py              # preflight: validates keys + one real end-to-end run
uvicorn backend.main:app --reload --port 8000
```

The database is Supabase Postgres. Apply `supabase/schema.sql` once from the
Supabase SQL editor, and use the **transaction pooler** connection string
(port 6543) as `DATABASE_URL` — not the direct connection on 5432. If your
password contains `@`, `/` or `:`, percent-encode it, or the URL will not parse.

Frontend is Vite + React + Tailwind with shadcn/ui components and lucide icons.
The build outputs to `frontend/`, which FastAPI serves — so the whole app runs
from one command. For UI development with hot reload, run `npm run dev` in `ui/`
alongside the backend; it proxies `/api` to port 8000.

Open <http://localhost:8000>.

### Before it goes anywhere public

Set `APP_ACCESS_KEY`. Every endpoint either spends Tavily and Gemini credits or
returns prospect data and drafts, so an open URL means someone else's quota and
your prospect list. A correct key is exchanged for an HttpOnly session cookie;
the key is never held in the browser. Leave it unset locally — startup logs a
warning whenever it is, so an accidentally public deployment says so on every
boot. Set `SESSION_SECRET` too, or sessions reset on every restart.

**Keys needed** (both free):
| Key | Where | Cost |
|---|---|---|
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com) | Free tier, 1,000 credits/month ≈ 160 runs |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Free tier, no card |
| `SUPERCARL_API_KEY` | [supercarl.ai](https://supercarl.ai) | **Optional** — adds the prospect's own profile and posts |

**Run `python verify.py` before any demo.** It checks both APIs and does one complete real run, so setup problems surface then rather than live.

---

## How it works

Upload a sheet → each prospect runs through seven stages → you get a draft with its reasoning attached.

```
  Targeting → Identity → Research → Extract → Ground → Judge → Draft
```

| Stage | What it does | Code |
|---|---|---|
| **Targeting** | Classifies seniority and function from the title, scores ICP fit, and checks relationship state. An existing customer or competitor is skipped *before* any research spend. | `pipeline/profile.py` |
| **Identity** | Works out *who this actually is*. Scores candidates on company/role/location/URL evidence. Two plausible people → it stops and asks rather than guessing. | `pipeline/identity.py` |
| **Research** | Generates queries from the prospect (not hardcoded) across two tiers — **person-level** (interviews, podcasts, talks, bylines, quotes, their own role change) and **company-level** (funding, hiring, IPO, expansion) — and fires them concurrently. Then walks outward from the confirmed person through their real employment history. | `pipeline/research.py`, `graph.py` |
| **Extract** | Pulls concrete facts from source text, each carrying verbatim key entities. Collapses syndicated duplicates; discards facts about other companies. | `pipeline/extract.py` |
| **Ground** | **Verifies every fact against the source text.** Anything unverifiable is dropped before it can be drafted. | `pipeline/grounding.py` |
| **Judge** | Applies four eligibility gates, then scores survivors on outreach value × buying intent. | `pipeline/judge.py` |
| **Draft** | Writes the email, then checks it actually used the hook's specific detail and avoided generic openers. Two failures → falls back honestly. | `pipeline/draft.py` |

Each stage writes a row to `run_stages` as it finishes, which is both the audit
trail and the progress bar. The section below is the same seven stages seen from
underneath: what is wired to what, what each one prevents, and how information
changes shape as it moves.

---

## System design

### The shape of it

One process. FastAPI serves both the JSON API and the built React app, so there
is no separate frontend server, no CORS, and one thing to deploy. All state
lives in Postgres — the process itself holds nothing that matters, which is what
lets it be restarted or replaced mid-flight without losing a run.

```
   browser (React SPA)
        │  fetch /api/…            ← same origin, served by the same process
        ▼
   ┌──────────────────────────────────────────────┐
   │  FastAPI  (backend/main.py)                  │
   │    security.gate  → session cookie on /api/* │
   │    routes         → thin; no logic lives here│
   └───────────┬──────────────────────────────────┘
               │ calls
               ▼
   ┌──────────────────────────────────────────────┐
   │  pipeline/   the actual work, stage by stage │
   │  runner.py orchestrates; each stage is a file│
   └───┬──────────────────────┬───────────────────┘
       │ reads/writes         │ calls out
       ▼                      ▼
   Postgres (Supabase)   Tavily   Gemini   Super Carl
   runs, stages,         search    LLM     profile+posts
   sources, drafts,                        (optional)
   personas, chat
```

Three outside services, and each is failed-over differently on purpose. Tavily
going down is fatal to a run (`research_failed`). Gemini going down is fatal to
a run. Super Carl going down is **not** — it degrades to web search alone,
because it is an enhancement and an enhancement must never be able to fail the
thing it enhances.

### How one lead flows through

The unit of work is a **run**: one row in `runs`, one prospect. Two HTTP calls
create and execute it, and the split matters — see "why polling" below.

```
POST /api/runs          → row created, status=queued, returns {run_id}
POST /api/runs/{id}/execute
                        → runner.run() executes all seven stages inline
                        → each stage appends a row to run_stages as it finishes
GET  /api/runs/{id}     → browser polls this; reads those stage rows back
```

Inside `execute`, information changes form five times. That is the whole system:

```
ProspectInput        name, company, role, url         ← what you uploaded
   │  research.py
   ▼
SearchHit[]          url, title, page text            ← what the web said
   │  extract.py
   ▼
ExtractedFact[]      claim + date + verbatim entities ← what it means
   │  grounding.py
   ▼
ExtractedFact[]      same, minus anything unverifiable← what is actually true
   │  judge.py
   ▼
one hook             the single fact worth opening on ← what is worth saying
   │  draft.py
   ▼
subject + body                                        ← what gets sent
```

Each arrow narrows. Nothing is ever added back in later — a fact that fails
grounding cannot reappear at draft time, because draft only ever sees what judge
handed it. That one-way narrowing is why an invented fact cannot reach an email.

### Why each step exists

A stage earns its place by preventing a specific, nameable failure. If you
delete the stage, you get the failure in the right-hand column.

| Stage | The question it answers | Delete it and you get |
|---|---|---|
| **Targeting** `profile.py` | Is this person worth spending money on at all? | Research credits burned on existing customers and competitors — and eventually a cold pitch sent *to a customer*, which is the worst output this system can produce |
| **Identity** `identity.py` | Which human is this, exactly? | Research about a stranger who shares the name. Everything downstream is then perfectly executed and entirely wrong |
| **Research** `research.py` | What does the public record say? | Nothing to write from. Runs in two separate tiers, person and company, because one query pool returns company news and attributes it to the individual |
| **Extract** `extract.py` | What are the concrete claims? | Raw page text at the drafting step, which the model happily paraphrases into things the page never said |
| **Ground** `grounding.py` | Is each claim actually in the source? | Invented facts in real emails. This is the load-bearing one |
| **Judge** `judge.py` | Which single fact is worth opening on? | An email built on an award from 2019, or on a layoff |
| **Draft** `draft.py` | Does the message use that fact specifically? | "I was impressed by your work at ⟨company⟩" — personalisation theatre |

**Why search is a step at all, rather than a prompt.** A model asked "what is
new with this person" answers from training data: confident, undated, and
unfalsifiable. Search makes the claim *checkable* — every fact arrives attached
to a URL whose text can be string-matched. The search step is not there to
inform the model, it is there to give grounding something to verify against.
Without retrieval there is nothing to ground, and without grounding there is no
way to tell a real fact from a fluent one.

**Why grounding is dumb on purpose.** Each extracted fact must carry verbatim
entities lifted from the source. Grounding string-matches them back against the
retrieved text. No model is asked "is this true?" — a model that invented the
fact will happily confirm it. A string comparison cannot be persuaded.

### Where state lives

| Table | Holds | Why it exists separately |
|---|---|---|
| `runs` | one prospect, its status, chosen hook, draft | the unit of work |
| `run_stages` | one row per stage, with its payload and timing | the audit trail, and the progress feed — same rows, two jobs |
| `run_sources` | every URL read | so "where did that come from?" is answerable without re-running |
| `run_chat` | the assistant thread | survives refresh, tab change, machine |
| `draft_revisions` | every version of every draft | what changed, who changed it, and why |
| `style_examples` | (original, edited) pairs | the voice-learning input |
| `fact_feedback` | facts included/excluded by hand, with reasons | the ranking-learning input |
| `personas`, `persona_memories` | who is writing, and what they have learned | learning belongs to a voice, not to the app |
| `person_profiles` | cached provider lookups | the provider allowance is small and re-runs are common |
| `outbound_*` | the campaign pipeline's own runs, contacts, stages | a different unit of work — see below |

### How the browser knows what is happening

There is no websocket and no SSE. The pipeline writes a `run_stages` row the
moment each stage finishes; the browser polls `GET /api/runs/{id}` and renders
those rows. Progress is therefore *real* — it is the pipeline's own record,
not an animation on a timer, and it cannot drift out of step with the work
because it **is** the work's output.

This started as a serverless constraint (a function cannot hold a queue in
memory between invocations) and stayed after the move to a long-running host,
because the stage rows had to be written for durability anyway. One mechanism,
two jobs, and a refresh mid-run loses nothing.

The same reasoning explains why disambiguation does not block. When identity is
ambiguous the run parks in `needs_disambiguation` and returns; the human's
answer arrives later as `POST /api/runs/{id}/resolve`, which re-enters the
pipeline with the company filled in. No resume state machine, no in-memory
waiting, nothing to lose on restart.

### The second pipeline: outbound campaigns

Lead research answers "what do I say to this person?". The campaign pipeline
answers "who should I be talking to at all?" — it starts from one company and
works outward. Its own runs, its own stages, its own tables.

```
target company
   │  competitors.py   who else plays here
   ▼
competitors  →  contacts.py    who works there, from public pages
   │
   ▼
contacts     →  waterfall.py   find and verify an address, cheapest source first
   │
   ▼
verified     →  runner.py      each contact gets a FULL lead research run
addresses         (the pipeline above, reused wholesale)
   │
   ▼
campaign     →  drafts per contact, grouped for review and sending
```

The reuse is the point: a campaign contact is researched by exactly the same
seven stages, with the same grounding, as one you typed in by hand. There is no
second, weaker path — which is why a campaign draft can be trusted as much as a
single one.

### The assistant, and what it can reach

The chat panel is not a text box that rewrites prose. It is given tools, so the
only way it can claim to have done something is to have actually done it.

```
you type  →  agent.py  →  api_catalog()   what can this app do?     (reads app.openapi())
                       →  api_call(…)     do one of those things    (in-process, over ASGI)
                                             │
                                             ▼
                                          the same FastAPI routes a click uses,
                                          through the same security gate,
                                          the same validation, the same pool
```

Because the catalogue is read from FastAPI's own spec, it cannot fall out of
date when a route is added or renamed. And because calls re-enter through the
HTTP layer rather than reaching into the database, the assistant has exactly the
authority a signed-in user has, and no path of its own.

Two things it cannot do. It cannot **assert a fact** — new information enters
only through an endpoint that grounds it against a source, so "add that he moved
to Stripe" is refused rather than written. And it cannot **send or delete**
without explicit confirmation in the conversation, enforced in `api_call`, not
merely requested in the prompt.

### The loop that makes it better

Three signals feed back, and each lands somewhere different:

```
you edit a draft        → draft_revisions + style_examples → next drafts few-shot your last 3 edits
you drop/keep a fact    → fact_feedback                    → judge.learned_weights shifts that category
you correct the writing → persona_memories                 → the persona rewrites its own instructions
```

No training and no fine-tuning. Each is just showing the next call what you
actually did last time.

### Where the money goes

Worth understanding before changing any knob, because two of the three services
are metered and one run touches all three.

| Spend | When | Bounded by |
|---|---|---|
| Tavily search | every query in research + traversal | `GRAPH_QUERY_BUDGET`, `MAX_RESULTS_PER_QUERY` |
| Gemini calls | query generation, extract, judge, draft | `EXTRACT_MAX_SOURCES` (extraction is the long pole) |
| Super Carl | one resolve + one profile fetch per person | cached `SUPERCARL_CACHE_DAYS`; skipped entirely without a key |

Two rules the code holds to: **a badge never costs money** (`/api/health`
answers from config alone unless you explicitly press the button), and **ten
contacts at one company cost one company research pass, not ten**.

---

## The decisions worth knowing about

**Person-level signal is scored separately from company-level signal, and preferred.**
The first version generated four company queries against one person query, so it
returned company news and attributed it to the individual — "congrats on your
funding round" is about their employer, and every other rep in that inbox is
sending the same line. Facts are now tagged `person` or `company`, retrieved
through separate query tiers, and scored independently with a preference for the
person tier. A hook about something *they* said or did is what makes the message
theirs.

**The system knows what you sell.** Writer persona (who is sending, what you
sell, the problem it removes) and per-intent weights feed hook selection, so an
AP-automation vendor ranks "hiring five AP clerks" above a Series A while an
investor ranks the reverse — same engine, config change, no code.

**It learns your voice from edits.** Rewrite a draft and the (original, edited)
pair is stored; the next drafts few-shot from your last three edits. No training,
no fine-tuning — just showing the model what you actually changed.

**Grounding verification is the most important safeguard here.** A model can produce a clean, confident, entirely invented fact — "raised $18M led by Accel" — that passes every other check (recent, safe, specific) and lands in a founder's inbox under a rep's name. So every fact must declare verbatim entities from the source, and those are string-checked against the retrieved text. It's deliberately dumb and deterministic: the model can't talk its way past it.

**`no_signal_found` and `research_failed` are different statuses.** If the search quota dies at row 40, those rows must not report "this prospect has no public signal." One is a finding about the prospect; the other is an infrastructure failure. Conflating them makes every metric downstream a lie.

**Outreach value and factual recency are different clocks.** A five-month-old funding round is still perfectly true and already useless — everyone congratulated them months ago. Scoring peaks inside ~21 days and decays after, layered on top of the hard 6-month factual gate.

**Hooks are scored by buying intent, not prominence.** A hiring signal ("five AP roles open") outranks an award, because one is evidence of a problem and the other is noise. Weights live in `config.INTENT_WEIGHTS`.

**Reputationally sensitive categories can never become a hook.** Layoffs, lawsuits, controversy, personal life — hard-excluded regardless of recency, and visibly logged as "found, excluded" rather than silently dropped.

**The judgment is rules, not vibes.** The model reads; `pipeline/judge.py` decides. That's what makes every outcome inspectable — "the model felt it was best" isn't an answer you can give a customer.

**No mock data anywhere.** Every number and quote in the UI came from a live API call. There are no fixtures, no seeded rows and no demo mode — the screenshots are the app.

---

## Edge cases it handles deliberately

| Scenario | Behaviour |
|---|---|
| Two plausible people with the same name | Stops, shows candidates, waits for confirmation |
| Only negative news available (layoffs, lawsuit) | Excluded as a hook, logged visibly, next-best used |
| Only stale signal available | Excluded on both factual and outreach-value grounds |
| Nothing usable found | Honest draft that doesn't fake personalisation |
| Model invents a fact | Grounding check drops it before drafting |
| Same press release across five sites | Collapsed to one fact — count isn't corroboration |
| Fact about a competitor in the same article | Discarded by subject-company check |
| Search quota exhausted | `research_failed`, never mislabelled as "no signal" |
| Messy pasted name (`Dr. X (she/her) \| Founder @ Y`) | Normalised before searching |
| Duplicate rows | Merged on normalised name + company |
| Ten contacts at one company | Researched once, cached |
| Prospect is an existing customer or competitor | Skipped before any research spend — nothing is broken when the system cold-pitches a customer, and it is still the worst thing it can do |
| Company milestone at a junior prospect | Flagged so it reads as context, not as their personal achievement |
| CXO with no single function | Not penalised on ICP fit — a CEO spans every function |

---

## Layout

Read it in the order information moves: `main.py` takes the request,
`runner.py` decides what happens, `pipeline/*` does it, `db.py` records it.

```
backend/
  config.py           tunables + hard-excluded categories
  taxonomy.py         ICP, seniority/function, person + company intent tiers
  models.py           schemas (also the structured-output contracts)
  db.py               Supabase Postgres: runs, stages, sources (asyncpg)
  outbound_db.py      the campaign pipeline's own tables
  main.py             routes, upload parsing        ← thin: no logic lives here
  security.py         access gate, session cookies, response headers
  integrations/
    search.py         Tavily — typed failures, bounded concurrency
    llm.py            Gemini — structured output, retry, timeouts
    personsignal.py   Super Carl — profile + posts, optional, never fatal
    mailer.py         SMTP send
    hunter.py         email discovery
    email_verify.py   address validation
    reachability.py   is this address worth sending to
  pipeline/
    runner.py         orchestration, stage events, company cache
    normalize.py      input hygiene                 ← incl. placeholder companies
    profile.py        seniority/function classification + ICP fit scoring
    identity.py       stage 0 — which human is this
    research.py       stages 1-2 + the traversal
    graph.py          research-as-graph: what to look at next, and the budget
    provenance.py     source ranking — LinkedIn > X > web, and corroboration
    extract.py        stage 3 + syndication collapsing + wrong-person gates
    grounding.py      verification                  ← the load-bearing one
    judge.py          eligibility gates + scoring   (pure logic)
    draft.py          stage 5 + post-checks
    agent.py          the assistant: api_catalog + api_call over the app's API
    persona.py        the writing voice, and what it has learned
    outbound.py       campaign orchestration        ← reuses runner.py wholesale
    competitors.py    who else plays in this market
    contacts.py       who works there, from public pages
    waterfall.py      find an address, cheapest source first
ui/                   Vite + React + Tailwind + shadcn/ui source
  ResearchGraph.tsx   live traversal drawing, arbitrary breadth and depth
frontend/             built assets — GITIGNORED, the Dockerfile builds it
Dockerfile            two stages: node builds the SPA, python runs the app
railway.json          selects the Dockerfile builder, healthcheck /api/ping
verify.py             preflight
discover_supercarl.py probe for a person-signal data provider
```

**Self-checks.** Non-trivial logic keeps a runnable check in the same file,
under `__main__` — no framework, no fixtures. Each one fails loudly if the
behaviour it names regresses:

```bash
python -m backend.security                    # access gate, cookie, HTTPS detection
python -m backend.pipeline.graph              # traversal budget and layout
python -m backend.pipeline.research           # query anchoring, URL pinning
python -m backend.pipeline.normalize          # placeholder companies
python -m backend.pipeline.agent              # what the assistant may reach
python -m backend.pipeline.contacts           # domain resolution
python -m backend.pipeline.provenance         # source ranking, corroboration
python -m backend.integrations.personsignal   # provider failure messages
python test_bugs.py                           # regression checks
```

`test_outbound.py` is an integration script, not a unit test: it drives a
running server over HTTP, so start the app before running it. Everything above
is offline and needs no keys and no database.


---

## Deploying to Vercel

> Railway is the currently deployed target — see the section below. This one is
> kept because the serverless constraints it describes are why several parts of
> the app are shaped the way they are, and those shapes did not change when the
> host did.

The app deploys as a single Vercel Function serving both the API and the built
frontend. `pyproject.toml` points Vercel at `backend.main:app`; `vercel.json`
sets the build command and the function's `maxDuration`.

1. Apply `supabase/schema.sql` once in the Supabase SQL editor.
2. Set the environment variables from `.env.example` in the Vercel project —
   `DATABASE_URL` (transaction pooler, port 6543), `TAVILY_API_KEY`,
   `GEMINI_API_KEY`, `APP_ACCESS_KEY`, `SESSION_SECRET`, and
   `SUPERCARL_API_KEY` if you have one.
3. Push, or `vercel deploy`.

**Three things about this app are shaped by serverless, and undoing any of them
breaks it:**

*Runs execute inside the request.* `POST /api/runs` creates the row and returns
an id immediately; `POST /api/runs/{id}/execute` does the work and returns when
it finishes. Nothing is detached into a background task, because a serverless
platform makes no promise that work outliving its response ever completes — a
fire-and-forget task is a run that silently disappears.

*Progress is polled, not streamed.* There is no SSE endpoint. A function cannot
hold a queue in memory between invocations, so the client polls
`GET /api/runs/{id}` and reads the stage rows the pipeline writes as it goes.
Those rows had to exist for durability anyway; this gives them a second job.

*Nothing in memory survives between requests.* Disambiguation does not block
waiting for an answer — the run parks in `needs_disambiguation` and `/resolve`
restarts the pipeline with the company filled in. Stale runs are retired by age
rather than swept at startup, because on serverless a cold start proves nothing
about what other instances are doing.

A full run takes 60-110 seconds against Vercel's 300s ceiling. That margin is
real but not generous: `LLM_TIMEOUT_S`, `EXTRACT_MAX_SOURCES` and
`GRAPH_QUERY_BUDGET` are the knobs that move it.

**Put the function in the database's region.** The transaction pooler forbids
prepared statements, so every query costs about two round trips, and a run
writes ~20 stage rows. Each 100ms of distance between app and database adds ~4
seconds to every run — measured from India against a Sydney project, that was
400ms RTT and ~16s per run spent purely waiting. `vercel.json` pins `bom1` to
match the database's `ap-south-1` (Mumbai). If you move the Supabase project, change
that line too; the default `iad1` would be just as far away as a laptop is.

## Deploying to Railway (the Dockerfile path)

Railway is a long-running host, which suits this app better than serverless
does: the process can hold a warm connection, and a run is never racing a
function ceiling. Deployment is the `Dockerfile`, and `railway.json` selects it.

**The build has to compile the frontend, and that is the whole reason a
Dockerfile exists here.** `frontend/` is gitignored — it is build output, and
committing it means the repo carries a stale copy. Vercel got away with this
because `vercel.json` runs the npm build itself; Railway's Python builder has no
equivalent hook, so it would install Python, find no `frontend/`, and serve
"Frontend is not built" on every page load. The image therefore builds it:

```
stage 1  node:22-slim    npm ci && npm run build     → /app/frontend
stage 2  python:3.12     pip install -r requirements.txt
                         COPY --from=1 /app/frontend  ← only the output crosses
```

Node never reaches the runtime image, and `.dockerignore` keeps `.env` out of
it — which matters for more than secret hygiene, since `config.py` calls
`load_dotenv()` at import and a baked-in `.env` would silently override every
variable Railway injects.

Set the same environment variables, plus `DB_POOL_MIN=1` — the process is
long-lived, so a warm connection saves the first request of each burst a ~0.5s
TLS handshake.

Two details that will cost you an afternoon if you miss them:

* **Clear any Custom Start Command in the service settings.** It overrides the
  Dockerfile's `CMD`, and Railway runs it without a shell, so a command
  containing `$PORT` arrives at uvicorn as the literal string `$PORT`.
* **`CMD` is exec-form on purpose**: `["sh","-c","exec python -m uvicorn …"]`.
  The `sh -c` expands `$PORT`; the `exec` then replaces the shell so python is
  PID 1 and actually receives `SIGTERM`. Shell form leaves `/bin/sh` as PID 1,
  the app never hears the signal, and the shutdown hook that hands pooled
  Postgres connections back never runs.

The healthcheck is `/api/ping`, not `/api/health`. `/api/health` calls Tavily,
Gemini and the person-signal provider for real — the right check before a demo,
the wrong one for a platform that probes on every deploy and restart, where it
would spend search credits just to confirm the app is up. Note that `/api/ping`
does touch the database, so a missing `DATABASE_URL` fails the deploy rather
than just failing a request.

Be aware what Railway's free plan actually is: **$1/month of credit** at 0.5 GB
RAM, which is hours of always-on uptime rather than a month of it. New accounts
get a one-time $5 trial, and when the balance hits zero Railway stops the
workload. Pick the region closest to the Supabase project for the reason above.

---

## Person-level signal, and why it is fetched this way

Web search reaches what is *written about* someone. It does not reach what they
*say themselves* — LinkedIn has no self-serve API, and Proxycurl, the main
compliant LinkedIn data API, shut down entirely. That gap matters, because the
strongest hook is usually a person publicly describing a problem they have.

Two ways to close it, and the difference is not cosmetic:

* **Session-cookie scrapers (PhantomBuster and similar)** drive *your own*
  LinkedIn account via its session cookie. Documented outcomes include warnings,
  restrictions and permanent bans — on the operator's personal account. Not used
  here.
* **An API provider (Super Carl)** returns profile data under an API key. The
  user's own account is never in the loop. This is what the optional
  `SUPERCARL_API_KEY` enables: `POST /api/v1/search/people` resolves the person,
  then `GET /api/v1/profiles/{id}/text` returns their profile text and posts.

When the key is present, that content joins the person tier of research and is
tagged person-level. When it is absent, fails, or times out, the run degrades
silently to web search alone — it is an enhancement, never a dependency.

Provider content gets **no extra trust**: it passes through the same extraction,
grounding verification and judgment gates as a news article. A fact sourced from
a paid provider still has to be verifiable against the text it came from.

`discover_supercarl.py` probes the API with your key and prints live response
shapes, for when the integration needs adjusting against reality.

## Known limits

Also: single-user, no CRM/sequencer integration (CSV export is the bridge), no
team coordination, English only, ICP qualifies an uploaded list rather than
sourcing prospects. Each is a deliberate scope call, documented in `docs/`.
