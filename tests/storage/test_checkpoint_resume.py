from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from jobscout.storage.db import get_checkpointer, get_connection, init_db


class _S(TypedDict):
    n: int
    note: str


def _build_counter_graph(checkpointer):
    """Toy 3-node graph: bump n, interrupt for a note, bump n again."""

    def step_a(s: _S) -> dict:
        return {"n": s["n"] + 1}

    def step_b(s: _S) -> dict:
        answer = interrupt({"ask": "continue?"})
        return {"note": answer}

    def step_c(s: _S) -> dict:
        return {"n": s["n"] + 1}

    g = StateGraph(_S)
    g.add_node("step_a", step_a)
    g.add_node("step_b", step_b)
    g.add_node("step_c", step_c)
    g.add_edge(START, "step_a")
    g.add_edge("step_a", "step_b")
    g.add_edge("step_b", "step_c")
    g.add_edge("step_c", END)
    return g.compile(checkpointer=checkpointer)


def test_checkpoint_and_app_tables_live_in_one_file(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    get_checkpointer(conn)  # creates checkpoint* tables on the same connection

    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert any(n.startswith("app_") for n in names)
    assert any(n.startswith("checkpoint") for n in names)
    conn.close()


def test_graph_paused_at_interrupt_resumes_after_connection_reopen(tmp_path):
    db_path = tmp_path / "j.sqlite"
    cfg = {"configurable": {"thread_id": "run-1"}}

    # --- process 1: run until the interrupt, then drop the connection ---
    conn1 = get_connection(db_path)
    init_db(conn1)
    graph1 = _build_counter_graph(get_checkpointer(conn1))
    graph1.invoke({"n": 0, "note": ""}, cfg)
    state1 = graph1.get_state(cfg)
    assert state1.next == ("step_b",)  # paused at the interrupt node
    conn1.close()

    # --- process 2: fresh connection + checkpointer on the same file ---
    conn2 = get_connection(db_path)
    graph2 = _build_counter_graph(get_checkpointer(conn2))
    state2 = graph2.get_state(cfg)
    assert state2.next == ("step_b",)  # state was loaded from disk, not memory

    result = graph2.invoke(Command(resume="go ahead"), cfg)
    assert result["n"] == 2
    assert result["note"] == "go ahead"
    conn2.close()
