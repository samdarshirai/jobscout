# Unit 3: Core Service Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One module — `jobscout.service` — is the only thing that touches a LangGraph graph or the SQLite store. It exposes four operations (trigger a Run, resume a Run at an Approval Gate, record a Verdict, run onboarding), owns exactly one `sqlite3` connection and one checkpointer for the process lifetime, and hands callers back only plain data.

**Architecture:** `CoreService` opens the store (`jobscout.storage.db`) once in `__init__`, builds one checkpointer, and compiles two skeleton graphs against it: a poll graph (`plan_search → search_plan_gate → finish`, with the gate an `interrupt()`) and an onboard graph (single node). The graph *nodes* are placeholders that units 5–17 replace one at a time — node by node — but the `CoreService` method signatures are permanent (DESIGN §12: "built in week 1, not refactored to later"). Public methods return a frozen `RunHandle` dataclass or `None`; no graph object ever leaves the module. A module-level `get_service()` is the process-wide singleton the CLI (unit 17), Telegram bot (unit 40), and web app (unit 41) all call.

**Tech Stack:** Python ≥3.12, `langgraph` + `langgraph-checkpoint-sqlite` (already pinned in unit 2), stdlib `sqlite3`, `dataclasses`, `functools.lru_cache`, pytest.

**Spec:** `docs/2026-09-01-build-plan.md` unit 3; `docs/DESIGN.md` §12 (one core service layer; CLI/Telegram/web are thin callers), §4 (deterministic graph, LLM only at named nodes, gates via `interrupt()` + checkpointer), §5 (onboard is a checkpointed subgraph), §10 (three Approval Gates; search-plan gate before any fetch), §7 (Verdicts logged from week 1). Vocabulary: `CONTEXT.md` ([[core-service-layer]], [[run]], [[approval-gate]], [[search-plan]], [[verdict]]).

## Global Constraints

- **The Core Service Layer is the only thing that touches the graph or the store.** Callers hold no graph objects and open no connection of their own (DESIGN §12). Public return types are `RunHandle` (a frozen dataclass of `str` / `dict` / `None`) or `None`.
- **Exactly one `sqlite3` connection and exactly one `get_checkpointer` result per `CoreService`, for the process lifetime** (carried from the Unit 2 final review: multiple `SqliteSaver` instances on one connection have independent locks and can interleave commits). Both graphs compile against that one checkpointer.
- **App writes stay single-statement and committed immediately.** A shared connection has no meaningful multi-statement transaction. `record_verdict` is one `INSERT` + `commit`. (Verdict recording is an intentional append, not a replayable idempotent mutation — the idempotency half of this rule binds the status updates later units add, not this insert.)
- **Built now, not stubbed for later refactor** (DESIGN §12). Later units extend this layer — they edit the skeleton graph builders and add methods — they never bypass it.
- The skeleton graph nodes are placeholders. `plan_search` returns a stub plan (no LLM); `finish` and `ingest` are near-empty. Units 5–7 fill `ingest`; units 8–17 fill the poll nodes. **Do not implement real discovery, scoring, or onboarding logic here.**
- No new dependencies. No changes to `pyproject.toml`, `uv.lock`, or `schema.sql`.
- Recurring criteria (spend logging §13, trace scrubbing §14) do not apply to this unit: the skeleton nodes make no LLM calls and emit no billable events. Note this in code comments where a real node will later.
- Raw `sqlite3` only — no ORM. Python ≥3.12; `uv` packaging; pytest; TDD (write failing test, see it fail, implement, see it pass, commit).

## Interfaces Produced (later units consume these)

- `jobscout.service.RunHandle` — `@dataclass(frozen=True)` with fields:
  - `run_id: str` — the graph `thread_id`.
  - `status: str` — `"paused"` (stopped at a gate) or `"completed"` (reached `END`).
  - `pending_gate: object | None` — the `interrupt()` payload when paused, else `None`. For the search-plan gate this is `{"gate": "search_plan", "plan": <dict>}`.
  - `state: dict` — the graph's current channel values (`dict(snapshot.values)`).
