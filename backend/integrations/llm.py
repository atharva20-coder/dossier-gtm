"""Gemini client — real API calls only.

Model split is deliberate (see DECISION_LOG.md D3):
  * MODEL_FAST  — mechanical stages (query generation, extraction). High volume,
                  low judgment. Cheap and quick.
  * MODEL_SMART — judgment and drafting. This is where output quality is the
                  whole point, so it gets the stronger model.

Structured stages use Gemini's native response_schema so we get validated JSON
instead of parsing prose and hoping.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Type, TypeVar, get_args

from google import genai
from google.genai import types
from pydantic import BaseModel

from .. import config

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_client: genai.Client | None = None


class LLMFailure(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def client() -> genai.Client:
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise LLMFailure("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


async def _generate(model: str, prompt: str, cfg: types.GenerateContentConfig) -> str:
    """Single call with a timeout. Runs the sync SDK in a thread so it doesn't
    block the event loop (which would defeat the whole point of parallel search)."""
    def _call():
        resp = client().models.generate_content(model=model, contents=prompt, config=cfg)
        return resp.text or ""

    try:
        return await asyncio.wait_for(asyncio.to_thread(_call), timeout=config.LLM_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise LLMFailure(f"{model}: timed out after {config.LLM_TIMEOUT_S}s")
    except Exception as e:  # SDK raises a variety of transport/API errors
        raise LLMFailure(f"{model}: {type(e).__name__}: {e}")


def _hidden_fields(schema: Type[BaseModel], _seen: set | None = None) -> set[str]:
    """Field names marked `llm: False`, across the model and everything nested
    inside it — the marked fields usually live on the inner model, not the
    wrapper the caller passes in."""
    _seen = _seen if _seen is not None else set()
    if schema in _seen:
        return set()
    _seen.add(schema)

    hidden: set[str] = set()
    for name, f in schema.model_fields.items():
        if isinstance(f.json_schema_extra, dict) and f.json_schema_extra.get("llm") is False:
            hidden.add(name)
        for arg in (f.annotation, *get_args(f.annotation)):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                hidden |= _hidden_fields(arg, _seen)
    return hidden


def _thinking(model: str) -> types.ThinkingConfig | None:
    """Cap reasoning tokens on the mechanical stages.

    Extraction, query generation and identity resolution are transcription
    jobs: read the text, copy out what it says. Left unbounded the model spends
    most of its time reasoning about work that does not need reasoning —
    measured at 12.8s per extraction batch against 5.0s with a 512-token cap,
    for the same facts. Below ~512 the quality does drop (2 facts instead of 4
    on the same sources), which is why this is a cap rather than an off switch.

    The judgment stages use MODEL_SMART and are left alone: thinking is what
    they are for.
    """
    if model != config.MODEL_FAST or config.FAST_THINKING_BUDGET < 0:
        return None
    return types.ThinkingConfig(thinking_budget=config.FAST_THINKING_BUDGET)


def _llm_schema(schema: Type[BaseModel]) -> dict:
    """The JSON schema to show the model, minus fields it must not fill.

    Some fields on these models are computed by the pipeline afterwards rather
    than read out of the sources — provenance URLs, for instance. Leaving them
    in the schema invites the model to invent values for them, and an invented
    source URL is precisely the class of failure the grounding stage exists to
    prevent. Mark such a field `json_schema_extra={"llm": False}` and it is
    never shown.
    """
    js = schema.model_json_schema()
    hidden = _hidden_fields(schema)
    # Nested models land in $defs, so every definition needs the same treatment.
    for block in [js, *js.get("$defs", {}).values()]:
        props = block.get("properties")
        if not props:
            continue
        for name in hidden & set(props):
            props.pop(name)
            if name in block.get("required", []):
                block["required"].remove(name)
    return js


async def structured(
    schema: Type[T],
    prompt: str,
    *,
    model: str | None = None,
    system: str = "",
    temperature: float = 0.2,
) -> T:
    """Call Gemini and get back a validated instance of `schema`.

    Retries once on malformed output — a parse failure must never become a
    silent empty result (see SCENARIOS.md D2).
    """
    model = model or config.MODEL_FAST
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_llm_schema(schema),
        temperature=temperature,
        system_instruction=system or None,
        thinking_config=_thinking(model),
    )

    last_err = ""
    for attempt in range(2):
        raw = await _generate(model, prompt, cfg)
        try:
            return schema.model_validate_json(raw)
        except Exception:
            try:
                return schema.model_validate(json.loads(raw))
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                log.warning("structured output parse failed (attempt %s): %s", attempt + 1, last_err)

    raise LLMFailure(f"{model}: could not parse structured output. {last_err}")


async def with_tools(
    prompt: str,
    tools: list[dict],
    run_tool,
    *,
    model: str | None = None,
    system: str = "",
    max_steps: int = 6,
) -> tuple[str, list[dict]]:
    """Run a tool-calling loop and return (final_text, calls_made).

    `tools` are Gemini function declarations; `run_tool(name, args)` performs one
    and returns a JSON-serialisable result. The loop stops when the model answers
    with text instead of a call, or when `max_steps` is reached — a bound rather
    than a guess, because a model that keeps calling tools in a circle would
    otherwise spend the whole request budget discovering that.
    """
    model = model or config.MODEL_SMART
    cfg = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=tools)],
        system_instruction=system or None,
        temperature=0.3,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=prompt)])]
    made: list[dict] = []

    for _ in range(max_steps):
        def _call():
            return client().models.generate_content(model=model, contents=contents, config=cfg)
        try:
            resp = await asyncio.wait_for(
                asyncio.to_thread(_call), timeout=config.LLM_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise LLMFailure(f"{model}: timed out after {config.LLM_TIMEOUT_S}s")
        except Exception as e:
            raise LLMFailure(f"{model}: {type(e).__name__}: {e}")

        candidate = (resp.candidates or [None])[0]
        parts = list(getattr(getattr(candidate, "content", None), "parts", None) or [])
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if not calls:
            return (resp.text or "").strip(), made

        contents.append(types.Content(role="model", parts=parts))
        replies = []
        for call in calls:
            args = dict(call.args or {})
            try:
                result = await run_tool(call.name, args)
            except Exception as e:      # a failing tool is a result, not a crash
                result = {"error": f"{type(e).__name__}: {e}"}
            made.append({"name": call.name, "args": args, "result": result})
            replies.append(types.Part.from_function_response(
                name=call.name, response={"result": result}))
        contents.append(types.Content(role="user", parts=replies))

    return ("I made the changes I could, but stopped before finishing — "
            "try asking for one thing at a time."), made


async def text(
    prompt: str,
    *,
    model: str | None = None,
    system: str = "",
    temperature: float = 0.7,
) -> str:
    """Free-text generation (drafting)."""
    cfg = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system or None,
    )
    return (await _generate(model or config.MODEL_SMART, prompt, cfg)).strip()


async def health() -> tuple[bool, str]:
    if not config.GEMINI_API_KEY:
        return False, "GEMINI_API_KEY not set"
    try:
        out = await text("Reply with the single word: ok", model=config.MODEL_FAST, temperature=0)
        return True, f"ok ({config.MODEL_FAST} responded)"
    except LLMFailure as e:
        return False, e.reason
