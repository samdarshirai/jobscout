"""Onboard graph — resume ingest is real; later stages are units 6-7 (DESIGN §5).

`onboard` is a checkpointed subgraph so it can stop halfway and resume.
This unit's `ingest` does the real stage-1 work: PDF -> text -> structured
Profile. Units 6-7 add static questions, dynamic questions, and criteria
derivation as further nodes between `ingest` and `END`.
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from jobscout.resume import extract_resume_text, parse_profile


class OnboardState(TypedDict):
    resume_path: str
    profile_draft: dict
    resume_text: str


def ingest(state: OnboardState) -> dict:
    resume_text = extract_resume_text(Path(state["resume_path"]))
    profile = parse_profile(resume_text)
    return {"resume_text": resume_text, "profile_draft": profile.model_dump()}


def build_onboard_graph() -> StateGraph:
    g = StateGraph(OnboardState)
    g.add_node("ingest", ingest)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", END)
    return g
