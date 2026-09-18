import pytest

from jobscout.service import CoreService, RunHandle, get_service


def _svc(tmp_path) -> CoreService:
    return CoreService(db_path=tmp_path / "j.sqlite")


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


def test_run_onboarding_completes(tmp_path):
    svc = _svc(tmp_path)
    h = svc.run_onboarding("cv.pdf")
    assert h.status == "completed"
    assert h.state["profile_draft"] == {"resume_path": "cv.pdf"}
    svc.close()


def test_one_connection_and_one_checkpointer_for_the_service(tmp_path):
    svc = _svc(tmp_path)
    svc.trigger_run()
    svc.run_onboarding()
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


def test_get_service_is_a_singleton_per_path(tmp_path):
    p = tmp_path / "j.sqlite"
    try:
        assert get_service(p) is get_service(p)
    finally:
        get_service(p).close()
        get_service.cache_clear()
