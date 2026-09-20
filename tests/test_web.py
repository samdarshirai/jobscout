from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobscout.criteria import Criteria, KnockoutRule, ScoredDimension, write_criteria_file
from jobscout.search_plan import SearchPlan
from jobscout.service import CoreService
from jobscout.web import _svc, api, app

FIXTURE = Path(__file__).parent.parent / "data" / "example" / "fake_resume.pdf"
_FAKE_PLAN = SearchPlan(queries=["senior frontend engineer"], sources=["adzuna"], companies=[])


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


@pytest.fixture
def client(tmp_path):
    svc = CoreService(
        db_path=tmp_path / "j.sqlite",
        criteria_path=tmp_path / "criteria.yaml",
        companies_path=tmp_path / "companies.yaml",
    )
    write_criteria_file(_sample_criteria(), svc._criteria_path)
    svc.sync_criteria_from_file()
    api.dependency_overrides[_svc] = lambda: svc
    yield TestClient(app), svc
    api.dependency_overrides.clear()
    svc.close()


def _seed_posting(svc: CoreService, posting_id="p1") -> None:
    svc._conn.execute(
        "INSERT INTO app_posting (id, source, company, title, city, url, jd_text, status) "
        "VALUES (?, 'adzuna', 'Acme', 'Engineer', 'Berlin', 'http://x', 'a JD about python', 'scored')",
        (posting_id,),
    )
    svc._conn.execute(
        "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json, criteria_version) "
        "VALUES (?, 'run-1', 80, 'good fit', '[]', 1)",
        (posting_id,),
    )
    svc._conn.commit()


def test_queue_empty(client):
    c, _ = client
    assert c.get("/api/queue").json() == []


def test_queue_lists_seeded_posting(client):
    c, svc = client
    _seed_posting(svc)
    body = c.get("/api/queue").json()
    assert len(body) == 1
    assert body[0]["posting_id"] == "p1"
    assert body[0]["score"] == 80


def test_posting_detail_round_trips(client):
    c, svc = client
    _seed_posting(svc)
    body = c.get("/api/postings/p1").json()
    assert body["company"] == "Acme"
    assert body["jd_text"] == "a JD about python"
    assert len(body["score_history"]) == 1


def test_posting_detail_404_for_unknown_posting(client):
    c, _ = client
    resp = c.get("/api/postings/nope")
    assert resp.status_code == 404


def test_run_detail_has_spend_and_scored_postings(client):
    c, svc = client
    _seed_posting(svc)
    svc._conn.execute(
        "INSERT INTO app_spend (run_id, node, model, prompt_tokens, completion_tokens, cost_usd) "
        "VALUES ('run-1', 'score', 'x', 10, 10, 0.01)"
    )
    svc._conn.commit()
    body = c.get("/api/runs/run-1").json()
    assert body["path"] == ["score"]
    assert body["cost_total"] == pytest.approx(0.01)
    assert body["postings"][0]["id"] == "p1"


def test_verdict_action_records_and_reflects_in_posting(client):
    c, svc = client
    _seed_posting(svc)
    resp = c.post("/api/postings/p1/verdict", json={"verdict": "up", "reason": "nice"})
    assert resp.status_code == 200
    verdicts = c.get("/api/postings/p1").json()["verdicts"]
    assert verdicts == [{"verdict": "up", "reason": "nice", "created_at": verdicts[0]["created_at"]}]


def test_skip_action_updates_status(client):
    c, svc = client
    _seed_posting(svc)
    c.post("/api/postings/p1/skip")
    status = svc._conn.execute("SELECT status FROM app_posting WHERE id='p1'").fetchone()["status"]
    assert status == "skipped"


def test_trigger_run_pauses_at_search_plan_gate(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr("jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN)
    body = c.post("/api/runs/trigger").json()
    assert body["status"] == "paused"
    assert body["pending_gate"]["gate"] == "search_plan"


def test_preference_view_empty(client):
    c, _ = client
    body = c.get("/api/preference").json()
    assert body == {"current": None, "history": []}


def test_eval_view_reports_none_with_no_seed_labels(client):
    c, _ = client
    body = c.get("/api/eval").json()
    assert body["baseline"]["n"] == 0
    assert body["bake_off"] == []
    assert body["ablations"] == []


def test_bearer_token_required_when_set(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("JOBSCOUT_WEB_TOKEN", "secret")
    assert c.get("/api/queue").status_code == 401
    assert c.get("/api/queue", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_index_page_served_at_root(client):
    c, _ = client
    resp = c.get("/")
    assert resp.status_code == 200
    assert "jobscout" in resp.text
