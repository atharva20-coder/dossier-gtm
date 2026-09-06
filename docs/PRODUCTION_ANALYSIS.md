# Production Failure Analysis
Dossier · PS-3 · What breaks when this runs for real

## The framing that matters most: silent vs loud failures

Every failure mode below is sorted by one question first, severity second:

**Does it fail loudly (error, empty result, visible warning) or silently (confident, plausible, wrong)?**

A run that crashes is a bad afternoon. A run that produces a polished, confident email referencing a funding round that never happened — sent to a real executive under a real rep's name — is a customer incident. Every safeguard in this system should be biased toward converting silent failures into loud ones.

This is the single organizing principle of the design, and the thing I'd defend hardest in review.

---

## Layer 1 — Input (the uploaded sheet)

| Failure | Silent? | Notes |
|---|---|---|
| Headers not in row 1 / merged cells / multiple sheets | Loud | Parser returns garbage rows — detectable, reject with a clear message |
| Column named `Full Name` / `prospect_name` / `Contact` instead of `Name` | **Silent** | Rows import with empty names and quietly do nothing. Needs fuzzy header mapping + an explicit "we mapped your columns like this, confirm?" step |
| Name field contains a pasted LinkedIn headline: `Radhika Patil \| Founder @ Cradlewise \| ex-Google` | **Silent** | Extremely common in real lists. Searches for the whole string return nothing, looks like "insufficient signal" when it's actually an input parsing failure. Needs name normalization (strip honorifics, pronouns, pipes, "ex-" suffixes) |
| `Dr.` / `(she/her)` / emoji in name | **Silent** | Same class — pollutes the query |
| Company field contains a URL or LinkedIn slug instead of a name | Semi-silent | Detectable by pattern, should be resolved to a company name, not searched literally |
| Non-ASCII names, transliteration variants (Rajesh/Rajeesh, romanization differences) | **Silent** | Returns nothing → misreported as "no signal" |
| Duplicate rows (same person twice) | **Silent** | Double cost, double outreach risk. Dedupe on normalized name+company before running |
| 10,000 rows dropped in | Loud (if capped) | Must be capped/estimated — see Layer 8 |
| PII in extra columns (personal phone, home address) | **Silent** | We now store personal data we never needed. Should ignore + never persist unrecognized columns |

**Key insight:** a large fraction of "the AI couldn't find anything" in production is actually **input hygiene failure**, not research failure. If you can't distinguish those two, your metrics lie to you and you'll "fix" the wrong layer.

---

## Layer 2 — Identity resolution

| Failure | Silent? | Notes |
|---|---|---|
| Common name + common company (compound ambiguity) | **Silent** if we guess | Our disambiguation rule handles the 2-candidate case; the nasty case is *many* weak candidates and none scoring ≥60 |
| Company name is a generic word (Notion, Stripe, Apple, Aegis) | **Silent** | Search returns the wrong entity's news entirely, confidently |
| Same company name, different countries (a dozen "Aegis" globals) | **Silent** | Location filter helps; absence of location is the risk |
| Company recently rebranded or was acquired | **Silent** | Searching the old name returns stale/no results; searching the new one attributes parent-company news to a subsidiary |
| Namesake of a public figure | **Silent + embarrassing** | System confidently attaches a famous person's news to an unrelated individual with the same name. Needs a "is this person actually at this company" cross-check, not just name match |
| Person's professional name ≠ legal name in the sheet | **Silent** | Returns nothing → misreported as no signal |

**The rule I'd enforce:** identity confidence must be carried forward as a *first-class field* through every downstream stage, not discarded after Stage 0. A hook derived from a 62-confidence identity match should be labeled differently in the UI than one from a 100 match — because the rep needs to know how much to trust it before they hit send.

---

## Layer 3 — Research / retrieval

