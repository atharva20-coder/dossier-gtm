# Requirements — AI-Powered Personalized Outreach (PS-3)
Zamp Case Study · AI Solutions Associate

## 1. Problem Framing

**Input:** a prospect (name, optionally company/role/location/URL).
**Output:** a personalized, non-generic outreach draft grounded in real, current, public signal about that prospect — with full reasoning visible — ready for human review before sending.
**Constraint:** must actually run live, on real inputs, in front of an interviewer, within a few seconds.

## 2. Functional Requirements

### FR1 — Prospect Input
- Accept: full name (required), company name, role/title, location, and a reference URL — all optional except name.
- More filters provided → higher-confidence identity resolution → faster, more accurate run.

### FR2 — Identity Resolution (Stage 0)
- Run a lightweight preliminary search using whatever filters were given.
- If exactly one high-confidence match is found → proceed automatically (keeps the happy path fast).
- If multiple plausible matches are found → surface them to the user and require a selection/refinement before continuing. Never silently guess.

### FR3 — Signal Research (Stage 1–2)
- Dynamically generate a set of targeted search queries from the resolved prospect (not hardcoded per-person).
- Categories to cover: company funding/financing, hiring/job postings, executive interviews/press, recent company news, company blog/about/team pages, personal site if discoverable.
- Execute all queries in parallel.
- Per-query timeout with graceful skip on failure/slow response — one bad source must never stall the run.

### FR4 — Signal Extraction (Stage 3)
- From retrieved page content, extract discrete candidate facts, each tagged with: source URL, approximate recency, and category (funding / hiring / press / product / other).

### FR5 — Relevance Judgment (Stage 4)
- Rank candidate facts by: recency, specificity, and "safe to reference" (see FR7).
- Select exactly one primary hook to draft from.
- If no candidate clears a minimum relevance/confidence bar → do not force a hook. Return an explicit "insufficient public signal" result with a safe, honestly-generic fallback draft option.

### FR6 — Draft Generation (Stage 5)
- Produce a short, conversational, specific message grounded in the selected hook.
- Must not read as a template with the name swapped in — the hook's specific detail must be load-bearing in the copy.
- Output is always a draft. No send capability. Human review is a required step before anything could go anywhere.

### FR7 — Guardrails (cross-cutting, applies at Stage 4)
- Exclude negative/reputational signal (layoffs, lawsuits, controversy) from being used as a "hook," even if it's the most recent/prominent item found.
- Exclude anything personal/non-professional in nature, even if surfaced.
- Exclude stale signal (configurable recency threshold, e.g. >6 months old deprioritized).

### FR8 — Run Transparency / Live Run View
- UI must show each pipeline stage as it executes in real time (not a spinner-then-result): resolving identity → searching → extracting → judging → drafting → done.
- Each stage's output must be inspectable — which queries ran, which sources were used, which facts were extracted, why the winning hook was chosen, what was discarded and why.

### FR9 — Dashboard / Run History
- Persistent list of all past runs: prospect, timestamp, status (completed / insufficient signal / needs disambiguation / error), chosen hook, draft preview.
- Must load instantly (local data, no external calls).

### FR10 — Edge Case Handling
1. No usable public signal found → honest fallback, not a hallucinated hook.
2. Negative news present → must not be used as the hook.
3. Stale signal only → must be deprioritized/excluded.
4. Ambiguous identity (multiple real matches) → disambiguation step triggers, does not silently guess.

## 3. Non-Functional Requirements

### NFR1 — Performance
- App shell (dashboard, history, forms) loads in <500ms — purely local, no external dependency.
- A full research run completes in ~5–10 seconds via parallelized search + fast models for mechanical stages, reserving the stronger model only for final drafting.
- No stage may block indefinitely — every external call has a timeout.

### NFR2 — Reliability / Live-Demo Safety
- Every external dependency (search API, LLM API) must degrade gracefully on failure/timeout rather than crashing the run.
- The system must be fully testable and demoable with mocked search/LLM responses before real API keys are available, and switch to live calls with zero code changes once keys are added.

### NFR3 — Transparency
- No hidden magic — every decision the process makes must be traceable to a visible reason in the UI.

### NFR4 — Ethical Scope
- Research restricted to publicly discoverable, professional-context information.
- No attempt to access LinkedIn/X through private APIs, scraping, or credentials — explicitly out of scope, stated as a design decision (see Section 5).

