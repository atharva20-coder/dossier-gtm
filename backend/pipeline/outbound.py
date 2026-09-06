"""Main Orchestrator for Outbound Campaigns."""

# `X | None` in an annotation is a syntax error before Python 3.10, and this
# app runs on 3.9. The future import defers annotation evaluation so the
# modern spelling works on both.
from __future__ import annotations
import asyncio
import logging
import re
import time
from typing import Any

from .. import outbound_db
from . import competitors, contacts, runner, waterfall
from . import draft as draft_pipeline
from ..models import ProspectInput, WriterConfig, StakeholderProfile
from .. import db

log = logging.getLogger(__name__)

# Researching a contact properly is a full pipeline run: minutes of wall clock
# and a handful of search credits each. Capped so a wide campaign cannot spend
# the month's allowance in one click, and defaulted low so the common case is
# cheap. Raise it per run with `config.deep`.
DEEP_DEFAULT = 3
DEEP_MAX = 10


async def run_outbound_pipeline(run_id: int):
    """Executes the competitor outbound pipeline end-to-end."""
    try:
        run = await outbound_db.get_outbound_run(run_id)
        if not run:
            return

        target_company = run["target_company"]
        config_dict = run.get("config", {})
        
        # 1. Discover Competitors
        await _stage_start(run_id, "competitor_discovery")
        start_time = time.time()
        
        comps = await competitors.find_competitors(target_company)
        await outbound_db.update_outbound_run(run_id, competitors=comps)
        
        elapsed = int((time.time() - start_time) * 1000)
        await _stage_done(run_id, "competitor_discovery", f"Found {len(comps)} competitors", {"competitors": comps}, elapsed)

        if not comps:
            await outbound_db.update_outbound_run(run_id, status="completed")
            return

        # 2. Find Contacts
        await _stage_start(run_id, "contact_search")
        start_time = time.time()
        
        # Limit the number of competitors to avoid exploding API limits on free tiers
        max_competitors = config_dict.get("max_competitors", 5)
        titles = config_dict.get("target_titles", [])  # e.g., ["VP Engineering", "Head of Sales"]
        contacts_per_company = config_dict.get("contacts_per_company", 5)
        
        # People come from public pages, read by the same finder the leads
        # screen uses. Every person carries the URL that named them, and one
        # without a source is dropped rather than shown — the rule the whole
        # app runs on.
        all_contacts = []
        for comp in comps[:max_competitors]:
            domain = comp.get("domain")
            if not domain:
                continue

            found = await contacts.find(domain, _role_groups(titles))
            for c in found["contacts"][:contacts_per_company]:
                first, _, last = (c.get("name") or "").partition(" ")
                all_contacts.append({
                    "name": c.get("name", ""),
                    "first_name": first, "last_name": last.strip(),
                    "company": comp["name"],
                    "domain": domain,
                    "role": c.get("role", ""),
                    "seniority": "",
                    "linkedin_url": c.get("linkedin_url", ""),
                    "source_competitor": target_company,
                })

        elapsed = int((time.time() - start_time) * 1000)
        await _stage_done(run_id, "contact_search",
                          f"Found {len(all_contacts)} contacts across "
                          f"{min(len(comps), max_competitors)} competitors",
                          {"count": len(all_contacts)}, elapsed)

        if not all_contacts:
            await outbound_db.update_outbound_run(run_id, status="completed")
            return

        # 3. Enrich Emails
        await _stage_start(run_id, "email_enrichment")
        start_time = time.time()
        
        enriched_count = 0
        verified_count = 0
        looked_up = 0
        # Why each provider stepped aside, counted once rather than repeated
        # per contact — the same reason forty times is one fact.
        why: dict[str, int] = {}

        for c in all_contacts:
            email_data = await waterfall.enrich_email(c["name"], c["company"], c["domain"])
            c["email"] = email_data.get("email", "")
            c["email_source"] = email_data.get("source", "")
            c["email_verified"] = email_data.get("verified", False)
            # Hunter returns the title and profile alongside the address, and
            # the lookup is already paid for. Its title is normalised where the
            # one read off a page is whatever that page called them.
            if email_data.get("role"):
                c["role"] = email_data["role"]
            if email_data.get("linkedin_url") and not c.get("linkedin_url"):
                c["linkedin_url"] = email_data["linkedin_url"]

            if c["email"]:
                enriched_count += 1
            if c["email_verified"]:
                verified_count += 1
            if c["email"] and not str(c["email_source"]).startswith("derived"):
                looked_up += 1
            for a in email_data.get("attempts", []):
                key = f"{a['provider']}: {a['outcome']}"
                why[key] = why.get(key, 0) + 1

            # Create the contact in DB immediately so UI can update
            await outbound_db.create_outbound_contact(run_id, c)

        elapsed = int((time.time() - start_time) * 1000)
        derived = enriched_count - looked_up
        bits = []
        if looked_up:
            bits.append(f"{looked_up} looked up")
        if derived:
            bits.append(f"{derived} calculated from the domain")
        if verified_count:
            bits.append(f"{verified_count} verified")
        detail = (f"{enriched_count} of {len(all_contacts)} have an address"
                  + (f" — {', '.join(bits)}" if bits else ""))
        await _stage_done(run_id, "email_enrichment", detail,
                          {"enriched_count": enriched_count,
                           "verified_count": verified_count,
                           "providers": why}, elapsed)

        # 4. Generate Openers (Drafting)
        # Re-use Dossier's existing writer functionality
        await _stage_start(run_id, "drafting")
        start_time = time.time()
        
        writer_cfg = WriterConfig(**(await db.get_config("writer") or {}))
        persona = await db.get_selected_persona()
        style_examples = await db.recent_style_examples(3)
        
        drafted_count = 0
        db_contacts = await outbound_db.get_outbound_contacts(run_id)
        
        # How many contacts get the full leads-page treatment: identity,
        # search, extraction, grounding and the judge. That is a real research
        # run each — minutes and search credits — so it is capped and the rest
        # fall back to the shallow opener rather than being left blank.
        deep_budget = int(config_dict.get("deep", DEEP_DEFAULT))
        deep_budget = max(0, min(deep_budget, DEEP_MAX))
        deep_done = 0

        for contact in db_contacts:
            # --- the deep path: research this person properly ---------------
            #
            # Delegated to the same runner the leads screen uses, so a contact
            # gets sources, the traversal graph and a grounded hook rather than
            # "works at a competitor" — and the lead it produces opens on the
            # leads screen unchanged. Reused wholesale because a second, lighter
            # research path would drift from the one that is actually tested.
            if deep_done < deep_budget:
                try:
                    lead_id = await db.create_run(f"outbound-{run_id}", {
                        "name": contact["name"], "company": contact["company"],
                        "role": contact["role"], "url": contact["linkedin_url"],
                        "email": contact.get("email") or "",
                    })
                    await runner.run(lead_id, ProspectInput(
                        name=contact["name"], company=contact["company"],
                        role=contact["role"], url=contact["linkedin_url"]))
                    lead = await db.get_run(lead_id)
                    deep_done += 1

                    pool = await db.pool()
                    await pool.execute(
                        "UPDATE outbound_contacts SET lead_run_id=$1 WHERE id=$2",
                        lead_id, contact["id"])

                    body = (lead or {}).get("draft_body") or ""
                    opener = _first_sentence(body)
                    if opener:
                        await pool.execute(
                            "UPDATE outbound_contacts SET opener_line=$1 WHERE id=$2",
                            opener, contact["id"])
                        drafted_count += 1
                        continue
                    # Researched but nothing worth saying: fall through to the
                    # shallow opener rather than sending an empty one.
                except Exception as e:                # noqa: BLE001
                    log.warning("deep research failed for %s: %s", contact["name"], e)

            # --- the shallow path ------------------------------------------
            # Written for everyone found, address or not: an opener is what
            # makes the contact worth anything, and a missing email is a
            # separate problem with its own fix.

            # Create a simple ProspectInput and mock hook
            p_input = ProspectInput(
                name=contact["name"],
                company=contact["company"],
                role=contact["role"],
                url=contact["linkedin_url"]
            )
            
            # For competitor campaigns, the "hook" is often that they are a competitor
            from ..models import ExtractedFact
            mock_hook = ExtractedFact(
                text=f"Works at {contact['company']}, a competitor to {target_company}.",
                level="company",
                category="other",
                date="",
                key_entities=[contact["company"], target_company],
                source_url=""
            )
            
            d = await draft_pipeline.write(
                p_input, 
                mock_hook, 
                writer=writer_cfg, 
                stakeholder=StakeholderProfile(seniority=contact["seniority"], function=contact["role"]),
                style_examples=[], # skip style for now to save time
                persona=persona
            )
            
            # One sentence, not the whole draft. The template already supplies a
            # greeting and a sign-off, so storing the full message here produced
            # "Hi Pedro, Pedro, ..." — two emails inside one.
            opener = _first_sentence(d.body) if d else ""
            if opener:
                pool = await db.pool()
                await pool.execute(
                    "UPDATE outbound_contacts SET opener_line = $1 WHERE id = $2",
                    opener, contact["id"])
                drafted_count += 1
                
        elapsed = int((time.time() - start_time) * 1000)
        await _stage_done(
            run_id, "drafting",
            f"Drafted {drafted_count} openers"
            + (f" — {deep_done} from full research, "
               f"{drafted_count - deep_done} from the competitor angle alone"
               if deep_done else " from the competitor angle"),
            {"drafted_count": drafted_count, "deep": deep_done}, elapsed)
        
        # 5. Create Campaign Groupings
        # Group by Persona/Role
        await _stage_start(run_id, "campaign_creation")
        
        # Grouped by function, from the title when no provider supplied a
        # seniority. Grouping on seniority alone put every contact in a segment
        # called "Unknown", because nothing fills a seniority column here —
        # and one campaign named "Unknown" is not a segmentation.
        # Every contact is segmented, not only the ones with an address. A
        # segment whose people all lack an email otherwise gets no campaign,
        # and those contacts can then never show a message — even though a
        # LinkedIn profile is a perfectly good way to reach them.
        groups: dict[str, int] = {}
        for c in db_contacts:
            seg = _segment_of(c)
            groups[seg] = groups.get(seg, 0) + 1
            if c.get("persona_segment") != seg:
                pool = await db.pool()
                await pool.execute(
                    "UPDATE outbound_contacts SET persona_segment = $1 WHERE id = $2",
                    seg, c["id"])
            
        for seg, count in groups.items():
            # One template per segment, written for that segment. A single
            # subject reused across every group is not a segmentation — it is
            # one campaign with four names, and it reads like a mail merge.
            subject, body = await _segment_template(seg, target_company, persona, writer_cfg)
            await outbound_db.create_outbound_campaign(
                run_id=run_id,
                name=f"{seg} at {target_company} competitors",
                persona=seg,
                subject=subject,
                body=body,
                contact_count=count
            )
            
        await _stage_done(run_id, "campaign_creation", f"Created {len(groups)} campaigns", {"campaigns_created": len(groups)}, 0)

        await outbound_db.update_outbound_run(run_id, status="completed")

    except Exception as e:
        log.exception(f"Outbound pipeline failed: {e}")
        await outbound_db.update_outbound_run(run_id, status="error")
        await _stage_done(run_id, "pipeline", f"Failed: {e}", {}, 0, status="failed")