- `jobscout.service.CoreService`
  - `__init__(self, db_path: pathlib.Path = DEFAULT_DB_PATH) -> None` — opens the connection, runs `init_db`, builds the checkpointer, compiles both graphs.
  - `trigger_run(self) -> RunHandle` — starts a poll Run on a fresh `thread_id`, runs it to the first gate or to `END`.
  - `resume_run(self, run_id: str, decision: object) -> RunHandle` — resumes the poll Run `run_id` with `Command(resume=decision)`.
  - `run_onboarding(self, resume_path: str | None = None) -> RunHandle` — runs the onboard graph on `thread_id="onboard"`.
  - `record_verdict(self, posting_id: str, verdict: str, reason: str | None = None) -> None` — one `INSERT` into `app_feedback`; `verdict` must be `"up"` or `"down"` (else `ValueError`).
  - `close(self) -> None` — closes the connection.
- `jobscout.service.get_service(db_path: pathlib.Path | None = None) -> CoreService` — `lru_cache`d singleton. Surfaces call `get_service()`; tests build `CoreService` directly.
- `jobscout.graph.poll.build_poll_graph() -> StateGraph` and `PollState` (TypedDict: `search_plan: dict`, `decision: str`, `postings: list`). Nodes: `plan_search`, `search_plan_gate`, `finish`. Units 8–17 insert `discover → dedupe → fetch_jd → staleness → score` between `search_plan_gate` and `finish`, and make `plan_search` a real structured LLM call.
- `jobscout.graph.onboard.build_onboard_graph() -> StateGraph` and `OnboardState` (TypedDict: `resume_path: str`, `profile_draft: dict`). Node: `ingest`. Units 5–7 replace `ingest` with resume extraction, static + dynamic questions, and criteria derivation.

---

## File Structure

- `src/jobscout/graph/__init__.py` — empty package marker.
- `src/jobscout/graph/poll.py` — `PollState`, the three node functions, the after-gate router, `build_poll_graph()`. One responsibility: the poll graph's shape.
- `src/jobscout/graph/onboard.py` — `OnboardState`, `ingest`, `build_onboard_graph()`.
- `src/jobscout/service.py` — `RunHandle`, `CoreService`, `_pending_gate`, `get_service`. One responsibility: the seam between surfaces and everything behind them.
- `tests/graph/test_skeleton_graphs.py` — the two graphs drive correctly under a checkpointer (no `CoreService`).
- `tests/test_service.py` — `CoreService` behaviour + the one-connection / one-checkpointer / no-graph-leak assertions.

(No `tests/graph/__init__.py` — matches the existing `tests/storage/` layout.)

---

## Task 1: Skeleton poll and onboard graphs

**Files:**
- Create: `src/jobscout/graph/__init__.py`
- Create: `src/jobscout/graph/poll.py`
- Create: `src/jobscout/graph/onboard.py`
- Test: `tests/graph/test_skeleton_graphs.py`

**Interfaces:**
- Consumes: `get_connection`, `init_db`, `get_checkpointer` from `jobscout.storage.db` (unit 2) — tests only; the builders themselves take no checkpointer.
- Produces: `build_poll_graph`, `PollState`, `build_onboard_graph`, `OnboardState` (signatures above).

- [ ] **Step 1: Write the failing graph tests**

`tests/graph/test_skeleton_graphs.py`:

```python
from langgraph.types import Command

from jobscout.graph.onboard import build_onboard_graph
from jobscout.graph.poll import build_poll_graph
from jobscout.storage.db import get_checkpointer, get_connection, init_db

POLL_INIT = {"search_plan": {}, "decision": "", "postings": []}


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


def test_onboard_graph_runs_to_end_and_is_checkpointed(tmp_path):
    graph, conn = _compile(build_onboard_graph, tmp_path)
    cfg = {"configurable": {"thread_id": "onboard"}}
    graph.invoke({"resume_path": "cv.pdf", "profile_draft": {}}, cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["profile_draft"] == {"resume_path": "cv.pdf"}
    assert state.config["configurable"]["thread_id"] == "onboard"
    conn.close()
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.graph'`.

