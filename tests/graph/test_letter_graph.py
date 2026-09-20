from langgraph.types import Command

from jobscout.graph.letter import build_letter_graph
from jobscout.storage.db import get_checkpointer, get_connection, init_db

LETTER_INIT = {
    "posting_id": "adzuna:1",
    "jd_text": "We need a senior backend engineer.",
    "resume_text": "5 years Python.",
    "profile": {"static_answers": {"german_level": "native"}},
    "voice_notes": None,
    "draft": {},
    "decision": "",
    "faithfulness": {},
}

_FAKE_DRAFT = {"body": "Dear team, ...", "language": "english", "flag": None}
_FAKE_FAITHFULNESS = {"claims": [{"claim": "5 years Python", "traceable": True, "resume_evidence": "5 years Python."}]}


def _compile(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    checkpointer_conn = get_connection(tmp_path / "j.sqlite")
    graph = build_letter_graph(conn).compile(checkpointer=get_checkpointer(checkpointer_conn))
    return graph, conn


class _FakeDraft:
    def model_dump(self):
        return _FAKE_DRAFT


class _FakeFaithfulness:
    def model_dump(self):
        return _FAKE_FAITHFULNESS


def _stub_draft(monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.letter.draft_letter",
        lambda jd_text, resume_text, profile, voice_notes: _FakeDraft(),
    )


def _stub_faithfulness(monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.letter.check_faithfulness",
        lambda letter_body, resume_text, profile: _FakeFaithfulness(),
    )


def test_letter_graph_pauses_at_the_outbound_letter_gate(tmp_path, monkeypatch):
    _stub_draft(monkeypatch)
    graph, conn = _compile(tmp_path)
    cfg = {"configurable": {"thread_id": "l1"}}

    graph.invoke(LETTER_INIT, cfg)
    state = graph.get_state(cfg)

    assert state.next == ("outbound_letter_gate",)
    assert state.values["draft"] == _FAKE_DRAFT
    assert state.values["faithfulness"] == {}
    conn.close()


def test_letter_graph_approve_runs_faithfulness_then_ends(tmp_path, monkeypatch):
    _stub_draft(monkeypatch)
    _stub_faithfulness(monkeypatch)
    graph, conn = _compile(tmp_path)
    cfg = {"configurable": {"thread_id": "l2"}}

    graph.invoke(LETTER_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)

    assert state.next == ()
    assert state.values["decision"] == "approve"
    assert state.values["faithfulness"] == _FAKE_FAITHFULNESS
    conn.close()


def test_letter_graph_reject_skips_faithfulness(tmp_path, monkeypatch):
    _stub_draft(monkeypatch)
    _stub_faithfulness(monkeypatch)  # would fail the test if it got called with wrong args
    graph, conn = _compile(tmp_path)
    cfg = {"configurable": {"thread_id": "l3"}}

    graph.invoke(LETTER_INIT, cfg)
    graph.invoke(Command(resume="reject"), cfg)
    state = graph.get_state(cfg)

    assert state.next == ()
    assert state.values["decision"] == "reject"
    assert state.values["faithfulness"] == {}
    conn.close()


def test_letter_graph_unrecognized_decision_fails_closed(tmp_path, monkeypatch):
    _stub_draft(monkeypatch)
    graph, conn = _compile(tmp_path)
    cfg = {"configurable": {"thread_id": "l4"}}

    graph.invoke(LETTER_INIT, cfg)
    graph.invoke(Command(resume="whatever"), cfg)
    state = graph.get_state(cfg)

    assert state.next == ()
    assert state.values["faithfulness"] == {}
    conn.close()
