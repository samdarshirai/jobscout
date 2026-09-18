import json
from pathlib import Path

import pytest

from jobscout.criteria import Criteria, KnockoutRule, ScoredDimension, write_criteria_file
from jobscout.resume import ExtractedProfile
from jobscout.service import CoreService, RunHandle, get_service

FIXTURE = Path(__file__).parent.parent / "data" / "example" / "fake_resume.pdf"


def _svc(tmp_path) -> CoreService:
    return CoreService(
        db_path=tmp_path / "j.sqlite", criteria_path=tmp_path / "criteria.yaml"
    )


def _sample_criteria() -> Criteria:
    return Criteria(
        knockout=[KnockoutRule(axis="seniority_band", rule="senior or mid only")],
        scored=[
            ScoredDimension(dimension="stack fit", weight=0.4, rubric="5 = 3+ core tools"),
            ScoredDimension(dimension="domain/product interest", weight=0.2, rubric="soft"),
            ScoredDimension(dimension="scope & seniority signals", weight=0.2, rubric="own"),
            ScoredDimension(dimension="eng-culture signals", weight=0.2, rubric="testing"),
        ],
        learn=[],
    )


def test_trigger_run_pauses_at_the_search_plan_gate(tmp_path):
    svc = _svc(tmp_path)
    h = svc.trigger_run()
    assert isinstance(h, RunHandle)
    assert h.status == "paused"
    assert h.pending_gate is not None
    assert h.pending_gate["gate"] == "search_plan"
    svc.close()


def test_resume_run_approve_completes(tmp_path):
    svc = _svc(tmp_path)
    h = svc.trigger_run()
    done = svc.resume_run(h.run_id, "approve")
    assert done.run_id == h.run_id
    assert done.status == "completed"
    assert done.pending_gate is None
    assert done.state["decision"] == "approve"
    svc.close()


def test_resume_run_reject_completes(tmp_path):
    svc = _svc(tmp_path)
    h = svc.trigger_run()
    done = svc.resume_run(h.run_id, "reject")
    assert done.status == "completed"
    assert done.state["decision"] == "reject"
    svc.close()


