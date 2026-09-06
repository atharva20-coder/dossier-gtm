# Decisions and edge cases

Every non-obvious choice in this codebase, why it was made, and what was
rejected. One line each, plain terms.

Format: **What** — why. *Rejected:* the other option, and why not.

Most entries here exist because something went wrong first. Where that is the
case the entry says what the symptom looked like, because the symptom is what
you will recognise if it comes back — not the fix.

**How this relates to the other docs.** `DECISION_LOG.md` (D1-D14) records the
project-shaping choices made before the build: which problem, which model, which
search provider. This file records what was decided *inside* the code once it met
real data — the failures that only appear when you run it against a real person
with a common name. `DEEPER_EDGE_CASES.md` proposed edge cases in advance; the
ones marked there as built are cross-referenced below.

---

## Finding the right person

**A person-level fact must come from a resolved profile or a source naming their
employer.** — Web search finds namesakes; nothing else caught them.
*Rejected:* trusting the grounding stage — it checks a fact is IN the source, not
that the source is about the right person, so a real podcast by a different
Shubham Verma passed every check.

**Every fact records which individual it is about, and a fact about someone else
is dropped at every level.** — A LinkedIn post congratulating an intern was
correctly tagged as company-level, so the namesake gate never looked at it, and
it won on score.
*Rejected:* only checking person-level facts — that is precisely the gap that
let it through.

**Name matching ignores honorifics and allows a missing middle name.** — Press
writes "Dr. Priya R. Nair" for the same person the sheet calls "Priya Nair".
*Rejected:* exact string match — it would drop the prospect's own facts.

**A shared surname is not a match.** — "Priya Nair" and "Rahul Nair" are two
people.

**The traversal never follows another person's name.** — Colleagues and
co-founders are doorways out of the prospect's story.
*Rejected:* letting downstream gates clean it up — cheaper not to spend the
search credit at all.

**A company that looks like a personal name is rescued by context.** — "Acme
Payments" has the same shape as "Ravi Shah"; the prospect's known employers are
checked before guessing.
*Rejected:* a bigger word list — no list separates those two reliably.

**Facts about a different company are dropped by comparing subject to employer.**
— Articles compare competitors constantly.

---

## Ranking what was found

**LinkedIn beats X beats web, for facts about the PERSON.** — What someone
publishes themselves is first-party; an article about them is second-hand.

**Web is not penalised for facts about the COMPANY.** — A journalist verifying a
funding round is not worth less than the company's own post.
*Rejected:* one ranking for both — it would have discarded good press coverage.

**A claim carried by both a first-party post and independent reporting scores
higher.** — Two parties who did not copy each other is real confirmation.
*Rejected:* counting sources — ten aggregators carrying one press release is one
fact wearing ten outfits, which is why syndication collapsing exists.

**Two social sources are not corroboration of each other.** — Same person, two
platforms, one claim.

**Provenance multipliers are small.** — They break ties between comparable
hooks; recency and buying intent still decide.
*Rejected:* large multipliers — a stale LinkedIn post would outrank fresh news.

**Syndication collapsing keeps every source URL, not just the winner's.** —
Otherwise corroboration cannot be detected after the merge.

**The person-signal provider's URL counts as LinkedIn evidence.** — It serves
LinkedIn profile and post content under its own hostname; ranking it as "web"
would underrate the most reliable source in the system.

---

## How research runs

**Research walks a graph outward from the prospect, wave by wave.** — A person
researching a lead follows what they learn; a flat query list is written before
anything is known.
*Rejected:* one bigger batch of queries — more of the same generic results.

**Every query keeps the prospect's name in it.** — Following the "Boston
Consulting Group" node without the anchor stops being research about the person
and becomes research about BCG.

**The query budget is rationed across depths.** — Depth 1 is always the widest
frontier, and spending greedily there left one credit for everything deeper, so
the graph came out one level deep.
*Rejected:* a bigger budget — it would have been eaten by the same first wave.

**A proposed entity is only followed if a source names it alongside the
prospect.** — Searching `"<person>" "<former employer>"` returns pages ABOUT
that employer, which name dozens of unrelated organisations; the traversal
followed Accenture, AWS and the University of Allahabad, and five of those
returned no sources at all.
*Rejected:* trusting the model's instruction not to propose unrelated things —
it lists what is on the page, which is what the page is about.

**A new entity is attributed to the node whose own results mentioned it.** —
That is what makes the picture a chain of reasoning rather than a fan-out.
*Rejected:* labelling nodes "found at depth 2" — that is a description, not a
parent, and every node ended up hanging off the root.