# "Hi Pedro," / "Hey Priya -" / "Dear Ms Nair,". Stripped when it opens a line.
_GREETING = re.compile(r"^\s*(hi|hey|hello|dear|good (morning|afternoon))\b[\s,!-]*",
                       re.I)


def _is_salutation(block: str) -> bool:
    """Whether this block is a greeting rather than a sentence.

    Keyed on shape, not on a list of words: drafts open with a bare "Pedro,"
    as often as with "Hi Pedro,", and a keyword list only ever catches the
    greetings someone thought of. A salutation is short and does not end a
    sentence; anything that ends in a full stop is content.
    """
    text = block.strip()
    if not text or text.endswith((".", "!", "?")):
        return False
    return len(text.split()) <= 5


def _first_sentence(body: str) -> str:
    """The opening sentence of a draft, without its greeting.

    The drafting stage returns a whole email. Only its first real sentence
    belongs in a templated campaign — the template supplies the greeting and
    the sign-off, and keeping both put two complete messages in one send.
    """
    text = (body or "").strip()
    if not text:
        return ""
    for block in re.split(r"\n\s*\n|\n", text):
        block = _GREETING.sub("", block.strip()).strip(" ,-—")
        if not block or _is_salutation(block):
            continue
        parts = re.split(r"(?<=[.!?])\s+", block)
        return parts[0].strip()
    return ""


