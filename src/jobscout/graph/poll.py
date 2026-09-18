"""Poll graph (DESIGN §4).

Real shape:  plan_search --(gate)--> discover -> dedupe -> fetch_jd -> staleness -> knockout -> score -> finish
This unit:   plan_search --(gate)--> discover -> dedupe -> fetch_jd -> staleness -> knockout -> finish

Unit 12 adds the `score` sub-agent between `knockout` and `finish`.
"""

import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout.criteria import KnockoutRule
from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH
from jobscout.discovery.curated_ats import discover_curated_ats
from jobscout.knockout import decide_knockout, extract_knockout_facts
from jobscout.search_plan import derive_search_plan


class PollState(TypedDict):
    criteria: dict
    search_plan: dict
    decision: str
    postings: list


def plan_search(state: PollState) -> dict:
    # ponytail: no Spend logging yet (§13) — same reasoning as onboard's LLM
    # nodes (units 5-7); the cap-enforcement machinery (unit 15) doesn't exist yet.
    plan = derive_search_plan(state["criteria"])
    return {"search_plan": plan.model_dump()}


def search_plan_gate(state: PollState) -> dict:
    """Approval Gate: the Run pauses here until the Operator approves the
    Search Plan (DESIGN §10). Resume value 'reject' aborts the Run."""
    decision = interrupt({"gate": "search_plan", "plan": state["search_plan"]})
    return {"decision": str(decision)}


def _after_gate(state: PollState) -> str:
    """Fail closed (DESIGN §10): only an explicit 'approve' proceeds.
    Anything else — 'reject', a typo, None — aborts the Run."""
    return "discover" if state["decision"] == "approve" else END


def dedupe(state: PollState) -> dict:
    """Exact-key dedupe only (DESIGN §11) — a same-id Posting discovered
    twice in one batch collapses to its first occurrence. Cross-Source
    fuzzy matching (same job, different id) is unit 17's cached LLM call."""
    seen: dict[str, dict] = {}
    for posting in state["postings"]:
        seen.setdefault(posting["id"], posting)
    return {"postings": list(seen.values())}


def fetch_jd(state: PollState) -> dict:
    """A Posting missing JD text does not reach scoring (DESIGN §4).
    ponytail: Curated ATS Sources (unit 9) already return full JD text
    inline — this is a filter, not a fetch, until units 21-22 add
    summary-only Sources (Adzuna/arbeitnow) that need a real HTTP call
    added here."""
    return {"postings": [p for p in state["postings"] if p.get("jd_text")]}


def staleness(state: PollState) -> dict:
    """Pass-through placeholder — real logic is units 18-19."""
    return {}


def knockout(state: PollState) -> dict:
    """An LLM extracts the fact, a rule decides, per Knockout axis (§6).
    A failing Posting gets status `excluded` + reason here but still flows
    through this Run's `postings` list — unit 13's Queue is what filters
    on `status` so an excluded Posting never lands there or gets a Score."""
    rules = [KnockoutRule(**r) for r in state["criteria"].get("knockout", [])]
    if not rules:
        return {}
    updated = []
    for posting in state["postings"]:
        facts = extract_knockout_facts(posting["jd_text"], rules)
        reason = decide_knockout(facts)
        if reason is not None:
            posting = {**posting, "status": "excluded", "status_reason": reason}
        updated.append(posting)
    return {"postings": updated}


def finish(state: PollState) -> dict:
    # Unit 12+ insert scoring before this node.
    return {}


def build_poll_graph(
    conn: sqlite3.Connection, companies_path: Path = DEFAULT_COMPANIES_PATH
) -> StateGraph:
    def discover(state: PollState) -> dict:
        # ponytail: Curated ATS Boards only. Units 21-22 add Adzuna/arbeitnow.
        return {"postings": discover_curated_ats(conn, companies_path)}

    g = StateGraph(PollState)
    g.add_node("plan_search", plan_search)
    g.add_node("search_plan_gate", search_plan_gate)
    g.add_node("discover", discover)
    g.add_node("dedupe", dedupe)
    g.add_node("fetch_jd", fetch_jd)
    g.add_node("staleness", staleness)
    g.add_node("knockout", knockout)
    g.add_node("finish", finish)
    g.add_edge(START, "plan_search")
    g.add_edge("plan_search", "search_plan_gate")
    g.add_conditional_edges(
        "search_plan_gate", _after_gate, {"discover": "discover", END: END}
    )
    g.add_edge("discover", "dedupe")
    g.add_edge("dedupe", "fetch_jd")
    g.add_edge("fetch_jd", "staleness")
    g.add_edge("staleness", "knockout")
    g.add_edge("knockout", "finish")
    g.add_edge("finish", END)
    return g