**Only nodes that actually got a query are marked explored.** — The trail records
what was done, not what was considered.

**Every node carries the sentence that put it there and the URL it was read
from.** — "Why is IIT Kharagpur on this graph?" was only answerable by querying
the database by hand; a research tool nobody can audit is a research tool nobody
should trust. Clicking a node now shows the quote and links the source.
*Rejected:* logging it server-side — the person who needs the answer is looking
at the screen, not the logs.

**Employer names are cut at the first lowercase word that is not a name
connector.** — The `at <Org>` pattern ran past the name into prose, producing
seeds like "Dunzo from Product, Engineering, Design and Analytics divisio", so a
search credit was spent on a sentence fragment.
*Rejected:* matching against a list of known company names — there is no such
list for every employer a prospect might have had.

**The traversal is skipped entirely unless identity was confirmed.** — Expanding
a graph around an unconfirmed name is the namesake problem with a bigger budget.

**LinkedIn and X are queried by `site:` search, not by API.** — Neither has a
usable self-serve API and Proxycurl shut down; a site-scoped query reads the
public index instead.

**A failed "what next" call ends the traversal quietly.** — It is an enhancement
on research that already succeeded; it must never fail a run.

**The prospect's current employer is not explored as a thread.** — It is already
the anchor in every query.

**Extraction reads sources in concurrent batches.** — Research returns 40-50
sources and one call could only ever be shown ten of them, while that one call
grew slow enough to hit the model timeout under load.
*Rejected:* a longer timeout — it would still have read only ten sources.

**One failed batch loses a slice of sources; all of them failing fails the run.**
— "No facts found" must never be how an outage is reported.

---

## Storage and state

**The database is the only memory; the browser keeps nothing.** — A prospect list
living in React state vanished on refresh, losing a 200-row upload.
*Rejected:* localStorage — two places holding the same truth is how they end up
disagreeing.

**An upload writes its rows before anything is run.** — That is what a reload
restores from. Creating a row costs nothing and starts no work.

**A page load asks the server for the latest batch.** — No client-side id to
remember, nothing to go stale.

**A finished run is never researched again automatically.** — Everything a run
produced is stored complete — facts, sources, graph, draft — so reloading reads
it back instead of re-deriving it. "Run all" covers only prospects that have
never been run, and re-running one asks first, because the cost is a fresh round
of search and model credits to arrive at what the database already holds.
*Rejected:* re-running on load to guarantee freshness — the freshness gained is
worth less than the quota it burns, and staleness is visible from the run date.

**Overriding the judge re-drafts from stored facts and spends no search
credits.** — The rules are wrong sometimes: a hook can be accurate, recent, and
still one no rep would send. Excluding facts or naming one costs a single
drafting call, and the evidence stays identical to what was reviewed.
*Rejected:* re-running the pipeline with the fact excluded — a fresh round of
search to reach a decision already made on facts already in hand.

**Fact ids are a hash of the fact text, not a position in the list.** — The list
is re-derived on every judgment, so an index would silently point at a different
fact the moment anything upstream changed.

**Re-running resets the existing row instead of creating a new one.** — One
prospect, one row, so the reloaded batch matches the screen.
*Rejected:* a new row per attempt — the batch listing would fill with duplicates.

**`relationship` is a stored column.** — The pipeline refuses to draft for
customers and competitors and reads that decision from the row; while it was
only in memory, any path that rebuilt the prospect from the database lost it.

**One connection pool per process, sized from real limits.** — Two pools at the
same database double the connection budget for no benefit.

**The pool is rebuilt when the event loop changes.** — asyncpg binds sockets to
one loop, and a warm serverless instance handed a fresh loop fails obscurely
instead of reconnecting.

**Stats are one query with `count(*) filter`, not six.** — Same answer, one scan.

**Stale runs are retired by age, not swept at startup.** — On serverless a cold
start proves nothing about what other instances are running right now; sweeping
would kill live work.

**Schema is applied once from a file, not on every boot.** — Serverless starts
constantly and concurrently; DDL per boot is wasted round trips and a race.

---

## Deployment

**Runs execute inside the request that starts them.** — Serverless makes no
promise that work outliving its response finishes, so a detached task is a run
that silently disappears.
*Rejected:* `asyncio.create_task` — worked locally, would have lost runs in
production.

**Progress is polled from stage rows; there is no SSE.** — A function cannot hold
a queue in memory between invocations, and the rows were being written for
durability anyway.
*Rejected:* keeping SSE and adding polling as a fallback — two code paths for
one job.

