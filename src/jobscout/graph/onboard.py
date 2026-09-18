"""Onboard graph — skeleton (DESIGN §5).

`onboard` is a checkpointed subgraph so it can stop halfway and resume.
This unit is a single pass-through node. Units 5-7 replace `ingest` with
resume-PDF extraction, static + dynamic questions, and criteria derivation.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class OnboardState(TypedDict):
    resume_path: str
    profile_draft: dict


def ingest(state: OnboardState) -> dict:
    # Units 5-7 replace this with the real onboarding stages.
    return {"profile_draft": {"resume_path": state["resume_path"]}}


def build_onboard_graph() -> StateGraph:
    g = StateGraph(OnboardState)
    g.add_node("ingest", ingest)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", END)
    return g