| Failure | Silent? | Notes |
|---|---|---|
| Search quota exhausted mid-batch | Loud per-call, **silent in aggregate** | Row 40 of 50 silently gets "no signal" for an infrastructure reason, not a real one. Must distinguish `no_signal_found` from `research_failed` as separate statuses — conflating them is a serious design error |
| Rate limited (429) | Same | Needs backoff + queue, not a dropped row |
| Paywalled article | **Silent** | Snippet suggests a fact; full text unverifiable. We may cite something we cannot actually read |
| SEO content farms that scraped real news | **Silent** | Looks like a source, is noise. Needs a domain quality signal |
| Company with an aggressive PR team | **Silent** | 50 promotional articles, zero substance — system picks a puff piece and drafts a hollow "congrats on your award" |
| Search index lag on very recent news | Benign | Just missed signal |
| Results in a language we didn't expect | Semi-silent | Extraction quality silently degrades |

---

## Layer 4 — Extraction (highest-severity layer)

**This is where the career-ending failure lives: hallucinated facts.**

The model reads five articles and produces a clean fact: *"Raised $18M Series B led by Accel, October 2026."* Nothing in any source said that. It's a plausible interpolation. It flows through judgment (recent ✓, funding category ✓, specific ✓), gets drafted into a warm, confident email, and goes to a founder who did not raise $18M.

Adjacent, subtler variants:
- Extracting a fact from the *"related articles"* sidebar rather than the article body
- Confusing **article publish date** with **event date** (a piece published this week about a 2024 event)
- Number/scale errors: `₹12 crore` ≠ `$12M`; "12 million" misread as the round size when it was ARR
- Attributing a fact to the wrong company when the article mentions several (competitor comparisons are the classic trap)

**The mitigation I'd treat as non-negotiable: grounding verification.** Every extracted fact must be traceable to a verbatim span in retrieved source text. Programmatic check — not "trust the model" — that the key entities (the number, the company name, the investor) literally appear in the retrieved content. Any fact that fails grounding is dropped before it can ever reach the drafting stage.

This single control converts the highest-severity silent failure in the system into a loud, cheap one. If I could only build one safeguard, it would be this one — ahead of every guardrail in the original spec.

---

## Layer 5 — Judgment

| Failure | Silent? | Notes |
|---|---|---|
| Negative news that isn't lexically negative | **Silent** | "Pivots," "restructures," "founder steps down to spend time with family," "explores strategic options" — all euphemisms. Keyword/category filters miss these; needs semantic judgment with explicit examples |
| Positive-sounding but actually distress | **Silent** | "Raised a bridge round" and "acquired" (acquihire / fire sale) frequently signal trouble. Congratulating these is tone-deaf |
| Signal decay ≠ factual recency | **Silent** | Our 6-month rule treats a 5-month-old funding round as eligible. In outreach terms it's dead — everyone already congratulated them 5 months ago; referencing it now reads as *late*, not researched. **Outreach value decays far faster than factual accuracy.** The sweet spot is roughly 1–21 days. This distinction is one of the sharpest things in this whole document and it isn't in the original spec |
| Role-appropriateness of the hook | **Silent** | Congratulating a mid-level IC on "your funding round" is odd — they didn't raise it. Hook selection should be conditioned on the prospect's seniority, not just the company's news |
| Hook reveals bad competitive timing | **Silent** | Referencing news that implies they just bought a competing solution |

---

## Layer 6 — Drafting

- **Hallucinated embellishment**: hook is real, but the draft adds a detail that isn't ("...after your Bangalore office expansion" — never mentioned anywhere)
- **Name handling**: wrong name part used as first name (family-name-first cultures), gender assumptions from names
- **Tone-to-seniority/culture mismatch**: startup-casual to a bank MD, over-formal to a founder
- **Language mismatch**: French prospect, English draft — decide deliberately, don't default silently
- **Implied surveillance**: phrasing that suggests private knowledge ("I saw you were looking at...") — instantly destroys trust, and is exactly the "creepy" line we designed against earlier
- **Batch homogeneity**: 50 drafts with an identical structural skeleton read as automation to anyone who compares notes

---

## Layer 7 — Human-in-the-loop

