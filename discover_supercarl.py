#!/usr/bin/env python3
"""Super Carl API discovery probe.

    python discover_supercarl.py

Run this on a machine with internet. It probes the Super Carl API with your key
and prints exactly what works: which auth header shape is accepted, which
endpoints respond, and what the response bodies look like.

Why a probe rather than a finished integration: their public docs don't publish
the auth header format or response schemas, and writing an integration against
a guessed contract produces code that fails in ways that are slow to debug.
Run this, paste the output back, and the integration gets written against
observed reality instead.

Nothing here is destructive — every call is a read.
"""
from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

KEY = os.getenv("SUPERCARL_API_KEY", "").strip()
BASE = "https://api.supercarl.ai"

# The person we'll look up. Public figure, easy to sanity-check the result.
TEST_LINKEDIN = "https://www.linkedin.com/in/radhikapatil"
TEST_QUERY = "co-founder of Cradlewise in Bengaluru"

AUTH_SHAPES = [
    ("Authorization: Bearer", {"Authorization": f"Bearer {KEY}"}),
    ("Authorization: raw",    {"Authorization": KEY}),
    ("x-api-key",             {"x-api-key": KEY}),
    ("X-API-Key",             {"X-API-Key": KEY}),
    ("api-key",               {"api-key": KEY}),
]

ENDPOINTS = [
    ("people search v1",  "POST", "/api/v1/search/people",
     {"linkedin_url": TEST_LINKEDIN}),
    ("people search v1 (username)", "POST", "/api/v1/search/people",
     {"linkedin_username": "radhikapatil"}),
    ("people query v2",   "POST", "/api/v2/search/people/query",
     {"query": TEST_QUERY}),
    ("people query v2 (q)", "POST", "/api/v2/search/people/query",
     {"q": TEST_QUERY}),
]


def show(label: str, r: httpx.Response, body_chars: int = 1500) -> None:
    print(f"    status {r.status_code}")
    ct = r.headers.get("content-type", "")
    try:
        data = r.json()
        text = json.dumps(data, indent=2)[:body_chars]
    except Exception:
        text = r.text[:body_chars]
    print(f"    content-type: {ct}")
    for line in text.splitlines():
        print(f"      {line}")
    if len(text) >= body_chars:
        print("      … (truncated)")


def main() -> int:
    if not KEY:
        print("SUPERCARL_API_KEY is not set. Add it to .env:")
        print("    SUPERCARL_API_KEY=carl_...")
        return 1

    print(f"\nSuper Carl API probe — key ending …{KEY[-6:]}\n" + "=" * 64)

    # ---- 1. find the auth shape it accepts --------------------------------
    print("\n1. Which auth header is accepted?")
    working_auth = None
    with httpx.Client(timeout=25) as c:
        for label, headers in AUTH_SHAPES:
            try:
                r = c.post(f"{BASE}/api/v2/search/people/query",
                           headers={**headers, "Content-Type": "application/json"},
                           json={"query": TEST_QUERY})
            except Exception as e:
                print(f"  {label:26s} -> network error: {type(e).__name__}")
                continue
            verdict = "ACCEPTED" if r.status_code < 400 else (
                "auth rejected" if r.status_code in (401, 403) else f"HTTP {r.status_code}")
            print(f"  {label:26s} -> {verdict}")
            if r.status_code < 400 and working_auth is None:
                working_auth = (label, headers)

    if not working_auth:
        print("\n  No auth shape was accepted. Either the key isn't active yet,")
        print("  or the API expects something not tried here. The last response:")
        with httpx.Client(timeout=25) as c:
            r = c.post(f"{BASE}/api/v2/search/people/query",
                       headers={"Authorization": f"Bearer {KEY}",
                                "Content-Type": "application/json"},
                       json={"query": TEST_QUERY})
            show("last", r)
        return 1

    label, headers = working_auth
    print(f"\n  -> using: {label}")

    # ---- 2. probe the endpoints -------------------------------------------
    print("\n2. Endpoint responses\n" + "-" * 64)
    with httpx.Client(timeout=40) as c:
        for name, method, path, body in ENDPOINTS:
            print(f"\n  [{name}] {method} {path}")
            print(f"    request: {json.dumps(body)}")
            try:
                r = c.request(method, f"{BASE}{path}",
                              headers={**headers, "Content-Type": "application/json"},
                              json=body)
            except Exception as e:
                print(f"    network error: {type(e).__name__}: {e}")
                continue
            show(name, r)

    # ---- 3. quota / plan hints --------------------------------------------
    print("\n3. Any quota or plan headers returned")
    with httpx.Client(timeout=25) as c:
        r = c.post(f"{BASE}/api/v2/search/people/query",
                   headers={**headers, "Content-Type": "application/json"},
                   json={"query": TEST_QUERY})
        interesting = {k: v for k, v in r.headers.items()
                       if any(t in k.lower() for t in
                              ("rate", "limit", "quota", "credit", "remaining", "plan"))}
        print(f"    {interesting or 'none reported'}")

    print("\n" + "=" * 64)
    print("Paste this whole output back and the integration gets written")
    print("against the real response shape.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
