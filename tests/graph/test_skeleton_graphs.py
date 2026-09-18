from pathlib import Path

from langgraph.types import Command

from jobscout.criteria import Criteria, KnockoutRule, ScoredDimension
from jobscout.graph.onboard import build_onboard_graph
from jobscout.graph.poll import build_poll_graph
from jobscout.knockout import AxisFact, KnockoutFacts
from jobscout.resume import ExtractedProfile
from jobscout.search_plan import SearchPlan
from jobscout.storage.db import get_checkpointer, get_connection, init_db

POLL_INIT = {"criteria": {}, "search_plan": {}, "decision": "", "postings": []}
FIXTURE = Path(__file__).parent.parent.parent / "data" / "example" / "fake_resume.pdf"
_FAKE_PLAN = SearchPlan(queries=["senior frontend engineer"], sources=["adzuna"], companies=[])
_FAKE_POSTINGS = [
    {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    }
]
_DUPLICATE_POSTINGS = [
    {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    },
    {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer (dupe)",
        "city": "Berlin",
        "url": "https://example.com/1-dupe",
        "jd_text": "JD (dupe copy)",
    },
]
_MIXED_JD_POSTINGS = [
    {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Has JD",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    },
    {
        "id": "ats:Acme:acme:2",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Missing JD",
        "city": "Berlin",
        "url": "https://example.com/2",
        "jd_text": "",
    },
]
_CROSS_SOURCE_POSTINGS = [
    {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD via ATS",
    },
    {
        "id": "adzuna:42",
        "source": "adzuna",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/2",
        "jd_text": "JD via Adzuna",
    },
]


def _compile(build_fn, tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    graph = build_fn(conn).compile(checkpointer=get_checkpointer(conn))
    return graph, conn


def _stub_search_plan(monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )


def _stub_discovery(monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: _FAKE_POSTINGS
    )


def test_poll_graph_pauses_at_the_search_plan_gate(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r1"}}
    graph.invoke(POLL_INIT, cfg)
    state = graph.get_state(cfg)
    assert state.next == ("search_plan_gate",)
    assert state.values["search_plan"] == _FAKE_PLAN.model_dump()
    conn.close()


def test_poll_graph_approve_runs_discover_then_ends(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    _stub_discovery(monkeypatch)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r2"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["decision"] == "approve"
    assert state.values["postings"] == _FAKE_POSTINGS
    conn.close()


def test_poll_graph_dedupes_same_id_within_one_batch(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: _DUPLICATE_POSTINGS
    )
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r5"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.values["postings"] == [_DUPLICATE_POSTINGS[0]]
    conn.close()


def test_poll_graph_drops_postings_with_no_jd_text(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: _MIXED_JD_POSTINGS
    )
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r6"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.values["postings"] == [_MIXED_JD_POSTINGS[0]]
    conn.close()


def test_poll_graph_collapses_a_cross_source_duplicate_when_tie_break_says_same(
    tmp_path, monkeypatch
):
    _stub_search_plan(monkeypatch)
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: _CROSS_SOURCE_POSTINGS
    )
    monkeypatch.setattr("jobscout.graph.poll.same_posting_cached", lambda conn, a, b: True)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r12"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.values["postings"] == [_CROSS_SOURCE_POSTINGS[0]]
    conn.close()


def test_poll_graph_keeps_a_cross_source_pair_when_tie_break_says_not_same(
    tmp_path, monkeypatch
):
    _stub_search_plan(monkeypatch)
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: _CROSS_SOURCE_POSTINGS
    )
    monkeypatch.setattr("jobscout.graph.poll.same_posting_cached", lambda conn, a, b: False)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r13"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.values["postings"] == _CROSS_SOURCE_POSTINGS
    conn.close()


def test_poll_graph_excludes_a_posting_failing_a_knockout(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(
        "jobscout.graph.poll.extract_knockout_facts",
        lambda jd_text, rules: KnockoutFacts(
            axes=[AxisFact(axis="german_required", passes=False, evidence="C1 German required")]
        ),
    )
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r7"}}
    init = {**POLL_INIT, "criteria": {"knockout": [{"axis": "german_required", "rule": "B2 max"}]}}
    graph.invoke(init, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    [posting] = state.values["postings"]
    assert posting["status"] == "excluded"
    assert posting["status_reason"] == "german_required: C1 German required"
    conn.close()


def test_poll_graph_keeps_a_posting_passing_every_knockout(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(
        "jobscout.graph.poll.extract_knockout_facts",
        lambda jd_text, rules: KnockoutFacts(
            axes=[AxisFact(axis="german_required", passes=True, evidence="no German mentioned")]
        ),
    )
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r8"}}
    init = {**POLL_INIT, "criteria": {"knockout": [{"axis": "german_required", "rule": "B2 max"}]}}
    graph.invoke(init, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.values["postings"] == _FAKE_POSTINGS
    conn.close()


def test_poll_graph_scores_a_posting_passing_its_knockouts(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(
        "jobscout.score.score_posting",
        lambda posting, scored, resume_text, companies, conn: {
            "score": 72,
            "rationale": "strong stack fit",
            "dimensions": [],
        },
    )
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r10"}}
    init = {
        **POLL_INIT,
        "criteria": {"scored": [{"dimension": "stack fit", "weight": 1.0, "rubric": "core tools"}]},
    }
    graph.invoke(init, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    [posting] = state.values["postings"]
    assert posting["status"] == "scored"
    assert posting["score"] == 72
    assert posting["rationale"] == "strong stack fit"
    conn.close()


def test_poll_graph_never_scores_a_posting_excluded_by_a_knockout(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    _stub_discovery(monkeypatch)
    monkeypatch.setattr(
        "jobscout.graph.poll.extract_knockout_facts",
        lambda jd_text, rules: KnockoutFacts(
            axes=[AxisFact(axis="seniority_band", passes=False, evidence="junior role")]
        ),
    )

    def _boom(*args, **kwargs):
        raise AssertionError("score_posting must not run on an excluded Posting")

    monkeypatch.setattr("jobscout.score.score_posting", _boom)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r11"}}
    init = {
        **POLL_INIT,
        "criteria": {
            "knockout": [{"axis": "seniority_band", "rule": "senior only"}],
            "scored": [{"dimension": "stack fit", "weight": 1.0, "rubric": "core tools"}],
        },
    }
    graph.invoke(init, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    [posting] = state.values["postings"]
    assert posting["status"] == "excluded"
    assert "score" not in posting
    conn.close()


def test_poll_graph_reject_routes_straight_to_end(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r3"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="reject"), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["decision"] == "reject"
    assert state.values["postings"] == []
    conn.close()


def test_poll_graph_unrecognized_decision_fails_closed(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
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
    graph, conn = _compile(lambda conn: build_onboard_graph(conn), tmp_path)
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