- No audit trail of who approved/edited/sent what → unusable in any compliance-sensitive account
- Rep rejects the hook — can they get the runner-up hook, or do they start over? (We compute a ranked list and then throw away everything but #1. That's wasteful and unhelpful.)
- Rep edits every draft heavily → that's the highest-signal quality feedback in the system, and by default we capture none of it
- Mid-run browser close / server restart → is the run resumable, or silently orphaned in `running` forever?

---

## Layer 8 — What bulk upload changed (a direct consequence of our Excel decision)

Moving from one prospect to a sheet of N fundamentally changes the operating profile:

- **Cost/quota math**: 50 rows × ~6 searches = 300 search credits (30% of the Tavily free tier in one click) and ~500 LLM calls. A 500-row sheet blows every free tier instantly. Needs a **pre-run cost/quota estimate shown before execution**, plus a hard cap.
- **Concurrency control**: 50 rows fired at once = 300 simultaneous outbound requests → rate limits, timeouts, thrash. Needs a bounded worker pool (semaphore), not `Promise.all` over everything.
- **Company-level caching**: 10 contacts at the same company should trigger the research once, not ten times. Massive cost saving and a correctness win (consistent facts across colleagues).
- **Partial failure semantics**: what does the batch report when 43 succeed, 5 fail on quota, 2 need disambiguation? A single "done" is a lie. Per-row status is mandatory (we have this).
- **Same-account hook collision**: covered earlier — 5 colleagues receiving the same hook in the same week reads as spam.
- **Idempotency**: re-uploading the same sheet shouldn't duplicate everything.

---

## Layer 9 — Legal / data protection (India + EU relevant to Zamp's client base)

- Processing personal data of identifiable individuals → lawful basis required under GDPR; India's DPDP Act applies to the Indian prospects in our own test set
- **Data retention**: we're caching scraped content about real people. How long? Right-to-erasure means we must be able to find and purge everything tied to one individual
- **Provenance for DSARs**: for any given draft, we must be able to say exactly what data was used and where it came from — which our per-run source logging already gives us, and is worth calling out as a deliberate design benefit rather than an accident
- Cold outreach compliance (CAN-SPAM / GDPR legitimate interest) sits downstream of us, but a suppression/do-not-contact check belongs *before* Stage 0

---

## Layer 10 — How you'd know it's actually working

Reply rate is the real ground truth but arrives weeks late. Leading proxies:

- **Rep edit distance** — if reps rewrite 80% of every draft, the system is theater. This is the single best early quality metric and it's free to collect.
- **Send rate** — drafts approved vs. discarded
- **Hook acceptance rate** — how often the rep keeps hook #1 vs. asks for another
- **Grounding failure rate** — how often extraction produces facts that fail source verification (a direct model-quality canary)
- **Insufficient-signal rate split by cause** — input hygiene vs. genuine thin footprint vs. infrastructure failure. Conflating these three is how teams spend a quarter fixing the wrong thing.
- **Golden-set regression tests** — a fixed set of prospects with known-good expected hooks, re-run on every prompt change. Prompt edits are code changes with no type system; without a golden set you are flying blind.

---

## Build / design / defer — the honest split for one week

**Build now (highest value per hour):**
1. **Grounding verification** — highest-severity silent failure in the system, cheap to implement, enormous to explain
2. **`no_signal_found` vs `research_failed` as distinct statuses** — nearly free, prevents the most misleading metric in the system
3. **Stale employer attribution** check (from the previous doc)
4. **Signal decay window** (prefer <21 days, deprioritize >60) rather than only the 6-month factual-recency rule

**Design + document, don't build:**
Cost estimation UI, company-level caching, bounded concurrency, suppression list, audit trail, rep-edit capture, golden-set regression harness.

**Defer explicitly, name it in the interview:**
Multi-language drafting, DSAR tooling, same-account hook coordination.

---

## The three sentences I'd lead with in the interview

1. "The failure I designed hardest against isn't the system breaking — it's the system being confidently wrong, because that's the one that reaches a customer's inbox with a rep's name on it."
2. "Factual recency and outreach value are different clocks: a 5-month-old funding round is still true and already useless."
3. "Bulk upload wasn't just a UI convenience — it changed the cost model, the concurrency model, and the failure semantics, and those needed to be designed for, not discovered in production."
