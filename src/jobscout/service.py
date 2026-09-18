"""Core Service Layer (DESIGN §12).

The single seam between the surfaces (CLI, Telegram, web) and everything
behind them: the LangGraph graphs and the SQLite store. Callers hold no
graph objects and open no connection of their own.

This layer is permanent. The graph *nodes* it drives are skeletons that
units 5-17 replace one at a time; the method signatures here do not change.
"""

import json
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
        if not self._poll.get_state(cfg).created_at:
            raise ValueError(f"no such run: {run_id!r}")
        self._poll.invoke(Command(resume=decision), cfg)
        return self._handle(self._poll, run_id)

    def run_onboarding(self, resume_path: str | None = None) -> RunHandle:
        if not resume_path:
            raise ValueError("run_onboarding needs a resume PDF path")
        cfg = {"configurable": {"thread_id": _ONBOARD_THREAD}}
        self._onboard.invoke(
            {
                "resume_path": resume_path,
                "profile_draft": {},
                "resume_text": "",
                "static_answers": {},
                "dynamic_questions": [],
                "dynamic_answers": {},
            },
            cfg,
        )
        handle = self._handle(self._onboard, _ONBOARD_THREAD)
        if handle.status == "completed":
            self._save_profile(handle.state)
        return handle

    def resume_onboarding(self, decision: object) -> RunHandle:
        cfg = {"configurable": {"thread_id": _ONBOARD_THREAD}}
        if not self._onboard.get_state(cfg).created_at:
            raise ValueError("no onboarding run in progress")
        self._onboard.invoke(Command(resume=decision), cfg)
        handle = self._handle(self._onboard, _ONBOARD_THREAD)
        if handle.status == "completed":
            self._save_profile(handle.state)
        return handle

    def _save_profile(self, state: dict) -> None:
        # ponytail: version read-then-insert isn't race-safe under concurrent
        # callers. Fine today — one CLI process, one command at a time. Add
        # locking / a unique constraint retry if a concurrent surface
        # (units 40-41) ever calls run_onboarding from more than one place.
        (version,) = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM app_profile"
        ).fetchone()
        data = {
            "resume": state["profile_draft"],
            "static_answers": state["static_answers"],
            "dynamic_answers": state["dynamic_answers"],
        }
        self._conn.execute(
            "INSERT INTO app_profile (version, data_json, resume_text) "
            "VALUES (?, ?, ?)",
            (version, json.dumps(data), state["resume_text"]),
        )
        self._conn.commit()

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
