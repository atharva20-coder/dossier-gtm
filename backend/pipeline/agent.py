"""The assistant that can actually change things.

WHY A TOOL LOOP RATHER THAN A REWRITE PROMPT
--------------------------------------------
"Make it shorter" is a text edit. "Drop the podcast fact and use the promotion
instead" is not — it changes which evidence the message rests on, which has to
go through the same judge and grounding path as everything else, and has to be
recorded so the next page load shows it.

Handing the model a text box and asking it to produce a new message would let it
answer both kinds of request the same way: by writing prose. It would happily
say "I've excluded that fact" while excluding nothing. Giving it tools means the
only way it can claim to have done something is to have actually done it, and
every action it takes is logged with its arguments.

WHAT IT CANNOT DO
-----------------
There is no tool that adds a fact, edits a source, or spends search credits. The
assistant rearranges what research already found and rewrites how it reads. New
information only ever enters through the pipeline, where it is grounded against
a real source — otherwise "include that he moved to Stripe" becomes a fact in an
email with nothing behind it.
"""
from __future__ import annotations

import json
import logging

from .. import config, db
from ..integrations import llm
from ..models import ExtractedFact, ProspectInput, StakeholderProfile, WriterConfig
from . import draft as draft_stage
from . import judge as judge_stage

log = logging.getLogger(__name__)

SYSTEM = """You are Dossier's assistant, working with someone on one prospect.

This is a conversation, not a command line. You have the whole thread, so
follow-ups like "no, the other one" or "why?" refer to what was just said.

WHAT YOU CAN DO
- Inspect what the research found and act on it with your tools.
- Answer questions about the evidence without changing anything. "Why did it
  pick this?" and "what else did you find?" are answered, not acted on.
- Ask a question back when the request is genuinely ambiguous — two facts
  match "the podcast one", or you cannot tell whether they want a fact dropped
  or just want a different opening line. Ask, then wait; do not guess and act.

WHEN TO ACT AND WHEN TO ASK
- A clear instruction gets carried out: act rather than describing how they
  could do it themselves.
- An ambiguous one gets one short question. Name the options — "the Lightcone
  podcast one, or the AI-agents one?" — so answering is a word, not an essay.
- Never ask more than one question at a time, and never ask when the answer is
  obvious from the thread.

FINDING OUT WHY, WHICH IS THE PART THAT LASTS
Dropping or choosing a fact changes how every future prospect is ranked, and
the act alone does not say what to change. "Awards say nothing about whether
someone needs this" is a rule about awards. "That one is four years old" is a
rule about age. Same click, opposite lessons.

So after you exclude or choose a fact — ACT FIRST, then ask, in the same reply:
- One short question, offering the two likeliest reasons so it can be answered
  in a word. "Dropped it. Was that awards generally, or just this one being old?"
- The moment they answer, call `explain_choice` with what they actually said.
- Ask at most once per fact, and skip it entirely if they already told you why
  while asking, or if they are clearly working fast and giving one-word orders.
- Never withhold the action until they explain, and never ask twice.

HARD RULES
- Never invent facts about the prospect. You may only use what `list_facts`
  returns. If asked to include something not there, say plainly that the
  research did not find it, and offer what is there instead.
- Excluding a fact or choosing a hook rewrites the message from the remaining
  evidence. That is expected; do it when asked.
- After acting, say what you did in one or two short sentences. No bullet
  lists, no restating the whole message back.
"""

TOOLS = [
    {
        "name": "list_facts",
        "description": ("Every fact the research produced for this prospect, with its id, "
                        "whether the rules judged it eligible, its score, whether it is "
                        "currently excluded, and which one is the hook."),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_sources",
        "description": "The sources the research read, with their titles and URLs.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "exclude_fact",
        "description": ("Stop a fact being used, and rewrite the message from what is left. "
                        "Use when the user says a fact is wrong, irrelevant or unusable."),
        "parameters": {
            "type": "object",
            "properties": {"fact_id": {"type": "string", "description": "id from list_facts"}},
            "required": ["fact_id"],
        },
    },
    {
        "name": "include_fact",
        "description": "Undo an exclusion, putting a fact back in play, and rewrite.",
        "parameters": {
            "type": "object",
            "properties": {"fact_id": {"type": "string"}},
            "required": ["fact_id"],
        },
    },
    {
        "name": "choose_hook",
        "description": ("Force one specific fact to be the hook the message is built on, "
                        "overriding the ranking, and rewrite the message around it."),
        "parameters": {
            "type": "object",
            "properties": {"fact_id": {"type": "string"}},
            "required": ["fact_id"],
        },
    },
    {
        "name": "explain_choice",
        "description": (
            "Record WHY the user included, excluded or chose a fact, in their own "
            "words. Call this as soon as they tell you — a sentence like 'awards "
            "don't tell you anything about need' or 'that one is years old'. Do not "
            "paraphrase it into something more general than they said."),
        "parameters": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "string", "description": "id from list_facts"},
                "reason": {"type": "string",
                           "description": "their reason, in their words, one sentence"},
            },
            "required": ["fact_id", "reason"],
        },
    },
    {
        "name": "rewrite_message",
        "description": ("Rewrite the message with a style instruction — shorter, warmer, "
                        "drop the closing line, lead with the question. Does not change "
                        "which facts are used."),
        "parameters": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string",
                                "description": "what to change about how it reads"},
            },
            "required": ["instruction"],
        },
    },
]


