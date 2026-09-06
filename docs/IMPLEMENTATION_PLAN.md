# Implementation Plan
Dossier · PS-3 · How we actually build this, in what order, and why

Every phase below states **what we build, why it earns its place, and what we deliberately don't build.** Decision gates marked 🔸 need your call before I proceed — I won't decide those silently.

---

## Scoping principle (the filter for everything below)

The brief grades four things: *does it run*, *are edge cases handled deliberately*, *is the UI intuitive*, *can you defend your decisions*. Anything that doesn't move one of those four is out of scope for this week, no matter how good an idea it is. The two analysis docs deliberately generated more ideas than we can build — the value of the unbuilt ones is as interview answers ("here's what I found and chose not to build, and why"), which is worth more than a half-built feature.

**Hard rule: nothing ships that can't run live in front of an interviewer.**

---

## Phase 1 — Core pipeline, mocked (Day 2)

Build the real 6-stage pipeline end-to-end against mock search/LLM responses, so the entire system is testable before API keys exist.

- `identity.py` — Stage 0, confidence scoring per Decision Point 1
- `research.py` — query generation + parallel search (bounded concurrency, not unbounded `gather`)
- `extract.py` — structured fact extraction
- `judge.py` — eligibility gates + hook selection
- `draft.py` — draft generation + post-generation checks
- `db.py` — SQLite: runs, stages, sources
- Mock layer returning realistic canned data per stage

**Why first:** everything downstream depends on the pipeline contract being right. Building the UI against a working backend is far cheaper than retrofitting.

**Not building:** real API calls yet.

---

## Phase 2 — The four production safeguards (Day 2–3)

These are the highest value-per-hour items from the production analysis. All four are cheap and all four are strong interview material.

### 2.1 Grounding verification ⭐ highest priority in the whole build
After extraction, programmatically verify each fact's key entities (numbers, company name, investor names) appear **verbatim in the retrieved source text**. Facts failing verification are dropped before they can reach drafting.

*Why:* hallucinated facts are the highest-severity silent failure in the system and the one that would actually end a rep's trust in the tool. This converts it into a loud, cheap failure. If only one thing from the analysis gets built, it's this.

### 2.2 `no_signal_found` vs `research_failed` as distinct statuses
Never let an infrastructure failure (quota, timeout, rate limit) report as "this prospect has no public signal."

*Why:* nearly free to implement, and conflating them makes every quality metric downstream a lie.

### 2.3 Signal decay window (outreach value ≠ factual recency)
Score hooks on an outreach-value curve — strongest at 1–21 days, decaying sharply after ~60 days — layered on top of the existing 6-month factual-recency gate.

*Why:* a 5-month-old funding round is still true and already useless. This distinction is one of the sharpest things in the analysis and it's a few lines of scoring code.

### 2.4 Stale employer attribution
After identity resolution, check for a more recent job-change signal than the company-attributed fact under consideration. Mismatch → exclude or flag.

*Why:* a *recent, true* fact attached to the *wrong employer* is a different failure axis than a stale fact, and it's the edge case that most shows depth.

---

## Phase 3 — The two GTM changes (Day 3)

### 3.1 Seller configuration
A small one-time config — what you sell, who you sell to, what pain you solve — passed into hook selection so the system optimizes for *connection to the offer*, not raw prominence.

*Why:* without it, the system picks "most interesting fact" instead of "most useful fact." For an AP-automation seller, "hiring 5 AP clerks" beats a Series A every time, and our current logic can't see that.

### 3.2 Tiered signal fallback (episodic → always-on)
When no episodic signal (funding/press/exec hire) qualifies, fall through to always-on signal (job postings, careers page, leadership page) rather than straight to "nothing found."

*Why:* for a realistic mid-market list, "no recent news" is close to the modal outcome, not an edge case. This is what makes the tool work on a real list instead of only on newsworthy startups.

🔸 **Decision gate:** saturation penalty (deprioritizing the funding hook precisely *because* everyone else uses it) is the most counterintuitive idea in the GTM doc. It's a genuinely strong talking point, but implementing it means sometimes *not* picking the most impressive-looking hook in a live demo — which could read as a bug to an interviewer who doesn't know the reasoning. **My recommendation: implement it but surface it explicitly in the UI** ("funding hook deprioritized — high outreach saturation") so it reads as deliberate. Your call.

---

## Phase 4 — UI (Day 3–4)

🔸 **Decision gate: structure is unresolved — see the separate UI discussion. Not starting this until we've settled it.**

Requirements regardless of structure:
- Upload → list → per-prospect run → draft, with no dead ends
- Every decision the system made must be visible in plain language
- The draft is the hero of the detail view, not a footnote
- Sources clickable (adoption feature, not decoration)
- Draft editable in place
- Run history persists

---

## Phase 5 — Live API integration (Day 4–5)

Swap mock layer for real Tavily + Gemini. Zero changes to pipeline logic — that's the whole point of the mock design.

Includes the batch mechanics that bulk upload forced on us:
- **Company-level caching** — 10 contacts at one company research once, not ten times (large cost saving + consistency across colleagues)
- **Bounded concurrency** — a worker pool, not 300 simultaneous requests
- **Pre-run cost/quota estimate** — show the credit cost before running a 50-row sheet
- **Per-row partial failure** — a batch where 43 succeed, 5 hit quota, 2 need disambiguation reports exactly that

---

## Phase 6 — Edge case verification (Day 5–6)

Each edge case gets a named test row in the sample sheet and is verified end-to-end against live APIs:

1. Insufficient signal → honest fallback (now with the tiered fallback attempted first)
2. Negative news present → excluded, next-best hook used
3. Stale signal only → excluded on both factual and outreach-value grounds
4. Ambiguous identity → disambiguation, no silent guess
5. Stale employer attribution → mismatch caught
6. *(if built)* Grounding failure → fact dropped, visible in the trail

---

## Phase 7 — Package & rehearse (Day 6–7)

- README: setup, one-command run, documented assumptions
- Demo script: exact run order, rehearsed
- 5-minute video
- Reply email to Palak

---

## What we're deliberately NOT building (and will say so)

Multi-language drafting · suppression/DNC list · CRM/sequencer integration · rep-edit capture & learning loop · golden-set regression harness · audit trail · same-account role-differentiated hooks · follow-up sequence generation · DSAR tooling.

Each of these is documented with a reason. "I identified it, here's why it didn't make week one" is a stronger answer than a half-built version of it.

---

## Sequencing rationale

Pipeline before UI (UI against a real backend is cheaper than retrofitting). Safeguards before GTM features (correctness before optimization). Mock before live (unblocks everything, and proves the swap works). Edge cases last (they're variations on a working system, not separate features).
