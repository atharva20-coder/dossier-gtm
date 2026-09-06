"""Personas that write, and then learn to write like you.

THE LOOP
--------
    1. A GTM person describes themselves in their own words.
    2. That becomes a persona: a character and a set of writing instructions.
    3. Every draft is written through those instructions.
    4. They edit the draft — by hand, or by telling the assistant what to change.
    5. Those edits are read back and the instructions are rewritten.

Step 5 is the point. A persona written once is a guess about how someone writes;
the edits are evidence. Somebody who deletes the closing question from every
draft is not going to file a bug about it, but they will keep deleting it, and
after three times the instruction should say so.

WHAT IT WRITES DOWN
-------------------
A small, curated rule set beneath the instructions the author wrote — never a
rewrite of those. Each pass may add, correct, merge or drop a rule, and most
passes do nothing at all.

Curation rather than appending, because a set that only grows goes wrong in two
ways. It accumulates rules that contradict each other, and the persona becomes
incoherent — it was told to end with an ask and also never to end with an ask.
And it grows without bound, until the prompt is longer than anything it
produces and nobody can read it well enough to correct it. So the set is
capped, duplicates are refused, and evidence that disagrees with an existing
rule replaces that rule instead of sitting next to it.

Never a name — not the sender's, not a recipient's, not a company's. A persona
is a way of writing that anyone could adopt; a name inside it makes it one
person's and puts an identity into every prompt that uses it. Names arrive at
drafting time from the sender's profile.

WHY IT BATCHES
--------------
Learning fires on a handful of edits, not on each one. A single edit is
ambiguous — the message may have been wrong about that prospect rather than
wrong in style — and reacting to it produces a persona that lurches. Several
edits together show what is consistent, which is the only thing worth writing
down as a rule.

WHY EVERY VERSION IS KEPT
-------------------------
A prompt that rewrites itself and keeps no history is a tool that quietly
becomes something else, and nobody can say when. Each revision records what
changed, why, and how many edits it came from, so the drift is inspectable and
any earlier version can be restored.

WHAT IT WILL NOT LEARN
----------------------
Facts. The instructions govern voice — tone, length, structure, words to avoid.
If someone edits a draft to add "they just raised a Series B", that is a claim
about the world and belongs in the research, not in a rule applied to every
future message. The prompt says so explicitly, because a persona that learned
to assert things would launder invented facts into every draft it touched.
"""
from __future__ import annotations

import difflib
import logging
import re

from pydantic import BaseModel, Field

from .. import config, db
from ..integrations import llm

log = logging.getLogger(__name__)


class GeneratedPersona(BaseModel):
    name: str = Field(description="Two or three words, e.g. 'Founder outreach'.")
    character: str = Field(
        description="One line: who this person is — role, seniority, who they write to. "
                    "A role, never a name.")
    seniority: str = Field(
        default="",
        description="Exactly one of: founder, ceo, cro, vp, ae, sdr. Closest match.")
    intent: str = Field(
        default="",
        description=("What their messages are FOR. Exactly one of: book_meeting, "
                     "open_relationship, partnership, research, re_engage, hiring, event."))
    product: str = Field(
        default="", description="What they sell, in one sentence. Empty if not stated.")
    problem: str = Field(
        default="", description="The pain it removes for the buyer. Empty if not stated.")
    proof: str = Field(
        default="",
        description="Customers, numbers or credibility they mentioned. Empty if none.")
    looking_for: str = Field(
        default="",
        description=("What makes someone worth writing to — the roles, companies, or "
                     "trigger events they are hunting for. Empty if not stated."))
    instructions: str = Field(
        description=("How they write, as imperative lines, one rule per line. Tone, "
                     "length, structure, openings and closings, words to avoid. No "
                     "facts about any company or prospect, and no names."))
    emoji: str = Field(description="A single emoji that suits them.")