- [ ] **Step 3: Write `src/jobscout/graph/__init__.py`**

Empty file.

- [ ] **Step 4: Write `src/jobscout/graph/poll.py`**

```python
"""Poll graph — skeleton (DESIGN §4).

Real shape:  plan_search --(gate)--> discover -> dedupe -> fetch_jd -> staleness -> score -> finish
This unit:   plan_search --(gate)--> finish

`plan_search` is a stub (no LLM). Units 8-17 make it the real structured
LLM call and insert discovery/scoring nodes between the gate and `finish`.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class PollState(TypedDict):
    search_plan: dict
    decision: str
    postings: list


def plan_search(state: PollState) -> dict:
    # ponytail: stub plan. Unit 8 replaces this with the structured LLM call
    # that emits real queries + Sources + companies, and logs Spend (§13).
    return {"search_plan": {"queries": [], "sources": [], "companies": []}}


def search_plan_gate(state: PollState) -> dict:
    """Approval Gate: the Run pauses here until the Operator approves the
    Search Plan (DESIGN §10). Resume value 'reject' aborts the Run."""
    decision = interrupt({"gate": "search_plan", "plan": state["search_plan"]})
    return {"decision": str(decision)}


def finish(state: PollState) -> dict:
    # Units 10+ insert discover/dedupe/fetch_jd/staleness/score before this node.
    return {}


def _after_gate(state: PollState) -> str:
    return END if state["decision"] == "reject" else "finish"


def build_poll_graph() -> StateGraph:
    g = StateGraph(PollState)
    g.add_node("plan_search", plan_search)
    g.add_node("search_plan_gate", search_plan_gate)
    g.add_node("finish", finish)
    g.add_edge(START, "plan_search")
    g.add_edge("plan_search", "search_plan_gate")
    g.add_conditional_edges(
        "search_plan_gate", _after_gate, {"finish": "finish", END: END}
    )
    g.add_edge("finish", END)
    return g
```

- [ ] **Step 5: Write `src/jobscout/graph/onboard.py`**

```python
"""Onboard graph — skeleton (DESIGN §5).

`onboard` is a checkpointed subgraph so it can stop halfway and resume.
This unit is a single pass-through node. Units 5-7 replace `ingest` with
resume-PDF extraction, static + dynamic questions, and criteria derivation.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class OnboardState(TypedDict):
    resume_path: str
    profile_draft: dict


def ingest(state: OnboardState) -> dict:
    # Units 5-7 replace this with the real onboarding stages.
    return {"profile_draft": {"resume_path": state["resume_path"]}}


def build_onboard_graph() -> StateGraph:
    g = StateGraph(OnboardState)
    g.add_node("ingest", ingest)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", END)
    return g
```

- [ ] **Step 6: Run the tests, verify they pass**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (16 existing + 4 here = 20).

- [ ] **Step 8: Commit**

```bash
git add src/jobscout/graph/__init__.py src/jobscout/graph/poll.py src/jobscout/graph/onboard.py tests/graph/test_skeleton_graphs.py
git commit -m "feat: skeleton poll and onboard graphs with the search-plan gate"
```

---

## Task 2: `CoreService` and the process singleton

**Files:**
- Create: `src/jobscout/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `build_poll_graph`, `build_onboard_graph` (Task 1); `DEFAULT_DB_PATH`, `get_connection`, `init_db`, `get_checkpointer` (unit 2); `Command` from `langgraph.types`.
- Produces: `RunHandle`, `CoreService`, `get_service` (signatures above).

- [ ] **Step 1: Write the failing service tests**

`tests/test_service.py`:

```python
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
```

- [ ] **Step 2: Verify the LangGraph inspection API shape**

Two attribute names this task's tests and `_pending_gate` depend on are version-sensitive. Confirm them against the installed `langgraph` (1.2.x from unit 2) before writing `service.py`. Write this to the scratchpad and run it:

`/private/tmp/claude-501/-Users-ronalisenapati-Ronali-jobscout/33667f1d-dc3f-488d-b468-35d861b7409e/scratchpad/verify_lg_api.py`:

```python
import pathlib
import tempfile

