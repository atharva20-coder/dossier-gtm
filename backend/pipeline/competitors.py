"""Competitor Discovery using Tavily web search and Gemini.

Searches for competitors of a target company and extracts structured
data about them.
"""

# `X | None` in an annotation is a syntax error before Python 3.10, and this
# app runs on 3.9. The future import defers annotation evaluation so the
# modern spelling works on both.
from __future__ import annotations
import logging
from typing import Any

from pydantic import BaseModel, Field
from .. import config
from ..integrations import llm, search
from . import normalize

log = logging.getLogger(__name__)


class Competitor(BaseModel):
    name: str = Field(description="The name of the competitor company")
    domain: str = Field(description="The primary domain of the competitor (e.g., 'stripe.com')")
    description: str = Field(description="A brief one-sentence description of what they do")
    similarity_reason: str = Field(description="Why they are a competitor (what product/market they share)")
    source_url: str = Field(default="", description="the result URL this company was named in")


class CompetitorList(BaseModel):
    competitors: list[Competitor] = Field(description="List of found competitors, up to 10")


EXTRACT_PROMPT = """From the search results below, identify the main competitors to '{company}'.

For each competitor you find:
- Give their exact name
- Give their primary domain (e.g., 'acme.com', NOT a full URL like 'https://www.acme.com/')
- Write a one-sentence description of what they do
- Explain briefly why they compete with {company} (e.g., 'They both offer expense management software for mid-market companies')

- Give the URL of the search result you took them from, exactly as shown.

Only include companies that genuinely compete in the same market. Do not include {company} itself.
A customer, an investor, a parent company or a partner is not a competitor.
If the results don't clearly identify competitors, return an empty list rather than guessing.

Search Results:
{results}
"""


async def find_competitors(company_name: str) -> list[dict[str, Any]]:
    """Discover competitors for a company using web search."""
    label = company_name.split("//")[-1].split("/")[0]

    # 1. Search the web. Two angles, because "competitors of X" and
    #    "alternatives to X" surface different pages.
    hits, _errors = await search.search_many([
        (f"top competitors and alternatives to {label}", "general"),
        (f'best alternatives to "{label}"', "general"),
    ])
    if not hits:
        return []

    formatted = "\n\n".join(
        f"URL: {h.url}\nTitle: {h.title}\nContent: {(h.content or '')[:1200]}"
        for h in hits[:12])

    # 2. Extract structured list
    prompt = EXTRACT_PROMPT.format(company=label, results=formatted)

    try:
        response = await llm.structured(CompetitorList, prompt, model=config.MODEL_SMART)
    except Exception as e:
        log.error(f"Competitor extraction failed: {e}")
        return []

    # 3. Keep only what the results actually said.
    #
    # An uncited competitor is a company the model recalled rather than read,
    # and a campaign built on recall targets the wrong people convincingly. The
    # target cannot be its own competitor either, however it was spelled.
    urls = {h.url.rstrip("/") for h in hits}
    target_key = normalize.company_key(label)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in response.competitors:
        key = normalize.company_key(c.name)
        if not key or key in seen or key == target_key:
            continue
        if not c.source_url or c.source_url.rstrip("/") not in urls:
            log.warning("dropped competitor %s — not cited in the search results", c.name)
            continue
        seen.add(key)
        out.append(c.model_dump())
    return out