**Create and execute are separate calls.** — The client needs the run id to poll
with before the work starts, and the work takes longer than a response should.

**Disambiguation parks the run instead of waiting.** — Nothing can block in
memory for a human across requests; `/resolve` restarts the pipeline with the
company filled in, which makes identity short-circuit.
*Rejected:* a resume state machine — restarting costs one cheap pass and no new
code path.

**The function is pinned to the database's region.** — The transaction pooler
forbids prepared statements, so each query is ~2 round trips and a run writes
~20 rows; 400ms of distance was ~16s per run.
*Rejected:* leaving the default region — the app would have been as far from the
database as a laptop is.

**Supabase transaction pooler, port 6543, prepared statements off.** — Serverless
opens and discards connections constantly; the direct connection exhausts the
instance. Transaction mode hands out a different backend per transaction, so a
prepared statement goes missing intermittently under load.

**`?pgbouncer=true` is stripped from the connection string.** — It is a Prisma
flag; asyncpg sends unknown parameters as server settings and the connection
fails.

**The password is percent-encoded.** — A literal `@` ends the userinfo part of
the URL and the host parses as whatever follows it.

**The platform healthcheck is `/api/ping`, not `/api/health`.** — `/api/health`
calls Tavily, Gemini and the provider for real; a host probing it on every deploy
and restart would spend search credits to confirm the app is up.

**Fields the pipeline owns are hidden from the model's JSON schema.** — Leaving
them in invites the model to invent values, and an invented source URL is exactly
what grounding exists to prevent.

---

## Access and hardening

**One shared access key, exchanged for a signed HttpOnly cookie.** — Every
endpoint spends credits or returns prospect data; an open URL is someone else's
quota and your prospect list.
*Rejected:* real accounts — user tables, resets and email for a tool that has one
team and no owner column yet.

**No key configured means the gate is open, and startup warns every time.** —
Local development stays frictionless; an accidentally public deployment says so
on every boot instead of failing silently.
*Rejected:* defaulting to closed — it would break `uvicorn` locally for no gain.

**The session cookie is HttpOnly and never read by JavaScript.** — A token this
code can read is one an injected script can read.

**`Secure` follows the request scheme rather than being hardcoded.** — Hosts
terminate TLS upstream and forward plain HTTP, so trusting `url.scheme` marks a
genuinely-HTTPS session as insecure; hardcoding it on means a plain-HTTP
deployment logs in successfully and the browser then never sends the cookie
back.

**The cookie signature covers its own expiry.** — Otherwise a client extends its
session by editing the cookie.

**Key and token comparisons are constant-time.** — A timing difference leaks the
key one character at a time.

**Failure reasons stored on a run are the exception TYPE, not its text.** —
Driver errors routinely contain the connection string, and this field is
displayed, exported to CSV and kept. The detail goes to the log instead.

**Upload size is checked on `Content-Length`, before parsing.** — The row cap
runs after pandas has read the file, by which point a large upload has already
taken the memory.

**Export can be scoped to one batch.** — The unscoped form returned every
prospect ever run.

**The pool is closed on shutdown.** — A severed pooled connection is held
server-side until it times out, and the pooler's client budget is small enough
that leaking a few per deploy is a real ceiling.

**No API response is cacheable.** — A response with no `Cache-Control` at all is
heuristically cacheable and browsers do cache it: re-reading a run right after
the assistant rewrote its draft returned the copy from before the rewrite, so
the new message never appeared and a reload appeared to lose it. `no-store` on
everything under `/api/`.
*Rejected:* `no-cache` — it permits storing and revalidating, and nothing this
API returns is worth re-serving.

**The assistant's reply carries the updated run; the UI uses it.** — Re-fetching
instead put a network round trip between the answer and the message updating,
which read as the rewrite not having happened.

---

## Input handling

**The LinkedIn URL column is matched on substring, not an exact header list.** —
"LinkedIn Profile URL", "linkedin_url" and "LI link" all missed an exact-match
list, so the URL never reached the resolver and research fell back to fuzzy
name-and-company matching.
*Rejected:* enumerating more header names — the variants are endless.

**Header separators are normalised before matching.** — `LinkedIn_Profile-URL`
and `linkedin profile url` are the same header to whoever typed them.

**A pasted profile URL fills in the name, but never overwrites a typed one.**

**Sales Navigator links are accepted and labelled as such.** — They identify the
lead; a `/in/` URL resolves more reliably, and the UI says so.

**Word-boundary matching when filtering location words out of employer names.** —
A substring test on "india" silently discarded every Indian institution, because
it is inside "Indian".