from jobscout.graph.poll import build_poll_graph
from jobscout.storage.db import get_checkpointer, get_connection, init_db

d = tempfile.mkdtemp()
conn = get_connection(pathlib.Path(d) / "v.sqlite")
init_db(conn)
saver = get_checkpointer(conn)
graph = build_poll_graph().compile(checkpointer=saver)
cfg = {"configurable": {"thread_id": "v1"}}
out = graph.invoke({"search_plan": {}, "decision": "", "postings": []}, cfg)

print("compiled graph has .checkpointer:", hasattr(graph, "checkpointer"))
print("saver has .conn:", hasattr(saver, "conn"))
print("invoke return type/keys:",
      list(out) if isinstance(out, dict) else type(out).__name__)
snap = graph.get_state(cfg)
print("snap.next:", snap.next)
print("snap has .interrupts:", hasattr(snap, "interrupts"),
      repr(getattr(snap, "interrupts", None)))
for t in getattr(snap, "tasks", ()):
    print("task.interrupts:", getattr(t, "interrupts", None))
```

Run: `uv run python <that path>`

Expected: `snap.next: ('search_plan_gate',)`, plus the interrupt payload `{'gate': 'search_plan', 'plan': {...}}` visible on **one** of: `snap.interrupts`, a `task.interrupts` entry, or an `__interrupt__` key in the invoke return.

**Decide from the output:**
- `graph.checkpointer` / `saver.conn` present as printed → keep the two asserts in `test_one_connection_and_one_checkpointer_for_the_service` as written. If either attribute has a different name, update those asserts to the real name(s), keeping the intent (one connection, one checkpointer, shared by both graphs).
- `snap.interrupts` populated → `_pending_gate` reads `snapshot.interrupts` first (code below already does).
- Only `task.interrupts` populated → the `_pending_gate` fallback loop below handles it; fine.
- Only the invoke-return `__interrupt__` key carries it → change `trigger_run` / `resume_run` to capture the invoke return and pass its `__interrupt__` payload into `_handle`; adjust `_pending_gate` accordingly.
- The payload appears on **none** of them → report **BLOCKED** with the script output. Do not guess.

- [ ] **Step 3: Run the service tests, verify they fail**

Run: `uv run pytest tests/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.service'`.

- [ ] **Step 4: Write `src/jobscout/service.py`**

Apply any attribute-name corrections from Step 2.

