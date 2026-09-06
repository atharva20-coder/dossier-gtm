"""Configuration. All secrets come from environment / .env — never hardcoded."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- API keys -------------------------------------------------------------
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
# The free plan is 1,000 searches a month and Tavily exposes no endpoint to
# ask what is left, so the app counts its own and warns before a campaign
# dies halfway through. Raise it if the plan changes.
TAVILY_MONTHLY_BUDGET = int(os.getenv("TAVILY_MONTHLY_BUDGET", "1000"))
# Stop starting new research below this much headroom, so a run that has
# begun can always finish.
TAVILY_RESERVE = int(os.getenv("TAVILY_RESERVE", "40"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Optional person-level signal provider. Absent = web search only, which still
# works; this is an enhancement, never a dependency.
SUPERCARL_API_KEY = os.getenv("SUPERCARL_API_KEY", "").strip()
SUPERCARL_TIMEOUT_S = float(os.getenv("SUPERCARL_TIMEOUT_S", "15"))
SUPERCARL_POSTS_LIMIT = int(os.getenv("SUPERCARL_POSTS_LIMIT", "15"))
# The provider is metered in whole calls and the allowance is small, so resolved
# profiles are cached. A profile changes over weeks; a stale one costs far less
# than running out of quota entirely.
SUPERCARL_CACHE_DAYS = int(os.getenv("SUPERCARL_CACHE_DAYS", "30"))

# --- Outbound Pipeline APIs -----------------------------------------------
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "").strip()
QEV_API_KEY = os.getenv("QEV_API_KEY", "").strip()  # QuickEmailVerification

# --- Access control -------------------------------------------------------
# One shared key gates the whole API. Unset means OPEN, which suits local
# development and is logged loudly at startup so it is never a silent mistake.
APP_ACCESS_KEY = os.getenv("APP_ACCESS_KEY", "").strip()
# Signs session cookies. Unset means a per-process random value, so sessions do
# not survive a restart or span instances — fine locally, wrong in production.
SESSION_SECRET_FALLBACK = secrets.token_urlsafe(32)
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip() or SESSION_SECRET_FALLBACK
SESSION_TTL_S = int(os.getenv("SESSION_TTL_S", str(60 * 60 * 12)))
# Cap the upload before pandas parses it; MAX_ROWS_PER_UPLOAD is checked after.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
# /api/health makes real Tavily and Gemini calls, and the UI shows its result in
# the header on every page load. Cached so refreshing a tab does not spend
# credits redrawing a badge.
HEALTH_CACHE_S = int(os.getenv("HEALTH_CACHE_S", "300"))

# --- Sending (Gmail SMTP) -------------------------------------------------
# An app password, not the account password: Gmail requires 2-step verification
# and a generated app password for SMTP. It is equivalent to mail-send access on
# that account, so it lives here and never in the database.
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
GMAIL_SENDER_NAME = os.getenv("GMAIL_SENDER_NAME", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_TIMEOUT_S = float(os.getenv("SMTP_TIMEOUT_S", "20"))

# --- Models ---------------------------------------------------------------
# Cheap/fast model for mechanical stages; stronger model where judgment matters.
MODEL_FAST = os.getenv("MODEL_FAST", "gemini-2.5-flash")
MODEL_SMART = os.getenv("MODEL_SMART", "gemini-3.8-flash")
# Reasoning-token cap for the fast/mechanical model. Extraction is transcription,
# not deliberation: 512 measured 2.6x faster than unbounded for the same facts,
# while lower budgets lost half of them. -1 removes the cap.
FAST_THINKING_BUDGET = int(os.getenv("FAST_THINKING_BUDGET", "512"))

# --- Tuning knobs (every one of these is a documented decision) -----------
SEARCH_TIMEOUT_S = float(os.getenv("SEARCH_TIMEOUT_S", "8"))
# Extraction is the long pole: ~35k characters of source text in, structured
# facts out, measured at 37s against a live 10-source corpus. 45s left almost
# no margin and runs were failing on ordinary variance, not on real problems.
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "90"))
MAX_CONCURRENT_SEARCHES = int(os.getenv("MAX_CONCURRENT_SEARCHES", "6"))
MAX_RESULTS_PER_QUERY = int(os.getenv("MAX_RESULTS_PER_QUERY", "5"))

# Extraction reads sources in concurrent batches. Research returns 40-50 sources
# now, far more than one model call can be shown, and one call over all of them
# was slow enough to hit the timeout under load. Batch size trades per-call
# latency against the number of calls; total sources read is the real cost knob.
EXTRACT_SOURCES_PER_CALL = int(os.getenv("EXTRACT_SOURCES_PER_CALL", "5"))
EXTRACT_MAX_SOURCES = int(os.getenv("EXTRACT_MAX_SOURCES", "20"))

# Research traversal (pipeline/graph.py). Every node explored is a search
# credit, so these are cost limits first and quality knobs second.
GRAPH_MAX_DEPTH = int(os.getenv("GRAPH_MAX_DEPTH", "2"))
GRAPH_MAX_NODES = int(os.getenv("GRAPH_MAX_NODES", "12"))
GRAPH_QUERY_BUDGET = int(os.getenv("GRAPH_QUERY_BUDGET", "8"))

# Factual recency gate: a fact older than this can never be the hook.
FACT_RECENCY_MAX_DAYS = int(os.getenv("FACT_RECENCY_MAX_DAYS", "180"))
# Outreach-value curve: value peaks inside this window and decays after.
OUTREACH_PEAK_DAYS = int(os.getenv("OUTREACH_PEAK_DAYS", "21"))
OUTREACH_DECAY_DAYS = int(os.getenv("OUTREACH_DECAY_DAYS", "60"))

# Identity resolution thresholds (see DECISION_LOGIC.md, Decision Point 1).
IDENTITY_AUTO_PROCEED_SCORE = int(os.getenv("IDENTITY_AUTO_PROCEED_SCORE", "60"))
IDENTITY_AMBIGUITY_MARGIN = int(os.getenv("IDENTITY_AMBIGUITY_MARGIN", "20"))

# Batch safety: bulk upload changed the cost model, so it needs a ceiling.
MAX_ROWS_PER_UPLOAD = int(os.getenv("MAX_ROWS_PER_UPLOAD", "200"))

# How many accepted edits a persona collects before it rewrites its own
# instructions. One edit is not a pattern — the message may have been wrong
# about that prospect rather than wrong in style — and learning from each in
# turn produces a persona that lurches.
PERSONA_LEARN_AFTER = int(os.getenv("PERSONA_LEARN_AFTER", "3"))
# Hard ceiling on learned rules. Past this the persona may only correct, merge
# or drop — never add. A rule set longer than the messages it produces is one
# nobody reads, and a rule nobody reads is one nobody corrects.
PERSONA_MEMORY_CAP = int(os.getenv("PERSONA_MEMORY_CAP", "10"))
SEARCHES_PER_RUN_ESTIMATE = 6

# --- Database (Supabase Postgres) ----------------------------------------
# Use the Supavisor TRANSACTION pooler string (port 6543), not the direct
# connection on 5432. See backend/db.py for why that choice is load-bearing on
# serverless, and why the pool size below must not be raised casually.
def _clean_dsn(raw: str) -> str:
    """Make a pasted Supabase connection string usable by asyncpg.

    Two things about the string Supabase hands you break this driver, and both
    are easy to paste without noticing:

    `?pgbouncer=true` is a Prisma flag. asyncpg forwards unknown query
    parameters to the server as settings, so the connection fails outright
    rather than ignoring it.

    An unencoded `@` in the password is ambiguous — the parser splits on the
    last one, so `pass@word@host` happens to work while `pass@word` in a
    URL with no other `@` does not. Percent-encoding it removes the guesswork.
    """
    import urllib.parse as _u

    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return ""

    parsed = _u.urlsplit(raw)
    # Drop driver-incompatible parameters, keep anything asyncpg understands
    # (sslmode and friends).
    drop = {"pgbouncer", "connection_limit", "pool_timeout", "schema"}
    query = [(k, v) for k, v in _u.parse_qsl(parsed.query) if k not in drop]

    netloc = parsed.netloc
    if "@" in netloc:
        creds, _, host = netloc.rpartition("@")
        user, _, password = creds.partition(":")
        if password:
            netloc = f"{user}:{_u.quote(_u.unquote(password), safe='')}@{host}"

    return _u.urlunsplit((parsed.scheme, netloc, parsed.path,
                          _u.urlencode(query), parsed.fragment))


DATABASE_URL = _clean_dsn(os.getenv("DATABASE_URL", ""))
# 4 x (concurrent Vercel instances) must stay inside the pooler's client budget,
# with headroom for migrations and the Supabase dashboard's own sessions.
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "4"))
# Idle connections held open. 0 suits serverless, where instances are ephemeral
# and a warm pool is wasted on most of them. On a long-lived host (Railway, a
# VM) set this to 1 so the first request of each burst does not pay the ~0.5s
# TLS handshake.
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "0"))
DB_COMMAND_TIMEOUT_S = float(os.getenv("DB_COMMAND_TIMEOUT_S", "15"))
# A run cannot outlive the serverless function ceiling (300s on Vercel), so one
# that has been silent for comfortably longer than that is dead, not slow.
RUN_STALE_AFTER_S = int(os.getenv("RUN_STALE_AFTER_S", "600"))

# Categories that may never become an outreach hook, regardless of recency
# or prominence. This is a hard filter, not a model preference.
BLOCKED_CATEGORIES = {
    "layoff",
    "lawsuit",
    "controversy",
    "personal_life",
    "bereavement",
    "health",
}

# Categories eligible to become a hook, roughly ordered by buying intent.
INTENT_WEIGHTS = {
    "exec_hire": 1.00,      # new leader with a mandate to change things
    "hiring": 0.95,         # direct evidence of the pain being solved
    "funding": 0.85,        # budget exists, but the most saturated trigger
    "expansion": 0.85,
    "product_launch": 0.70,
    "exec_statement": 0.70,
    "partnership": 0.60,
    "award": 0.25,          # feels personal, signals no need
    "other": 0.40,
}


def missing_keys() -> list[str]:
    missing = []
    if not TAVILY_API_KEY:
        missing.append("TAVILY_API_KEY")
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    return missing

# --- ranking: recency and relevance -----------------------------------------
# An undated fact is missing information, not fresh. Low enough that anything
# carrying a real recent date beats it.
UNDATED_VALUE = float(os.getenv("UNDATED_VALUE", "0.22"))
# How much a fact's connection to what the sender sells moves its score. A fact
# with no overlap is not banned — it is outranked.
RELEVANCE_MISS = float(os.getenv("RELEVANCE_MISS", "0.65"))
RELEVANCE_STEP = float(os.getenv("RELEVANCE_STEP", "0.22"))
RELEVANCE_MAX = float(os.getenv("RELEVANCE_MAX", "1.9"))
# Two facts describing the same event, one dated and one not: the undated one
# inherits the age rather than escaping the recency gate on a technicality.
# 0.45 sits between the true twin (0.556 on the run that exposed this) and
# the nearest false positive (0.286) — margin on both sides, not a tuned edge.
TWIN_SIMILARITY = float(os.getenv("TWIN_SIMILARITY", "0.45"))
