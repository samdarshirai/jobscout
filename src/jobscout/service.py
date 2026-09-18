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

from jobscout.criteria import Criteria, DEFAULT_CRITERIA_PATH, read_criteria_file, write_criteria_file
from jobscout.graph.onboard import build_onboard_graph
from jobscout.graph.poll import build_poll_graph
from jobscout.storage.db import (
    DEFAULT_DB_PATH,
    get_checkpointer,
    get_connection,
    init_db,
)

_VERDICTS = ("up", "down")
_POLL_INIT = {"criteria": {}, "search_plan": {}, "decision": "", "postings": []}
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
    def __init__(
        self, db_path: Path = DEFAULT_DB_PATH, criteria_path: Path | None = None
    ) -> None:
        self._conn: sqlite3.Connection = get_connection(db_path)
        init_db(self._conn)
        self._criteria_path = criteria_path or DEFAULT_CRITERIA_PATH
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
        init = dict(_POLL_INIT, criteria=self._latest_criteria_data())
        self._poll.invoke(init, cfg)
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
                "criteria_draft": {},
            },
            cfg,
        )
        handle = self._handle(self._onboard, _ONBOARD_THREAD)
        if handle.status == "completed":
            self._save_onboarding_results(handle.state)
        return handle

    def resume_onboarding(self, decision: object) -> RunHandle:
        cfg = {"configurable": {"thread_id": _ONBOARD_THREAD}}
        snap = self._onboard.get_state(cfg)
        if not snap.created_at:
            raise ValueError("no onboarding run in progress")
        if not snap.next:  # already completed — nothing to resume, no-op
            return self._handle(self._onboard, _ONBOARD_THREAD)
        self._onboard.invoke(Command(resume=decision), cfg)
        handle = self._handle(self._onboard, _ONBOARD_THREAD)
        if handle.status == "completed":
            self._save_onboarding_results(handle.state)
        return handle

    def reset_onboarding(self) -> None:
        # Ruling: tag, don't delete (matches schema.sql's pre_reset design);
        # app_posting/app_score/app_feedback have no pre_reset column and are
        # intentionally left untouched — see plan Global Constraints.
        self._conn.execute("UPDATE app_profile SET pre_reset = 1")
        self._conn.execute("UPDATE app_criteria SET pre_reset = 1")
        self._conn.commit()
        self._checkpointer.delete_thread(_ONBOARD_THREAD)

    def sync_criteria_from_file(self, path: Path | None = None) -> int | None:
        criteria = read_criteria_file(path or self._criteria_path)
        new_data = json.dumps(criteria.model_dump())
        latest = self._latest_criteria_row()
        if latest is not None and latest["data_json"] == new_data:
            return None
        (profile_version,) = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM app_profile"
        ).fetchone()
        (version,) = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM app_criteria"
        ).fetchone()
        self._conn.execute(
            "INSERT INTO app_criteria (version, data_json, profile_version) "
            "VALUES (?, ?, ?)",
            (version, new_data, profile_version),
        )
        self._conn.commit()
        return version

    def _latest_criteria_row(self):
        return self._conn.execute(
            "SELECT data_json FROM app_criteria ORDER BY version DESC LIMIT 1"
        ).fetchone()

    def _latest_criteria_data(self) -> dict:
        row = self._latest_criteria_row()
        if row is None:
            raise ValueError("no criteria found — run onboarding first")
        return json.loads(row["data_json"])

    def _save_onboarding_results(self, state: dict) -> None:
        profile_version = self._save_profile(state)
        self._save_criteria(state, profile_version)

    def _save_profile(self, state: dict) -> int:
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
        return version

    def _save_criteria(self, state: dict, profile_version: int) -> None:
        data = state["criteria_draft"]
        (version,) = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM app_criteria"
        ).fetchone()
        self._conn.execute(
            "INSERT INTO app_criteria (version, data_json, profile_version) "
            "VALUES (?, ?, ?)",
            (version, json.dumps(data), profile_version),
        )
        self._conn.commit()
        write_criteria_file(Criteria(**data), self._criteria_path)

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
