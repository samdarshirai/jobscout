from pathlib import Path

from langgraph.types import Command

from jobscout.criteria import Criteria, KnockoutRule, ScoredDimension
from jobscout.graph.onboard import build_onboard_graph
from jobscout.graph.poll import build_poll_graph
from jobscout.resume import ExtractedProfile
from jobscout.storage.db import get_checkpointer, get_connection, init_db

POLL_INIT = {"search_plan": {}, "decision": "", "postings": []}
FIXTURE = Path(__file__).parent.parent.parent / "data" / "example" / "fake_resume.pdf"


def _compile(builder, tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    graph = builder().compile(checkpointer=get_checkpointer(conn))
    return graph, conn


def test_poll_graph_pauses_at_the_search_plan_gate(tmp_path):
    graph, conn = _compile(build_poll_graph, tmp_path)
    cfg = {"configurable": {"thread_id": "r1"}}
    graph.invoke(POLL_INIT, cfg)
    assert graph.get_state(cfg).next == ("search_plan_gate",)
    conn.close()


def test_poll_graph_approve_runs_to_end(tmp_path):
    graph, conn = _compile(build_poll_graph, tmp_path)
    cfg = {"configurable": {"thread_id": "r2"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["decision"] == "approve"
    conn.close()


def test_poll_graph_reject_routes_straight_to_end(tmp_path):
    graph, conn = _compile(build_poll_graph, tmp_path)
    cfg = {"configurable": {"thread_id": "r3"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="reject"), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["decision"] == "reject"
    conn.close()


def test_poll_graph_unrecognized_decision_fails_closed(tmp_path):
    graph, conn = _compile(build_poll_graph, tmp_path)
    cfg = {"configurable": {"thread_id": "r4"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="garbage"), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["decision"] == "garbage"
    conn.close()


def test_onboard_graph_walks_through_both_gates_and_derives_criteria(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React", "TypeScript"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    fake_criteria = Criteria(
        knockout=[KnockoutRule(axis="seniority_band", rule="senior or mid only")],
        scored=[
            ScoredDimension(dimension="stack fit", weight=0.4, rubric="5 = 3+ core tools"),
            ScoredDimension(dimension="domain/product interest", weight=0.2, rubric="soft"),
            ScoredDimension(dimension="scope & seniority signals", weight=0.2, rubric="own"),
            ScoredDimension(dimension="eng-culture signals", weight=0.2, rubric="testing"),
        ],
        learn=[],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Vue ok?", "IC or lead?", "Remote only?"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.derive_criteria", lambda profile: fake_criteria
    )
    graph, conn = _compile(build_onboard_graph, tmp_path)
    cfg = {"configurable": {"thread_id": "onboard"}}

    graph.invoke(
        {
            "resume_path": str(FIXTURE),
            "profile_draft": {},
            "resume_text": "",
            "static_answers": {},
            "dynamic_questions": [],
            "dynamic_answers": {},
            "criteria_draft": {},
        },
        cfg,
    )
    state = graph.get_state(cfg)
    assert state.next == ("static_questions_gate",)

    static_answers = {"german_level": "B2", "work_mode": "remote"}
    graph.invoke(Command(resume=static_answers), cfg)
    state = graph.get_state(cfg)
    assert state.next == ("dynamic_questions_gate",)

    dynamic_answers = {"Vue ok?": "yes", "IC or lead?": "open to lead"}
    graph.invoke(Command(resume=dynamic_answers), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["criteria_draft"] == fake_criteria.model_dump()
    assert state.values["static_answers"] == static_answers
    assert state.values["dynamic_answers"] == dynamic_answers
    assert state.values["profile_draft"] == fake_profile.model_dump()
    conn.close()
