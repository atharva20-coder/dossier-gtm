"""Stage 5 — draft generation, with post-generation verification.

Three inputs shape the message, beyond the hook itself:

  1. WRITER PERSONA — who is sending this and what they sell. Without it the
     system optimises for "most interesting fact about the prospect" instead of
     "fact that connects them to my offer", which is the wrong objective.

  2. STAKEHOLDER PROFILE — a CFO and a VP Ops get genuinely different messages
     off identical research, because they care about different consequences.

  3. LEARNED STYLE — past drafts the user edited, fed back as examples. The
     system converges on their voice instead of a generic assistant voice.

The draft is then CHECKED, not trusted: it must carry the hook's specific
detail, and must avoid stock openers. Two failures and we fall back honestly
rather than ship something that claims personalisation it does not have.
"""
from __future__ import annotations

import re

from .. import config
from ..integrations import llm
from ..integrations.llm import LLMFailure
from ..models import (
    DraftResult,
    ExtractedFact,
    ProspectInput,
    StakeholderProfile,
    StyleExample,
    WriterConfig,
)
from . import grounding

BASE_SYSTEM = """You are the best outbound writer a GTM team has ever had.

You have written for founders and for SDR teams, and you know the difference is
not tone — it is what each of them has earned the right to say. You have read
enough replies to know which sentences get them and which get deleted, and the
gap between the two is almost never eloquence.

WHAT YOU KNOW THAT MOST PEOPLE WRITING COLD EMAIL DO NOT

Nobody owes you a reply. Attention is borrowed against a promise that the next
sentence is worth it, and the debt comes due every line. A message earns its
length; it does not get length by default.

Relevance is demonstrated, never asserted. "I've been following your work" is a
claim. "You moved into the founder's office at Zamp in June" is evidence. One of
them proves you looked and the other proves you have a template.

The recipient is the subject of the message. Count the sentences that are about
them versus about you — if it is not at least two to one, the message is about
you and reads that way. Their situation, their consequence, their decision.

Timing is what makes cold warm. A person who just changed role, just raised, or
is publicly hiring is mid-decision about something. That window is the entire
reason the message is being sent now rather than last quarter, and naming it is
what separates outreach from spam.

Specificity is the only credible proof of effort. A number, a name, a date, a
quote. Vagueness is indistinguishable from a mail merge, because that is what it
usually is.

Match altitude. An operator cares that the work gets harder; an executive cares
what it costs and what it risks. The same fact means different things at
different levels, and saying the operator's version to a CFO reads as junior.

The ask should be almost free to say yes to, and equally easy to decline. "Worth
fifteen minutes on Thursday?" is answerable. "Let me know if you'd like to learn
more" makes them do your work and gets silence.

Confidence is brevity. Hedging, throat-clearing and over-explaining all read as
someone who is not sure they should be writing. If the message is right, it is
short.

WHAT YOU WILL NOT DO

You never invent a fact. Not a headcount, not a funding round, not a tool they
use, not a priority they hold. Everything specific in the message traces to the
verified fact you were given. If that fact is thin, you write a shorter message
— you never pad it with plausible detail, because plausible detail that turns
out to be wrong destroys the sender's credibility in one line.

You never write flattery that costs nothing. Praise that could apply to any
company is not a compliment, it is filler.

You never use language that signals template: the openings everyone has seen,
the words that appear in every SaaS email, the enthusiasm nobody feels.

You write one message with one idea and one ask, and then you stop."""


def _signature_note(writer: WriterConfig | None) -> str:
    """Say why the signature is blank, when it is.

    The prompt refuses to invent a sender name, which is right — an email
    signed with a made-up person is worse than one signed with nothing. But an
    email that just stops after "Best," reads as a bug unless the reason is
    stated, and the fix is one field in Setup.
    """
    if writer and (writer.sender_name or "").strip():
        return ""
    return (" Signed off without a name — add yours in Setup and it will appear "
            "on every draft.")


def split_subject(text: str) -> tuple[str, str]:
    """Separate a leading "Subject: ..." line from the email itself.

    The model writes one document because that is how an email is written — the
    subject has to suit the opening line, and asking for them separately gets a
    subject that reads like it was bolted on. They are stored apart because the
    UI shows and exports them apart.
    """
    lines = (text or "").strip().split("\n")
    subject = ""
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^\s*subject\s*:", lines[0], re.I):
        subject = re.sub(r"^\s*subject\s*:\s*", "", lines.pop(0), flags=re.I).strip()
        subject = subject.strip('"').strip("'")
    while lines and not lines[0].strip():
        lines.pop(0)
    return subject, "\n".join(lines).strip()