**Field labels are cut off extracted employer names.** — Profile text runs fields
together, so a name absorbs the label of whatever follows it.

---

## Interface

**A prospect opens as a full page, not a side drawer.** — The research graph is
the only view of how the system reached its answer and can be a dozen nodes
across three levels; a drawer showed it four nodes at a time.
*Rejected:* keeping the drawer and adding a graph tab — the graph is the reason
to open a prospect, not a secondary view of it.

**The evidence panel is docked beside the graph, not floating over it.** — A
floating panel covers the thing it describes, and the two are meant to be read
against each other. It sticks and scrolls on its own so a long evidence list
does not drag the map out of view; below `xl` the columns stack, map first.

**Clicking a graph node shows the sentence that put it there.** — See "Every
node carries the sentence that put it there" above.

**Below 1024px the four columns become four tabs, chosen in JS not CSS.** — The
obvious `hidden lg:flex` on two copies of the tree mounts every pane twice, so
every request they fire on mount runs twice and the hidden copy drifts out of
sync with the visible one.
*Rejected:* CSS-only — cheaper to write, but `SendBar` alone would poll the
Gmail status twice per lead.

**`h-dvh`, not `h-screen`.** — Mobile browsers count their collapsing address
bar in `vh`, so `h-screen` puts the pinned chat box underneath the toolbar.

**The message column's top search bar is hidden below `lg`.** — It duplicates
the pinned chat box and costs 64px of a phone screen; the duplicate was already
flagged on desktop, where the room exists to keep it.

**Tone chips send an instruction down the existing assistant path.** — 😎 Casual
and ✂️ Shorter are plain instructions into the same `rewrite_message` tool a
typed request uses, so a tone change is drafted, saved and learned from
identically.
*Rejected:* a dedicated tone endpoint — a second rewrite path to keep in step
with the first, for no behaviour the first does not already have.

**Every tone instruction ends by pinning the facts.** — "Make it casual" is
exactly the kind of open instruction a model answers by inventing a friendlier
detail.

**The bottom toolbar was deleted rather than made responsive.** — It was a copy
of the toolbar directly above the same card.

**The palette is CSS variables named by role, one per shade that existed.** —
Collapsing similar greys into a semantic scale would have been tidier and would
have changed the light theme the design was matched against pixel by pixel;
one-to-one means light mode renders byte-identical.
*Rejected:* rewriting the panes onto shadcn's `bg-muted`/`text-muted-foreground`
tokens — ~180 judgment calls, each a chance to shift the light design.

**Dark is recomputed, not inverted.** — A straight inversion collapses `#FAFAFA`
and `#FFFFFF` into one flat black, and the message stops reading as a card on a
pane.

**The theme class is applied in `main.tsx`, before React renders.** — A provider
cannot run early enough to stop a dark machine flashing a white screen, and
`/dashboard` renders no rail to hold the switcher.

**The switcher is one cycling button: system → light → dark.** — Three radio
rows do not belong in a 52px icon rail. It replaced the collapse button, which
was never wired to anything.

**An explicit theme choice ignores the OS afterwards.** — Only "system" tracks
`prefers-color-scheme`; choosing dark and having it snap back at sunrise is not
what choosing it meant.

**Every draft stores the identity that wrote it.** — Without it the interface
names whichever identity is active as the author of a message a different one
wrote, and switching identity looks like it did nothing to leads already
drafted. Name stored alongside the id, because an identity can be deleted and
the draft it wrote still has an author.

**Switching identity offers to redraft only the lead on screen.** — Redrafting
the whole list would spend a model call per lead and silently replace drafts the
user may have edited by hand.
*Rejected:* redrafting nothing — that was the state that read as broken.

**The rail is the only identity switcher.** — One click switches; it no longer
also opens the settings dialog, which put a modal over the lead every time you
wanted the other voice. The dialog edits whoever is active.

**Rewrite and Re-research are two labelled buttons that state their cost.** —
One was hidden until you had changed something, the other was an unlabelled icon
in a different pane, and neither said which one spends credits.

**A disabled button that has never done anything was deleted, not kept.** — It
is not a feature, it is a question the user has to ask.

**Addresses are graded A–F before a send, never verified.** — Confirming a
mailbox exists means probing the receiving server, which most providers refuse,
rate-limit, or answer "yes" to for everything. Every check is local except one
MX lookup, so grading is free, instant, and never contacts the recipient.
*Rejected:* a verification API — a paid dependency for an answer that is
frequently wrong anyway.

