# Failure Scenario Catalog
Dossier · PS-3 · "What if X happens?" → what the app does → why

The question an interviewer actually asks is *"what happens if…"* — so this is organized as scenarios, not system layers. Every row is a real thing that will happen in a real deployment.

**Status key:** ✅ built · 🔨 planned this week · 📋 designed, deferred (interview answer)

---

## A. The uploaded file

| # | What if… | What the app does | Why | Status |
|---|---|---|---|---|
| A1 | the file isn't valid Excel/CSV | Rejects with a specific message naming the problem, doesn't crash | Loud failure, user can fix it | 🔨 |
| A2 | the file is empty or headers-only | "No prospect rows found" — distinct from a parse failure | Different cause, different fix | 🔨 |
| A3 | there's no `Name` column, or it's called `Full Name` / `Contact` / `prospect_name` | Fuzzy header matching, then a **"we mapped your columns like this — confirm?"** step | Silent failure otherwise: rows import blank and quietly do nothing | 🔨 |
| A4 | headers are in row 3, not row 1 | Detects the header row, or lets the user pick it | Common in exported CRM sheets | 📋 |
| A5 | the name field is `Dr. Radhika Patil (she/her) \| CEO @ Cradlewise` | Normalizes: strips honorifics, pronouns, pipes, trailing titles, before searching | People paste LinkedIn headlines constantly. Unnormalized, the search returns nothing and it gets **misreported as "no signal"** when it's actually an input bug | 🔨 |
| A6 | the same prospect appears twice | Dedupes on normalized name + company, tells the user how many were merged | Double cost and double outreach risk | 🔨 |
| A7 | 5,000 rows are uploaded | Shows a cost/credit estimate and requires explicit confirmation; hard cap above a threshold | 5,000 rows ≈ 30,000 search credits — 30× the monthly free tier, in one click | 🔨 |
| A8 | only the name is filled in, nothing else | Runs anyway at lower identity confidence; likely triggers disambiguation | Degrade, don't refuse | ✅ |
| A9 | the company field holds a URL or LinkedIn slug | Resolves it to a company name rather than searching the raw string | Searching `linkedin.com/company/cradlewise` returns junk | 📋 |
| A10 | names use non-Latin scripts or transliteration variants | Passed through unmangled; variant spellings noted as a known limitation | Mangling silently produces "no signal" | 📋 |
| A11 | the sheet contains extra PII columns (personal phone, home address) | Ignored and never persisted | We shouldn't hold personal data we never needed — GDPR/DPDP exposure for zero benefit | 🔨 |

---

## B. Identity resolution

| # | What if… | What the app does | Why | Status |
|---|---|---|---|---|
| B1 | two real people share the name and no company was given | **Stops and asks.** Shows candidates with distinguishing details | Researching the wrong person wastes the run and can badly embarrass the rep | ✅ |
| B2 | the company name is a generic word (Notion, Apple, Aegis) | Weights location/URL heavily; flags low confidence if neither is present | Otherwise it confidently researches an entirely different company | 🔨 |
| B3 | nothing matches at all | Status `low_confidence_identity` — proceeds with a visible caveat rather than pretending certainty | The rep needs to know how much to trust it | 🔨 |
| B4 | the prospect left the company since the news was published | Stale-employer check: looks for a newer job-change signal, excludes or flags the mismatch | A *recent, true* fact about the *wrong employer* is a distinct failure from a stale fact | 🔨 |
| B5 | the company was acquired or renamed | Flags the mismatch instead of attributing parent-company news to the subsidiary | Over-attribution reads as sloppy research | 📋 |
| B6 | the prospect shares a name with a famous person | Confidence scoring requires company/role corroboration, not name match alone | Otherwise it attaches a celebrity's news to an unrelated person — memorable in the worst way | 🔨 |
| B7 | identity confidence is low but not zero | Confidence is carried forward as a **first-class field** through every later stage and shown in the UI | A hook from a 62-confidence match should not look identical to one from a 100 match | 🔨 |

---

## C. Research / search

| # | What if… | What the app does | Why | Status |
|---|---|---|---|---|
| C1 | search returns zero results | `no_signal_found` — a legitimate finding, not an error | This is a real answer about the prospect | ✅ |
| C2 | the API key is invalid or expired | `research_failed` with the actual cause surfaced | Different problem, different fix | 🔨 |
| C3 | quota is exhausted at row 40 of 50 | Remaining rows marked `research_failed`, batch pauses, user told exactly what happened | **Critical:** otherwise 10 rows silently report "this prospect has no public signal" when the truth is "we ran out of credits." Conflating these makes every downstream metric a lie | 🔨 |
| C4 | one query times out | That query is dropped, the run continues on the others | One slow source must never stall a run | ✅ |
| C5 | *every* query times out | `research_failed`, not `no_signal_found` | Same distinction as C3 | 🔨 |
| C6 | results are all paywalled | Confidence lowered; flagged as "limited verification" | We shouldn't cite what we couldn't read | 📋 |
| C7 | results are SEO spam / content farms | Domain quality signal filters them before extraction | Looks like a source, is noise | 📋 |
| C8 | the same press release appears on 5 sites | Near-duplicate detection collapses them into **one** fact | 5 hits isn't 5 confirmations — it's one press release in five costumes. Treating count as confidence is being fooled by syndication | 🔨 |
| C9 | the company has an aggressive PR team (50 promotional articles) | Specificity gate rejects puff pieces; falls through to substantive signal | Otherwise it drafts "congrats on your award," which says nothing | ✅ |

---

## D. Extraction & judgment