def test_resume_run_rejects_unknown_run_id(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.resume_run("does-not-exist", "approve")
    svc.close()


def test_record_verdict_writes_one_feedback_row(tmp_path):
    svc = _svc(tmp_path)
    svc._conn.execute(
        "INSERT INTO app_posting (id, source, company, title) "
        "VALUES ('p1', 'arbeitnow', 'ACME', 'Frontend Engineer')"
    )
    svc._conn.commit()
    svc.record_verdict("p1", "up", "great stack")
    rows = svc._conn.execute(
        "SELECT posting_id, verdict, reason FROM app_feedback"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["verdict"] == "up"
    assert rows[0]["reason"] == "great stack"
    svc.close()


def test_record_verdict_rejects_unknown_verdict(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.record_verdict("p1", "maybe")
    svc.close()


def test_run_onboarding_pauses_at_the_static_questions_gate(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React", "TypeScript", "GraphQL"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    svc = _svc(tmp_path)
    h = svc.run_onboarding(str(FIXTURE))
    assert h.status == "paused"
    assert h.pending_gate["gate"] == "static_questions"
    svc.close()


def test_resume_onboarding_walks_to_the_dynamic_questions_gate(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Vue ok?", "IC or lead?", "Remote only?"],
    )
    svc = _svc(tmp_path)
    svc.run_onboarding(str(FIXTURE))
    h = svc.resume_onboarding({"work_mode": "remote"})
    assert h.status == "paused"
    assert h.pending_gate == {
        "gate": "dynamic_questions",
        "questions": ["Vue ok?", "IC or lead?", "Remote only?"],
    }
    svc.close()


def test_resume_onboarding_completes_and_persists_merged_profile(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    fake_criteria = _sample_criteria()
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Vue ok?"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.derive_criteria", lambda profile: fake_criteria
    )
    svc = _svc(tmp_path)
    svc.run_onboarding(str(FIXTURE))
    svc.resume_onboarding({"work_mode": "remote"})
    h = svc.resume_onboarding({"Vue ok?": "yes"})

    assert h.status == "completed"
    assert h.state["static_answers"] == {"work_mode": "remote"}
    assert h.state["dynamic_answers"] == {"Vue ok?": "yes"}
    assert h.state["criteria_draft"] == fake_criteria.model_dump()

    profile_rows = svc._conn.execute(
        "SELECT version, data_json, resume_text FROM app_profile"
    ).fetchall()
    assert len(profile_rows) == 1
    data = json.loads(profile_rows[0]["data_json"])
    assert data["resume"] == fake_profile.model_dump()
    assert data["static_answers"] == {"work_mode": "remote"}
    assert data["dynamic_answers"] == {"Vue ok?": "yes"}

    criteria_rows = svc._conn.execute(
        "SELECT version, data_json, profile_version FROM app_criteria"
    ).fetchall()
    assert len(criteria_rows) == 1
    assert criteria_rows[0]["version"] == 1
    assert criteria_rows[0]["profile_version"] == 1
    assert json.loads(criteria_rows[0]["data_json"]) == fake_criteria.model_dump()

    assert svc._criteria_path.exists()
    assert "seniority_band" in svc._criteria_path.read_text()
    svc.close()


def test_resume_onboarding_after_completion_is_a_harmless_no_op(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Vue ok?"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.derive_criteria", lambda profile: _sample_criteria()
    )
    svc = _svc(tmp_path)
    svc.run_onboarding(str(FIXTURE))
    svc.resume_onboarding({"work_mode": "remote"})
    first = svc.resume_onboarding({"Vue ok?": "yes"})

    again = svc.resume_onboarding({"Vue ok?": "yes"})

    assert again.status == "completed"
    assert again.state == first.state

    rows = svc._conn.execute("SELECT version FROM app_profile").fetchall()
    assert len(rows) == 1
    criteria_rows = svc._conn.execute("SELECT version FROM app_criteria").fetchall()
    assert len(criteria_rows) == 1
    svc.close()


def test_resume_onboarding_rejects_when_no_run_in_progress(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError, match="no onboarding run"):
        svc.resume_onboarding("anything")
    svc.close()


def test_sync_criteria_from_file_bumps_a_new_version_on_change(tmp_path):
    svc = _svc(tmp_path)
    criteria_v1 = _sample_criteria()
    write_criteria_file(criteria_v1, svc._criteria_path)

    version1 = svc.sync_criteria_from_file()
    assert version1 == 1

    criteria_v2 = criteria_v1.model_copy(update={"learn": ["new signal"]})
    write_criteria_file(criteria_v2, svc._criteria_path)

    version2 = svc.sync_criteria_from_file()
    assert version2 == 2

    rows = svc._conn.execute("SELECT version FROM app_criteria ORDER BY version").fetchall()
    assert [r["version"] for r in rows] == [1, 2]
    svc.close()


def test_sync_criteria_from_file_is_a_noop_when_unchanged(tmp_path):
    svc = _svc(tmp_path)
    criteria = _sample_criteria()
    write_criteria_file(criteria, svc._criteria_path)
    svc.sync_criteria_from_file()

    result = svc.sync_criteria_from_file()

    assert result is None
    rows = svc._conn.execute("SELECT version FROM app_criteria").fetchall()
    assert len(rows) == 1
    svc.close()


def test_reset_onboarding_tags_pre_reset_and_clears_the_thread(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    fake_criteria = _sample_criteria()
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Q1?"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.derive_criteria", lambda profile: fake_criteria
    )
    svc = _svc(tmp_path)
    svc.run_onboarding(str(FIXTURE))
    svc.resume_onboarding({"work_mode": "remote"})
    svc.resume_onboarding({"Q1?": "answer"})

    svc.reset_onboarding()

    profile_rows = svc._conn.execute("SELECT pre_reset FROM app_profile").fetchall()
    assert all(r["pre_reset"] == 1 for r in profile_rows)
    criteria_rows = svc._conn.execute("SELECT pre_reset FROM app_criteria").fetchall()
    assert all(r["pre_reset"] == 1 for r in criteria_rows)

    h = svc.run_onboarding(str(FIXTURE))
    assert h.status == "paused"
    assert h.pending_gate["gate"] == "static_questions"
    svc.close()


def test_one_connection_and_one_checkpointer_for_the_service(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile",
        lambda resume_text: ExtractedProfile(
            roles=[], years_experience=0.0, stack=[], seniority_signals=[]
        ),
    )
    svc = _svc(tmp_path)
    svc.trigger_run()
    svc.run_onboarding(str(FIXTURE))
    assert svc._poll.checkpointer is svc._checkpointer
    assert svc._onboard.checkpointer is svc._checkpointer
    assert svc._checkpointer.conn is svc._conn
    svc.close()


def test_public_surface_hands_back_no_graph_objects(tmp_path):
    svc = _svc(tmp_path)
    h = svc.trigger_run()
    for value in (h.run_id, h.status, h.pending_gate, h.state):
        assert isinstance(value, (str, dict, list, bool, int, type(None)))
    svc.close()


def test_run_onboarding_requires_a_resume_path(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError, match="resume PDF path"):
        svc.run_onboarding()
    svc.close()


def test_get_service_is_a_singleton_per_path(tmp_path):
    p = tmp_path / "j.sqlite"
    try:
        assert get_service(p) is get_service(p)
    finally:
        get_service(p).close()
        get_service.cache_clear()