**Only an F blocks a send.** — An F is a domain that provably cannot receive
mail. A shared inbox or a personal account is a judgment the user is allowed to
overrule, and often should.

**MX is a raw DNS packet, not dnspython.** — One packet out, one in, against
forty lines of struct parsing; the alternative is a deployment dependency for
that. A failed lookup returns unknown rather than false, so a blocked UDP port
never grades a good address as dead.

**The grade lookup runs in a thread.** — It is a blocking socket read on the
request path, the same reason `smtplib` runs in one.

**Accounts are a first-class input, not just people.** — Every stage assumed a
named person, which is right for a CRM export and wrong for a target account
list — the more common thing a GTM team actually has.

**A contact with no source URL is dropped, not shown greyed out.** — A name the
model produced without a page behind it is a name it invented. The source URL
must also be one of the results actually retrieved.

**A domain typed into the company box does not become the company name.** — The
drafting prompt reads that field, and addressing someone at "zamp.finance" is
how a message announces it was automated.

**A company domain must match the company's name exactly, as a slug.** — Token
overlap resolved "Zamp" to zamp-racing.com, a helmet manufacturer, and then
derived four confident A-grade addresses at the wrong company. A wrong domain is
worse than none: no domain shows a message, a wrong one shows an answer.
*Rejected:* fuzzy matching — the failure it causes is silent and confident.

**The domain comes from pages this lead's research already read, before any
search.** — Those pages were retrieved while confirming this person at this
company, so they are anchored to the right one. A bare name search is not.

**Directory sites can never be a company domain.** — The top result for a
company name is very often its LinkedIn page, and deriving addresses from that
gives addresses at linkedin.com.

**Addresses are found by a waterfall, and every step reports hit, miss or
skipped.** — "Found nothing" and "never looked" are different answers, and a
pipeline that conflates them cannot be debugged or trusted.

**Order is by confidence: what someone wrote down beats what we calculated.** —
Only the last step derives anything, and it is labelled derived the whole way to
the screen.

**A derived address is never filled into the send field.** — An address found on
a page is; arithmetic is not. Auto-filling a guess is the app putting a stranger
in front of a real person under the user's name.

**The provider allowance is never spent looking for a field it does not
promise.** — The cached profile is read for an address; a fetch is not made for
one.

**The grader and the contact finder compare domains the same way.** — They did
not, and the finder accepted north-wind.com for "Northwind" while the grader
said the domain did not match, on the same screen.

**A job change is detected by comparing what research found to the row as
imported.** — The resolver already knew someone's current company and title and
discarded them whenever the row had values — exactly the case where a CRM row is
stale.

**Only a high-confidence, resolved match may report a move.** — A shaky match
claiming a new employer is more likely the wrong person than a real move, and
acting on it overwrites a correct row with a stranger's job.

**"VP, Sales" and "Vice President of Sales" are the same job.** — Without
reducing titles to their content, every re-run of a CRM export reports a change
that is a difference in house style.

**Priority needs both fit and an angle; a job change outranks both.** — A
perfect-fit lead with no angle is not the one to start with, and a good angle at
a company you do not sell to is a wasted sentence. A fresh move decays in weeks.

**Priority is computed on the server and sent with the row.** — Deriving it
again in the UI is how the screen and the CSV start disagreeing.

**Sorting by priority is opt-in.** — The default order is the one the user built
by adding and uploading; silently rearranging it is how a list stops being
somewhere you can find the row you saw a minute ago.

**Export carries the address, its grade, and what has already been sent.** — A
CSV that cannot say which rows already went out is one you cannot safely import
into a sequencer.

**The CSV grades addresses without DNS.** — One export can hold a thousand rows,
and a lookup each would turn it into a minutes-long request.

## The dashboard

**Charts are hand-drawn SVG, not a chart library.** — Three forms of a few dozen
lines each, against ~180KB of runtime and a second dependency to keep in step
with the theme.
*Rejected:* Recharts/Chart.js — the bundle is already over the warning threshold.

**Every colour comes from a CSS variable, so the palette follows the theme.** —
Dark is re-stepped for the dark surface, not flipped: on a dark ground the
STRONGEST step of an ordinal ramp is the lightest one.

**The palette was validated, not chosen by eye.** — The ordinal ramp is one hue,
monotone in lightness, pale end clearing the surface at 2.11:1 light and 2.25:1
dark; the single-series hue clears 3:1 against both. Colourblind-safety is
computable, so it was computed.

**Outcomes use an ordinal ramp, not a categorical palette.** — A fact about the
person is strictly better than one about their company, which is strictly better
than nothing. One hue in three steps says that; four different hues would say
the outcomes are merely different.

