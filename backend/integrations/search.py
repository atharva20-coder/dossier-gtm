"""Tavily search client — real API calls only.

Design notes:
  * Every failure is typed. A search that fails for infrastructure reasons
    (quota, auth, timeout) must NEVER be reported as "this prospect has no
    public signal" — that conflation makes every downstream metric a lie.
  * Concurrency is bounded. 50 rows x 6 queries fired at once is 300
    simultaneous requests, which is a self-inflicted rate limit.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .. import config
from ..models import SearchHit

log = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"


class SearchFailure(Exception):
    """Infrastructure failure — distinct from 'found nothing'."""

    def __init__(self, reason: str, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


async def _one_query(
    client: httpx.AsyncClient,
    query: str,
    topic: str,
    sem: asyncio.Semaphore,
) -> tuple[str, list[SearchHit], str | None]:
    """Run a single query. Returns (query, hits, error). Never raises."""
    payload = {
        "query": query,
        "search_depth": "basic",          # 1 credit; 'advanced' costs 2x for marginal gain
        "max_results": config.MAX_RESULTS_PER_QUERY,
        "topic": topic,                    # 'news' index is fresher for press/funding
        "include_raw_content": "text",    # we need real page text for grounding checks
        "include_answer": False,           # we do our own synthesis
    }
    if topic == "news":
        payload["time_range"] = "year"     # pre-filter ancient results at the source

    async with sem:
        try:
            r = await client.post(
                TAVILY_URL,
                json=payload,
                headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
                timeout=config.SEARCH_TIMEOUT_S,
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            return query, [], f"network: {type(e).__name__}"

    if r.status_code == 401:
        return query, [], "auth: invalid or missing Tavily API key"
    if r.status_code == 429:
        return query, [], "rate_limit: Tavily rate limit hit"
    if r.status_code in (432, 433):
        return query, [], "quota: Tavily plan/credit limit exhausted"
    if r.status_code >= 400:
        return query, [], f"http_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return query, [], "bad_json"

    hits = []
    for item in data.get("results", []):
        body = item.get("raw_content") or item.get("content") or ""
        hits.append(
            SearchHit(
                title=item.get("title", ""),
                url=item.get("url", ""),
                content=body[:6000],       # cap per-source text sent to the model
                score=float(item.get("score") or 0.0),
                query=query,
            )
        )
    return query, hits, None


async def search_many(queries: list[tuple[str, str]]) -> tuple[list[SearchHit], list[str]]:
    """Run queries concurrently (bounded).

    `queries` is a list of (query_string, topic) pairs.
    Returns (hits, errors). If EVERY query failed for infrastructure reasons,
    the caller must treat this as research_failed — not as 'no signal'.
    """
    if not config.TAVILY_API_KEY:
        raise SearchFailure("auth: TAVILY_API_KEY is not set")

    sem = asyncio.Semaphore(config.MAX_CONCURRENT_SEARCHES)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_one_query(client, q, topic, sem) for q, topic in queries]
        )

    hits: list[SearchHit] = []
    errors: list[str] = []
    for query, qhits, err in results:
        if err:
            errors.append(f"{query}: {err}")
        hits.extend(qhits)

    # Deduplicate by URL, keeping the highest-scoring copy.
    best: dict[str, SearchHit] = {}
    for h in hits:
        if h.url and (h.url not in best or h.score > best[h.url].score):
            best[h.url] = h

    return list(best.values()), errors


async def health() -> tuple[bool, str]:
    """Cheap liveness probe used by /api/health so setup problems surface
    before a demo, not during one."""
    if not config.TAVILY_API_KEY:
        return False, "TAVILY_API_KEY not set"
    try:
        hits, errors = await search_many([("Anthropic", "general")])
    except SearchFailure as e:
        return False, e.reason
    if errors and not hits:
        return False, errors[0]
    return True, f"ok ({len(hits)} results)"
