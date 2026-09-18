import json
from pathlib import Path

import pytest

from jobscout.criteria import Criteria, KnockoutRule, ScoredDimension, write_criteria_file
from jobscout.knockout import AxisFact, KnockoutFacts
from jobscout.resume import ExtractedProfile
from jobscout.search_plan import SearchPlan
from jobscout.service import CoreService, RunHandle, get_service

FIXTURE = Path(__file__).parent.parent / "data" / "example" / "fake_resume.pdf"
_FAKE_PLAN = SearchPlan(queries=["senior frontend engineer"], sources=["adzuna"], companies=[])


def _svc(tmp_path) -> CoreService:
    return CoreService(
        db_path=tmp_path / "j.sqlite",
        criteria_path=tmp_path / "criteria.yaml",
        companies_path=tmp_path / "companies.yaml",
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


def _seed_criteria(svc: CoreService) -> None:
    write_criteria_file(_sample_criteria(), svc._criteria_path)
    svc.sync_criteria_from_file()


def test_trigger_run_pauses_at_the_search_plan_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    h = svc.trigger_run()
    assert isinstance(h, RunHandle)
    assert h.status == "paused"
    assert h.pending_gate is not None
    assert h.pending_gate["gate"] == "search_plan"
    assert h.pending_gate["plan"] == _FAKE_PLAN.model_dump()
    svc.close()


def test_trigger_run_requires_criteria(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError, match="no criteria found"):
        svc.trigger_run()
    svc.close()


def _blow_spend_cap(svc: CoreService) -> None:
    svc._conn.execute(
        "INSERT INTO app_spend (run_id, node, model, cost_usd) VALUES ('r0', 'score', 'm', 25.0)"
    )
    svc._conn.commit()


def test_trigger_run_refuses_once_spend_cap_is_reached(tmp_path):
    from jobscout.spend import SpendCapExceeded

    svc = _svc(tmp_path)
    _seed_criteria(svc)
    _blow_spend_cap(svc)
    with pytest.raises(SpendCapExceeded):
        svc.trigger_run()
    svc.close()


def test_run_onboarding_refuses_once_spend_cap_is_reached(tmp_path):
    from jobscout.spend import SpendCapExceeded

    svc = _svc(tmp_path)
    _blow_spend_cap(svc)
    with pytest.raises(SpendCapExceeded):
        svc.run_onboarding(str(FIXTURE))
    svc.close()


def test_total_spend_reflects_logged_rows(tmp_path):
    svc = _svc(tmp_path)
    _blow_spend_cap(svc)
    assert svc.total_spend() == 25.0
    svc.close()


def test_resume_run_approve_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    h = svc.trigger_run()
    done = svc.resume_run(h.run_id, "approve")
    assert done.run_id == h.run_id
    assert done.status == "completed"
    assert done.pending_gate is None
    assert done.state["decision"] == "approve"
    svc.close()


def test_resume_run_reject_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    h = svc.trigger_run()
    done = svc.resume_run(h.run_id, "reject")
    assert done.status == "completed"
    assert done.state["decision"] == "reject"
    svc.close()


def test_resume_run_approve_persists_discovered_postings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    fake_posting = {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    }
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: [fake_posting]
    )
    monkeypatch.setattr(
        "jobscout.graph.poll.extract_knockout_facts",
        lambda jd_text, rules: KnockoutFacts(
            axes=[AxisFact(axis="seniority_band", passes=True, evidence="Engineer")]
        ),
    )
    monkeypatch.setattr(
        "jobscout.score.score_posting",
        lambda posting, scored, resume_text, companies, conn: {
            "score": 80,
            "rationale": "good fit",
            "dimensions": [],
        },
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    h = svc.trigger_run()
    svc.resume_run(h.run_id, "approve")

    row = svc._conn.execute(
        "SELECT source, company, title, jd_text FROM app_posting WHERE id = ?",
        (fake_posting["id"],),
    ).fetchone()
    assert row["company"] == "Acme"
    assert row["jd_text"] == "JD"
    svc.close()


def test_resume_run_approve_resets_missed_polls_on_a_reappearing_posting(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    fake_posting = {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    }
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: [fake_posting]
    )
    monkeypatch.setattr(
        "jobscout.graph.poll.extract_knockout_facts",
        lambda jd_text, rules: KnockoutFacts(
            axes=[AxisFact(axis="seniority_band", passes=True, evidence="Engineer")]
        ),
    )
    monkeypatch.setattr(
        "jobscout.score.score_posting",
        lambda posting, scored, resume_text, companies, conn: {
            "score": 80,
            "rationale": "good fit",
            "dimensions": [],
        },
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    svc._conn.execute(
        "INSERT INTO app_posting (id, source, company, title, missed_polls) "
        "VALUES (?, 'ats:Acme', 'Acme', 'Engineer', 1)",
        (fake_posting["id"],),
    )
    svc._conn.commit()
    h = svc.trigger_run()
    svc.resume_run(h.run_id, "approve")

    row = svc._conn.execute(
        "SELECT missed_polls FROM app_posting WHERE id = ?", (fake_posting["id"],)
    ).fetchone()
    assert row["missed_polls"] == 0
    svc.close()


def test_resume_run_approve_persists_a_score(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    fake_posting = {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    }
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: [fake_posting]
    )
    monkeypatch.setattr(
        "jobscout.graph.poll.extract_knockout_facts",
        lambda jd_text, rules: KnockoutFacts(
            axes=[AxisFact(axis="seniority_band", passes=True, evidence="Engineer")]
        ),
    )
    monkeypatch.setattr(
        "jobscout.score.score_posting",
        lambda posting, scored, resume_text, companies, conn: {
            "score": 65,
            "rationale": "decent stack fit",
            "dimensions": [{"dimension": "stack fit", "score": 3}],
        },
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    h = svc.trigger_run()
    svc.resume_run(h.run_id, "approve")

    row = svc._conn.execute(
        "SELECT posting_id, run_id, score, rationale, dimensions_json, criteria_version "
        "FROM app_score WHERE posting_id = ?",
        (fake_posting["id"],),
    ).fetchone()
    assert row["run_id"] == h.run_id
    assert row["score"] == 65
    assert row["rationale"] == "decent stack fit"
    assert json.loads(row["dimensions_json"]) == [{"dimension": "stack fit", "score": 3}]
    assert row["criteria_version"] == 1
    svc.close()


def test_resume_run_approve_persists_a_knockout_exclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    fake_posting = {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    }
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: [fake_posting]
    )
    monkeypatch.setattr(
        "jobscout.graph.poll.extract_knockout_facts",
        lambda jd_text, rules: KnockoutFacts(
            axes=[AxisFact(axis="seniority_band", passes=False, evidence="Junior role")]
        ),
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    h = svc.trigger_run()
    svc.resume_run(h.run_id, "approve")

    row = svc._conn.execute(
        "SELECT status, status_reason FROM app_posting WHERE id = ?",
        (fake_posting["id"],),
    ).fetchone()
    assert row["status"] == "excluded"
    assert row["status_reason"] == "seniority_band: Junior role"
    svc.close()


def test_resume_run_reject_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    h = svc.trigger_run()
    svc.resume_run(h.run_id, "reject")

    rows = svc._conn.execute("SELECT * FROM app_posting").fetchall()
    assert rows == []
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


def _seed_posting_and_score(svc, posting_id, company, title, score, run_id="r1"):
    svc._conn.execute(
        "INSERT INTO app_posting (id, source, company, title) VALUES (?, 'arbeitnow', ?, ?)",
        (posting_id, company, title),
    )
    svc._conn.execute(
        "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json) "
        "VALUES (?, ?, ?, 'why', '[]')",
        (posting_id, run_id, score),
    )
    svc._conn.commit()


def test_get_queue_sorts_by_score_descending(tmp_path):
    svc = _svc(tmp_path)
    _seed_posting_and_score(svc, "p1", "Acme", "Backend Engineer", 40)
    _seed_posting_and_score(svc, "p2", "Acme", "Frontend Engineer", 90)

    queue = svc.get_queue()

    assert [e.posting_id for e in queue] == ["p2", "p1"]
    svc.close()


def test_get_queue_flags_weak_fit_below_50(tmp_path):
    svc = _svc(tmp_path)
    _seed_posting_and_score(svc, "p1", "Acme", "Backend Engineer", 49)
    _seed_posting_and_score(svc, "p2", "Acme", "Frontend Engineer", 50)

    queue = svc.get_queue()

    assert {e.posting_id: e.weak_fit for e in queue} == {"p1": True, "p2": False}
    svc.close()


def test_get_queue_excludes_a_posting_with_no_score(tmp_path):
    svc = _svc(tmp_path)
    svc._conn.execute(
        "INSERT INTO app_posting (id, source, company, title, status, status_reason) "
        "VALUES ('p1', 'arbeitnow', 'Acme', 'Junior Engineer', 'excluded', 'too junior')"
    )
    svc._conn.commit()

    assert svc.get_queue() == []
    svc.close()


def test_get_queue_shows_only_the_latest_score_for_a_rescored_posting(tmp_path):
    svc = _svc(tmp_path)
    _seed_posting_and_score(svc, "p1", "Acme", "Backend Engineer", 40, run_id="r1")
    svc._conn.execute(
        "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json) "
        "VALUES ('p1', 'r2', 85, 'improved', '[]')"
    )
    svc._conn.commit()

    queue = svc.get_queue()

    assert len(queue) == 1
    assert queue[0].score == 85
    svc.close()


def test_get_queue_flags_changed_for_a_posting_scored_more_than_once(tmp_path):
    svc = _svc(tmp_path)
    _seed_posting_and_score(svc, "p1", "Acme", "Backend Engineer", 40, run_id="r1")
    svc._conn.execute(
        "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json) "
        "VALUES ('p1', 'r2', 85, 'improved', '[]')"
    )
    svc._conn.commit()

    [entry] = svc.get_queue()

    assert entry.changed is True
    svc.close()


def test_get_queue_does_not_flag_changed_for_a_posting_scored_once(tmp_path):
    svc = _svc(tmp_path)
    _seed_posting_and_score(svc, "p1", "Acme", "Backend Engineer", 40)

    [entry] = svc.get_queue()

    assert entry.changed is False
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
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    svc.trigger_run()
    svc.run_onboarding(str(FIXTURE))
    assert svc._poll.checkpointer is svc._checkpointer
    assert svc._onboard.checkpointer is svc._checkpointer
    assert svc._checkpointer.conn is svc._conn
    svc.close()


def test_public_surface_hands_back_no_graph_objects(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
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