**No dual axes, ever, and one measure per chart.** — Two measures of different
scale get two charts.

**A legend whenever there is more than one series; none for a single series.** —
The title names a lone series, and a legend box for it is furniture.

**The table under the charts is not redundant.** — A chart that cannot be read as
a table is unreadable to anyone the colours fail, and it is where the exact
values live.

**Every figure is computed from the runs themselves.** — A counter that can drift
from the rows it describes is worse than no counter.

**Empty panels say they are empty rather than showing a zero.** — Runs made
before drafts recorded an author have no author, and inventing one would be
attributing a message to someone who did not write it.

---

## Research inside a campaign

**Every contact is researched; the limit is the budget, not a count.** — A count
is a guess about cost. The month's search allowance is the actual constraint, so
that is what governs.

**The app counts its own Tavily searches.** — Tavily meters monthly and exposes
no endpoint to ask what is left, so the only way to warn before a campaign dies
part-way is to count what is spent. Counting is an under-estimate — a search
made outside this app is invisible — which is the right direction to be wrong in
for a budget.

**The budget is checked before each contact, not once at the start.** — A run
that begins with room can still run out, and stopping cleanly with a reason
beats failing halfway with a provider error.

**A reserve is held back.** — New research stops below 40 remaining, so
discovery and contact search on a campaign already under way can still finish.

**Contacts are researched concurrently, bounded at three.** — Each one is
minutes of waiting on someone else's API; in series a four-contact campaign was
four times slower for nothing. Bounded because the searches inside each run are
already parallel, and an unbounded fan-out on top of that is a self-inflicted
rate limit.

**Running out degrades, it does not fail.** — Contacts are still found,
addresses still looked up, leads still created, openers still written from the
competitor angle. Only the deep research stops, and the receipt says so.

**The warning is read from a local count, on the free health path.** — A warning
that itself spends credit is the joke version of this feature.

**Every campaign contact becomes a lead, researched or not.** — A person found
through a campaign is the same kind of thing as one typed in by hand: same list,
same actions, same send path. Creating the row is free; researching is what
costs, so the rest arrive queued and can be run individually whenever they are
worth it.

**A campaign-sourced lead is marked in the list.** — Otherwise an unfamiliar name
appears with no way to explain it. Read off `batch_id`, which already records
the origin, rather than storing it twice.

**The campaign template uses the same persona brief the drafter does.** — Reduced
to "name — character — role" it lost the persona's writing instructions, its
seniority and intent stance, and every rule learned from edits — while the
opener beside it kept all of them. Two halves of one email in two voices.

**A campaign contact is researched by the same runner a lead is.** — Delegated
wholesale rather than given a lighter second pipeline: a cheaper research path
would drift from the one that is actually exercised, and the contact then gets
sources, the traversal graph and a grounded hook for free.

**The contact becomes a real lead, and links to it.** — `lead_run_id` on the
contact, so "open as lead" shows the whole research trail on the screen built
for it.

**Deep research is capped and defaults low.** — Each one is a full pipeline run:
minutes of wall clock and a handful of search credits. Three by default, ten
maximum, raised per run with `config.deep`.

**Contacts past the cap keep the shallow opener.** — Falling back is better than
leaving an opener blank, and the receipt says how many of each.

**A researched contact that found nothing worth saying falls back too.** — An
empty opener is worse than the competitor angle.

---

## Sending from a campaign

**One contact per request, and no send-all.** — A campaign that can mail forty
strangers from one button is a different product with a different blast radius.

**`mailer.check_async` is the only gate, on every screen.** — The contact is
presented to the mailer as a run rather than teaching the mailer a second shape,
so a blocked address or an unreachable domain is refused identically whichever
screen asked.

**Once sent, the control is replaced by the record.** — Not left primed to send
again; `sent_at` is the idempotency key as much as the audit trail.

**The send button says which addresses were calculated.** — An unverified one is
sendable and the tooltip says to read it once more first.

**The leads screen and a campaign share one address lookup.** — Two copies would
disagree about the same person the first time either was touched.

---

## Spending an allowance

**A page load never spends anything.** — `/api/health` answered from
configuration alone; only `?force=1` probes. Checking Tavily is a real search
against a 1,000/month allowance and checking Gemini is a real model call, so a
badge redrawn on every reload was quietly eating the free tier.
*Rejected:* caching the probe — a cache shortens the bleeding, it does not stop
it, and a tab left open still pays every time the cache expires.

