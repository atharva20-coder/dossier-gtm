"""Research as graph traversal, the way a person actually does it.

WHY THIS EXISTS
---------------
The original design generated a flat list of queries once, ran them, and
stopped. That is not how anyone researches a prospect. A person starts with a
name, finds out where they work, notices they came from somewhere else, follows
that, spots a product they built or a conference they spoke at, and follows
those too. Each thing you learn tells you what to look for next. A flat query
list cannot do that, because every query has to be written before anything is
known.

So research runs as a bounded traversal over a small graph of entities:

    ("person", "Priya Nair")            <- the root, always
      +-- ("org",   "Acme")             from the uploaded row
      +-- ("org",   "Northwind")        from her resolved profile: a past employer
      +-- ("topic", "payments fraud")   mined from what the first wave returned
            +-- ("event", "FinCon 2026")   mined from the second wave

Each unexplored node becomes a search query, the results are read, new entities
are added, and the next wave explores those. Waves stop at a depth limit or when
the query budget runs out — whichever comes first.

THE RULE THAT MAKES THIS WORK
-----------------------------
Every query stays anchored to the prospect's name. Always. Without that anchor,
following the "Northwind" node stops being research about Priya Nair and becomes
research about Northwind, and the pipeline fills with true, well-sourced,
completely irrelevant facts about a company she left in 2019. The graph decides
WHAT to ask about; the anchor keeps every answer about WHOM.

This is also why the traversal never adds a node for a different person. Other
people surface constantly — co-founders, colleagues, the interviewer on a
podcast — and each is a doorway out of the prospect's own story. Facts about
them are dropped downstream by `drop_third_party_people`, but not spending
queries on them in the first place is cheaper and keeps the corpus cleaner.

COST
----
Every node explored is a search credit. The budget is small and explicit, and
the traversal reports exactly which queries it spent it on, because "the agent
decided to search more" is not an acceptable answer to a bill.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import config
from ..integrations import llm
from ..models import ProspectInput, SearchHit
from pydantic import BaseModel, Field

# Node kinds, and how each turns into a question about the prospect.
ORG = "org"
TOPIC = "topic"
EVENT = "event"
WORK = "work"

# How a node of each kind is phrased once anchored to the prospect's name.
_TEMPLATES: dict[str, str] = {
    ORG: '"{name}" "{value}"',
    TOPIC: '"{name}" "{value}"',
    EVENT: '"{name}" "{value}"',
    WORK: '"{name}" "{value}"',
}


@dataclass(frozen=True)
class Node:
    kind: str
    value: str
    depth: int
    via: str = ""            # human-readable provenance, shown in the UI
    parent: str = ""         # key() of the node this was discovered from; "" = the root
    evidence: str = ""       # the text that named it
    evidence_url: str = ""   # where that text was read

    def key(self) -> str:
        return f"{self.kind}:{self.value.strip().lower()}"


@dataclass
class Graph:
    """The prospect and everything worth asking about them."""
    root: ProspectInput
    nodes: dict[str, Node] = field(default_factory=dict)
    explored: set[str] = field(default_factory=set)
    queries_spent: int = 0
    sources_found: dict[str, int] = field(default_factory=dict)

    def add(self, node: Node) -> bool:
        from .normalize import company_key

        if not _usable(node.value) or node.key() in self.nodes:
            return False
        # The prospect's current employer is already the anchor in every query,
        # so exploring it as a thread spends a credit re-asking what we started
        # from. It came back as a "discovery" from the first wave, which is what
        # reading the profile of someone who works there is bound to suggest.
        if node.kind == ORG and company_key(node.value) == company_key(self.root.company):
            return False
        if len(self.nodes) >= config.GRAPH_MAX_NODES:
            return False
        self.nodes[node.key()] = node
        return True

    def frontier(self, depth: int) -> list[Node]:
        return [n for n in self.nodes.values()
                if n.depth == depth and n.key() not in self.explored]

    def known_orgs(self) -> set[str]:
        """Organisations already established for this prospect — their employer
        and everything their profile named. Used to keep a company that happens
        to look like a personal name from being discarded as one."""
        from .normalize import company_key

        keys = {company_key(self.root.company)}
        keys |= {company_key(n.value) for n in self.nodes.values() if n.kind == ORG}
        return {k for k in keys if k}

    def trail(self) -> list[dict]:
        """What was explored and what suggested it — the audit trail the UI shows."""
        return [{"kind": n.kind, "value": n.value, "depth": n.depth, "via": n.via}
                for n in self.nodes.values() if n.key() in self.explored]

    def snapshot(self) -> dict:
        """The whole graph as the UI draws it, including the root.

        Emitted after every wave rather than once at the end, because the point
        of showing the traversal is watching it happen. Nodes carry their own
        parent id so the frontend never has to guess the shape — the branching
        is whatever the research actually did, at whatever depth it reached.
        """
        root_id = "root"
        nodes = [{
            "id": root_id, "kind": "person", "label": self.root.name or "prospect",
            "sublabel": self.root.company, "depth": 0, "parent": None,
            "status": "done", "sources": 0,
        }]
        for n in self.nodes.values():
            # `parent` is the node this one was actually discovered from, so the
            # drawing shows the chain of reasoning: this employer led to that
            # topic, which led to that talk. Hanging everything off the root
            # instead would flatten a real traversal into a single fan-out and
            # throw away the only interesting thing about it.
            parent = n.parent if n.parent in self.nodes and n.parent != n.key() else root_id
            nodes.append({
                "id": n.key(),
                "kind": n.kind,
                "label": n.value,
                "sublabel": n.via,
                "depth": n.depth,
                "parent": parent,
                "status": "done" if n.key() in self.explored else "pending",
                "sources": self.sources_found.get(n.key(), 0),
                # The quoted text that put this node on the graph, and where it
                # was read. Without it, "why is IIT Kharagpur here?" is only
                # answerable by reading the database by hand.
                "evidence": n.evidence,
                "evidence_url": n.evidence_url,
            })
        return {"nodes": nodes, "queries_spent": self.queries_spent,
                "budget": config.GRAPH_QUERY_BUDGET}


def _usable(value: str) -> bool:
    v = (value or "").strip()
    # Two characters is not an entity, and an eighty-character "entity" is a
    # sentence the model mislabelled.
    return 3 <= len(v) <= 60 and bool(re.search(r"[A-Za-z]", v))


def queries_for(g: Graph, nodes: list[Node]) -> list[tuple[str, str]]:
    """Turn frontier nodes into anchored search queries.

    The prospect's name is in every one of them. See the module docstring for
    why that is the load-bearing detail rather than a formatting choice.
    """
    name = g.root.name.strip()
    if not name:
        return []
    out: list[tuple[str, str]] = []
    for n in nodes:
        tpl = _TEMPLATES.get(n.kind, _TEMPLATES[TOPIC])
        out.append((tpl.format(name=name, value=n.value.strip()), "general"))
    return out


def seed(p: ProspectInput, orgs: list[str] | list[tuple[str, str]],
         source_url: str = "") -> Graph:
    """Depth-1 nodes: what we know before reading anything.

    The prospect's own employment history is the highest-quality seed available,
    because a past employer is specific to this individual in a way their name
    never is — pairing the two finds them and excludes their namesakes.

    `orgs` may be plain names or (name, evidence) pairs; the evidence is the
    profile line that named the employer, kept so the UI can show it.
    """
    g = Graph(root=p)
    for org in orgs:
        name, evidence = org if isinstance(org, tuple) else (org, "")
        g.add(Node(ORG, name, depth=1, via="their profile",
                   evidence=evidence, evidence_url=source_url))
    return g


class _Frontier(BaseModel):
    """What to look at next, chosen from what has been read so far."""
    organisations: list[str] = Field(
        default_factory=list,
        description="Companies, schools or institutions THIS PERSON is tied to.")
    topics: list[str] = Field(
        default_factory=list,
        description=("Specific subjects this person works on or speaks about — a "
                     "product they built, a problem they talk about publicly. "
                     "Not generic industry words like 'technology' or 'AI'."))
    events: list[str] = Field(
        default_factory=list,
        description="Named conferences, podcasts, awards or publications tied to them.")


_FRONTIER_PROMPT = """You are researching one specific person and deciding what to look up NEXT.

