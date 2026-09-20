"""Cover Letter graph (DESIGN §4, §8, §10; build-plan units 23-24).

`draft_letter` -> the outbound-letter Approval Gate -> `check_faithfulness`
-> `finish`. On demand only, per Posting — a thumbs-up or an explicit
"draft letter" triggers a Run of THIS graph (CoreService's job to invoke,
not this module's). Never part of the poll Run and never auto-run for
the whole Queue (DESIGN §8).
"""

import sqlite3
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout import spend
from jobscout.faithfulness import check_faithfulness
from jobscout.letter import draft_letter


class LetterState(TypedDict):
    posting_id: str
    jd_text: str
    resume_text: str
    profile: dict
    voice_notes: str | None
    draft: dict
    decision: str
    faithfulness: dict


def outbound_letter_gate(state: LetterState) -> dict:
    """Approval Gate (DESIGN §10): every letter stops here before it can
    enter a Package (unit 28) — she reviews the draft and decides."""
    decision = interrupt({"gate": "outbound_letter", "draft": state["draft"]})
    return {"decision": str(decision)}


def _after_gate(state: LetterState) -> str:
    """Fail closed (DESIGN §10), same as the search-plan gate (poll.py):
    only an explicit 'approve' proceeds."""
    return "check_faithfulness" if state["decision"] == "approve" else END


def finish(state: LetterState) -> dict:
    return {}


def build_letter_graph(conn: sqlite3.Connection) -> StateGraph:
    def draft(state: LetterState, config: RunnableConfig) -> dict:
        run_id = config["configurable"]["thread_id"]
        result, rows = spend.run_and_track(
            draft_letter,
            state["jd_text"],
            state["resume_text"],
            state["profile"],
            state["voice_notes"],
        )
        spend.log_spend(conn, run_id, "draft_letter", rows)
        return {"draft": result.model_dump()}

    def faithfulness(state: LetterState, config: RunnableConfig) -> dict:
        run_id = config["configurable"]["thread_id"]
        result, rows = spend.run_and_track(
            check_faithfulness, state["draft"]["body"], state["resume_text"], state["profile"]
        )
        spend.log_spend(conn, run_id, "check_faithfulness", rows)
        return {"faithfulness": result.model_dump()}

    g = StateGraph(LetterState)
    g.add_node("draft_letter", draft)
    g.add_node("outbound_letter_gate", outbound_letter_gate)
    g.add_node("check_faithfulness", faithfulness)
    g.add_node("finish", finish)
    g.add_edge(START, "draft_letter")
    g.add_edge("draft_letter", "outbound_letter_gate")
    g.add_conditional_edges(
        "outbound_letter_gate",
        _after_gate,
        {"check_faithfulness": "check_faithfulness", END: END},
    )
    g.add_edge("check_faithfulness", "finish")
    g.add_edge("finish", END)
    return g