**One deliberate button, in Setup.** — Without it `force=1` is unreachable, and
a rule nobody can invoke is not a feature.

**The verifier is never probed, even deliberately.** — It has no free quota
endpoint, so proving the key works costs one of the 100 a day. It reports
configuration and lets the first real verification report the truth.

**Every field from the verifier is the STRING "true"/"false".** — Testing them
for truthiness makes "false" true, which is the classic way this API is misread.

**An accept-all domain is never marked verified.** — The server accepts every
address there, so "valid" says nothing about the mailbox. The verifier returned
"valid" for an address that was invented; certifying it would have shipped a
fabricated address as confirmed.

**Bad input is not a provider refusal.** — A nonexistent domain returns 400, and
calling that "the key was rejected" sends someone to check a key that is fine.

**PDL and Apollo were removed rather than kept behind a flag.** — Apollo spends
credits despite the free plan, and neither was in the path any run took.

---

## Optional providers

**Apollo was removed, not made optional.** — `mixed_people/api_search` spends
Apollo credits; the plan adopted it believing the `api_search` endpoints were
credit-free, and they are not. A paid dependency for something the free path
already does is not a fallback, it is a second code path that can never run.
*Rejected:* keeping it behind a key — every run in this repo was demoed on the
search path, so Apollo was the branch that never executed.

**Hunter's status lives under `verification`, not at the top level.** — Read from
the top level it was always None, so Hunter's own "valid" was discarded, the paid
lookup was wasted, and a correct verified address fell through to a calculated
guess.

**An accept-all domain can never be verified, so nothing tries.** — The server
accepts every address there, which makes "valid" meaningless; spending a
verification credit on it buys nothing.

**Hunter verifies when no dedicated verifier is configured.** — Its verifications
are metered separately from its searches (100/month vs 50), so this costs
nothing from the finder's budget.

**An address the verifier calls invalid is cleared, not shipped.** — Better no
address than one that bounces. A derived guess that fails verification is
exactly the case this catches.

**Hunter's title and profile are kept.** — They arrive with an address already
paid for, and the title is normalised where one read off a page is whatever that
page called them.

**The health check reports the remaining monthly budget, not "ok".** —
`/account` is free, and running out mid-campaign is the failure worth seeing
coming.

**Contacts come from public pages, cited.** — The same finder the leads screen
uses. A person with no source URL is dropped rather than shown.

**Every enrichment provider is optional, and absent, wrong, expired and
out-of-quota all behave the same.** — The run completes on the free path; only
the reason shown differs.

**Requests have a 12-second timeout.** — aiohttp's default is five minutes. A
provider that accepts the connection and never answers would stall the pipeline
five minutes per contact — forty contacts is a three-hour run that looks alive.
An expired key fails fast; a black-holed network does not.

**A refusal is a typed exception, not an empty result.** — Every module caught
bare `Exception` and returned nothing, so an expired key was indistinguishable
from "no match" — and the receipt said "no match", which is the one thing that
stops a user fixing it.

**Statuses become actions, not codes.** — 401 and 403 both mean check the key;
402 and 429 both mean wait. A literal status code teaches nobody anything.

**A verifier that errored has said nothing about the address.** — Treating
silence as "invalid" deleted a good address every time the key expired.

**The receipts count each provider's outcome once.** — The same reason forty
times is one fact, and it is the most useful sentence on the screen: an expired
key that silently degraded to the free path.

---

## Competitor outbound

**Outbound lives in the same shell as the leads workspace.** — It was a centred
card page: the rail and the app's chrome vanished on arrival and came back on
leaving, which reads as two products stitched together. Same rail, same two
resizable panes, same density and tokens.

**The run is in the URL, so a reload lands on it.** — The same reason a lead is.

**Navigate first, then execute.** — The pipeline takes minutes. Waiting for it
before showing anything left the user on a spinner with nothing to watch.

**Contacts and campaigns are re-read on every poll, not once at the end.** — They
land stage by stage, and a pipeline that shows nothing for four minutes looks
broken.

**One template per segment, written for that segment.** — A single subject reused
across every group is not a segmentation; it is one campaign with four names.

**Placeholders are quadrupled in the prompt source.** — `str.format` turns
`{{first_name}}` into `{first_name}`, so the model was being shown single braces
and correctly emitting them — which the export then failed to substitute.

**Only the first sentence of a draft becomes the opener.** — The drafting stage
returns a whole email, and the template supplies its own greeting and sign-off.
Storing the full message produced "Hi Pedro, Pedro, …" — two emails in one send.