# How much a claim costs, by who is making it. Seniority is not decoration: a
# founder saying "I built this" is credible and an SDR saying it is not, and a
# CxO-to-CxO note can be blunt in a way a first-touch SDR note cannot.
SENIORITY_STANCE = {
    "founder": ("You built the thing. You may say so. Write peer-to-peer: short, "
                "direct, no deference and no permission-asking. Your scarcity is "
                "real, so the ask can be small and concrete."),
    "ceo": ("Peer-level to another executive. Speak about outcomes and trade-offs, "
            "not features. Brevity reads as confidence; length reads as pitching."),
    "cro": ("You own the number. Frame everything as revenue consequence, and be "
            "specific about which part of their funnel or motion you mean."),
    "vp": ("Senior enough to be direct, close enough to the work to be concrete. "
           "Reference the operational reality, not the strategy deck."),
    "ae": ("You have not earned their attention yet — the observation has to do "
           "that work. No familiarity you have not established. One clear ask."),
    "sdr": ("First touch. You are not the expert and should not pretend to be: "
            "lead entirely with what you noticed, keep it under four sentences, "
            "and ask for a conversation rather than a decision."),
}

# What the message is FOR. The ask at the end is not a formality — it is the
# whole point, and it should match the intent rather than defaulting to a demo.
INTENT_STANCE = {
    "book_meeting": ("Aim for a short call. Make the ask specific and small — a "
                     "named length, a named week. Never 'let me know if you'd be "
                     "interested', which asks them to do the work."),
    "open_relationship": ("Do not ask for a meeting. The goal is a reply and a "
                          "reason to talk later. End with a genuine question they "
                          "can answer in one line."),
    "partnership": ("Two-sided from the first sentence. Say what you would bring "
                    "before what you want. Concrete overlap, not 'exploring "
                    "synergies'."),
    "research": ("You want their perspective, not their budget. Ask one real "
                 "question and mean it. Do not pitch at all — any product mention "
                 "undermines the request."),
    "re_engage": ("You have spoken before. Reference why now is different, not "
                  "that you are 'following up'. Give them a reason to reopen it."),
    "hiring": ("You are talking about their career, not your product. Be specific "
               "about why them, and honest about what the role is."),
    "event": ("An invitation, not a pitch. Say who else will be there and why it "
              "is relevant to them specifically."),
}