```python
"""Core Service Layer (DESIGN §12).

The single seam between the surfaces (CLI, Telegram, web) and everything
behind them: the LangGraph graphs and the SQLite store. Callers hold no
graph objects and open no connection of their own.

This layer is permanent. The graph *nodes* it drives are skeletons that
units 5-17 replace one at a time; the method signatures here do not change.
"""

import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from langgraph.types import Command

from jobscout.graph.onboard import build_onboard_graph
from jobscout.graph.poll import build_poll_graph
from jobscout.storage.db import (
    DEFAULT_DB_PATH,
    get_checkpointer,
    get_connection,
    init_db,
)

_VERDICTS = ("up", "down")
_POLL_INIT = {"search_plan": {}, "decision": "", "postings": []}
_ONBOARD_THREAD = "onboard"


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    status: str  # "paused" | "completed"
    pending_gate: object | None
    state: dict


def _pending_gate(snapshot) -> object | None:
    for intr in getattr(snapshot, "interrupts", ()) or ():
        return intr.value
    for task in getattr(snapshot, "tasks", ()) or ():
        for intr in getattr(task, "interrupts", ()) or ():
            return intr.value
    return None


class CoreService:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self._conn: sqlite3.Connection = get_connection(db_path)
        init_db(self._conn)
        # ponytail: one connection + one checkpointer for the whole process.
        # Single-process app; move to a pool / async saver only if real
        # concurrency shows up (Unit 2 final review).
        self._checkpointer = get_checkpointer(self._conn)
        self._poll = build_poll_graph().compile(checkpointer=self._checkpointer)
        self._onboard = build_onboard_graph().compile(
            checkpointer=self._checkpointer
        )

    # ---- runs ----------------------------------------------------------
    def trigger_run(self) -> RunHandle:
        run_id = uuid4().hex
        cfg = {"configurable": {"thread_id": run_id}}
        self._poll.invoke(dict(_POLL_INIT), cfg)
        return self._handle(self._poll, run_id)

    def resume_run(self, run_id: str, decision: object) -> RunHandle:
        cfg = {"configurable": {"thread_id": run_id}}
        self._poll.invoke(Command(resume=decision), cfg)
        return self._handle(self._poll, run_id)

    def run_onboarding(self, resume_path: str | None = None) -> RunHandle:
        cfg = {"configurable": {"thread_id": _ONBOARD_THREAD}}
        self._onboard.invoke(
            {"resume_path": resume_path or "", "profile_draft": {}}, cfg
        )
        return self._handle(self._onboard, _ONBOARD_THREAD)

    # ---- verdict ------------------------------------------------------
    def record_verdict(
        self, posting_id: str, verdict: str, reason: str | None = None
    ) -> None:
        if verdict not in _VERDICTS:
            raise ValueError(
                f"verdict must be one of {_VERDICTS}, got {verdict!r}"
            )
        # Single statement + commit — see Global Constraints.
        self._conn.execute(
            "INSERT INTO app_feedback (posting_id, verdict, reason) "
            "VALUES (?, ?, ?)",
            (posting_id, verdict, reason),
        )
        self._conn.commit()

    # ---- lifecycle --------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    # ---- internals ------------------------------------------------
    def _handle(self, graph, run_id: str) -> RunHandle:
        cfg = {"configurable": {"thread_id": run_id}}
        snap = graph.get_state(cfg)
        paused = bool(snap.next)
        return RunHandle(
            run_id=run_id,
            status="paused" if paused else "completed",
            pending_gate=_pending_gate(snap) if paused else None,
            state=dict(snap.values),
        )


@lru_cache(maxsize=None)
def get_service(db_path: Path | None = None) -> CoreService:
    """Process-wide singleton. Surfaces call `get_service()`; tests build
    `CoreService` directly. `lru_cache` keys on `db_path`, so production
    (always `None`) gets exactly one instance."""
    return CoreService(db_path or DEFAULT_DB_PATH)
```

- [ ] **Step 5: Run the service tests, verify they pass**

Run: `uv run pytest tests/test_service.py -v`
Expected: PASS (9 tests).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (20 from Task 1 + 9 here = 29).

- [ ] **Step 7: Delete the scratchpad verification script**

```bash
rm -f /private/tmp/claude-501/-Users-ronalisenapati-Ronali-jobscout/33667f1d-dc3f-488d-b468-35d861b7409e/scratchpad/verify_lg_api.py
```

- [ ] **Step 8: Commit**

```bash
git add src/jobscout/service.py tests/test_service.py
git commit -m "feat: Core Service Layer — one connection, one checkpointer, plain-data API"
```

---

## Self-Review

**1. Spec coverage (build-plan unit 3 acceptance criteria):**

