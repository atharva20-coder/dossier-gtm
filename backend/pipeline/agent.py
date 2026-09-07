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

WHAT IT CAN REACH
-----------------
Everything the interface can, through `api_catalog` and `api_call`: research a
new person, run the pipeline, manage personas and outbound campaigns, find
contacts, change config, export. Calls go through the app's own HTTP API in
process, over ASGI, carrying a session cookie — so they pass the same security
gate, hit the same validation and the same connection pool as a click would.
There is no second code path to keep in step, and no tool that can reach the
database behind the API's back.

THE ONE THING THAT DID NOT CHANGE
---------------------------------
The assistant still cannot assert a fact of its own. New information enters only
by an endpoint that grounds it against a real source, exactly as a click would
have — otherwise "include that he moved to Stripe" becomes a line in an email
with nothing behind it. Widening what it can DO deliberately did not widen what
it can CLAIM.

Two actions are refused outright without explicit approval in the conversation:
sending mail, which cannot be recalled, and deleting, which takes the research
with it. See _needs_confirm.
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

DRIVING THE WHOLE APPLICATION
You are not limited to this prospect's facts. `api_catalog` lists every
operation the app can perform and `api_call` performs one, as the user. Between
them you can research a new person, start and execute runs, upload leads, manage
personas, run outbound campaigns, find contacts and addresses, change config and
export — anything the interface can do.

- When a request goes beyond the current facts, call `api_catalog` first and
  work from what it lists. Do not guess a path.
- Paths take real values: /api/runs/41/execute, never /api/runs/{run_id}.
- Report what actually came back. A non-2xx status is a failure — say so and say
  what it said, rather than narrating the call as if it worked.
- Research spends search and model credits. For an ordinary request just do it;
  for something obviously expensive — a bulk run over many leads — say what it
  will cost and get a yes first.

SENDING AND DELETING
`api_call` refuses anything that sends mail or deletes, unless you pass
confirm=true. Pass it only after the user has approved that exact action in
plain words in this conversation. "Send it" about the message under discussion
is approval; "draft something to Pedro" is not. Never confirm on your own
initiative, and never re-send because a first attempt looked ambiguous.

HARD RULES
- Never invent facts about the prospect. Use what `list_facts` returns, or what
  a real API call actually returned. If asked to include something neither has,
  say plainly that the research did not find it, and offer what is there instead.
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
    {
        "name": "api_catalog",
        "description": (
            "Every operation this application can perform, with its method, path, "
            "query parameters and request-body fields. Call this FIRST whenever the "
            "request goes beyond the current prospect's facts — researching a new "
            "person, starting or executing a run, personas, outbound campaigns, "
            "contact discovery, config, exports, sending. It is read from the app's "
            "live spec, so it is always current."),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "api_call",
        "description": (
            "Perform one operation from api_catalog, as the user. This is how you "
            "actually do things: research a prospect, run the pipeline, edit a "
            "persona, find contacts, export. Paths take real values, not "
            "placeholders — /api/runs/41, never /api/runs/{run_id}. Anything that "
            "sends mail or deletes is refused unless confirm=true, which you may "
            "only pass after the user has said yes in plain words."),
        "parameters": {
            "type": "object",
            "properties": {
                "method": {"type": "string",
                           "description": "GET, POST, PATCH, PUT or DELETE"},
                "path": {"type": "string",
                         "description": "e.g. /api/runs or /api/runs/41/execute"},
                "body": {"type": "object",
                         "description": "JSON request body, for POST/PATCH/PUT"},
                "query": {"type": "object", "description": "query-string parameters"},
                "confirm": {"type": "boolean",
                            "description": ("true only after the user explicitly "
                                            "approved this exact send or deletion")},
            },
            "required": ["method", "path"],
        },
    },
]


