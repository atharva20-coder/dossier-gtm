# Technical Spec — API Integrations & App Requirements
Zamp Case Study · PS-3 · Personalized Outreach

## 1. Tavily Search API — Integration Spec

Verified against current docs (docs.tavily.com), Sept 2026.

- **Endpoint:** `POST https://api.tavily.com/search`
- **Auth:** header `Authorization: Bearer tvly-<YOUR_KEY>`
- **Required param:** `query` (string)

**Params we'll actually use, per call:**

| Param | Value we set | Why |
|---|---|---|
| `search_depth` | `"basic"` | 1 credit/call — `"advanced"` costs 2x for marginal gain here; free tier stretches further on basic |
| `max_results` | `5` | Enough per query without bloating extraction input |
| `topic` | `"news"` for funding/press queries, `"general"` for team/about pages | Tavily's news index is fresher for our funding/press/hiring queries |
| `time_range` | `"year"` | Filters out ancient results at the source, before our own recency guardrail even runs |
| `include_raw_content` | `"text"` | We need actual page text for extraction, not just the snippet |
| `include_answer` | `false` | We do our own synthesis; don't need Tavily's built-in answer |

**Credit budget per run:** ~5-6 queries × 1 credit (basic) = 5-6 credits. Free tier (1,000 credits/mo) supports ~150-200 full runs/month — more than enough for build + testing + interview.

**Response fields we consume:** `results[].title`, `results[].url`, `results[].content` (or `raw_content`), `results[].score`.

**Failure handling:** per-call timeout of 6s; on timeout/429/5xx, that single query is dropped and the run proceeds with whatever other queries succeeded. If **all** queries fail, the run reports a clean "research unavailable" status rather than crashing.

## 2. Gemini API — Integration Spec

Verified against current docs (ai.google.dev), Sept 2026.

- **SDK:** `google-genai` (Python), via `from google import genai`.
- **Auth:** `GEMINI_API_KEY` env var, picked up automatically by the SDK. Free key from aistudio.google.com/apikey — Google AI Studio issues free-tier keys for the Flash models with generous per-minute rate limits (exact current limits shown at key-creation time; fine for a single-run-at-a-time demo tool).
- **Call pattern:** `client.interactions.create(model=..., input=..., response_format=...)`.

**Model assignment per stage** (cheap/fast model for mechanical steps, the stronger flash model reserved for what actually needs judgment/quality — avoiding `gemini-3.1-pro-preview` since it's preview-status with no free tier, which is exactly the kind of live-demo risk we're designing away from):

| Stage | Model | Why |
|---|---|---|
| Stage 0 — Identity resolution | `gemini-2.5-flash` | Fast, cheap, free-tier; lightweight disambiguation check |
| Stage 1 — Query generation | `gemini-2.5-flash` | Mechanical: turn input fields into 5-6 search queries |
| Stage 3 — Signal extraction | `gemini-2.5-flash` | Structured pull of facts from page text; volume-heavy, doesn't need top-tier reasoning |
| Stage 4 — Relevance judgment + guardrails | `gemini-3.8-flash` | This is the actual judgment call (recency, relevance, negative-news filter) — worth the upgrade to Google's most intelligent Flash tier |
| Stage 5 — Draft generation | `gemini-3.8-flash` | Output quality is the whole point of the deliverable — don't cheap out here |

**Structured output approach:** every stage except Stage 5 (drafting, which returns prose) uses `response_format` with a Pydantic model passed as `schema` — Gemini returns strict JSON conforming to it, avoiding fragile prompt-and-parse. E.g. Stage 4 uses a `HookSelection` Pydantic model: `{hook, source_url, confidence, reasoning, discarded: [{fact, reason}]}`.

**Token/cost budget per run:** roughly 8-10 `gemini-2.5-flash` calls (fractions of a cent each) + 2 `gemini-3.8-flash` calls ≈ low single-digit cents per run, and largely covered by the free tier's rate-limited quota during dev/testing.

**Failure handling:** each call wrapped with a timeout + one retry; on final failure, the stage returns a typed error result, the run status becomes `error` at that stage, and the UI shows exactly where and why — never a silent hang.

## 3. Application Requirements

### 3.1 Environment variables (`.env`, gitignored)
```
TAVILY_API_KEY=tvly-...
GEMINI_API_KEY=...
MOCK_MODE=false        # true = use canned responses, no external calls, no keys needed
```

### 3.2 Python dependencies
```
fastapi
uvicorn[standard]
httpx           # async HTTP client for Tavily calls
google-genai    # official Gemini SDK
python-dotenv
pydantic
aiosqlite       # async SQLite for run storage
```

### 3.3 Folder structure
```
zamp-case-study/
  backend/
    main.py          # FastAPI app, routes
    pipeline/
      identity.py    # Stage 0
      research.py    # Stage 1-2 (query gen + parallel Tavily calls)
      extract.py     # Stage 3
      judge.py       # Stage 4
      draft.py       # Stage 5
    integrations/
      search.py       # Tavily wrapper + mock
      llm.py          # Gemini wrapper + mock
    db.py             # SQLite schema + queries
    models.py         # Pydantic request/response schemas
  frontend/
    index.html        # single-page UI: input, live run view, dashboard
  fixtures/
    prospects.json     # the 5 rehearsed test cases
  .env
  requirements.txt
  README.md
```

### 3.4 REST API surface (consumed by the frontend)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/runs` | Start a new run (body: name, company, role, location, url). Returns `run_id` immediately. |
| `GET` | `/api/runs/{run_id}/stream` | Server-Sent Events stream of stage updates for the live run view. |
| `GET` | `/api/runs/{run_id}` | Full detail of one completed/in-progress run (for dashboard drill-down). |
| `GET` | `/api/runs` | List of all runs, most recent first (dashboard). |
| `POST` | `/api/runs/{run_id}/resolve` | Submit the user's pick when Stage 0 returns multiple candidates. |

### 3.5 Data model (SQLite)
```
runs(id, input_json, status, created_at, chosen_hook, draft_text)
run_stages(id, run_id, stage_name, status, started_at, finished_at, output_json)
run_sources(id, run_id, url, title, category, retrieved_at)
```

### 3.6 Concurrency
- Stage 1-2 search queries executed with `asyncio.gather` — all 5-6 Tavily calls fire concurrently, not sequentially.
- Stage 3 extraction can also run concurrently per-source.
- Stage 0, 4, 5 are inherently single-call, sequential by nature.

### 3.7 Run command
```
uvicorn backend.main:app --reload --port 8000
```
Single command, no build step, no external services beyond the two APIs. Opens at `http://localhost:8000`.

### 3.8 Mock mode
When `MOCK_MODE=true` or no API keys are present, `integrations/search.py` and `integrations/llm.py` return realistic canned data per stage, so the entire pipeline, UI, and edge cases are buildable and testable right now, and flip to live with zero code changes once keys are added.

Sources: [Tavily Search API Reference](https://docs.tavily.com/documentation/api-reference/endpoint/search), [Gemini API Models](https://ai.google.dev/gemini-api/docs/models), [Gemini API Quickstart](https://ai.google.dev/gemini-api/docs/quickstart), [Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing)
