"""Onboard graph — resume ingest, static/dynamic questions, criteria (DESIGN §5).

`onboard` is a checkpointed subgraph so each stage can pause and resume later.
"""

import sqlite3
from pathlib import Path
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout import spend
from jobscout.criteria import derive_criteria
from jobscout.questions import STATIC_QUESTIONS, generate_dynamic_questions
from jobscout.resume import extract_resume_text, parse_profile


class OnboardState(TypedDict):
    resume_path: str
    profile_draft: dict
    resume_text: str
    static_answers: dict
    dynamic_questions: list[str]
    dynamic_answers: dict
    criteria_draft: dict


def static_questions_gate(state: OnboardState) -> dict:
    """Pauses until the candidate answers the static questions (DESIGN §5 stage 2)."""
    answers = interrupt({"gate": "static_questions", "questions": STATIC_QUESTIONS})
    return {"static_answers": dict(answers)}


def dynamic_questions_gate(state: OnboardState) -> dict:
    """Pauses until the candidate answers the LLM-generated follow-ups
    (DESIGN §5 stage 3)."""
    answers = interrupt(
        {"gate": "dynamic_questions", "questions": state["dynamic_questions"]}
    )
    return {"dynamic_answers": dict(answers)}


def build_onboard_graph(conn: sqlite3.Connection) -> StateGraph:
    def ingest(state: OnboardState, config: RunnableConfig) -> dict:
        run_id = config["configurable"]["thread_id"]
        resume_text = extract_resume_text(Path(state["resume_path"]))
        profile, rows = spend.run_and_track(parse_profile, resume_text)
        spend.log_spend(conn, run_id, "ingest", rows)
        return {"resume_text": resume_text, "profile_draft": profile.model_dump()}

    def draft_dynamic_questions(state: OnboardState, config: RunnableConfig) -> dict:
        run_id = config["configurable"]["thread_id"]
        questions, rows = spend.run_and_track(
            generate_dynamic_questions, state["resume_text"], state["static_answers"]
        )
        spend.log_spend(conn, run_id, "draft_dynamic_questions", rows)
        return {"dynamic_questions": questions}

    def draft_criteria(state: OnboardState, config: RunnableConfig) -> dict:
        run_id = config["configurable"]["thread_id"]
        profile = {
            "resume": state["profile_draft"],
            "static_answers": state["static_answers"],
            "dynamic_answers": state["dynamic_answers"],
        }
        criteria, rows = spend.run_and_track(derive_criteria, profile)
        spend.log_spend(conn, run_id, "draft_criteria", rows)
        return {"criteria_draft": criteria.model_dump()}

    g = StateGraph(OnboardState)
    g.add_node("ingest", ingest)
    g.add_node("static_questions_gate", static_questions_gate)
    g.add_node("draft_dynamic_questions", draft_dynamic_questions)
    g.add_node("dynamic_questions_gate", dynamic_questions_gate)
    g.add_node("draft_criteria", draft_criteria)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "static_questions_gate")
    g.add_edge("static_questions_gate", "draft_dynamic_questions")
    g.add_edge("draft_dynamic_questions", "dynamic_questions_gate")
    g.add_edge("dynamic_questions_gate", "draft_criteria")
    g.add_edge("draft_criteria", END)
    return g