GENERATE_PROMPT = """Turn a GTM person's description of themselves into a working brief.

THEY WROTE:
\"\"\"
{description}
\"\"\"

You are building the brief every one of their outreach messages will be written
from. It has to answer four things, because a message missing any of them is
either generic or not credible:

  WHO IS WRITING — their role and seniority. A founder may say "I built this";
  an SDR may not. This decides what a sentence is allowed to claim.

  WHAT THE MESSAGE IS FOR — the intent. It decides the ask at the end. Someone
  opening a relationship should not be asking for a demo.

  WHAT THEY SELL — the product, the problem it removes, and any proof. This is
  the bridge from the prospect's situation to the reason for writing.

  HOW THEY WRITE — tone, length, structure, what they never say.

Extract only what they actually told you. Leave a field empty rather than
inventing a plausible answer: an invented product line becomes a claim in every
message this persona ever writes.

Rules for the instructions field specifically:
- Only voice: tone, length, sentence shape, how to open, how to close, words and
  phrases to avoid, level of formality.
- NEVER write anyone's name — not the sender's, not a recipient's, not a company
  or product name. Say "the sender's first name" or "the recipient's first name"
  instead. A persona is a way of writing, reusable by anyone; a name baked into
  it makes it one person's and puts an identity into every prompt that uses it.
  Names come from the sender's profile at drafting time.
- Never any fact about a company, product, prospect or market. Those come from
  research, not from a persona.
- Prefer what they actually said over what is generically good practice. If they
  said they write long, write that down even though short is usually better.
- Where they were vague, stay vague rather than inventing a preference.
- Six to ten lines. Each one has to be something a writer could obey or break.

The `character` line describes a role, never a person: "founder writing to other
founders", not anybody's name.
"""


LEARN_PROMPT = """Maintain a writing persona's learned rules from edits its author made.

THE INSTRUCTIONS THEY WROTE — never change these, only work alongside them:
\"\"\"
{instructions}
\"\"\"

RULES LEARNED SO FAR ({count} of a maximum {cap}):
{memories}

WHAT THE AUTHOR CHANGED — each is a draft this persona produced, and the version
they were willing to send:

{lessons}

You are curating a small, clean rule set — not a changelog. Return the
operations needed to keep it accurate and tidy. Usually that is one operation.
Often it is none.

OPERATIONS
- add     — a genuinely new pattern. Only when nothing existing covers it.
- replace — an existing rule is wrong, too broad, or too narrow. Give `target`
            and the corrected `rule`. Use this to MERGE two overlapping rules
            into one as well: replace the first, then drop the second.
- drop    — the evidence now contradicts a rule, or it duplicates another.
- none    — nothing consistent to act on. This is the common answer.

HARD RULES
- Change nothing unless the SAME pattern appears in at least two of the edits.
  One edit is not a pattern: the message may have been wrong about that prospect
  rather than wrong in style.
- Never contradict an existing rule by adding next to it. If the new evidence
  disagrees with rule N, `replace` or `drop` rule N. Two rules that pull in
  opposite directions make the persona incoherent.
- Never restate something the author's own instructions already say. Their words
  are already in the prompt; repeating them is noise.
- The set must stay at or under {cap} rules. At the cap you may only replace,
  drop or merge — never add. Prefer one general rule over three specific ones.
- Every rule is one imperative line about VOICE: tone, length, structure,
  openings, closings, vocabulary.
- NEVER write anyone's name — not the sender's, not a recipient's, not a company
  or product. Say "the sender" or "the recipient". Names come from the sender's
  profile, not from a rule applied to every message.
- NEVER record a fact about a company, product, person or market. If an edit
  added information, that came from the author's own knowledge and must not
  become a rule applied to every future message.
"""


