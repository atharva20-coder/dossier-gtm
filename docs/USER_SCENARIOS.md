# User Scenarios
Dossier · PS-3 · What real people actually do with this, beyond the demo path

The failure analysis asked "what breaks." This asks a different question:
**what does a real person actually do with this tool on a Tuesday, and where does it let them down even when nothing is technically broken?**

Most of these are not bugs. They're situations where the software works perfectly and is still unhelpful — which is the harder failure to see.

---

## 1. Who is actually using this (it isn't only an SDR)

The tool takes "a list of people at companies" and returns "a reason to talk to them." That shape fits far more jobs than outbound sales:

| User | What changes about their needs |
|---|---|
| **SDR / BDR** | Volume, speed, and *prioritisation* — which 12 of these 50 are worth today |
| **Founder doing their own sales** | Very low volume, very high care. Wants depth per prospect, will hand-edit everything |
| **Sales manager** | Doesn't run it — *reviews* it. Wants to see quality across the team, not one draft |
| **Recruiter** | Same mechanic, inverted: the "prospect" is a candidate, the hook is why *they'd* want to move. Job-posting signal becomes irrelevant, personal achievement signal becomes central |
| **Investor sourcing deals** | The funding signal we deprioritise as "saturated" is the *primary* signal for them |
| **Partnerships / BD** | Wants mutual-fit signal, not pain signal |
| **ABM marketer** | Works at account level, not person level — wants one hook per *company*, then variants per persona |

**Why this matters for the case study:** it shows the design isn't over-fitted to one persona, and it's an honest answer to "who else could use this?" The rules that would need to change per persona are the *intent weights* — which is a config change, not a rebuild. That's a good architectural answer.

---

## 2. Before the run — where the list actually came from

The demo assumes a clean list. Real lists have an origin story, and the origin predicts the failure:

| Where the list came from | What's true about it | What the tool should do |
|---|---|---|
| Exported from CRM | Job titles are 6–18 months stale; people have moved | Stale-employer risk is *high* — flag mismatches loudly |
| Conference attendee list | Names + companies, no roles, many juniors, many duplicate companies | Company-level caching pays off hugely; role-appropriateness matters |
| Scraped/bought list | Unknown quality, possible competitors and existing customers mixed in | Relationship-state check (§3) becomes essential |
| Hand-built by the rep | Small, high-intent, well-known to them | The rep will disagree with the hook more often — needs the runner-up |
| Inbound sign-ups | Warm, and the "cold outreach" framing is wrong entirely | Should not use cold-open phrasing at all |

**A scenario worth naming:** the rep uploads a CRM export where the `Company` column is the employer *at time of export*. Every row is stale by construction, and the tool would happily research the wrong employer for the entire list. That's not an edge case — it's a whole-list systematic error caused by data provenance.

---

## 3. Relationship state — the biggest blind spot in the current design

**The tool has no idea what relationship already exists with this person.** It treats every row as a cold stranger. In reality a typical list contains:

- **Existing customers** — sending a cold pitch to someone who already pays you is embarrassing and actively damages the account. This is the single worst outcome the tool can currently produce, and nothing in the pipeline prevents it.
- **Active open opportunities** — the AE is mid-negotiation; an SDR cold-emailing the same person undercuts them.
- **Competitors** — a competitor's employee in the list means we draft our pitch and hand it straight to them.
- **People already contacted recently** — the same rep emailed them three weeks ago. A second "congrats on the funding" is worse than silence.
- **Opted out / unsubscribed** — a compliance problem, not a taste problem.
- **The user's own colleagues** — internal test rows and teammates end up in lists constantly.

**What I'd do:** an optional `relationship` column in the upload (`customer` / `open_opp` / `competitor` / `contacted` / blank), checked *before* Stage 0 — because researching them at all is wasted spend, and drafting to them is the harm. It costs almost nothing to build and it's the difference between a demo and something you'd let a real team point at their book of business.

This is the scenario I'd raise unprompted in the interview, because it's the one where the software works flawlessly and still does damage.

---

## 4. During the run