**A salutation is detected by shape, not by a keyword list.** — Drafts open with
a bare "Pedro," as often as "Hi Pedro,", and a keyword list only catches the
greetings someone thought of. Short, and not ending a sentence, is the test.

**Every derived address carries its provenance to the CSV.** — `email_source` and
`email_verified` sit beside the address in the export, because whoever imports it
is the last person who can notice that nobody looked it up.

**LinkedIn engagement is not scraped, and no stub pretends otherwise.** — Reading
who liked a competitor's post needs LinkedIn's permission or a scraping
provider, and this app has neither. The substitute is the decision-makers at
those competitors, from public pages, each citing the page that named them.

**Every provider is optional; the pipeline runs with none of them.** — Apollo,
Hunter, PDL and the verifier all need keys. Without them contact discovery falls
through to search, and enrichment falls through to domain conventions — free,
uncapped, and honest about being calculated.
*Rejected:* requiring keys — a pipeline that cannot run on the resources at hand
is a diagram.

**An unconfigured verifier must not delete addresses.** — It returns nothing,
and treating nothing as "invalid" silently discarded every address found.

**A competitor with no citation is dropped.** — An uncited competitor is one the
model recalled rather than read, and a campaign built on recall targets the
wrong people convincingly.

**Contacts are segmented by title, not by the seniority column.** — Seniority is
filled by a provider that may be absent, and grouping on it put every contact
into one campaign called "Unknown".

**Accented names are transliterated, not stripped.** — Splitting on `[^A-Za-z]`
discarded accented characters and everything between them, turning "Jaakko
Iso-Järvenpää" into `jaakko.rvenp@` — a confident address for a person who does
not have it.

**The derived step never re-discovers a domain it already has.** — Routing it
through the address waterfall cost one web search per contact to answer a
question already answered.

**Each domain is resolved once per run, in a thread.** — Forty contacts share
four domains, and a lookup each is forty blocking socket reads on the event loop.

**Nothing is ever sent by a campaign.** — It drafts. A person sends, one message
at a time, from the lead screen.

**`supabase/schema.sql` is generated from `backend/db.py`.** — A comment said to
keep them identical and they drifted: the file was missing every persona table,
which a fresh database would only reveal at boot. A test now fails on drift.

---

**List and card layouts for the same batch.** — A hook is a sentence, and a
sentence in a table cell is read two lines at a time; cards are for reading
results, the list is for scanning a batch.
*Rejected:* replacing the table with cards — scanning 200 rows for status is
what the table is good at.

---

## Spending money carefully

**The health check does not call the person-signal provider.** — Its allowance
is metered in whole calls and is small; the header shows this on every page
load, so probing there would consume the entire quota without researching a
single prospect. Configuration is reported instead, and a real probe is opt-in.
*Rejected:* caching the probe — a cache still spends one call every time it
lapses, forever.

**`/api/health` is cached for five minutes.** — It makes a real Tavily search
and a real Gemini call, and the header requests it on every load; a refreshed
tab was quietly eating the free tier to redraw a badge.

**Resolved profiles are cached in the database for 30 days.** — Two paid calls
per lookup means re-running one lead, or researching several colleagues at one
company, is where a small allowance actually goes. A profile changes over weeks;
a slightly stale one costs far less than having no quota left.

**Every paid provider call is recorded.** — With an allowance in the tens,
"how many have I used" needs an exact answer, not an estimate.

**The list screen sends summary rows only.** — Stages carry facts, verdicts,
sources and the research graph: ~200KB across four leads, and growing with every
run ever done. A page load went from 4.5s to 1.2s by sending the ~4KB the list
actually renders and fetching one run in full when it is opened.

**Runs are claimed with a conditional UPDATE, not a read-then-write.** — A
double-clicked button lands both requests in the gap between checking the status
and setting it, and the cost of losing that race is the entire run executed
twice.
*Rejected:* debouncing in the browser alone — it narrows the window instead of
closing it, and does nothing about two tabs.

**The screen is re-read from the server after every change.** — Assembling a row
from the pieces the client happened to see is how the screen and the database
drift apart, and the database is what the next page load reads.

---

## Known trade-offs

**The research cache is process-local.** — A miss costs one extra research pass
and nothing else; a shared cache table would add a round trip to every run to
save a fraction of them.

**A hook may name a third party the prospect interacted with.** — "You mentored
X" is about the prospect and passes every gate, but it puts someone else's name
in a cold email. Left as a judgment call rather than a rule.

**Extraction is non-deterministic.** — The same prospect can yield 1 fact or 5 on
different runs; the gates are deterministic, the model reading them is not.