# ------------------------------------------------------------- app control ---
# The assistant drives the whole application through its own HTTP API rather
# than through fifty hand-written tool schemas. FastAPI already publishes the
# catalogue at app.openapi(), so this cannot drift out of date when an endpoint
# is added, renamed or removed — a hand-maintained tool list silently would.
#
# Calls are dispatched in-process over ASGI: no socket, no second uvicorn, and
# the same connection pool. They pass through security.gate exactly as a browser
# request does, carrying a freshly issued session cookie, so the assistant acts
# with the user's authority and no more.
_BLOCKED = (
    "/api/auth",        # the browser's session is not the assistant's business
    "/api/ping",        # a healthcheck it could only answer about itself
)
# Calling the chat endpoint from inside a chat turn is unbounded recursion.
_BLOCKED_SUFFIX = ("/chat",)

# Mail to a real person cannot be recalled, and a deleted run takes its research
# with it. Both need the user to have actually said so in this conversation. The
# model sets confirm=true only after asking; this check is what makes that a
# rule rather than a suggestion it can talk itself out of.
_CONFIRM_METHODS = ("DELETE",)
_CONFIRM_SUFFIX = ("/send",)


def _needs_confirm(method: str, path: str) -> bool:
    return method.upper() in _CONFIRM_METHODS or path.rstrip("/").endswith(_CONFIRM_SUFFIX)


def _allowed(path: str) -> bool:
    return (path.startswith("/api/")
            and not path.startswith(_BLOCKED)
            and not path.rstrip("/").endswith(_BLOCKED_SUFFIX))


def _body_fields(op: dict, schemas: dict) -> dict:
    """Property names and types of an endpoint's request body, one level deep.

    Enough for the model to construct a call; not the whole component graph,
    which would cost more tokens than the endpoint list itself.
    """
    ref = (((op.get("requestBody") or {}).get("content") or {})
           .get("application/json") or {}).get("schema") or {}
    name = (ref.get("$ref") or "").rsplit("/", 1)[-1]
    props = (schemas.get(name) or {}).get("properties") or ref.get("properties") or {}
    return {k: (v.get("type") or "object") for k, v in props.items()}


def api_catalog() -> list[dict]:
    """Every endpoint the assistant may call, read from the app's own spec."""
    from ..main import app
    spec = app.openapi()
    schemas = (spec.get("components") or {}).get("schemas") or {}
    out: list[dict] = []
    for path, ops in (spec.get("paths") or {}).items():
        if not _allowed(path):
            continue
        for method, op in ops.items():
            if method.upper() not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
                continue
            entry: dict = {
                "method": method.upper(),
                "path": path,
                "what": (op.get("summary") or "").strip()[:120],
            }
            params = [q.get("name") for q in (op.get("parameters") or [])
                      if q.get("in") == "query"]
            if params:
                entry["query"] = params
            fields = _body_fields(op, schemas)
            if fields:
                entry["body"] = fields
            if _needs_confirm(method, path):
                entry["confirm_required"] = True
            out.append(entry)
    return out