def _persona_block(persona: dict | None) -> str:
    """The whole GTM brief: who is writing, why, what they sell, and how.

    A message is only worth sending when four things line up — who it is from,
    what it is for, what it is about, and how it sounds. Splitting those across
    a persona and a settings page produced drafts that were stylistically right
    and strategically empty: correct tone, no reason for the recipient to care.

    Order matters here. Identity first, because seniority determines what a
    sentence is allowed to claim. Then intent, because it determines the ask.
    Then the product, because it determines the bridge from their situation to
    the reason for writing. Voice last, and learned rules after that, because
    later instructions win and what someone actually does beats what they said
    they do.
    """
    if not persona:
        return ""

    # Written by hand? Then it IS the brief, verbatim.
    #
    # The assembled version below is a scaffold for someone who would rather
    # answer questions than write a page. Someone who has written the page
    # should not have it treated as raw material for a brief this app composes
    # — that is the app overruling the person whose agent it is. Learned rules
    # still apply on top, because those came from their own edits.
    hand_written = (persona.get("brief") or "").strip()
    if hand_written:
        out = ["THE BRIEF — written by the sender. Follow it exactly. It outranks "
               "every other instruction about how to write.\n", hand_written]
        sample = (persona.get("sample") or "").strip()
        if sample:
            out.append(
                "\nA MESSAGE THEY WROTE THEMSELVES — match this voice. Do not copy "
                "its facts; this prospect is a different person.\n"
                f"---\n{sample}\n---")
        learned = [m["rule"] for m in (persona.get("memories") or [])
                   if m.get("rule") and not m.get("folded")]
        if learned:
            out.append(
                "\nLEARNED FROM THEIR EDITS — inferred from changes they made to "
                "real drafts, and these OVERRIDE anything above that contradicts "
                "them:\n" + "\n".join(f"- {r}" for r in learned))
        return "\n".join(out) + "\n\n"

    out = ["THE BRIEF — this outranks every other instruction about how to write.\n"]

    who = persona.get("character") or persona.get("name") or "a salesperson"
    seniority = (persona.get("seniority") or "").strip()
    # State the seniority even when there is no canned stance for it. Dropping an
    # unrecognised value would silently discard the single field that decides
    # what a sentence is allowed to claim.
    out.append(f"WHO IS WRITING: {who}"
               + (f" — seniority: {seniority}" if seniority else ""))
    stance = SENIORITY_STANCE.get(seniority.lower())
    if stance:
        out.append(f"  What that lets them say: {stance}")
    elif seniority:
        out.append("  Judge from that seniority what they may credibly claim: a "
                   "founder can say they built it, a first-touch rep cannot.")

    intent = (persona.get("intent") or "").strip().lower()
    if intent:
        readable = intent.replace("_", " ")
        out.append(f"\nWHAT THIS MESSAGE IS FOR: {readable}")
        guide = INTENT_STANCE.get(intent)
        if guide:
            out.append(f"  How that shapes the ask: {guide}")

    if persona.get("product"):
        out.append(f"\nWHAT THEY SELL: {persona['product']}")
    if persona.get("problem"):
        out.append(f"THE PROBLEM IT REMOVES: {persona['problem']}")
    if persona.get("proof"):
        out.append(f"CREDIBILITY — use at most once, only if it fits this prospect: "
                   f"{persona['proof']}")
    if persona.get("looking_for"):
        out.append(f"WHY THIS PERSON IS WORTH WRITING TO: {persona['looking_for']}")

    if persona.get("instructions"):
        out.append(f"\nHOW THEY WRITE — follow this closely:\n{persona['instructions']}")

    if (persona.get("sample") or "").strip():
        out.append("\nA MESSAGE THEY WROTE THEMSELVES — match this voice. Do not copy "
                   "its facts; this prospect is a different person.\n"
                   f"---\n{persona['sample'].strip()}\n---")

    # Only the rules not yet written into the instructions above. A rule that
    # has been folded in is already there; appending it again would state it
    # twice, and the second copy claims to override the first.
    memories = [m["rule"] for m in (persona.get("memories") or [])
                if m.get("rule") and not m.get("folded")]
    if memories:
        out.append(
            "\nLEARNED FROM THEIR EDITS — inferred from changes they made to real "
            "drafts, and these OVERRIDE anything above that contradicts them:\n"
            + "\n".join(f"- {r}" for r in memories))

    return "\n".join(out) + "\n\n"


def _writer_block(w: WriterConfig | None) -> str:
    if not w or not w.configured():
        return "FROM: a salesperson (no sender profile configured).\n"
    bits = [f"FROM: {w.sender_name or 'a salesperson'}"
            + (f", {w.sender_role}" if w.sender_role else "")
            + (f" at {w.sender_company}" if w.sender_company else "")]
    if w.product:
        bits.append(f"WHAT THEY SELL: {w.product}")
    if w.problem_solved:
        bits.append(f"PROBLEM IT REMOVES: {w.problem_solved}")
    if w.proof:
        bits.append(f"CREDIBILITY (use at most once, briefly): {w.proof}")
    intent_map = {
        "book_meeting": "get a short intro call",
        "intro": "open a relationship, not push a meeting",
        "partnership": "explore a partnership",
        "hiring": "start a hiring conversation",
        "research": "ask for their perspective, not sell",
    }
    bits.append(f"GOAL OF THE EMAIL: {intent_map.get(w.intent, w.intent)}")
    if w.tone:
        bits.append(f"TONE: {w.tone}")
    return "\n".join(bits) + "\n"


def _background_block(facts: list[ExtractedFact] | None,
                      current: str = "") -> str:
    """Career history, offered as context and fenced off from the hook.

    Where someone worked ten years ago is a poor reason to email them today and
    a real part of knowing who they are: a VP at one company then, an SVP
    somewhere else now, is a different reader from someone in their first such
    role. The fence is explicit because the model will otherwise reach for the
    most colourful line here and open with it.
    """
    if not facts:
        return ""
    lines = "\n".join(f"- {f.text}" for f in facts[:6])
    where = f" They are at {current} NOW." if current else ""
    return (
        "\n\nTHEIR CAREER SO FAR — FORMER ROLES, CONTEXT ONLY\n"
        f"{lines}\n"
        f"Every line above is a PAST role at a FORMER employer.{where} Naming one "
        "of these as their current job is the single worst mistake this message "
        "can make: it is wrong, it is checkable in one click, and it proves "
        "nobody read anything.\n"
        "Use it only to judge seniority and what they have already lived through "
        "— someone who has run this function before needs a different pitch from "
        "someone doing it for the first time. NEVER open with it and never "
        "present it as news.\n"
    )


