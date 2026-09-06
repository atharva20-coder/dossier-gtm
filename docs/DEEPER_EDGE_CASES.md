# Deeper Edge Case Analysis

> **Status.** This was written before the build, proposing edge cases worth
> handling. Two of them (A: stale employer attribution, B: syndication mistaken
> for corroboration) are now implemented — B as `collapse_syndicated`, extended
> so corroboration across independent source types still counts. Building against
> real data surfaced several the analysis did not predict, most of them about
> attributing a fact to the wrong *person* rather than the wrong fact. Those are
> in `DECISIONS.md`.
Dossier · Zamp Case Study · PS-3

The first 4 edge cases (insufficient signal, negative news, stale signal, ambiguous identity) are the ones anyone who reads the problem statement carefully will land on. This document goes one level deeper — the kind of failure mode you only catch by actually thinking about what goes wrong in *production*, with real people, not toy examples. Ranked by how much they demonstrate real judgment, not just coverage.

---

## Tier 1 — Genuinely sharp, worth actually building

### A. Stale employer attribution (different from stale *fact*)
Our current "stale signal" edge case catches an old *fact* (a funding round from 14 months ago). It does **not** catch a recent, true, well-sourced fact that's attached to the wrong *employer* — because the prospect has since moved on. Example: an article from 3 weeks ago says "Rahul Sharma, Head of Growth at ByteForge, on how they're scaling..." — completely current, completely real — except Rahul left ByteForge two months ago and now works at Quickcart. Referencing his old company's news as if it's current is worse than referencing nothing at all: it signals you didn't actually check who you're talking to, not just that you didn't research deeply.

**Why this is a different axis of the problem, not a repeat:** recency-of-fact and currency-of-employer are two independent failure modes. A system that only checks "is this recent" will confidently walk into this trap.

**How to catch it:** after identity resolution, run one targeted check — search for "{name} joined" / "{name} new role" — if a more recent job-change signal exists than the company-attributed fact being considered, flag the mismatch and either exclude that fact or surface a warning rather than silently drafting against a stale affiliation.

### B. Source syndication mistaken for corroboration
When 5 "sources" all report the same funding round, it's tempting to treat that as high-confidence corroboration. But most funding news is one press release redistributed verbatim across a dozen aggregator sites — 5 hits isn't 5 independent confirmations, it's 1 fact wearing 5 outfits. A naive system would over-trust it; a sharp one recognizes syndicated duplication and treats it as **one** data point, not five.

**Why this matters:** it's the difference between a system that looks smart because it found "a lot of sources" and one that's actually reasoning about information quality, which is exactly the kind of thing a non-technical stakeholder would never think to ask about but would respect once you explain it.

**How to catch it:** a simple near-duplicate-text check across retrieved sources (e.g. shared-sentence overlap above a threshold) collapses syndicated copies into one fact before they ever reach the judgment stage.

---

## Tier 2 — Real, worth naming as considered scope, not necessarily built this week

*(Brought up explicitly and deliberately in your interview narrative — "here's what I identified and consciously chose not to build in week one, and why" is itself a strong signal, arguably stronger than silently building a 6th half-baked feature.)*

### C. Personal-sensitive-but-positive signal
Our "personal_life" exclusion category currently implies obviously-bad content. But some publicly-posted personal news is positive on its face and *still* wrong to use as a sales hook — a pregnancy announcement, a health journey, a family event. The guardrail needs to be about "is this appropriate to reference in a cold business outreach," not "is this negative." Worth explicitly broadening the category's definition, even if the underlying mechanism doesn't change.

### D. Suppression / do-not-contact check
In a real production system, before any research even starts, you'd check the prospect against a do-not-contact or previously-bounced list. Not researching or drafting toward someone who opted out isn't an "AI judgment" problem, it's a compliance one — but it's exactly the kind of thing that matters enormously to Zamp's actual enterprise clients. A one-line mention that this would sit *before* Stage 0 in a real deployment shows you're thinking about the whole system, not just the clever AI part.

### E. Same-account coordination
If a rep uploads a whole account's worth of contacts (5 people at the same company), independently drafting outreach for each one risks 5 near-identical "congrats on your funding round!" messages landing in that company's inboxes within days of each other — which reads as spam/automation to the recipients even though each individual message was "personalized." A mature system would notice repeated company hooks across a batch and vary the angle or space out language.

### F. Re-run consistency
If a rep runs the same prospect twice (maybe by accident, maybe to double-check), should they get the same hook and a similarly-toned draft, or does non-determinism give them two different "personalized" messages that don't agree with each other? Worth a one-line mention that in production you'd cache the chosen hook per prospect for some TTL rather than re-deriving it fresh each time.

---

## Recommendation

Build **A (stale employer attribution)** into the actual running prototype — it's conceptually rich, cleanly demonstrable, genuinely different from your existing 4 cases, and directly shows you understand that "recent" and "correctly attributed" are two different things. That gets you to 5 real, deliberately-chosen edge cases, still well within "a few handled deeply" rather than "many handled shallowly."

Treat **B (syndication)** as a strong secondary build if there's time — it's a great one-liner even just mentioned in the video/interview even if not fully wired into the UI.

Treat **C–F** as your answer to "what would you build next" — a stronger answer than most candidates will have, because it shows you kept thinking past what you shipped.
