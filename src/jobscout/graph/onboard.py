"""Onboard graph — resume ingest, static questions, dynamic questions (DESIGN §5).

`onboard` is a checkpointed subgraph so each stage can pause and resume later.
Unit 7 adds criteria derivation after this graph reaches END.
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout.questions import STATIC_QUESTIONS, generate_dynamic_questions
from jobscout.resume import extract_resume_text, parse_profile


class OnboardState(TypedDict):
    resume_path: str
    profile_draft: dict
    resume_text: str
    static_answers: dict
    dynamic_questions: list[str]
    dynamic_answers: dict


def ingest(state: OnboardState) -> dict:
    resume_text = extract_resume_text(Path(state["resume_path"]))
    profile = parse_profile(resume_text)
    return {"resume_text": resume_text, "profile_draft": profile.model_dump()}


def static_questions_gate(state: OnboardState) -> dict:
    """Pauses until the candidate answers the static questions (DESIGN §5 stage 2)."""
    answers = interrupt({"gate": "static_questions", "questions": STATIC_QUESTIONS})
    return {"static_answers": dict(answers)}


def draft_dynamic_questions(state: OnboardState) -> dict:
    # ponytail: no Spend logging yet (§13) — same reasoning as parse_profile
    # (unit 5); the cap-enforcement machinery (unit 15) doesn't exist yet.
    questions = generate_dynamic_questions(state["resume_text"], state["static_answers"])
    return {"dynamic_questions": questions}


def dynamic_questions_gate(state: OnboardState) -> dict:
    """Pauses until the candidate answers the LLM-generated follow-ups
    (DESIGN §5 stage 3)."""
    answers = interrupt(
        {"gate": "dynamic_questions", "questions": state["dynamic_questions"]}
    )
    return {"dynamic_answers": dict(answers)}


def build_onboard_graph() -> StateGraph:
    g = StateGraph(OnboardState)
    g.add_node("ingest", ingest)
    g.add_node("static_questions_gate", static_questions_gate)
    g.add_node("draft_dynamic_questions", draft_dynamic_questions)
    g.add_node("dynamic_questions_gate", dynamic_questions_gate)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "static_questions_gate")
    g.add_edge("static_questions_gate", "draft_dynamic_questions")
    g.add_edge("draft_dynamic_questions", "dynamic_questions_gate")
    g.add_edge("dynamic_questions_gate", END)
    return g