def _fact_rows(run: dict, verdicts: list[dict], overrides: dict) -> list[dict]:
    excluded = set(overrides.get("excluded") or [])
    chosen = overrides.get("chosen") or ""
    out = []
    for v in verdicts:
        fid = v.get("fact_id") or judge_stage.fact_id_for_text(
            (v.get("fact") or {}).get("text", ""))
        fact = v.get("fact") or {}
        out.append({
            "fact_id": fid,
            "text": fact.get("text", ""),
            "level": fact.get("level", ""),
            "category": fact.get("category", ""),
            "date": fact.get("date") or "unknown",
            "source_url": fact.get("source_url", ""),
            "eligible": bool(v.get("eligible")),
            "score": v.get("score", 0),
            "excluded": fid in excluded,
            "is_hook": (chosen == fid) if chosen else fact.get("text") == run.get("chosen_hook"),
            "why": v.get("reason", ""),
        })
    return out


async def run_turn(run_id: int, message: str, stage_payload) -> dict:
    """Handle one chat turn. Returns {reply, actions, run}."""
    run = await db.get_run(run_id)
    if not run:
        raise ValueError("Run not found")

    verdicts = stage_payload(run, "judge").get("verdicts") or []
    overrides = dict(run.get("fact_overrides") or {})
    prospect = ProspectInput(
        name=run.get("name") or "", company=run.get("company") or "",
        role=run.get("role") or "", location=run.get("location") or "")

    async def rebuild() -> None:
        """Re-judge from the surviving facts and rewrite. Spends no search."""
        nonlocal run, overrides
        excluded = set(overrides.get("excluded") or [])
        chosen = overrides.get("chosen") or ""
        facts = [ExtractedFact(**v["fact"]) for v in verdicts
                 if (v.get("fact_id") or judge_stage.fact_id_for_text(v["fact"]["text"]))
                 not in excluded]
        if not facts:
            raise ValueError("every fact is excluded — nothing left to write from")

        writer = WriterConfig(**(await db.get_config("writer") or {}))
        persona = await db.get_selected_persona()
        stakeholder = StakeholderProfile(
            **(stage_payload(run, "profile").get("stakeholder") or {}))

        if chosen:
            hook = next((f for f in facts
                         if judge_stage.fact_id_for_text(f.text) == chosen), None)
        else:
            hook = (await judge_stage.judge(
                facts, target_company=prospect.company, writer=writer,
                stakeholder=stakeholder, persona=persona,
                name=prospect.name, role=prospect.role,
                learned=judge_stage.learned_weights(
                    await db.hook_outcomes(),
                    await db.fact_feedback_counts()))).chosen

        d = await draft_stage.write(prospect, hook, writer=writer,
                                    stakeholder=stakeholder, persona=persona)
        await db.add_draft_revision(
            run_id, run.get("draft_body") or "", d.body, subject=d.subject,
            source="assistant", instruction="re-judged after a change of evidence",
            hook=hook.text if hook else "", persona_id=(persona or {}).get("id"))
        await db.update_run(
            run_id,
            status="completed" if hook else "no_signal_found",
            chosen_hook=hook.text if hook else None,
            hook_level=hook.level if hook else None,
            hook_category=hook.category if hook else None,
            hook_date=hook.date if hook else None,
            hook_source=hook.source_url if hook else None,
            draft_subject=d.subject, draft_body=d.body, **db.authored(persona),
            failure_reason=None if d.body else d.note,
            fact_overrides={"excluded": sorted(excluded), "chosen": chosen})
        run = await db.get_run(run_id)

    async def run_tool(name: str, args: dict):
        nonlocal overrides, run
        if name == "list_facts":
            return _fact_rows(run, verdicts, overrides)
        if name == "list_sources":
            return [{"title": s.get("title"), "url": s.get("url"), "found_by": s.get("query")}
                    for s in (run.get("sources") or [])][:40]

        if name in ("exclude_fact", "include_fact", "choose_hook"):
            fid = str(args.get("fact_id") or "")
            known = {r["fact_id"] for r in _fact_rows(run, verdicts, overrides)}
            if fid not in known:
                return {"error": "no fact has that id — call list_facts first"}
            excluded = set(overrides.get("excluded") or [])
            chosen = overrides.get("chosen") or ""
            if name == "exclude_fact":
                excluded.add(fid)
                if chosen == fid:
                    chosen = ""
            elif name == "include_fact":
                excluded.discard(fid)
            else:
                excluded.discard(fid)
                chosen = fid
            overrides = {"excluded": sorted(excluded), "chosen": chosen}
            # Asking for a fact to be dropped and unticking it in the findings
            # column are the same judgement, so they teach the same thing. The
            # only difference recorded is which way the user said it.
            row = next((r for r in _fact_rows(run, verdicts, overrides)
                        if r["fact_id"] == fid), None)
            if row:
                await db.record_fact_feedback(
                    run_id,
                    {"exclude_fact": "excluded", "include_fact": "included",
                     "choose_hook": "chose"}[name],
                    row, "assistant")
            await rebuild()
            return {"ok": True, "hook_now": run.get("chosen_hook")}

        if name == "explain_choice":
            fid = str(args.get("fact_id") or "")
            reason = str(args.get("reason") or "").strip()
            if not reason:
                return {"error": "no reason given"}
            ok = await db.attach_fact_reason(run_id, fid, reason)
            return {"ok": ok} if ok else {
                "error": "nothing was done to that fact to attach a reason to"}

        if name == "rewrite_message":
            writer = WriterConfig(**(await db.get_config("writer") or {}))
            persona = await db.get_selected_persona()
            stakeholder = StakeholderProfile(
                **(stage_payload(run, "profile").get("stakeholder") or {}))
            hook = next((ExtractedFact(**v["fact"]) for v in verdicts
                         if (v.get("fact") or {}).get("text") == run.get("chosen_hook")), None)
            d = await draft_stage.revise(
                prospect, hook, run.get("draft_body") or "",
                str(args.get("instruction") or ""),
                writer=writer, stakeholder=stakeholder, persona=persona)
            if d.body:
                before = run.get("draft_body") or ""
                # The subject is part of the rewrite. Storing only the body left
                # the header line describing the draft before this one.
                fields = {"draft_body": d.body, **db.authored(persona)}
                if d.subject:
                    fields["draft_subject"] = d.subject
                await db.add_draft_revision(
                    run_id, before, d.body, subject=d.subject or "",
                    source="assistant", instruction=str(args.get("instruction") or ""),
                    hook=run.get("chosen_hook") or "",
                    persona_id=(persona or {}).get("id"))
                await db.update_run(run_id, **fields)
                run = await db.get_run(run_id)
                # An instruction plus the rewrite it produced is the clearest
                # evidence there is: the change AND the reason for it.
                from . import persona as persona_stage
                await persona_stage.note_edit(
                    run_id, before, d.body, str(args.get("instruction") or ""))
            return {"ok": True, "grounded": d.grounded, "note": d.note}

        return {"error": f"no such tool: {name}"}

    facts_now = _fact_rows(run, verdicts, overrides)

    # The thread so far, so a follow-up means what it says. Without this every
    # message arrived as if it were the first, so "no, the other one" had
    # nothing to refer to and the assistant could not ask a question and then
    # use the answer — it would ask again.
    history = await db.chat_turns(run_id, limit=config.CHAT_HISTORY_TURNS)
    thread = "\n".join(
        f"USER: {t['you']}\nYOU: {t['reply']}" for t in history if t.get("you"))

    prompt = (
        f"PROSPECT: {prospect.name}"
        f"{' at ' + prospect.company if prospect.company else ''}"
        f"{', ' + prospect.role if prospect.role else ''}\n\n"
        f"CURRENT MESSAGE:\n{run.get('draft_body') or '(none yet)'}\n\n"
        f"FACTS AVAILABLE ({len(facts_now)}): "
        f"{json.dumps([{k: f[k] for k in ('fact_id', 'text', 'category', 'eligible', 'excluded', 'is_hook')} for f in facts_now])}\n\n"
        + (f"CONVERSATION SO FAR:\n{thread}\n\n" if thread else "")
        + f"USER: {message.strip()}"
    )

    try:
        reply, actions = await llm.with_tools(
            prompt, TOOLS, run_tool, model=config.MODEL_SMART, system=SYSTEM)
    except llm.LLMFailure as e:
        return {"reply": f"I could not reach the model ({e.reason}).",
                "actions": [], "run": run}

    return {"reply": reply or "Done.", "actions": actions, "run": await db.get_run(run_id)}
