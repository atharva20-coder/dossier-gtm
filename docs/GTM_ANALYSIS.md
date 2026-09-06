# GTM Analysis
Dossier · PS-3 · What this looks like to a revenue team, not an engineering team

The production doc asked "what breaks." This one asks a different question: **does this thing actually book meetings, and will a rep still be using it in week three?** Most AI GTM tools fail on the second question, not the first.

---

## 1. The reframe: this is a trigger-detection tool that happens to write emails

Reps don't want personalized emails. They want meetings. Personalization is a means, and it's not even the biggest lever. In order of impact on outbound results:

1. **Targeting** — is this even the right account/person? A perfect email to the wrong person fails 100% of the time.
2. **Timing / trigger** — is there a reason to reach out *right now*?
3. **Offer relevance** — does what I sell connect to what they're dealing with?
4. **Personalization** — the craft of the message.
5. Channel, cadence, follow-up discipline.

Our system's real output isn't the draft — it's **the hook**, which is a *trigger*. That reframes the product: the most valuable thing on screen may not be the email body at all, it's **"here are the 12 of your 50 prospects who have a live reason to be contacted this week, and why."** Prioritization is worth more to a rep than prose.

**Design consequence:** the dashboard should be sortable by hook strength/recency, not just run status. The rep's real question is "who do I work today?"

---

## 2. The biggest design gap: the system doesn't know what you're selling

Right now judgment ranks hooks by recency × specificity × safety. Nowhere does it consider **what the rep sells**. That means it optimizes for "most interesting fact about this prospect" rather than "fact that best connects this prospect to my solution" — which is the actual objective.

Concretely: for a vendor selling AP automation, a job posting reading *"hiring 5 accounts-payable clerks"* is a **far** better hook than a $12M Series A — because it's direct evidence of the exact pain they solve. Our current logic would pick the funding round every time, because it's more recent and more "specific."

**Fix:** a one-time seller configuration — what you sell, who you sell to, what pain you solve — passed into the judgment stage so hook selection optimizes for *connection to the offer*, not raw prominence. This is a small input change with a large quality change, and it's the thing I'd add first from this entire document.

---

## 3. Triggers ranked by buying intent (not by how flashy they are)

| Trigger | Buying intent | Why |
|---|---|---|
| New exec hired into the relevant function | **Highest** | New leaders change vendors in their first 90 days; they're explicitly mandated to fix something |
| Job postings for roles adjacent to your pain | **Very high** | Direct evidence of the problem, and dramatically under-used by most reps |
| Funding round | High but saturated (see §4) | Budget exists — everyone knows it |
| Expansion into a new market/geo | High | Operational strain, new compliance surface |
| Product launch | Medium | Creates load, but may not map to your offer |
| Award / "best places to work" | **Low** | Feels personal, signals nothing about need |

Our current model has no notion of intent tiering — it treats a hiring post and an award as equivalent categories. They aren't.

---

## 4. The counterintuitive one: the most obvious hook is the worst hook

A founder who just raised gets **hundreds** of congratulatory emails in the following two weeks. Funding is the single most saturated trigger in B2B outbound. By optimizing for the most prominent recent signal, our system is deliberately walking every rep into the most crowded moment in that prospect's inbox.

Sophisticated GTM teams do the opposite — they use the *second-order* signal precisely because nobody else found it. "I saw you're hiring three ops leads in Austin" outperforms "congrats on the Series A" for exactly this reason.

**Design consequence:** hook scoring should include a **saturation penalty** — deprioritize the trigger everyone else is also using. This is genuinely non-obvious and it's the GTM insight I'd lead with in the interview, because it inverts the naive assumption the whole tool was built on.

---

## 5. "Insufficient signal" is not an edge case — for most real lists it's the *modal* outcome

This is the most important thing in this document.

Our test set is founders of newsworthy startups. A real mid-market prospect list is not that. Most B2B companies — the $20M–500M revenue mid-market where most deals actually live — generate **almost no press coverage in any given quarter**. Run a realistic 200-row list and "no recent news" won't be an edge case at 5%; it'll plausibly be 40–60% of rows.

That means a tool whose value proposition collapses when there's no news is a tool that fails on the majority of a typical list. The fallback path deserves *more* design investment than the happy path, not less.

**The fix is tiered signal availability:**

| Tier | Signal | Availability |
|---|---|---|
| **Always-on** | Job postings, website/careers page copy, company size/industry, tech stack, leadership page | ~Every company |
| **Episodic** | Funding, exec hires, product launches, press | A minority in any given window |

The system should reach for episodic signal first, then **fall through to always-on signal** — not fall through to "sorry, nothing found." A hook built from "you've had a Head of Revenue Operations role open for 6 weeks" is real, specific, current, and available for almost any company. That's the difference between a demo tool and a deployable one.

---

## 6. Adoption is the #1 failure mode of GTM tools — not quality