PERSON:   {name}
COMPANY:  {company}
ALREADY LOOKED AT: {seen}

WHAT HAS BEEN READ SO FAR:
{corpus}

List the most promising things to search next that would reveal something
concrete and recent about {name} THEMSELVES.

RULES:
- Only name things tied to {name} specifically. If the text is about a
  different person who happens to share the name, ignore it entirely.
- Never list another person's name. Colleagues, co-founders, interviewers and
  people they mention are not what we are researching.
- Be specific. "AI" is useless; "invoice reconciliation agents" is useful.
- Do not repeat anything in ALREADY LOOKED AT.
- Return nothing at all rather than padding the list with guesses.
"""


def _attribute(
    value: str, hits_by_node: dict[str, list[SearchHit]], person: str = ""
) -> tuple[str, bool]:
    """Where this entity was found, and whether it is tied to the prospect.

    Returns (parent_node_key, tied_to_person).

    The wave explored several nodes at once, so "what suggested this" is only
    answerable by looking at whose results the entity actually appears in. Each
    hit records the query that produced it and each query came from one node, so
    the attribution is available without asking the model for it.

    The second half matters more. Appearing SOMEWHERE in the results is far too
    weak a test: searching "Shubham Verma" "Boston Consulting Group" returns
    pages about BCG, and those pages name dozens of other organisations — so the
    traversal happily proposed Accenture, AWS and the University of Allahabad
    and spent real credits discovering nothing about the prospect. An entity
    only counts if it appears in a source that ALSO names him. Co-occurrence on
    one page is weak evidence, but it is the difference between following his
    history and following his former employer's.
    """
    needle = (value or "").strip().lower()
    if not needle:
        return "", False

    name = (person or "").strip().lower()
    # A surname alone is too loose; the full name as written is the test, with
    # the last token as a fallback for sources that use "Verma" after first use.
    surname = name.split()[-1] if name else ""

    parent, tied = "", False
    for node_key, hits in hits_by_node.items():
        for h in hits:
            text = f"{h.title} {h.content}".lower()
            if needle not in text:
                continue
            if not parent:
                parent = node_key
            if name and (name in text or (surname and surname in text)):
                return node_key, True
    return parent, tied


def _evidence_for(value: str, hits_by_node: dict[str, list[SearchHit]]) -> tuple[str, str]:
    """A quotable sentence containing this entity, and the URL it came from."""
    needle = (value or "").strip().lower()
    for hits in hits_by_node.values():
        for h in hits:
            for sentence in re.split(r"(?<=[.!?])\s+", h.content or ""):
                if needle and needle in sentence.lower():
                    return sentence.strip()[:300], h.url
    return "", ""


async def propose(
    g: Graph,
    hits: list[SearchHit],
    depth: int,
    hits_by_node: dict[str, list[SearchHit]] | None = None,
) -> list[Node]:
    """Ask what to explore next, given everything read so far.

    A model call rather than a regex because the judgment here — which of the
    forty proper nouns on this page is actually a thread worth pulling — is
    exactly what pattern matching is bad at. It is one cheap call per wave, and
    a failure is not fatal: no proposals just means the traversal stops early.
    """
    if not hits:
        return []
    corpus = "\n\n".join(f"{h.title}\n{h.content[:1200]}" for h in hits[:12])
    seen = ", ".join(n.value for n in g.nodes.values()) or "(nothing yet)"

    try:
        f = await llm.structured(
            _Frontier,
            _FRONTIER_PROMPT.format(name=g.root.name, company=g.root.company or "(unknown)",
                                    seen=seen[:600], corpus=corpus[:14000]),
            model=config.MODEL_FAST,
            system=("You choose research directions about one named individual. "
                    "You never propose looking up a different person."),
            temperature=0.2,
        )
    except Exception:
        return []          # the traversal is an enhancement; it must never fail a run

    known = g.known_orgs()
    by_node = hits_by_node or {}
    proposed: list[Node] = []
    for kind, values in ((ORG, f.organisations), (TOPIC, f.topics), (EVENT, f.events)):
        for v in values:
            parent, tied = _attribute(v, by_node, g.root.name)
            # Only follow what the sources actually connect to this person. The
            # model is told to propose only things tied to them; this is the
            # check that holds when it lists whatever else was on the page.
            if by_node and not tied:
                continue
            via = g.nodes[parent].value if parent in g.nodes else f"read at depth {depth - 1}"
            quote, url = _evidence_for(v, by_node)
            n = Node(kind, v.strip(), depth=depth, via=via, parent=parent,
                     evidence=quote, evidence_url=url)
            if _is_person_name(v, g.root.name, known) or not g.add(n):
                continue
            proposed.append(n)
    return proposed


# A bare two-or-three-word capitalised phrase with no organisational word in it
# is usually somebody's name. The model is told not to propose people; this is
# the check that holds when it does anyway.
#
# "Usually" is doing real work there: "Acme Payments" has exactly the same shape
# as "Ravi Shah". No word list separates those reliably, which is why the check
# also takes the organisations already established for this prospect — their
# employer and the ones named in their profile — and treats those as known orgs
# rather than guessing at them.
_ORG_WORDS = re.compile(
    r"\b(inc|ltd|llc|llp|plc|gmbh|corp|company|co|group|holdings|technologies|"
    r"technology|labs?|systems|solutions|partners|ventures|capital|bank|"
    r"university|institute|college|school|foundation|academy|network|media|"
    r"consulting|services|studio|summit|conference|podcast|award|prize|"
    r"payments|finance|financial|health|cloud|data|software|digital|energy|"
    r"retail|motors|foods|pharma|telecom|logistics|robotics|security|"
    r"analytics|insurance|industries|works|global|international)\b", re.I)


def _is_person_name(value: str, prospect: str, known_orgs: set[str] | None = None) -> bool:
    from .extract import same_person
    from .normalize import company_key

    v = (value or "").strip()
    if same_person(v, prospect):
        return False          # the prospect themselves is not a stray person
    if _ORG_WORDS.search(v):
        return False
    if known_orgs and company_key(v) in known_orgs:
        return False          # already established as an organisation
    words = v.split()
    return 2 <= len(words) <= 3 and all(w[:1].isupper() and w[1:].islower() for w in words)


def _demo() -> None:
    p = ProspectInput(name="Priya Nair", company="Acme")
    # "Acme" is the prospect's current employer and the anchor in every query,
    # so it is not a thread to explore — only Northwind becomes a node.
    g = seed(p, ["Northwind Logistics", "Acme"])
    assert len(g.nodes) == 1, {k: n.value for k, n in g.nodes.items()}
    g.add(Node(ORG, "Stanford University", depth=1))

    qs = [q for q, _ in queries_for(g, list(g.nodes.values()))]
    assert all('"Priya Nair"' in q for q in qs), qs
    assert any("Northwind" in q for q in qs)

    # Budget and de-duplication.
    assert not g.add(Node(ORG, "Northwind Logistics", 1)), "duplicate node"
    assert not g.add(Node(ORG, "x", 1)), "too short to be an entity"
    assert not g.add(Node(ORG, "Acme Pvt Ltd", 2)), "the current employer is the anchor"

    # People must never become nodes, however they are labelled.
    assert _is_person_name("Ravi Shah", "Priya Nair")
    assert _is_person_name("Wei Ming Zhang", "Priya Nair")
    assert not _is_person_name("Priya Nair", "Priya Nair"), "the prospect is not a stray"
    assert not _is_person_name("Boston Consulting Group", "Priya Nair")
    assert not _is_person_name("Stanford University", "Priya Nair")
    assert not _is_person_name("FinCon", "Priya Nair"), "one word is not a full name"
    # A company shaped exactly like a personal name, rescued by context.
    assert _is_person_name("Acme Payments", "Priya Nair", set()) is False, "org word"
    assert _is_person_name("Cradle Wise", "Priya Nair", set()) is True
    assert _is_person_name("Cradle Wise", "Priya Nair", {"cradle wise"}) is False

    # An empty prospect name yields no queries at all rather than unanchored ones.
    assert queries_for(Graph(root=ProspectInput(name="")), [Node(ORG, "Acme", 1)]) == []

    # --- the drawing must show a chain, not a fan-out from the root ---------
    g2 = seed(p, [("Northwind Logistics", "Analyst at Northwind Logistics, 2019-2021")],
              "https://supercarl.ai/profile/x")
    assert next(iter(g2.nodes.values())).evidence.startswith("Analyst at")
    nw = next(iter(g2.nodes.values()))
    g2.explored.add(nw.key())
    g2.add(Node(TOPIC, "freight telematics", depth=2, via=nw.value, parent=nw.key()))
    snap = g2.snapshot()
    child = next(n for n in snap["nodes"] if n["label"] == "freight telematics")
    assert child["parent"] == nw.key(), snap
    assert child["parent"] != "root", "a depth-2 finding must hang off what found it"
    assert next(n for n in snap["nodes"] if n["id"] == nw.key())["parent"] == "root"

    # A parent that was never added falls back to the root rather than dangling.
    g2.add(Node(EVENT, "FinCon 2026", depth=2, parent="org:does-not-exist"))
    snap = g2.snapshot()
    assert next(n for n in snap["nodes"] if n["label"] == "FinCon 2026")["parent"] == "root"

    # Attribution picks the node whose own results mentioned the entity, and
    # reports whether the source ties it to the prospect.
    tied_hit = SearchHit(title="", url="u", score=1.0, query="q",
                         content="Priya Nair at Northwind ran freight telematics")
    loose_hit = SearchHit(title="", url="u", score=1.0, query="q",
                          content="Northwind competes with Acme Freight and Globex")
    by = {"org:northwind logistics": [tied_hit]}
    assert _attribute("freight telematics", by, "Priya Nair") == ("org:northwind logistics", True)
    assert _attribute("absent thing", by, "Priya Nair") == ("", False)

    loose = {"org:northwind logistics": [loose_hit]}
    parent, tied = _attribute("Globex", loose, "Priya Nair")
    assert parent == "org:northwind logistics" and tied is False, \
        "an entity on the page but not tied to the person must not be followed"

    # A source using only the surname still counts as tied.
    sn = SearchHit(title="", url="u", score=1.0, query="q",
                   content="Nair led the telematics rollout at Globex")
    assert _attribute("Globex", {"k": [sn]}, "Priya Nair")[1] is True

    # Every node carries the sentence that put it on the graph.
    ev = SearchHit(title="", url="https://ex.test/a", score=1.0, query="q",
                   content="Unrelated first line. Priya Nair joined Globex in 2021. More text.")
    quote, url = _evidence_for("Globex", {"k": [ev]})
    assert "Priya Nair joined Globex" in quote and url == "https://ex.test/a", (quote, url)
    assert _evidence_for("absent", {"k": [ev]}) == ("", "")
    snap2 = g2.snapshot()
    assert any(n.get("evidence") for n in snap2["nodes"]), "seeds carry their profile line"
    print("graph checks passed")


if __name__ == "__main__":
    _demo()
