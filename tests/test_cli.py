from types import SimpleNamespace

import yaml
from typer.testing import CliRunner

from jobscout.cli import app
from jobscout.service import QueueEntry

runner = CliRunner()


class _FakeService:
    def __init__(self):
        self.calls = []
        self.next_handle = None
        self.queue = []
        self.total = 0.0
        self.summary = ""
        self.rescored_count = 0

    def trigger_run(self):
        return self.next_handle

    def resume_run(self, run_id, decision):
        self.calls.append(("resume_run", run_id, decision))
        return self.next_handle

    def run_onboarding(self, resume_path):
        self.calls.append(("run_onboarding", resume_path))
        return self.next_handle

    def resume_onboarding(self, answers):
        self.calls.append(("resume_onboarding", answers))
        return self.next_handle

    def reset_onboarding(self):
        self.calls.append(("reset_onboarding",))

    def get_queue(self):
        return self.queue

    def total_spend(self):
        return self.total

    def record_verdict(self, posting_id, verdict, reason=None):
        if verdict not in ("up", "down"):
            raise ValueError(f"verdict must be one of ('up', 'down'), got {verdict!r}")
        self.calls.append(("record_verdict", posting_id, verdict, reason))

    def learn(self):
        self.calls.append(("learn",))
        return self.summary

    def rescore(self):
        self.calls.append(("rescore",))
        return self.rescored_count


def _install_fake(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = _FakeService()
    monkeypatch.setattr("jobscout.cli.get_service", lambda: fake)
    return fake


def test_poll_writes_a_fail_closed_gate_file_when_paused(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.next_handle = SimpleNamespace(
        status="paused",
        run_id="r1",
        pending_gate={"gate": "search_plan", "plan": {"queries": ["a"], "sources": [], "companies": []}},
    )
    result = runner.invoke(app, ["poll"])
    assert result.exit_code == 0
    doc = yaml.safe_load((tmp_path / "data" / "gates" / "poll_r1.yaml").read_text())
    assert doc["decision"] == "reject"
    assert doc["plan"]["queries"] == ["a"]


def test_resume_poll_reads_decision_from_gate_file(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.next_handle = SimpleNamespace(status="completed", run_id="r1", pending_gate=None)
    gate_path = tmp_path / "data" / "gates" / "poll_r1.yaml"
    gate_path.parent.mkdir(parents=True)
    gate_path.write_text("gate: search_plan\nplan: {}\ndecision: approve\n")

    result = runner.invoke(app, ["resume", "poll", "r1"])

    assert result.exit_code == 0
    assert fake.calls == [("resume_run", "r1", "approve")]


def test_onboard_requires_a_resume_path(monkeypatch, tmp_path):
    _install_fake(monkeypatch, tmp_path)
    result = runner.invoke(app, ["onboard"])
    assert result.exit_code != 0


def test_onboard_reset_needs_no_resume_path(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    result = runner.invoke(app, ["onboard", "--reset"])
    assert result.exit_code == 0
    assert fake.calls == [("reset_onboarding",)]


def test_resume_onboard_round_trips_static_answers(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.next_handle = SimpleNamespace(status="completed", run_id="onboard", pending_gate=None)
    gate_path = tmp_path / "data" / "gates" / "onboard.yaml"
    gate_path.parent.mkdir(parents=True)
    gate_path.write_text("gate: static_questions\nanswers:\n  german_level: B2\n")

    result = runner.invoke(app, ["resume", "onboard"])

    assert result.exit_code == 0
    assert fake.calls == [("resume_onboarding", {"german_level": "B2"})]


def test_queue_shows_postings(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.queue = [
        QueueEntry(
            posting_id="p1",
            company="Acme",
            title="Backend Engineer",
            city="Berlin",
            url="https://x",
            score=72,
            weak_fit=False,
            changed=False,
            rationale="why",
            dimensions=[],
        )
    ]
    result = runner.invoke(app, ["queue"])
    assert result.exit_code == 0
    assert "Acme" in result.output
    assert "Backend Engineer" in result.output


def test_spend_shows_total_and_cap(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.total = 5.5
    result = runner.invoke(app, ["spend"])
    assert result.exit_code == 0
    assert "5.50" in result.output
    assert "20.00" in result.output


def test_thumb_records_a_verdict(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    result = runner.invoke(app, ["thumb", "p1", "up", "--reason", "great stack"])
    assert result.exit_code == 0
    assert fake.calls == [("record_verdict", "p1", "up", "great stack")]


def test_thumb_rejects_an_invalid_verdict(monkeypatch, tmp_path):
    _install_fake(monkeypatch, tmp_path)
    result = runner.invoke(app, ["thumb", "p1", "maybe"])
    assert result.exit_code != 0


def test_learn_regenerates_the_preference_summary(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.summary = "likes React, dislikes on-call"
    result = runner.invoke(app, ["learn"])
    assert result.exit_code == 0
    assert fake.calls == [("learn",)]
    assert "likes React, dislikes on-call" in result.output


def test_rescore_reports_how_many_postings_were_rescored(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.rescored_count = 3
    result = runner.invoke(app, ["rescore"])
    assert result.exit_code == 0
    assert fake.calls == [("rescore",)]
    assert "3" in result.output