Reps live inside Outreach, Salesloft, Apollo, HubSpot. A standalone app that produces drafts requiring copy-paste has a well-documented outcome: enthusiastic week one, abandoned by week three. Quality doesn't save it.

Realistic integration story to state explicitly: drafts land in the sequencer/CRM as a task or a custom field on the record, in the rep's existing workflow. For the case study I'd keep the standalone UI (correct scope for a week), but naming this shows you understand why good GTM tools die.

**Related:** the buyer and the user are different people. The SDR uses it; the VP Sales/RevOps buys it. The rep cares about "does this save me time and not embarrass me." The buyer cares about pipeline per rep, new-rep ramp time, and consistency across the team. The dashboard should speak to the second audience too.

---

## 7. Rep trust is a one-strike system

Connect this back to the hallucination risk from the production doc, but with the GTM consequence attached: if a rep sends a draft citing a funding round that didn't happen, and the prospect replies "we never raised that" — that rep never opens the tool again, and they tell the team. **One visible hallucination doesn't cause an incident, it causes churn.**

This is why showing sources isn't a "transparency nice-to-have" — it's an **adoption feature**. The rep needs to click through and verify before sending, and needs to be able to defend the claim if it comes up on the call. Same reason the draft must be editable in their own voice: reps rewrite anything that doesn't sound like them, and a tool they rewrite entirely is a tool they'll stop opening.

---

## 8. What this motion actually is: enterprise/mid-market, multi-threaded — not spray-and-pray

Deep per-prospect research is uneconomical in a high-velocity SMB motion (hundreds of touches/day, low ACV). It's *very* economical in enterprise/mid-market, where an account is worth five to seven figures and you contact 5–8 stakeholders per account.

That has a direct product implication: in enterprise, **multi-threading** means the same account, different personas — and the hook should be **differentiated by role**. CFO cares about cost and risk; VP Ops cares about throughput and headcount; IT cares about integration. Same company trigger, three genuinely different angles.

Note this flips the "same-account collision" risk from the production doc into a feature: instead of avoiding duplicate hooks across colleagues, deliberately generate *role-differentiated* angles from one shared piece of research. It also halves the research cost per account.

---

## 9. ROI math (the actual thing a buyer asks)

- Manual: ~20 min research per prospect. 200 prospects/month = **~66 hours/rep/month**.
- Dossier: ~6 seconds and roughly **$0.02–0.05 per prospect** in API cost.
- Even at a conservative loaded SDR cost, the research step alone is a **two-to-three-order-of-magnitude** cost difference.

But the stronger argument isn't cost savings — it's **throughput at constant quality**: the same rep covers 3–5× more accounts without dropping to generic messaging, or holds volume constant and lifts reply rate. Frame it as pipeline generated per rep, not hours saved, because that's the number the buyer is compensated on.

Worth stating plainly in the demo: *"this costs about two cents per prospect and replaces about twenty minutes."*

---

## 10. Constraints a GTM team will raise that engineers don't think about

- **Deliverability**: 500 drafts ≠ 500 sends. Beyond ~50/day/mailbox you burn domain reputation. Structurally identical "personalized" emails still trip spam filters — batch homogeneity is a deliverability problem, not just an aesthetic one.
- **Follow-ups**: ~80% of replies come from touches 2–5, not touch 1. We draft one email. A real deployment drafts the *sequence*, or at minimum admits touch 1 is a fraction of the job.
- **Brand/message control**: marketing will not accept an AI writing arbitrary claims about the product. Needs approved messaging/value-prop constraints as an input — which conveniently is the same seller-config from §2.
- **Suppression lists**: active opportunities, existing customers, and competitors must never be researched or drafted to.

---

## 11. What a GTM buyer will ask in the demo — and the answer

| Question | Answer |
|---|---|
| "How do I know it won't embarrass my reps?" | Grounding verification + visible sources on every claim; negative/sensitive news hard-excluded |
| "What about prospects with no news?" | Tiered fallback to always-on signal (job posts, careers page) — §5 |
| "How does it fit my reps' workflow?" | Standalone for now; designed to push into the sequencer/CRM |
| "What's it cost?" | ~2¢/prospect vs ~20 min of rep time |
| "Can I control the messaging?" | Seller config constrains claims to approved value prop |
| "How do I know it's working?" | Rep edit distance, send rate, hook acceptance rate — before reply-rate data matures |

---

## The GTM points I'd lead with

1. **"The most obvious hook is usually the worst hook"** — funding is the most saturated trigger in outbound; the second-order signal is what actually differentiates. Our scoring should penalize saturation, not reward prominence.
2. **"For a realistic mid-market list, 'no recent news' isn't an edge case — it's close to half the list."** That's why the fallback to always-on signals like job postings matters more than the happy path.
3. **"The system currently doesn't know what the rep sells"** — so it optimizes for the most interesting fact rather than the most *connecting* one. That's the first thing I'd fix.
4. **"Adoption, not accuracy, kills GTM tools"** — sources visible and drafts editable are adoption features, not transparency features.
