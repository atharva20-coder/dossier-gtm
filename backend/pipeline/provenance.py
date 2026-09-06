"""Where a fact came from, and how much that is worth.

Not all sources are equal evidence about a person, and the gap is largest
exactly where it matters most. What someone says in their own LinkedIn post is
first-party: they wrote it, they published it, and it is current. A conference
programme listing their name is second-hand. A directory scrape that mentions
them is barely evidence at all, and — as the namesake podcast taught us — is
where the wrong person gets in.

So the ordering for PERSON-level facts is LinkedIn, then X, then the open web.

For COMPANY-level facts the open web is genuinely good: funding rounds, launches
and expansions are reported by journalists who verify them, and a TechCrunch
piece is not worth less than a company's own post about itself. Web sources are
therefore ranked normally at company level — but a first-party post still edges
ahead, because it is the company speaking rather than someone summarising.

Finally, corroboration. A fact that shows up BOTH on a social profile and in
independent web reporting has been confirmed by two parties who did not copy
each other, which is a different and stronger thing than one source repeated.
That earns a bonus — but only across TIERS. Ten aggregators carrying one press
release is one fact wearing ten outfits, and `collapse_syndicated` already
exists to stop that being mistaken for confirmation.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Tier names, strongest first. Kept as plain strings because they appear in
# stage details the user reads.
LINKEDIN = "linkedin"
X = "x"
WEB = "web"

_LINKEDIN_HOSTS = ("linkedin.com", "lnkd.in")
_X_HOSTS = ("x.com", "twitter.com", "t.co")

# The person-signal provider returns LinkedIn profile and post content behind
# its own URL, so it is LinkedIn evidence wearing a different hostname. Ranking
# it as "web" would systematically underrate the single most reliable source the
# system has.
_LINKEDIN_PROXY_HOSTS = ("supercarl.ai",)


def tier_of_url(url: str) -> str:
    """Which evidence tier a source URL belongs to."""
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return WEB
    host = host[4:] if host.startswith("www.") else host

    def matches(candidates: tuple[str, ...]) -> bool:
        # Suffix match on a dot boundary so "notlinkedin.com" never counts.
        return any(host == c or host.endswith("." + c) for c in candidates)

    if matches(_LINKEDIN_HOSTS) or matches(_LINKEDIN_PROXY_HOSTS):
        return LINKEDIN
    if matches(_X_HOSTS):
        return X
    return WEB


# Multipliers applied to a fact's score. Person-level and company-level are
# separate scales because the same source means different things at each level:
# a stranger's blog post about a person is weak evidence, while a journalist's
# article about a company is strong evidence.
#
# These are deliberately modest. They are meant to break ties between comparable
# hooks, not to let a stale LinkedIn post outrank fresh, relevant news — recency
# and intent still dominate the score.
WEIGHTS: dict[str, dict[str, float]] = {
    "person": {LINKEDIN: 1.30, X: 1.15, WEB: 0.85},
    "company": {LINKEDIN: 1.10, X: 1.05, WEB: 1.00},
}

# Applied when one fact is supported by both a first-party post and independent
# web reporting. Two parties, not one repeated.
CORROBORATION_BONUS = 1.20


def source_weight(level: str, tier: str) -> float:
    scale = WEIGHTS.get(level if level in WEIGHTS else "company", WEIGHTS["company"])
    return scale.get(tier, scale[WEB])


def tiers_present(urls: list[str]) -> set[str]:
    return {tier_of_url(u) for u in urls if u}


def is_corroborated(urls: list[str]) -> bool:
    """True when a first-party post AND independent web reporting both carry it."""
    tiers = tiers_present(urls)
    return bool(tiers & {LINKEDIN, X}) and WEB in tiers


def describe(level: str, urls: list[str]) -> str:
    """One human-readable clause explaining the provenance adjustment.

    The judge's reasons are read by a rep deciding whether to trust a hook, so
    every multiplier applied to a score has to be sayable in plain words.
    """
    tiers = tiers_present(urls)
    best = LINKEDIN if LINKEDIN in tiers else (X if X in tiers else WEB)
    label = {LINKEDIN: "LinkedIn", X: "X", WEB: "web search"}[best]

    if is_corroborated(urls):
        return f"corroborated — carried both on {label} and by independent web reporting"
    if best == WEB:
        return ("from web search" if level != "person"
                else "from web search only — no first-party post backs it")
    return f"first-party {label} post"


def multiplier(level: str, urls: list[str]) -> float:
    """Total provenance multiplier for a fact supported by these URLs."""
    tiers = tiers_present(urls)
    best = LINKEDIN if LINKEDIN in tiers else (X if X in tiers else WEB)
    m = source_weight(level, best)
    if is_corroborated(urls):
        m *= CORROBORATION_BONUS
    return m


def _demo() -> None:
    assert tier_of_url("https://www.linkedin.com/in/x/") == LINKEDIN
    assert tier_of_url("https://supercarl.ai/profile/abc") == LINKEDIN
    assert tier_of_url("https://x.com/someone/status/1") == X
    assert tier_of_url("https://twitter.com/someone") == X
    assert tier_of_url("https://techcrunch.com/a") == WEB
    assert tier_of_url("https://notlinkedin.com/a") == WEB, "suffix must match on a dot"
    assert tier_of_url("") == WEB

    li, web = "https://linkedin.com/posts/1", "https://techcrunch.com/a"
    assert multiplier("person", [li]) > multiplier("person", [web])
    assert multiplier("person", [li, web]) > multiplier("person", [li])
    assert not is_corroborated([li, "https://x.com/a"]), "two social sources are not corroboration"
    assert is_corroborated([li, web])
    # Company level: web is respectable, not penalised.
    assert source_weight("company", WEB) == 1.00
    assert source_weight("person", WEB) < 1.00
    print("provenance checks passed")


if __name__ == "__main__":
    _demo()
