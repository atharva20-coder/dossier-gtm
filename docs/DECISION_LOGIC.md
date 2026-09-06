# Decision Logic & Edge Case Specification
Dossier · Zamp Case Study · PS-3

This is the part that actually gets graded. Every decision point below has an explicit trigger condition and a stated reason — not "ask the LLM and hope." The LLM does the *reading*; these rules do the *deciding*. That split is deliberate: it's what makes the reasoning inspectable and defensible instead of a black box.

---

## Decision Point 1 — Identity Resolution (Stage 0)

**Inputs:** name (required), company/role/location/url (optional), results of a lightweight preliminary search.

**Rule:**
- Compute a match confidence per candidate found: `+40` if company matches, `+20` if role/title is consistent, `+20` if location matches, `+20` if the supplied URL resolves to the same entity. Max 100.
- If exactly one candidate scores ≥ 60 **and** no other candidate scores within 20 points of it → auto-proceed (this is what keeps a well-specified happy-path input fast).
- If two or more candidates score ≥ 60 and within 20 points of each other → **STOP. Trigger disambiguation.** Show the candidates with what distinguishes them, ask the user to pick or add a filter.
- If zero candidates score ≥ 60 → proceed with the highest scorer, but flag the run as `low_confidence_identity` — this is visible in the UI as a caveat, not hidden.

**Why this threshold design, not just "let the model decide":** a fixed, inspectable scoring rule means you can explain in the interview *exactly* why the system asked for clarification on one prospect and not another, rather than "the model felt uncertain." That's the difference between a product decision and a shrug.

---

## Decision Point 2 — What Counts As Usable Signal (Stage 4)

**Inputs:** extracted candidate facts, each tagged `{category, recency, source_url, source_score}`.

**Rule — a candidate fact is eligible to become the hook only if ALL of these hold:**
1. **Recency:** dated within the last 6 months, OR undated but from a source explicitly marked `topic=news` (assume near-current). Older than 6 months → deprioritized to a secondary list, never the primary hook.
2. **Category safety:** category is one of `funding`, `hiring`, `product_launch`, `executive_statement`, `company_initiative`. Categories `layoff`, `lawsuit`, `controversy`, `personal_life` are **hard-excluded from ever becoming the hook**, regardless of recency or prominence — this is a filter, not a preference the LLM can override.
3. **Specificity:** the fact must name a concrete detail (an amount, a role, a quote, a product name) — a vague fact like "company is growing" is rejected as not specific enough to write a non-generic line from.
4. **Source quality:** `source_score` (from Tavily's relevance score) above a minimum floor — a low-relevance scraped fragment isn't trusted even if it looks juicy.

**Selection:** among eligible candidates, pick the single one with the best combined (recency × specificity) — ties broken toward the more recent one.

**If zero candidates are eligible →** this is Edge Case 1 (below). Do not force a selection.

---

## Decision Point 3 — Draft Generation Constraints (Stage 5)

- The chosen hook's specific detail (the number, the name, the quote) **must appear verbatim or near-verbatim in the draft** — this is checked programmatically after generation (a simple substring/fuzzy-match check against the hook's key term), not just trusted. If the check fails, regenerate once with a stricter prompt; if it fails twice, fall back to Edge Case 1's honest-fallback path rather than ship an ungrounded draft.
- Draft must not contain the words "hope this finds you well," "reaching out," "I noticed" as an opener — a small explicit style blocklist against the most generic outreach tells, checked post-generation.

---

## The 4 Edge Cases — Exact Trigger, Exact Behavior, Exact Reasoning

### Edge Case 1 — No usable signal found
**Trigger:** Decision Point 2 returns zero eligible candidates (obscure prospect, thin public footprint, or all findings fail recency/specificity/safety checks).
**Behavior:** run status = `insufficient_signal`. System does NOT draft a fabricated-sounding "personalized" message. Instead it offers a clearly-labeled generic-but-honest draft (references the industry/role generically, states no specific hook was found) OR simply reports the gap and stops, your choice — I'd recommend the labeled generic draft, since it's still useful to the rep and honest about its own limits.
**Why this is correct judgment, not a failure:** a system that hallucinates a "personal touch" when there isn't one is worse than one that admits the gap — it's the same principle as a human analyst saying "I couldn't find anything solid on this one" instead of making something up to look thorough.

### Edge Case 2 — Negative news present
**Trigger:** the most recent/prominent fact found is category `layoff`, `lawsuit`, or `controversy` (per Decision Point 2's hard-exclusion).
**Behavior:** that fact is visibly logged as "found, excluded — reason: reputationally sensitive," and the system proceeds to the next-best eligible candidate (or falls to Edge Case 1 if nothing else qualifies).
**Why:** referencing a company's layoffs as a sales "hook" is not just tone-deaf, it can be actively harmful outreach — this is the single clearest "does this candidate understand real-world consequences" test in the whole build.

### Edge Case 3 — Stale signal only
**Trigger:** all available candidate facts are older than 6 months (Decision Point 2, rule 1).
**Behavior:** same path as Edge Case 1 — reported honestly as insufficient *current* signal, not silently drafted off year-old news presented as if it were fresh.
**Why:** referencing a "funding round" that closed 14 months ago as if it just happened reads as lazy/careless research, which is precisely the failure mode this whole tool exists to prevent.

### Edge Case 4 — Ambiguous identity
**Trigger:** Decision Point 1's disambiguation condition (two-plus close-scoring candidates).
**Behavior:** run pauses at Stage 0, UI presents the candidates with their distinguishing details, waits for user input via `POST /api/runs/{id}/resolve` before continuing.
**Why:** researching and drafting toward the wrong "John Smith" isn't a minor error, it's a completely wasted (or actively embarrassing) outreach — pausing to confirm identity is the correct trade of one extra click against a real failure mode.

---

## How This Shows Up In The UI (ties back to FR8)

Every one of these decisions must render as a plain-language line in the live run view and the run detail — e.g. *"Found: Series A funding, $12M, Sept 3 2026 → ELIGIBLE (recent, specific, safe)"* and *"Found: reported layoffs, Aug 2026 → EXCLUDED (reputationally sensitive category)."* This is what makes the demo defensible: you're not saying "trust the AI," you're pointing at a visible rule firing.

## Talking Points For The Interview (mapped 1:1 to the logic above)

- "Why did it ask for clarification on this prospect?" → point at Decision Point 1's scoring rule.
- "How do you know it won't just reference something from years ago?" → point at Decision Point 2's recency rule.
- "What stops it from referencing a layoff as a conversation starter?" → point at the hard-exclusion category list, and Edge Case 2.
- "What happens when there's genuinely nothing to say?" → point at Edge Case 1 and the honest-fallback design.
- "How do you know the draft isn't just generic with a name swapped in?" → point at Decision Point 3's verbatim-detail check.