def render_message(contact: dict, campaign: dict | None,
                   sender_name: str = "") -> tuple[str, str]:
    """The message this contact would actually receive: subject and body.

    One implementation, called by the screen and by the CSV export. They used to
    substitute placeholders separately, which meant the message a rep reviewed
    and the message a sequencer sent could quietly differ — the worst kind of
    bug to find, because both look right on their own.

    A contact with no campaign still has an opener, so it is returned rather
    than nothing: a bare opener is what a rep would send by hand.
    """
    opener = (contact.get("opener_line") or "").strip()
    template = (campaign or {}).get("template_body") or ""
    subject = (campaign or {}).get("template_subject") or ""

    if not template:
        return subject, opener

    body = (template
            .replace("{{first_name}}", contact.get("first_name")
                     or contact.get("name") or "there")
            .replace("{{company}}", contact.get("company") or "")
            .replace("{{opener_line}}", opener)
            .replace("{{sender_name}}", sender_name or ""))
    # A template whose placeholder the model never wrote still needs the
    # opener, or the personalisation is silently dropped.
    if opener and opener not in body:
        body = f"{opener}\n\n{body}"
    return subject, body.strip()


TEMPLATE_PROMPT = """Write one cold email template for this audience.

It is sent to every person in the segment, so every sentence must be true of all
of them. Each person's own opening line is inserted where {{{{opener_line}}}}
sits — do not write an opener, and do not repeat what one would say.

AUDIENCE
{segment} at companies that compete with {target}.

WHO IS WRITING
{writer}

RULES
- First line "Subject: ...", then a blank line, then the body.
- The body MUST contain {{{{first_name}}}} once at the start and
  {{{{opener_line}}}} on its own line. End with {{{{sender_name}}}}. Write the
  braces exactly as shown, doubled. No other placeholders.
- Three or four sentences after the opener. One ask, and make it small.
- Say what is being offered and why it matters to THIS audience specifically —
  a CFO and a head of sales do not care about the same thing.
- Invent nothing about their company. You know only that it competes with
  {target}.
- No "I hope this finds you well", no "quick question", no flattery.
"""


