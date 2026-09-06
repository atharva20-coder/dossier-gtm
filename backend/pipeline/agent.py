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

SYSTEM = """You are the assistant inside a sales-research tool, working on one prospect.

You can inspect what the research found and act on it with the tools you have.
Prefer acting over describing: if the user asks for a change you have a tool for,
call it rather than explaining how they could do it themselves.

HARD RULES
- Never invent facts about the prospect. You may only use what `list_facts`
  returns. If asked to include something not there, say plainly that the research
  did not find it.
- Excluding a fact or choosing a hook rewrites the message from the remaining
  evidence. That is expected; do it when asked.
- After acting, reply in one or two short sentences saying what you did. No
  bullet lists, no restating the whole message back.
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
            hook = judge_stage.judge(facts, target_company=prospect.company,
                                     writer=writer, stakeholder=stakeholder).chosen

        d = await draft_stage.write(prospect, hook, writer=writer,
                                    stakeholder=stakeholder, persona=persona)
        await db.update_run(
            run_id,
            status="completed" if hook else "no_signal_found",
            chosen_hook=hook.text if hook else None,
            hook_level=hook.level if hook else None,
            hook_category=hook.category if hook else None,
            hook_date=hook.date if hook else None,
            hook_source=hook.source_url if hook else None,
            draft_subject=d.subject, draft_body=d.body, **db.authored(persona),
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
            await rebuild()
            return {"ok": True, "hook_now": run.get("chosen_hook")}

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
    prompt = (
        f"PROSPECT: {prospect.name}"
        f"{' at ' + prospect.company if prospect.company else ''}"
        f"{', ' + prospect.role if prospect.role else ''}\n\n"
        f"CURRENT MESSAGE:\n{run.get('draft_body') or '(none yet)'}\n\n"
        f"FACTS AVAILABLE ({len(facts_now)}): "
        f"{json.dumps([{k: f[k] for k in ('fact_id', 'text', 'eligible', 'excluded', 'is_hook')} for f in facts_now])}\n\n"
        f"USER: {message.strip()}"
    )

    try:
        reply, actions = await llm.with_tools(
            prompt, TOOLS, run_tool, model=config.MODEL_SMART, system=SYSTEM)
    except llm.LLMFailure as e:
        return {"reply": f"I could not reach the model ({e.reason}).",
                "actions": [], "run": run}

    return {"reply": reply or "Done.", "actions": actions, "run": await db.get_run(run_id)}