| Scenario | Currently | Should be |
|---|---|---|
| Rep uploads 50 rows but only wants to run 12 promising ones | Run-all or one-at-a-time clicking | Select rows → run selected |
| Rep starts a 50-row batch and closes the laptop | Runs are persisted; interrupted ones marked | Resumable, with "23 of 50 done" visible on return |
| Rep watches the first 3 runs, sees the pattern, wants the rest to just go | Fine | Fine — but should show aggregate progress, not per-row only |
| Rep hits the disambiguation prompt and **doesn't know the answer either** | They're stuck | Offer "skip this one" — an unanswerable question shouldn't block the batch |
| Rep realises mid-batch the whole list is wrong | No stop control | Cancel batch |
| Credits run out at row 30 | Rows 31–50 correctly marked `research_failed` | Plus: stop the batch immediately rather than burning through 20 doomed rows |

---

## 5. After the run — the output has to *leave* the tool

**This is the biggest practical gap, and it's a workflow gap, not a technical one.**

Right now the drafts live in the app. A rep sends email from Outreach, Salesloft, Apollo, HubSpot, or Gmail. Copying 40 drafts out by hand one at a time is worse than not having the tool — this is precisely how good GTM tools die in week three.

**Minimum viable answer: export to CSV/XLSX** — one row per prospect with name, company, hook, source URL, and draft body, ready to bulk-import into any sequencer. It's an hour of work and it's the difference between "neat demo" and "I could actually use this."

I'd rank this as the highest-value remaining feature in the entire backlog, above any further AI sophistication.

Related after-the-run scenarios:
- **The rep edits a draft** → the edit must survive navigation (currently held in memory only)
- **The rep wants a different hook** → we compute a ranked list and discard the runners-up. Surfacing "use the next-best hook instead" is nearly free
- **The rep wants a tone change** ("shorter", "less formal", "mention we work with Swiggy") → regenerate with an instruction, same hook
- **The rep wants to know *why* a prospect got nothing** → needs an actionable answer ("searched funding/hiring/press, found nothing in the last 6 months"), not just a status chip
- **A prospect replies "how did you know that?"** → the rep must be able to produce the source in seconds. This is why clickable sources are an *adoption* feature, not decoration

---

## 6. Team scenarios (the tool is single-player today)

- **Two reps on the same team upload overlapping lists** — the same prospect gets researched twice and possibly emailed twice, by two people, with the same hook. From the prospect's side that reads as spam from one company.
- **Five colleagues at one target account** in an enterprise motion — the correct behaviour isn't to avoid duplication, it's to *deliberately vary the angle by persona* (CFO: cost/risk, VP Ops: throughput, IT: integration) off one shared research pass. Same cost, five differentiated messages.
- **A manager wants to see what the team is sending** — the dashboard currently answers "what did I run," not "what is my team sending in my name."

---

## 7. Over time (the lifecycle nobody demos)

- **The same prospect, three months later** — the hook we used is now stale. Re-running should know we've contacted them and pick a *different* angle, not repeat the funding line.
- **A hook that was true becomes false** — the funding round gets rescinded, the exec who was hired leaves. Drafts written yesterday may be wrong today.
- **The list ages** — a list uploaded in January is substantially wrong by June. Roles change ~20%+ annually.
- **The rep's own product changes** — messaging drifts; drafts generated against last quarter's positioning are off-message.

---

## 8. Trust moments — the specific instants where a user decides to keep or abandon the tool

1. **First run.** If run #1 produces something obviously generic or wrong, there rarely is a run #10.
2. **First time they check a source.** If the source doesn't say what the hook claims — over. This is exactly what grounding verification protects.
3. **First time a prospect pushes back** ("we never raised that"). Unrecoverable.
4. **First time it says "I found nothing."** Counter-intuitively, this *builds* trust — it proves the tool isn't just always confidently producing something. Honest failure is a feature.
5. **First time they edit heavily.** If every draft needs a rewrite, the time saving is illusory and they'll quietly stop.

---

## 9. What these scenarios actually change

**Would change the build if there were time (ranked):**
1. **Export to CSV/XLSX** — without it the tool is a dead end
2. **Relationship state column** — prevents the worst possible output (cold-pitching a customer)
3. **Select-and-run subset** — matches how anyone actually works a list
4. **Runner-up hook** — already computed, currently thrown away
5. **"Skip" on an unanswerable disambiguation** — unblocks the batch

**Genuinely better as interview answers:**
Team/multi-user coordination · persona-differentiated hooks per account · lifecycle re-contact awareness · tone-instruction regeneration · manager review view.

---

## The scenario I'd lead with

> "The tool as built will happily research an existing customer and write them a cold pitch. Nothing is broken when it does that — every stage works perfectly — and it's still the worst thing the product can do. That's why I'd put a relationship-state check *before* the pipeline rather than more intelligence inside it."