| Criterion | Task / test |
|---|---|
| A single module exposes: trigger a Run, resume a Run at a gate, record a Verdict, run onboarding (§12) | Task 2 — `CoreService.trigger_run` / `resume_run` / `record_verdict` / `run_onboarding`; `test_trigger_run_*`, `test_resume_run_*`, `test_record_verdict_*`, `test_run_onboarding_completes` |
| The layer is the only thing that touches the graph or the store; callers hold no graph objects (§12) | `RunHandle` is `str`/`dict`/`None` only; `test_public_surface_hands_back_no_graph_objects`; graphs + connection are private attributes |
| Built now, not stubbed for later refactor (§12) | Real `CoreService` with permanent signatures; skeleton lives in the graph *nodes*, called out in every builder's docstring as unit-5–17 work |
| Owns exactly one `sqlite3` connection and one `get_checkpointer` result for the process lifetime, hands them out (Unit 2 review) | `__init__` builds one of each; both graphs compile against `self._checkpointer`; `test_one_connection_and_one_checkpointer_for_the_service`; `get_service` singleton via `lru_cache` |
| App writes single-statement and idempotent | `record_verdict` = one `INSERT` + `commit`; constraint block explains verdict is an append and the idempotency half binds later units' status updates |
| Search-plan Approval Gate via `interrupt()` + checkpointer, answerable later, resumes on approval / aborts on rejection (§4, §10) | Task 1 `search_plan_gate` node; `test_poll_graph_pauses_at_the_search_plan_gate`, `_approve_runs_to_end`, `_reject_routes_straight_to_end` |
| `onboard` is a checkpointed subgraph (§5) | `build_onboard_graph` compiled with the checkpointer; `test_onboard_graph_runs_to_end_and_is_checkpointed` |

No gaps. (Onboarding *content* — resume ingest, questions, criteria — is units 5–7 and correctly excluded here.)

**2. Placeholder scan:** No "TBD" / "add error handling" / bare "write tests". Every code step is complete. The skeleton nodes are deliberate, documented placeholders scoped by the Global Constraints, not plan placeholders. Task 2 Step 2 is a real verification command with a concrete BLOCKED path.

**3. Type consistency:** `RunHandle` / `CoreService` / `get_service` / `build_poll_graph` / `build_onboard_graph` / `PollState` / `OnboardState` — used identically in the Interfaces block, the source, and both test files. `PollState` keys (`search_plan`, `decision`, `postings`) match `_POLL_INIT` and the node returns. `pending_gate` payload shape `{"gate": "search_plan", "plan": ...}` is produced in `search_plan_gate` and asserted in `test_trigger_run_pauses_at_the_search_plan_gate`. `thread_id="onboard"` constant (`_ONBOARD_THREAD`) matches the Task 1 onboard test.

**4. Known risk carried into the plan, not hidden:** `CompiledStateGraph.checkpointer`, `SqliteSaver.conn`, and how a paused `StateSnapshot` exposes its `interrupt()` payload are version-sensitive (LangGraph reorganises these between minors). Task 2 Step 2 verifies all three against the installed version and routes a genuine mismatch to BLOCKED rather than a guess. `_pending_gate` already tries both the `snapshot.interrupts` and `task.interrupts` surfaces.

**Notes for later units:**
- Units 8–17 edit `build_poll_graph`: `plan_search` becomes a real structured LLM call (+ Spend logging §13, + Scrubbed trace §14); `discover → dedupe → fetch_jd → staleness → score` nodes go between `search_plan_gate` and `finish`. The `_after_gate` router already handles reject → `END`.
- Units 5–7 replace `onboard.ingest` with the three onboarding stages. If mid-onboard resume becomes real (an `interrupt()` inside onboard), `run_onboarding` will need a `resume_onboarding` sibling — add it to `CoreService`, do not bypass.
- `record_verdict` does not re-score anything (DESIGN §7) — keep it that way; re-scoring is `jobscout rescore` (unit 26).
- The outbound-letter and scope-expansion gates (units 23, 27) resume through `resume_run` too — the poll graph will have multiple gate nodes; `pending_gate["gate"]` disambiguates which one.
- `get_service()` is the only entry point the CLI (unit 17), Telegram bot (unit 40), and web app (unit 41) use. They never import `jobscout.graph.*` or `jobscout.storage.*`.
