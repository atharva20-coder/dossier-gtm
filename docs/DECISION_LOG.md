# Decision Log
Dossier · PS-3 · Every decision, why it was made, and what we rejected

Running record. Each entry: the decision, the reasoning, the alternative rejected. This doubles as interview prep — the brief says you'll need to "talk fluently about every decision you made," and this is that script.

New decisions get appended here as we make them. Nothing gets decided silently.

---

### D1 — Chose PS-3 (personalised outreach) over PS-1 (invoices) and PS-2 (vendor onboarding)
**Reasoning:** you had genuine conviction and detailed opinions on PS-3 and none on the others, and the brief explicitly grades how fluently you can discuss your decisions live. Interest produces a better demo and a better conversation.
**Rejected:** PS-2 was the "safer" build (no OCR, no live web dependency) but you were lukewarm on it. PS-1 forces document parsing/OCR, the flakiest possible live-demo dependency.
**Risk accepted:** PS-3 depends on live web search during the interview. Mitigated by mock mode + graceful degradation.

---

### D2 — Python (FastAPI) + vanilla JS frontend
**Reasoning:** full control, no framework magic to explain, runs with one command, and every line is defensible under questioning — which matters because the brief grades explainability.
**Rejected:** n8n/Make/Zapier — faster to assemble but harder to host reliably for a live demo, and "I dragged nodes around" is a weaker interview answer than owning the logic.

---

### D3 — Gemini as the LLM, not Claude or OpenAI
**Reasoning:** your preference, plus Google AI Studio issues a genuinely free API key with no card required. Verified against current docs.
**Confirmed along the way:** a Claude Pro subscription does **not** include API credits — Anthropic bills the API entirely separately. So Pro wouldn't have covered this.
**Model split:** `gemini-2.5-flash` for mechanical stages (identity, query generation, extraction), `gemini-3.8-flash` for judgment and drafting. Cheap where quality doesn't matter, strong where it does.
**Rejected:** `gemini-3.1-pro-preview` — preview status, no free tier. Preview APIs are exactly the live-demo fragility we're designing against.

---

### D4 — Tavily for search, not OpenRouter-as-everything
**Reasoning:** Tavily's free tier is 1,000 credits/month ≈ 150–200 full runs. OpenRouter's free tier caps at **50 requests/day**, which at ~10 LLM calls per run is about 5 runs per day — unusable on a day when you're debugging and re-running constantly.
**Rejected:** OpenRouter as primary. Kept as a documented fallback if Gemini rate-limits.

---

### D5 — No LinkedIn or X API integration
**Reasoning:** LinkedIn has no self-serve API beyond basic login (feed/connections access requires partner approval, a business process, not a week). X's read access requires a paid tier (~$100+/mo); the free tier is effectively write-only. Building toward either risks burning days with no guarantee of access.
**Consequence turned into an asset:** this became a *deliberate scope decision* to defend rather than a gap. Most candidates won't have investigated it at all.
**Mitigation:** the brief's own examples (funding round, recent hire, public problem) are almost entirely reachable via general web search.

---

### D6 — Personal-site embedded posts used opportunistically, never depended on
**Reasoning:** when a prospect's own site embeds their posts, that content is on a public page we can legitimately read. X-style embeds usually include the text as fallback markup; LinkedIn embeds are iframes pointing back to linkedin.com, so they're not readable this way.
**Decision:** treat as bonus signal when present, never as a dependency.

---

### D7 — Named "Dossier"; rejected "AI Stalker"
**Reasoning:** the name is read by a hiring team at a company serving DoorDash, Uber, and Stripe. "Stalker" branding directly undercuts the guardrails we spent the most design effort on (no personal-life info, no invasive signal) and hands an interviewer an easy reason to question judgment about what's appropriate to ship to a customer.
**Kept the underlying intent:** deep, multi-source *professional* OSINT — thorough research, explicitly bounded to public professional context.

---

### D8 — Bulk Excel upload instead of a single-prospect form
**Reasoning:** your requirement, and it matches how GTM teams actually work (account lists, not one name at a time).
**Consequences we then had to design for:** cost/quota math (50 rows ≈ 300 search credits — 30% of the monthly free tier in one click), concurrency control, company-level caching, per-row partial failure semantics, and same-account hook collision. Bulk upload wasn't a UI convenience — it changed the operating model.

---

### D9 — Mock mode implementing the *real* decision logic
**Reasoning:** lets the entire pipeline, UI, and every edge case be built and tested before API keys exist, and the mock swaps for live calls with zero changes to judgment logic. Also a genuine live-demo safety net.
**Important distinction:** the mock replaces the *data source*, not the *reasoning*. The eligibility rules running in the prototype are the real ones.

---

### D10 — Four core edge cases, plus stale-employer as a fifth
**Reasoning:** the brief asks for 2–4 and explicitly warns that three handled well beats ten handled shallowly. The four core cases (insufficient signal, negative news, stale signal, ambiguous identity) each test a different judgment. Stale-employer attribution earns a fifth slot because it's a genuinely different failure axis from stale-fact.
**Rejected:** maximizing edge-case count. The brief penalises breadth-over-depth explicitly.

---

### D11 — Grounding verification as the top production safeguard
**Reasoning:** a hallucinated fact passes every guardrail we wrote (recent ✓ safe category ✓ specific ✓) and lands in a real founder's inbox under a real rep's name. It's the highest-severity *silent* failure in the system. Verifying extracted facts appear verbatim in source text converts it into a loud, cheap one.
**Priority:** ahead of everything in the original spec, including features we'd already planned.

---

### D12 — Signal decay treated separately from factual recency
**Reasoning:** a 5-month-old funding round passes our 6-month recency gate but is worthless as outreach — everyone congratulated them months ago. Factual accuracy and outreach value decay on different clocks (outreach sweet spot ≈ 1–21 days).

---

### D13 — Tiered signal fallback: episodic → always-on
**Reasoning:** for a realistic mid-market list, "no recent news" is plausibly 40–60% of rows, not a 5% edge case. Falling back to always-on signal (job postings, careers page) is what makes the tool work on a real list instead of only on newsworthy startups.
**Reframe:** "insufficient signal" moved from edge case to near-primary path, and its design got upgraded accordingly.

---

### D14 — Seller configuration added as a pipeline input
**Reasoning:** the system currently ranks hooks by recency × specificity × safety with no knowledge of what the rep sells — so it optimizes for "most interesting fact" rather than "fact that connects to my offer." For an AP-automation vendor, "hiring 5 AP clerks" beats a Series A, and current logic can't see that.

---

### D15 — Implementation decisions moved to `DECISIONS.md`

**Reasoning:** the decisions from here on are code-level and there are ~60 of
them — namesake gates, source ranking, traversal budgets, serverless
constraints, the access gate. Written in this format they would bury D1-D14
under detail that only matters if you are reading the file they describe.
`DECISIONS.md` holds them one line each, grouped by area, in the same
what/why/rejected shape.

**Rejected:** appending them here — the log's value is that it is short enough
to read before an interview.

---

## Open decisions (not yet made — need your call)

- 🔸 **UI structure** — unresolved, blocking Phase 4.
- 🔸 **Saturation penalty** — implement the "deprioritize the obvious funding hook" logic, or keep it as a talking point only? Recommendation: implement *and* surface the reason in the UI so it reads as deliberate rather than broken.
