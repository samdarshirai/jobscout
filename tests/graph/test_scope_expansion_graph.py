from langgraph.types import Command

from jobscout.graph.scope_expansion import build_scope_expansion_graph
from jobscout.storage.db import get_checkpointer, get_connection, init_db

INIT = {
    "criteria": {"knockout": [{"axis": "salary_floor", "rule": "at least €70000"}]},
    "recent_exclusions": [{"title": "Backend Eng", "company": "Acme", "status_reason": "salary_floor: €55k"}],
    "proposal": {},
    "decision": "",
}

_FAKE_PROPOSAL = {
    "axis": "salary_floor",
    "change": "salary_floor: €70000 → €60000",
    "evidence": "4 of 5 excluded postings failed only on salary_floor.",
    "surfaced_titles": ["Backend Eng"],
}


class _FakeProposal:
    def model_dump(self):
        return _FAKE_PROPOSAL


def _compile(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    checkpointer_conn = get_connection(tmp_path / "j.sqlite")
    graph = build_scope_expansion_graph(conn).compile(checkpointer=get_checkpointer(checkpointer_conn))
    return graph, conn


def _stub_propose(monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.scope_expansion.propose_scope_expansion",
        lambda criteria, recent_exclusions: _FakeProposal(),
    )


def test_scope_expansion_graph_pauses_at_its_gate(tmp_path, monkeypatch):
    _stub_propose(monkeypatch)
    graph, conn = _compile(tmp_path)
    cfg = {"configurable": {"thread_id": "s1"}}

    graph.invoke(INIT, cfg)
    state = graph.get_state(cfg)

    assert state.next == ("scope_expansion_gate",)
    assert state.values["proposal"] == _FAKE_PROPOSAL
    conn.close()


def test_scope_expansion_graph_approve_completes(tmp_path, monkeypatch):
    _stub_propose(monkeypatch)
    graph, conn = _compile(tmp_path)
    cfg = {"configurable": {"thread_id": "s2"}}

    graph.invoke(INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)

    assert state.next == ()
    assert state.values["decision"] == "approve"
    conn.close()


def test_scope_expansion_graph_reject_completes(tmp_path, monkeypatch):
    _stub_propose(monkeypatch)
    graph, conn = _compile(tmp_path)
    cfg = {"configurable": {"thread_id": "s3"}}

    graph.invoke(INIT, cfg)
    graph.invoke(Command(resume="reject"), cfg)
    state = graph.get_state(cfg)

    assert state.next == ()
    assert state.values["decision"] == "reject"
    conn.close()
