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

Upload a sheet → each prospect runs through six stages → you get a draft with its reasoning attached.

```
  Targeting → Identity → Research → Extract → Ground → Judge → Draft
```

| Stage | What it does |
|---|---|
| **Targeting** | Classifies seniority and function from the title, scores ICP fit, and checks relationship state. An existing customer or competitor is skipped *before* any research spend. |
| **Identity** | Works out *who this actually is*. Scores candidates on company/role/location/URL evidence. Two plausible people → it stops and asks rather than guessing. |
| **Research** | Generates queries from the prospect (not hardcoded) across two tiers — **person-level** (interviews, podcasts, talks, bylines, quotes, their own role change) and **company-level** (funding, hiring, IPO, expansion) — and fires them concurrently. |
| **Extract** | Pulls concrete facts from source text, each carrying verbatim key entities. Collapses syndicated duplicates; discards facts about other companies. |
| **Ground** | **Verifies every fact against the source text.** Anything unverifiable is dropped before it can be drafted. |
| **Judge** | Applies four eligibility gates, then scores survivors on outreach value × buying intent. |
| **Draft** | Writes the email, then checks it actually used the hook's specific detail and avoided generic openers. Two failures → falls back honestly. |

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

```
backend/
  config.py           tunables + hard-excluded categories
  taxonomy.py         ICP, seniority/function, person + company intent tiers
  models.py           schemas (also the structured-output contracts)
  db.py               Supabase Postgres: runs, stages, sources (asyncpg)
  main.py             routes, upload parsing
  security.py         access gate, session cookies, response headers
  integrations/
    search.py         Tavily — typed failures, bounded concurrency
    llm.py            Gemini — structured output, retry, timeouts
  pipeline/
    normalize.py      input hygiene
    profile.py        seniority/function classification + ICP fit scoring
    identity.py       stage 0
    research.py       stages 1-2 + the traversal
    graph.py          research-as-graph: what to look at next, and the budget
    provenance.py     source ranking — LinkedIn > X > web, and corroboration
    extract.py        stage 3 + syndication collapsing + wrong-person gates
    grounding.py      verification
    judge.py          eligibility gates + scoring  (pure logic)
    draft.py          stage 5 + post-checks
    runner.py         orchestration, events, company cache
ui/                   Vite + React + Tailwind + shadcn/ui source
  ResearchGraph.tsx   live traversal drawing, arbitrary breadth and depth
frontend/             built assets, served by FastAPI
verify.py             preflight
discover_supercarl.py probe for a person-signal data provider
```

```bash
python -m backend.security                    # access-gate self-check
python -m backend.pipeline.graph              # traversal self-check
cd ui && npx tsx src/components/ResearchGraph.check.ts   # layout self-check
```

Tests that touch Supabase (`test_persistence.py`) need `DATABASE_URL`; they
write to a throwaway batch and delete it afterwards. Everything else runs
offline.

---

## Deploying to Vercel

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

## Falling back to Railway

Railway is a long-running host, so nothing about the app has to change —
`Procfile` and `railway.json` are already here and the built frontend is
committed, so no Node step is needed at deploy time. Set the same environment
variables, and set `DB_POOL_MIN=1` since the process is long-lived and should
keep a warm connection.

The healthcheck is `/api/ping`, not `/api/health`. `/api/health` calls Tavily,
Gemini and the person-signal provider for real — the right check before a demo,
the wrong one for a platform that probes on every deploy and restart, where it
would spend search credits just to confirm the app is up.

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