def _stakeholder_block(s: StakeholderProfile | None) -> str:
    if not s or not s.title_known:
        return ""
    return (
        f"\nWHO YOU ARE WRITING TO:\n"
        f"  seniority: {s.seniority} — they care about: {s.angle_seniority}\n"
        f"  function:  {s.function} — frame the impact around: {s.angle_function}\n"
        f"Angle the middle sentence at THAT, not at a generic benefit.\n"
    )


def _style_block(examples: list[StyleExample]) -> str:
    """Few-shot the user's own voice from drafts they edited.

    This is the cheapest possible version of "learn from me": no training, no
    fine-tuning, just showing the model what this person actually changed.
    """
    if not examples:
        return ""
    out = ["\nHOW THIS SENDER WRITES — they edited past drafts like this.",
           "Match their voice: sentence length, formality, sign-off, how direct the ask is.",
           "Do NOT copy the content, only the style."]
    for i, ex in enumerate(examples[:3], 1):
        out.append(f"\n  Example {i} — what the model wrote:\n    {ex.original.strip()[:400]}")
        out.append(f"  What they changed it to:\n    {ex.edited.strip()[:400]}")
    return "\n".join(out) + "\n"


HOOK_PROMPT = """Write one cold outreach message.

{writer}
WHO IT IS TO
  {name}{role_part} at {company}
{stakeholder}
THE ONE VERIFIED FACT YOU MAY REFERENCE
  {hook}
  ({level}-level {category}, source: {source}, dated {date})
{level_guidance}
{style}
HOW A MESSAGE LIKE THIS EARNS A REPLY

Every good cold message does four things in order, and skipping any of them is
why most get deleted:

1. PROVE YOU LOOKED. Open on the specific detail in the fact above — the number,
   the name, the actual thing. Not "I saw your recent news", not "congrats on the
   growth". If the opening line could be sent to fifty other people, it is wrong.

2. MAKE IT THEIR PROBLEM, NOT YOUR PRODUCT. One sentence connecting that fact to
   a consequence THIS person plausibly feels, given their role. A VP of Ops and a
   CFO care about different sides of the same event. Say the consequence; do not
   explain the product yet.

3. EARN THE BRIDGE — OR DO NOT BUILD ONE. Only now, and only in one clause, say
   what you do about that consequence.

   Before you write it, ask honestly: would this person recognise the link
   between the fact above and what this sender sells? If the fact is a quote
   about design philosophy and the product is research tooling, the answer is
   no, and stapling them together with "which creates operational lift around…"
   fools nobody — it reads as a mail merge that found a keyword.

   When the link is genuinely weak, DO NOT force it. Drop step 3 entirely and
   write a two-sentence message instead: the observation, and one real question
   about it that you would ask whether or not you sold anything. A short honest
   note gets replies. A strained bridge gets deleted and remembered.

4. ASK FOR ONE SMALL THING, matching the intent in the brief. Specific and easy
   to say yes or no to.

RULES
- 3-4 sentences. Under 90 words unless the brief says otherwise.
- Invent NOTHING beyond the verified fact. No made-up details about their team,
  headcount, tooling, funding, plans or priorities. If you want a detail you do
  not have, write around it.
- Never these: "hope this finds you well", "I wanted to reach out", "just
  reaching out", "I noticed", "quick question", "circle back", "synergy",
  "game changer", "revolutionise", "in today's fast-paced".
- No flattery that costs nothing — "impressive growth", "exciting journey".
  Praise is only worth writing when it is specific enough to be wrong.
- One ask. Not two, not an ask plus a PS.
- Their first name only. Never "Dear".
- Do not reuse the same connective phrasing across messages. If a sentence like
  "creates operational lift around X" would fit any prospect, it is filler with
  a noun swapped in.

WRITE THE WHOLE EMAIL, in exactly this shape:

Subject: <six words or fewer, specific, lowercase-ish, no colon-heavy marketing phrasing>

<greeting using their first name>

<the message>

<a sign-off line>
<the sender's name>
<the sender's role and company, only if both are known>

ALWAYS include the sign-off and signature unless the brief says otherwise —
a message that stops dead after the ask reads as truncated. Nothing before
"Subject:" and nothing after the signature. No commentary, no placeholders, no
square brackets — if you do not know the sender's name, end at the sign-off
rather than inventing one.

THE BRIEF AT THE TOP OUTRANKS THIS SHAPE. Where it says something different
about openings, closings, the signature or the length, follow it. This template
is the default, not the rule.
"""