FOLD_PROMPT = """Write newly learned rules into a persona's own instructions.

THEIR INSTRUCTIONS AS THEY STAND:
\"\"\"
{instructions}
\"\"\"

WHAT HAS JUST BEEN LEARNED, from edits they made to real drafts:
{rules}

Return the instructions with the new rules written in — one or two lines, in
the same voice as the lines already there, so the whole thing reads as one set
someone wrote rather than a list with an appendix.

HARD RULES
- Keep every existing line that the new rules do not contradict, WORD FOR WORD.
  These are their words. Rewriting them to sound better is losing them.
- Where a new rule contradicts an existing line, replace that line rather than
  adding next to it. Two lines pulling opposite ways make the persona incoherent.
- Add at most two lines. If a new rule is already covered by an existing line,
  add nothing for it.
- Every line is one imperative about VOICE: tone, length, sentence shape, how to
  open, how to close, words to avoid.
- NEVER a name — not the sender's, not a recipient's, not a company or product.
  A persona is a way of writing that anyone could use.
- NEVER a fact about a company, product, prospect or market.
- Keep the whole thing under fourteen lines. At that length, merge rather than
  append.

Also return `summary`: one short clause naming what changed, for the history.
"""


class FoldResult(BaseModel):
    instructions: str = Field(description="The full updated instructions, all lines.")
    summary: str = Field(
        default="",
        description="One short clause on what changed, e.g. 'added a rule about "
                    "where the ask goes'.")


class MemoryOp(BaseModel):
    action: str = Field(description="One of: add, replace, drop, none.")
    target: int | None = Field(
        default=None,
        description="The number of the existing rule this replaces or drops.")
    rule: str = Field(default="", description="The rule text, for add and replace.")
    why: str = Field(default="", description="One short clause on why, for the log.")


class LearnResult(BaseModel):
    operations: list[MemoryOp] = Field(
        default_factory=list,
        description="What to change. Empty, or a single 'none', when nothing was learned.")


async def generate(description: str) -> GeneratedPersona:
    """Build a persona from someone describing themselves."""
    return await llm.structured(
        GeneratedPersona,
        GENERATE_PROMPT.format(description=description.strip()),
        model=config.MODEL_SMART,
        system=("You turn a person's description of how they write into precise, "
                "followable instructions. You never invent preferences they did "
                "not express."),
        temperature=0.3,
    )


def _diff(before: str, after: str, limit: int = 1400) -> str:
    """A unified diff, so the model sees what changed rather than two essays."""
    d = difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile="drafted", tofile="sent", lineterm="", n=1)
    return "\n".join(list(d)[:60])[:limit]


def _same_rule(a: str, b: str) -> bool:
    """Whether two rules say the same thing, allowing for wording drift."""
    norm = lambda t: " ".join(re.sub(r"[^\w\s]", " ", (t or "").lower()).split())
    x, y = norm(a), norm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    return difflib.SequenceMatcher(None, x, y).ratio() >= 0.86


