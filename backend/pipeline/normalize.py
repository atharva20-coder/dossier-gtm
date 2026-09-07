"""Input normalisation.

Why this module exists: a large share of production "the AI found nothing" is
actually input hygiene failure, not research failure. People paste LinkedIn
headlines into name columns constantly:

    "Dr. Radhika Patil (she/her) | Founder @ Cradlewise | ex-Google"

Searched literally, that returns nothing, and the run then reports
"no public signal" — a silent failure blaming the prospect for our parsing bug.
"""
from __future__ import annotations

import re

HONORIFICS = {
    "mr", "mrs", "ms", "miss", "dr", "prof", "professor", "sir", "shri", "smt",
    "er", "ca", "adv", "capt", "col", "maj", "lt", "rev", "hon",
}

PRONOUNS = {
    "he/him", "she/her", "they/them", "he/they", "she/they", "any/all",
    "him/his", "her/hers",
}

# Trailing credentials we drop from a personal name.
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "mba", "cfa", "cpa", "esq"}


def clean_name(raw: str) -> str:
    """Reduce a messy pasted string to a searchable personal name."""
    if not raw:
        return ""

    s = str(raw).strip()

    # 1. Anything after a pipe / bullet / en-dash is almost always a headline,
    #    not part of the name: "Radhika Patil | Founder @ Cradlewise"
    s = re.split(r"[|•·–—]", s)[0]

    # 2. Drop parenthetical asides, which are usually pronouns or nicknames.
    s = re.sub(r"\([^)]*\)", " ", s)

    # 3. Drop an "@ Company" fragment if one survived.
    s = re.split(r"\s+@\s*", s)[0]

    # 4. Emails sometimes get pasted into name fields.
    s = re.sub(r"\S+@\S+\.\S+", " ", s)

    # 5. Strip emoji / symbol noise, keep letters (incl. non-Latin), marks,
    #    spaces, hyphens and apostrophes.
    s = re.sub(r"[^\w\s'\-\.]", " ", s, flags=re.UNICODE)
    s = re.sub(r"[\d_]+", " ", s)

    tokens = [t for t in re.split(r"\s+", s) if t]

    out: list[str] = []
    for t in tokens:
        bare = t.strip(".").lower()
        if bare in PRONOUNS:
            continue
        if bare in HONORIFICS and not out:      # only strip a leading honorific
            continue
        if bare in SUFFIXES and out:            # only strip a trailing suffix
            continue
        if bare.startswith("ex-"):
            continue
        out.append(t.strip("."))

    name = " ".join(out).strip()
    # Collapse ALL-CAPS input to title case; leave normal casing alone.
    if name and name.isupper():
        name = name.title()
    return name


# Things that mean "there is no company here", written where a company goes.
# A spreadsheet column has them typed by hand; the identity stage produces them
# when a model is asked for a field it could not find and answers in prose.
#
# They must become "" rather than survive as text. An empty company is handled
# everywhere — it disables the site-scoped social queries and the graph
# traversal, both of which need a real employer to disambiguate with. A string
# does none of that: it is truthy, so it flows into a search as
# `site:x.com "Atharva" "Not stated"`, spending a credit on a query that cannot
# match anything, and into a draft as the name of where someone works.
_NO_COMPANY = {
    "", "-", "--", "n/a", "na", "none", "null", "nil", "nan",
    "not stated", "not specified", "not provided", "not available",
    "not found", "not known", "unknown", "unspecified", "undisclosed",
    "no company", "not applicable", "tbd", "?", "unnamed",
}


def is_placeholder_company(raw: str) -> bool:
    """Whether a company field is one of the ways of writing 'blank'."""
    return re.sub(r"[\s.()\[\]]+", " ", str(raw or "")).strip().lower() in _NO_COMPANY


def clean_company(raw: str) -> str:
    """Normalise a company field that may contain a URL or LinkedIn slug."""
    if not raw or is_placeholder_company(raw):
        return ""
    s = str(raw).strip()

    m = re.search(r"linkedin\.com/company/([^/?#\s]+)", s, re.I)
    if m:
        return m.group(1).replace("-", " ").strip().title()

    if re.match(r"^https?://", s, re.I) or re.match(r"^[\w-]+\.(com|in|io|ai|co|org|net)\b", s, re.I):
        host = re.sub(r"^https?://", "", s, flags=re.I).split("/")[0]
        host = re.sub(r"^www\.", "", host, flags=re.I)
        return host.split(".")[0].replace("-", " ").title()

    return re.sub(r"\s+", " ", s)


# Legal suffixes ignored when comparing two company names for equality.
_LEGAL = r"(pvt|private|ltd|limited|llp|llc|inc|incorporated|corp|corporation|co|company|gmbh|plc|technologies|technology|labs|holdings)"


def company_key(name: str) -> str:
    """Comparison key so 'Cradlewise' == 'Cradlewise Pvt Ltd'."""
    s = (name or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    tokens = [t for t in s.split() if t and not re.fullmatch(_LEGAL, t)]
    return " ".join(tokens).strip()


def dedupe_key(name: str, company: str) -> str:
    return f"{clean_name(name).lower()}|{company_key(company)}"


def role_key(raw: str) -> str:
    """A title reduced to what makes two titles the same job.

    Seniority words and connectors are dropped so "VP, Sales" and "Vice
    President of Sales" compare equal — otherwise every re-run of a CRM row
    reports a job change that is really a difference in house style.
    """
    import re as _re
    text = (raw or "").lower()
    text = _re.sub(r"[^a-z0-9]+", " ", text)
    noise = {"the", "of", "and", "at", "a", "an", "vp", "svp", "evp", "avp",
             "vice", "president", "head", "director", "chief", "officer",
             "lead", "global", "senior", "sr", "junior", "jr", "group",
             "regional", "deputy", "acting", "interim", "co", "gm", "general"}
    return " ".join(sorted({w for w in text.split() if w and w not in noise}))


def _demo() -> None:
    """Self-check: a written-out blank must not survive as a company name."""
    for blank in ("Not stated", "not specified", "N/A", "n/a.", "unknown",
                  "  NONE  ", "-", "TBD", "Not Provided", "(not stated)", "nil"):
        assert clean_company(blank) == "", blank
        assert is_placeholder_company(blank), blank

    # Real companies that merely look small or odd must survive untouched.
    for real in ("Zamp", "Brex", "X", "Meta", "N26", "Na Pali Coast Co"):
        assert clean_company(real) == real, real
        assert not is_placeholder_company(real), real

    # The existing behaviour is unchanged.
    assert clean_company("https://linkedin.com/company/zamp-hq") == "Zamp Hq"
    assert clean_company("zamp.com") == "Zamp"
    assert clean_company("  Zamp   Technologies ") == "Zamp Technologies"
    assert clean_company("") == ""
    print("normalize checks passed")


if __name__ == "__main__":
    _demo()