### NFR5 — Portability
- Runs with a single command, no cloud account/deployment required for the interview demo (local web app is sufficient, per FAQ: "even a clean console output is fine").

## 4. System Architecture

```
[Frontend: single-page HTML/JS]
   - Prospect input form (name, company, role, location, URL)
   - Live run view (stage-by-stage, real-time)
   - Dashboard (run history, from local API)
        |
        v  (HTTP, local)
[Backend: Python / FastAPI]
   - /run          -> executes the pipeline, streams stage updates
   - /runs         -> lists run history (dashboard)
   - /runs/{id}    -> full detail of one run
        |
        v
[Pipeline modules]
   - identity.py   (Stage 0)
   - research.py   (Stage 1-2: query gen + parallel search)
   - extract.py    (Stage 3)
   - judge.py      (Stage 4: ranking + guardrails)
   - draft.py      (Stage 5)
        |
        v
[Pluggable integrations]
   - search.py   -> Tavily API   (interface + mock fallback)
   - llm.py      -> Gemini API (interface + mock fallback)
        |
        v
[Storage]
   - SQLite: runs, stages, sources, drafts
```

## 5. Explicit Scope Decisions (for the interview narrative)

- **No LinkedIn/X API integration.** LinkedIn has no self-serve API beyond basic login; X's read access requires a paid tier. Building against either would risk burning the week on access problems with no guarantee of approval. Public web search (news, funding, press, jobs, company pages) covers the large majority of the brief's own examples (funding round, recent hire, public problem) without this dependency.
- **Personal-site embedded posts are used opportunistically, not depended on** — when a prospect's own site embeds a real post, it's read as bonus signal; the system never assumes it will be there.
- **The system never sends anything.** Output is always a reviewable draft.

## 6. Things You (Atharva) Need To Get

| # | Item | Why | Cost | Where |
|---|------|-----|------|-------|
| 1 | Tavily API key | Web search for Stage 1-2 | Free (1,000 searches/mo) | tavily.com → sign up → dashboard shows key |
| 2 | Gemini API key | Extraction, judgment, drafting (Stages 0, 3-5) | Free tier (rate-limited), no card needed | aistudio.google.com/apikey → Create API key |
| 3 | A screen recorder | Deliverable 2 (5-min demo video) | Free | Loom (free tier) or OS-native screen recording |
| 4 | 4-5 rehearsed test prospects | Happy path + edge cases, ready to run live in interview | — | We'll define these together (Section 7) |

Nothing else is required — no hosting, no domain, no paid infra. The whole thing runs locally with one command.

## 7. Test Prospects To Prepare (draft list — refine together)

1. **Happy path:** Radhika Patil / Cradlewise — real, recent (Sep 3, 2026), well-covered funding news.
2. **Edge case — insufficient signal:** an obscure/early-stage or low-profile prospect with little public footprint.
3. **Edge case — negative news present:** a real company currently in the news for something negative (layoffs/controversy) — verify the process correctly avoids using it as the hook.
4. **Edge case — stale signal only:** a company whose only discoverable news is >6-12 months old.
5. **Edge case — ambiguous identity:** a common name (e.g. "Rahul Sharma," "John Smith") with multiple plausible real matches — verify disambiguation triggers instead of a silent guess.

## 8. Build Order (unchanged from earlier discussion)

1. Backend pipeline, happy path only, mocked integrations.
2. Frontend (live run view + dashboard) against the working mock backend.
3. Wire in real Tavily + Gemini keys, verify happy path live.
4. Build + verify each of the 4 edge cases, one at a time.
5. Finalize all 5 test fixtures.
6. Full run-through, fix rough edges.
7. Package: README, one-command run, documented assumptions, demo video outline.

## 9. Mapping To Grading Criteria (from the candidate guide)

| They said they'd look at | Where it's covered here |
|---|---|
| "Process actually runs, not a mockup" | Real search + real LLM calls, mock is dev-only and swappable |
| "Deals with edge cases gracefully" | FR10, Section 7 |
| "UI is part of the grade — live run view + dashboard" | FR8, FR9 |
| "Explain it clearly to a non-technical buyer" | FR8 transparency — every decision traceable in plain language |
| "Your judgment behind design choices" | Section 5 — explicit, defensible scope decisions |
