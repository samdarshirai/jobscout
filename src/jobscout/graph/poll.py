"""Poll graph — skeleton (DESIGN §4).

Real shape:  plan_search --(gate)--> discover -> dedupe -> fetch_jd -> staleness -> score -> finish
This unit:   plan_search --(gate)--> finish

`plan_search` is a stub (no LLM). Units 8-17 make it the real structured
LLM call and insert discovery/scoring nodes between the gate and `finish`.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class PollState(TypedDict):
    search_plan: dict
    decision: str
    postings: list


def plan_search(state: PollState) -> dict:
    # ponytail: stub plan. Unit 8 replaces this with the structured LLM call
    # that emits real queries + Sources + companies, and logs Spend (§13).
    return {"search_plan": {"queries": [], "sources": [], "companies": []}}


def search_plan_gate(state: PollState) -> dict:
    """Approval Gate: the Run pauses here until the Operator approves the
    Search Plan (DESIGN §10). Resume value 'reject' aborts the Run."""
    decision = interrupt({"gate": "search_plan", "plan": state["search_plan"]})
    return {"decision": str(decision)}


def finish(state: PollState) -> dict:
    # Units 10+ insert discover/dedupe/fetch_jd/staleness/score before this node.
    return {}


def _after_gate(state: PollState) -> str:
    return END if state["decision"] == "reject" else "finish"


def build_poll_graph() -> StateGraph:
    g = StateGraph(PollState)
    g.add_node("plan_search", plan_search)
    g.add_node("search_plan_gate", search_plan_gate)
    g.add_node("finish", finish)
    g.add_edge(START, "plan_search")
    g.add_edge("plan_search", "search_plan_gate")
    g.add_conditional_edges(
        "search_plan_gate", _after_gate, {"finish": "finish", END: END}
    )
    g.add_edge("finish", END)
    return g