async def api_call(method: str, path: str,
                   body: dict | None = None, query: dict | None = None,
                   confirm: bool = False) -> dict:
    """Perform one API call as the user. Returns {status, data}."""
    import httpx

    from .. import security
    from ..main import app

    method = (method or "GET").upper()
    if not _allowed(path):
        return {"error": f"{path} is not callable from chat"}
    if _needs_confirm(method, path) and not confirm:
        return {"error": "refused: this sends or destroys something. Ask the user "
                         "to confirm in plain words first, then call again with "
                         "confirm=true.",
                "needs_confirm": True}

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://internal",
            cookies={security.COOKIE: security.issue()},
            # An execute call runs the whole pipeline; it is the slow one.
            timeout=float(config.RUN_STALE_AFTER_S)) as client:
        try:
            r = await client.request(method, path, json=body or None,
                                     params=query or None)
        except Exception as e:  # noqa: BLE001 — a tool failure is a result, not a crash
            log.warning("api_call %s %s failed: %s", method, path, type(e).__name__)
            return {"error": f"call failed ({type(e).__name__})"}

    try:
        data = r.json()
    except ValueError:
        data = r.text[:4000]
    # A run listing or an export can be enormous; the model needs the shape and
    # the first rows, not every byte.
    text = json.dumps(data, default=str)
    if len(text) > 12000:
        data = {"truncated": True, "preview": text[:12000]}
    return {"status": r.status_code, "data": data}


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
                    await db.hook_outcomes((persona or {}).get("id")),
                    await db.fact_feedback_counts(
                        (persona or {}).get("id"))))).chosen

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
                    row, "assistant",
                    persona_id=(await db.get_selected_persona() or {}).get("id"))
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

        if name == "api_catalog":
            return api_catalog()

        if name == "api_call":
            result = await api_call(
                str(args.get("method") or "GET"),
                str(args.get("path") or ""),
                body=args.get("body") if isinstance(args.get("body"), dict) else None,
                query=args.get("query") if isinstance(args.get("query"), dict) else None,
                confirm=bool(args.get("confirm")),
            )
            # A call may have changed this very run — re-read it so the reply and
            # the returned run are the state after the change, not before it.
            fresh = await db.get_run(run_id)
            if fresh:
                run = fresh
            return result

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
            prompt, TOOLS, run_tool, model=config.MODEL_SMART, system=SYSTEM,
            # Driving the app takes more steps than rearranging facts did:
            # catalogue, then act, then read the result back. Six ran out
            # mid-task and the turn ended with the work half done.
            max_steps=config.CHAT_MAX_TOOL_STEPS)
    except llm.LLMFailure as e:
        return {"reply": f"I could not reach the model ({e.reason}).",
                "actions": [], "run": run}

    return {"reply": reply or "Done.", "actions": actions, "run": await db.get_run(run_id)}


def _demo() -> None:
    """Self-check for the guards. The catalogue can grow; these must not slip."""
    import asyncio

    # Recursion and the browser's session are out of reach.
    assert not _allowed("/api/runs/41/chat"), "chat must not call itself"
    assert not _allowed("/api/auth"), "session is the browser's business"
    assert not _allowed("/api/ping")
    assert not _allowed("/"), "only the API is reachable"
    assert not _allowed("/assets/index.js")

    # The ordinary surface is.
    assert _allowed("/api/runs") and _allowed("/api/runs/41/execute")
    assert _allowed("/api/personas") and _allowed("/api/outbound/runs")

    # Irreversible actions are gated; reading and ordinary writes are not.
    assert _needs_confirm("POST", "/api/runs/41/send")
    assert _needs_confirm("POST", "/api/outbound/runs/2/contacts/9/send/")
    assert _needs_confirm("DELETE", "/api/runs/41")
    assert not _needs_confirm("GET", "/api/runs")
    assert not _needs_confirm("POST", "/api/runs/41/execute")
    # "/send" must match a path segment, not a prefix of one.
    assert not _needs_confirm("GET", "/api/send/status")

    # The gate is enforced in api_call, not merely described in the prompt.
    refused = asyncio.run(api_call("POST", "/api/runs/41/send", body={}))
    assert refused.get("needs_confirm"), refused
    blocked = asyncio.run(api_call("POST", "/api/runs/41/chat", body={}))
    assert "error" in blocked and "needs_confirm" not in blocked, blocked

    # Body fields are read from the component schema the endpoint references.
    schemas = {"SendRequest": {"properties": {"address": {"type": "string"}}}}
    op = {"requestBody": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/SendRequest"}}}}}
    assert _body_fields(op, schemas) == {"address": "string"}
    assert _body_fields({}, schemas) == {}

    cat = api_catalog()
    paths = {(e["method"], e["path"]) for e in cat}
    assert ("POST", "/api/runs") in paths, "the catalogue must reach real endpoints"
    assert not any(p.endswith("/chat") for _, p in paths)
    assert not any(p.startswith("/api/auth") for _, p in paths)
    assert all(e.get("confirm_required") for e in cat
               if _needs_confirm(e["method"], e["path"])), \
        "anything gated must be advertised as gated"
    print(f"agent checks passed ({len(cat)} operations reachable)")


if __name__ == "__main__":
    _demo()