async def learn(persona: dict) -> list[dict]:
    """Curate the persona's learned rules from pending edits.

    Returns the operations actually applied — usually none. This is deliberately
    a maintenance pass rather than an append: a rule set that only ever grows
    ends up holding rules that contradict each other, and a prompt nobody can
    read is a prompt nobody can correct.
    """
    lessons = await db.pending_lessons(persona["id"])
    if len(lessons) < config.PERSONA_LEARN_AFTER:
        return []

    memories = await db.active_memories(persona["id"])
    memory_block = "\n".join(
        f"  {i + 1}. {m['rule']}" for i, m in enumerate(memories)) or "  (nothing yet)"

    blocks = []
    for i, ln in enumerate(lessons, start=1):
        asked = f"\nThey asked for: {ln['instruction']}" if ln["instruction"] else ""
        blocks.append(f"--- edit {i} ---{asked}\n{_diff(ln['before'], ln['after'])}")

    try:
        result = await llm.structured(
            LearnResult,
            LEARN_PROMPT.format(
                instructions=persona.get("instructions") or "(none yet)",
                memories=memory_block, count=len(memories),
                cap=config.PERSONA_MEMORY_CAP, lessons="\n\n".join(blocks)),
            model=config.MODEL_SMART,
            system=("You curate a small set of rules describing HOW someone writes. "
                    "You keep it consistent and short, and you never record names "
                    "or facts."),
            temperature=0.2,
        )
    except Exception:
        log.exception("persona learning failed for %s", persona["id"])
        return []

    # Read either way: edits that showed no pattern have been considered, and
    # holding them back would replay them forever.
    await db.mark_lessons_applied([ln["id"] for ln in lessons])

    applied: list[dict] = []
    live = list(memories)

    for op in result.operations[:3]:      # a maintenance pass, not a rewrite
        action = (op.action or "none").strip().lower()
        rule = (op.rule or "").strip()
        target = live[op.target - 1] if op.target and 1 <= op.target <= len(live) else None

        if action == "drop" and target:
            await db.forget_memory(target["id"])
            live = [m for m in live if m["id"] != target["id"]]
            applied.append({"action": "drop", "rule": target["rule"], "why": op.why})

        elif action == "replace" and target and rule:
            new = await db.add_memory(persona["id"], rule, len(lessons), target["id"])
            live = [m for m in live if m["id"] != target["id"]] + [new]
            applied.append({"action": "replace", "rule": rule,
                            "replaced": target["rule"], "why": op.why,
                            "memory_id": new["id"]})

        elif action == "add" and rule:
            # Guards the model is asked to respect but might not: no duplicates,
            # and never past the cap.
            if any(_same_rule(rule, m["rule"]) for m in live):
                continue
            if len(live) >= config.PERSONA_MEMORY_CAP:
                log.info("persona %s at memory cap, skipping add", persona["id"])
                continue
            new = await db.add_memory(persona["id"], rule, len(lessons))
            live.append(new)
            applied.append({"action": "add", "rule": rule, "why": op.why,
                            "memory_id": new["id"]})

    # ---- write what was learned into the instructions themselves ---------
    #
    # A rule appended to the prompt as a separate block works, and reads as a
    # sticky note on someone else's document. The persona is supposed to BE the
    # brief, so what it learns belongs in the brief — which also means the
    # change is visible as text, and a version of that text is kept.
    fresh = [a for a in applied if a["action"] in ("add", "replace") and a.get("rule")]
    if fresh:
        await _fold_into_instructions(persona, fresh, len(lessons))

    return applied


async def _fold_into_instructions(persona: dict, learned: list[dict],
                                  lesson_count: int) -> None:
    """Rewrite the persona's instructions to include what it just learned.

    Never raises. Failing to fold leaves the rule in the appended block, where
    it still takes effect — a tidier prompt is not worth losing the lesson.
    """
    current = (persona.get("instructions") or "").strip()
    if not current:
        return
    try:
        result = await llm.structured(
            FoldResult,
            FOLD_PROMPT.format(
                instructions=current,
                rules="\n".join(f"- {a['rule']}" for a in learned)),
            model=config.MODEL_SMART,
            system=("You maintain one person's writing instructions. You preserve "
                    "their wording and add as little as possible."),
            temperature=0.2,
        )
    except Exception:
        log.exception("could not fold learned rules into instructions")
        return

    updated = (result.instructions or "").strip()
    # A fold that returns nothing, or throws the author's lines away, is worse
    # than not folding. Length is a blunt check and it catches exactly that.
    if not updated or len(updated) < len(current) * 0.5:
        log.warning("fold produced a suspiciously short result; keeping the original")
        return

    await db.update_persona(persona["id"], instructions=updated)
    await db.add_persona_revision(
        persona["id"], updated,
        result.summary or "learned from your edits", lesson_count)
    await db.mark_memories_folded(
        [a["memory_id"] for a in learned if a.get("memory_id")])
    for a in learned:
        a["folded"] = True
        a["instructions_summary"] = result.summary


async def note_edit(run_id: int | None, before: str, after: str,
                    instruction: str = "") -> list[dict]:
    """Record one accepted edit and learn from it if enough have accumulated.

    Called from every path that changes a draft on purpose. Never raises: a
    failure to learn must not fail the edit the user actually asked for.
    """
    try:
        persona = await db.get_selected_persona()
        if not persona:
            return []
        await db.record_lesson(persona["id"], run_id, before, after, instruction)
        return await learn(persona)
    except Exception:
        log.exception("could not record persona lesson")
        return []