| # | What if… | What the app does | Why | Status |
|---|---|---|---|---|
| D1 | **the model hallucinates a fact** | **Grounding verification:** key entities (numbers, company, investors) must appear verbatim in retrieved source text, or the fact is dropped before drafting | The highest-severity silent failure in the system. It passes every other guardrail — recent ✓ safe ✓ specific ✓ — and reaches a real founder under a real rep's name | 🔨 ⭐ |
| D2 | the model returns malformed JSON | Schema validation + one retry, then a loud stage error | Never let a parse failure become a silent empty result | 🔨 |
| D3 | the fact is about a *different* company mentioned in the same article | Entity check against the target company before the fact is accepted | Competitor-comparison articles are the classic trap | 🔨 |
| D4 | the article's publish date ≠ the event date | Prefers the event date where extractable; flags uncertainty | A piece published today about a 2024 event isn't recent news | 📋 |
| D5 | the article has no date at all | Treated as unknown recency and deprioritized, never assumed fresh | Assuming fresh is the dangerous direction | 🔨 |
| D6 | every fact fails eligibility | Tiered fallback runs first (job postings, careers page); only then `insufficient_signal` | For a realistic mid-market list this is close to the *modal* outcome, not a rare edge | 🔨 |
| D7 | the only signal found is negative (layoffs, lawsuit) | Hard-excluded from ever becoming a hook, logged visibly as "found, excluded — reputationally sensitive", next-best used | Referencing layoffs as a sales hook is actively harmful outreach | ✅ |
| D8 | the negative news is *euphemistic* ("exploring strategic options", "stepping down to spend time with family") | Semantic judgment with explicit examples, not keyword matching | Keyword filters miss exactly these, and these are the common phrasings | 🔨 |
| D9 | the signal is positive-sounding but actually distress ("raised a bridge round", "acquired") | Flagged for review rather than celebrated | Congratulating a fire sale is worse than sending nothing | 📋 |
| D10 | the only eligible hook is 5 months old | Passes the factual-recency gate but scores near-zero on outreach value | Everyone congratulated them 5 months ago — using it now reads as *late*, not researched | 🔨 |
| D11 | the hook doesn't fit the prospect's seniority | Hook selection conditioned on role | Congratulating a junior IC on "your funding round" is odd — they didn't raise it | 📋 |

---

## E. Drafting

| # | What if… | What the app does | Why | Status |
|---|---|---|---|---|
| E1 | the draft doesn't actually contain the hook's specific detail | Programmatic check; regenerate once; if it fails twice, fall back to the honest no-hook path | Otherwise it's a generic email wearing a personalized label | 🔨 |
| E2 | the draft adds a detail that was never in the hook | Grounding check applied to the draft too, not just extraction | Hallucination can enter at *either* stage | 🔨 |
| E3 | the LLM call fails entirely | Returns the hook + sources **without** a draft | Still genuinely useful — the rep can write it themselves in 30 seconds. Far better than a failed run | 🔨 |
| E4 | the name format makes first-name extraction ambiguous | Uses the full name rather than guessing wrong | Getting someone's name wrong in the first line destroys the whole point | 🔨 |
| E5 | 50 drafts come out structurally identical | Batch homogeneity check / varied templates | Identical structure trips spam filters and reads as automation if colleagues compare | 📋 |

---

## F. Infrastructure & the live demo

| # | What if… | What the app does | Why | Status |
|---|---|---|---|---|
| F1 | **the internet dies during the interview** | `MOCK_MODE=true` runs the full pipeline with canned data — the demo still runs end to end | This is the single most important operational safeguard *for the interview itself*. The brief warns that a demo breaking live is hard to recover from | ✅ |
| F2 | the browser is closed mid-run | Run state persisted in SQLite; marked `interrupted`, not left hanging in `running` forever | Orphaned states are how dashboards start lying | 🔨 |
| F3 | the server restarts mid-batch | Same — completed rows keep their results, incomplete rows are resumable | | 🔨 |
| F4 | the play button is clicked twice on one row | Guarded; button disabled while running | Prevents double spend and duplicate runs | ✅ |
| F5 | the API is slow (30s+) | Per-call timeouts with partial results, never an indefinite hang | | 🔨 |
| F6 | 50 rows all fire at once | Bounded worker pool, not unbounded concurrency | 50 rows × 6 queries = 300 simultaneous requests = instant rate limiting | 🔨 |
| F7 | 10 prospects share one company | Company-level research cache — researched once, not ten times | Large cost saving, and consistent facts across colleagues at the same account | 🔨 |

---

## G. User behaviour

| # | What if… | What the app does | Why | Status |
|---|---|---|---|---|
| G1 | the same prospect is run twice | Cached hook returned within a TTL rather than re-derived | Otherwise non-determinism hands the rep two different "personalised" messages that contradict each other | 📋 |
| G2 | the rep doesn't like the chosen hook | Offers the runner-up hook | We already compute a ranked list and currently throw away everything but #1 — that's wasteful | 🔨 |
| G3 | the rep edits the draft | Edit saved with the run | Rep edit distance is the single best free quality signal available | 📋 |
| G4 | the same sheet is uploaded twice | Detected, user asked whether to merge or start a fresh batch | | 📋 |

---

## The four scenarios I'd make sure to mention unprompted

1. **C3 — quota exhaustion silently reported as "no signal."** Shows you understand that a metric can lie to you.
2. **D1 — hallucinated facts.** The highest-severity failure, and the one with a cheap, concrete fix.
3. **F1 — no internet during the demo.** Shows you thought about the interview itself as an operating environment.
4. **D6 — "no signal" being the modal case on a real list, not an edge case.** Shows you thought past the demo data to a real deployment.