PERSON_GUIDANCE = """
This hook is about THEM PERSONALLY. Write to that directly — it is the reason
this email will not read like the other forty in their inbox. Reference what
they said or did, not what their employer announced.
"""

COMPANY_GUIDANCE = """
This hook is about their COMPANY, not them personally. Do not congratulate them
for it as though it were their personal achievement — reference it as context
and move quickly to what it means for their work.
"""

NO_HOOK_PROMPT = """Write a short, honest cold outreach email.

{writer}
TO: {name}{role_part} at {company}
{stakeholder}{style}
We found NO specific, current, public information about this person or company.
So do not pretend otherwise. Write something that is:
- Brief (3 sentences max, under 70 words)
- Honest — no fake personalisation, no invented detail, no "I've been following
  your work"
- Focused on a plausible challenge for someone in their role, stated as a
  question rather than an assumption
- Ends with a low-friction ask

NEVER use: "hope this finds you well", "I wanted to reach out", "I noticed".
Invent NO facts about them.

WRITE THE WHOLE EMAIL, in exactly this shape:

Subject: <six words or fewer>

<greeting using their first name>

<the sentences>

<a sign-off line>
<the sender's name>
<the sender's role and company, only if both are known>

Nothing before "Subject:" and nothing after the signature. No placeholders and
no square brackets — if the sender's name is unknown, end at the sign-off.

THE PERSONA'S INSTRUCTIONS OUTRANK THIS SHAPE.
"""


def _role_part(role: str) -> str:
    return f" ({role})" if role else ""


REVISE_PROMPT = """Rewrite this outreach message following the instruction.

WHO IT IS TO
  {name}{role_part} at {company}
{stakeholder}

WHAT IT IS BUILT ON — the one verified fact this message may reference:
  {hook}

{writer}
CURRENT MESSAGE:
\"\"\"
{current}
\"\"\"

INSTRUCTION FROM THE SENDER:
  {instruction}

RULES — these outrank the instruction:
- Use ONLY the verified fact above. Do not add companies, numbers, dates, job
  titles or events that are not in it. If the instruction asks for detail you do
  not have, write the message without that detail rather than inventing it.
- Keep it a message this person could actually receive: no placeholders, no
  "[insert X]", no meta-commentary about the instruction.
- Return the WHOLE email, keeping its shape: greeting, body, sign-off and
  signature. If the current message has a "Subject:" line, keep one.
- Nothing before the subject or greeting, nothing after the signature.

Everything else about the message — tone, length, structure, what to cut — is
the sender's to direct. Follow the instruction.
"""


async def revise(
    p: ProspectInput,
    hook: ExtractedFact | None,
    current: str,
    instruction: str,
    *,
    writer: WriterConfig | None = None,
    stakeholder: StakeholderProfile | None = None,
    persona: dict | None = None,
) -> DraftResult:
    """Rewrite an existing draft according to a plain-language instruction.

    The rep knows things the pipeline does not — that this prospect hates
    flattery, that the team already spoke to them last quarter, that the message
    is two lines too long. Rather than making them rewrite from scratch or fight
    the drafting rules, they say what to change.

    The grounding rules still bind. An instruction can change tone, length,
    emphasis and structure; it cannot introduce a fact, because the whole system
    rests on every claim in a message being traceable to a source. That is why
    the rules sit below the instruction in the prompt and say outright that they
    outrank it.
    """
    if not instruction.strip():
        return DraftResult(subject="", body=current, grounded=True, note="No instruction given.")

    prompt = REVISE_PROMPT.format(
        name=p.name, role_part=_role_part(p.role), company=p.company or "their company",
        stakeholder=_stakeholder_block(stakeholder),
        writer=_persona_block(persona) + _writer_block(writer),
        hook=hook.text if hook else "(no verified fact — keep the message generic)",
        current=current or "(no draft yet)", instruction=instruction.strip(),
    )
    system = BASE_SYSTEM + (
        f" You are writing as {writer.sender_name or 'the sender'}"
        f"{', ' + writer.sender_role if writer and writer.sender_role else ''}."
        if writer and writer.configured() else ""
    )
    try:
        raw = await llm.text(prompt, model=config.MODEL_SMART, system=system, temperature=0.6)
    except LLMFailure as e:
        return DraftResult(subject="", body=current, grounded=False,
                           note=f"Could not revise ({e.reason}) — the message is unchanged.")
    subject, body = split_subject(raw)

    # The hook's specific detail must survive a rewrite, or the message has
    # quietly become generic while still claiming to be grounded.
    if hook is not None:
        ok, why = grounding.verify_draft(body, hook)
        if not ok:
            return DraftResult(subject=subject, body=body, grounded=False,
                               note=f"Revised, but it no longer carries the hook's detail — {why}.")
    return DraftResult(subject=subject, body=body, grounded=True, note="Revised.")