async def _segment_template(segment: str, target: str, persona: dict | None,
                            writer: WriterConfig) -> tuple[str, str]:
    """Subject and body for one segment, in the sender's voice.

    Falls back to a plain template rather than failing the run: a campaign with
    a dull template is fixable in the UI, a run that died at the last stage
    throws away every search it already paid for.
    """
    from .draft import split_subject
    from ..integrations import llm

    voice = " — ".join(x for x in [(persona or {}).get("name"),
                                   (persona or {}).get("character"),
                                   writer.sender_role, writer.product] if x)
    try:
        raw = await llm.text(
            TEMPLATE_PROMPT.format(segment=segment, target=target,
                                   writer=voice or "a founder"),
            temperature=0.6)
        subject, body = split_subject(raw)
        if subject and "{{first_name}}" in body:
            return subject, body
        log.warning("template for %s came back malformed; using the plain one", segment)
    except Exception as e:                       # noqa: BLE001 — never fail the run
        log.warning("template for %s failed: %s", segment, e)

    return (f"{segment} at {target} alternatives",
            "Hi {{first_name}},\n\n{{opener_line}}\n\nBest,\n{{sender_name}}")


# Which function a title belongs to, by the words that actually appear in
# titles. Ordered: the first match wins, so "VP Revenue Operations" reads as
# sales rather than operations.
SEGMENT_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Founders & CEO", ("founder", "co-founder", "chief executive", "ceo", "managing director")),
    ("Sales", ("sales", "revenue", "cro", "account executive", "business development")),
    ("Marketing", ("marketing", "growth", "demand", "cmo", "brand")),
    ("Finance", ("finance", "financial", "cfo", "controller", "accounting", "treasury")),
    ("Product", ("product", "cpo")),
    ("Engineering", ("engineering", "technology", "cto", "technical", "platform")),
    ("People", ("people", "talent", "hr", "human resources", "recruiting", "chro")),
    ("Operations", ("operations", "coo", "ops")),
]


def _segment_of(contact: dict) -> str:
    """Which campaign this person belongs in.

    Reads the title, because the title is the one field that is always present:
    the seniority column is filled by a provider this app may not have a key
    for, and grouping on it alone puts everyone in "Unknown".
    """
    text = f"{contact.get('role') or ''} {contact.get('seniority') or ''}".lower()
    for name, words in SEGMENT_WORDS:
        if any(w in text for w in words):
            return name
    return "Other decision-makers"


def _role_groups(titles: list[str]) -> list[str]:
    """Map the configured target titles onto the finder's role groups.

    The ICP stores titles like "VP" and "Director", which say seniority but not
    function. The finder searches by function, so anything unrecognised falls
    back to founders and sales rather than searching for nobody.
    """
    text = " ".join(titles or []).lower()
    groups = [g for g in contacts.ROLE_GROUPS if g in text]
    return groups or ["founder", "sales"]


async def _stage_start(run_id: int, stage: str):
    await outbound_db.add_outbound_stage(run_id, stage, "started", "In progress...")

async def _stage_done(run_id: int, stage: str, detail: str, payload: dict, elapsed_ms: int, status: str = "done"):
    await outbound_db.add_outbound_stage(run_id, stage, status, detail, payload)
