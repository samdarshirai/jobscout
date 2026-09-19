"""Scope Expansion graph (DESIGN §10 "Scope-expansion mechanics"; build-plan
unit 27).

`propose` -> the `scope_expansion` Approval Gate -> `END`. Triggered by
CoreService after a poll Run completes and finds 3 consecutive thin
Polls (`scope_expansion.trigger_met`) — not part of `poll.py`'s own
graph, same "own small on-demand graph, own gate" shape as
`graph/letter.py`. Applying an approved proposal to Criteria, or
logging + suppressing a rejected one, happens in CoreService after this
graph completes — same as how `poll.py`'s discovered Postings are saved
by CoreService only once its Run finishes, not inside the graph.
"""

import sqlite3
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout import spend
from jobscout.scope_expansion import propose_scope_expansion


class ScopeExpansionState(TypedDict):
    criteria: dict
    recent_exclusions: list[dict]
    proposal: dict
    decision: str


def scope_expansion_gate(state: ScopeExpansionState) -> dict:
    """Approval Gate (DESIGN §10): she approves or rejects the ONE
    proposed change before Criteria is ever touched."""
    decision = interrupt({"gate": "scope_expansion", "proposal": state["proposal"]})
    return {"decision": str(decision)}


def build_scope_expansion_graph(conn: sqlite3.Connection) -> StateGraph:
    def propose(state: ScopeExpansionState, config: RunnableConfig) -> dict:
        run_id = config["configurable"]["thread_id"]
        result, rows = spend.run_and_track(
            propose_scope_expansion, state["criteria"], state["recent_exclusions"]
        )
        spend.log_spend(conn, run_id, "propose_scope_expansion", rows)
        return {"proposal": result.model_dump()}

    g = StateGraph(ScopeExpansionState)
    g.add_node("propose", propose)
    g.add_node("scope_expansion_gate", scope_expansion_gate)
    g.add_edge(START, "propose")
    g.add_edge("propose", "scope_expansion_gate")
    g.add_edge("scope_expansion_gate", END)
    return g