async def write(
    p: ProspectInput,
    hook: ExtractedFact | None,
    *,
    writer: WriterConfig | None = None,
    stakeholder: StakeholderProfile | None = None,
    style_examples: list[StyleExample] | None = None,
    persona: dict | None = None,
    background: list[ExtractedFact] | None = None,
) -> DraftResult:
    role_part = _role_part(p.role)
    company = p.company or "their company"
    wblock = _persona_block(persona) + _writer_block(writer)
    sblock = _stakeholder_block(stakeholder) + _background_block(background, company)
    stblock = _style_block(style_examples or [])
    system = BASE_SYSTEM + (
        f" You are writing as {writer.sender_name or 'the sender'}"
        f"{', ' + writer.sender_role if writer and writer.sender_role else ''}."
        if writer and writer.configured() else ""
    )

    # ---- No eligible hook: be honest rather than fabricate ----------------
    if hook is None:
        try:
            body = await llm.text(
                NO_HOOK_PROMPT.format(writer=wblock, name=p.name, role_part=role_part,
                                      company=company, stakeholder=sblock, style=stblock),
                model=config.MODEL_SMART, system=system, temperature=0.6,
            )
            subject, body = split_subject(body)
            return DraftResult(
                subject=subject or f"Quick question, {company}", body=body, grounded=True,
                note=("No specific public signal was found — this is a deliberately generic "
                      "draft, labelled as such rather than faking personalisation."
                      + _signature_note(writer)),
            )
        except LLMFailure as e:
            return DraftResult(subject="", body="", grounded=False,
                               note=f"No hook found, and draft generation was unavailable ({e.reason}).")

    # ---- Hook available ---------------------------------------------------
    level = hook.level if hook.level in ("person", "company") else "company"
    prompt = HOOK_PROMPT.format(
        writer=wblock, name=p.name, role_part=role_part, company=company,
        stakeholder=sblock, hook=hook.text, level=level, category=hook.category,
        source=hook.source_url or "n/a", date=hook.date or "date unknown",
        level_guidance=PERSON_GUIDANCE if level == "person" else COMPANY_GUIDANCE,
        style=stblock,
    )

    last_note = ""
    for attempt in range(2):
        try:
            body = await llm.text(prompt, model=config.MODEL_SMART, system=system,
                                  temperature=0.7 if attempt == 0 else 0.35)
        except LLMFailure as e:
            return DraftResult(
                subject="", body="", grounded=False,
                note=(f"Draft generation failed ({e.reason}). The verified hook and its "
                      f"sources are still available — the rep can write from those."),
            )

        subject, body = split_subject(body)
        carries, why = grounding.verify_draft(body, hook)
        generic = grounding.generic_opener_used(body)
        if carries and not generic:
            return DraftResult(
                subject=subject or f"{hook.category.replace('_', ' ').title()} — quick thought",
                body=body, grounded=True,
                note=f"{why}. Hook was {level}-level." + _signature_note(writer),
            )

        last_note = why if not carries else f"used a generic opener: '{generic}'"
        prompt += (f"\n\nYOUR PREVIOUS ATTEMPT FAILED THE CHECK: {last_note}. "
                   f"Rewrite it. The specific detail from the hook must appear "
                   f"explicitly in the first sentence.")

    return DraftResult(
        subject="", body="", grounded=False,
        note=(f"Draft rejected after 2 attempts ({last_note}). Falling back rather than "
              f"sending a message that claims personalisation it does not have."),
    )
