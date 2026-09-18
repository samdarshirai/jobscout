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
from jobscout import spend
from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH
from jobscout.graph.onboard import build_onboard_graph
from jobscout.graph.poll import build_poll_graph
from jobscout.storage.db import (
    DEFAULT_DB_PATH,
    get_checkpointer,
    get_connection,
    init_db,
)
from jobscout.tracing import configure_tracing

_VERDICTS = ("up", "down")
_POLL_INIT = {"criteria": {}, "search_plan": {}, "decision": "", "postings": []}
_ONBOARD_THREAD = "onboard"


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    status: str  # "paused" | "completed"
    pending_gate: object | None
    state: dict


@dataclass(frozen=True)
class QueueEntry:
    posting_id: str
    company: str
    title: str
    city: str | None
    url: str | None
    score: int
    weak_fit: bool  # score < 50 (§6: Weak-Fit Flag)
    changed: bool  # scored more than once — the JD changed since first seen (§11 unit 20)
    rationale: str
    dimensions: list[dict]


def _pending_gate(snapshot) -> object | None:
    for intr in getattr(snapshot, "interrupts", ()) or ():
        return intr.value
    for task in getattr(snapshot, "tasks", ()) or ():
        for intr in getattr(task, "interrupts", ()) or ():
            return intr.value
    return None


class CoreService:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        criteria_path: Path | None = None,
        companies_path: Path | None = None,
    ) -> None:
        configure_tracing()  # §16: tracing on from the first Run
        self._conn: sqlite3.Connection = get_connection(db_path)
        init_db(self._conn)
        self._criteria_path = criteria_path or DEFAULT_CRITERIA_PATH
        self._companies_path = companies_path or DEFAULT_COMPANIES_PATH
        # The checkpointer gets its OWN connection to the same file, not
        # self._conn. LangGraph's own executor can run a node body and a
        # checkpoint write on different threads within one invoke(), and
        # sharing one sqlite3.Connection object across threads is unsafe
        # even with check_same_thread=False — that flag only disables
        # Python's guard, it doesn't make concurrent access to one
        # connection object safe. Confirmed: intermittent "cannot commit -
        # no transaction is active" under repeated runs before this split.
        # WAL mode (schema.sql) already anticipated more than one
        # connection to this file ("a reader can run without being blocked
        # by a writer") — this is that escape hatch, not a new one (Unit 2
        # final review already flagged "move to a pool... if real
        # concurrency shows up"; LangGraph's own executor is that).
        self._checkpointer_conn = get_connection(db_path)
        self._checkpointer = get_checkpointer(self._checkpointer_conn)
        self._poll = build_poll_graph(self._conn, self._companies_path).compile(
            checkpointer=self._checkpointer
        )
        self._onboard = build_onboard_graph(self._conn).compile(
            checkpointer=self._checkpointer
        )

    def total_spend(self) -> float:
        return spend.total_spend(self._conn)

    def _check_spend_cap(self) -> None:
        # Start-of-call guard only (§13): stops a *new* trigger/resume once
        # over cap, doesn't interrupt a Run already mid-flight past it.
        if self.total_spend() >= spend.CAP_USD:
            raise spend.SpendCapExceeded(
                f"spend cap (${spend.CAP_USD}) reached — no new Run until reviewed"
            )

    # ---- runs ----------------------------------------------------------
    def trigger_run(self) -> RunHandle:
        self._check_spend_cap()
        run_id = uuid4().hex
        cfg = {"configurable": {"thread_id": run_id}}
        init = dict(_POLL_INIT, criteria=self._latest_criteria_data())
        self._poll.invoke(init, cfg)
        return self._handle(self._poll, run_id)

    def resume_run(self, run_id: str, decision: object) -> RunHandle:
        self._check_spend_cap()
        cfg = {"configurable": {"thread_id": run_id}}
        if not self._poll.get_state(cfg).created_at:
            raise ValueError(f"no such run: {run_id!r}")
        self._poll.invoke(Command(resume=decision), cfg)
        handle = self._handle(self._poll, run_id)
        if handle.status == "completed":
            postings = handle.state.get("postings", [])
            self._save_discovered_postings(postings)
            self._save_scores(run_id, postings)
        return handle

    def run_onboarding(self, resume_path: str | None = None) -> RunHandle:
        self._check_spend_cap()
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
        self._check_spend_cap()
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

    def _save_discovered_postings(self, postings: list[dict]) -> None:
        # unit 11: a Posting failing a Knockout carries status="excluded" +
        # status_reason (§6) — everything else defaults to "new" as before.
        # unit 19: every Posting here was seen in this Poll, so its
        # consecutive-miss streak resets — this is also how a Posting that
        # went briefly stale un-stales on reappearing (§11).
        for p in postings:
            row = {"status": "new", "status_reason": None, "content_hash": None, **p}
            self._conn.execute(
                "INSERT INTO app_posting "
                "(id, source, company, title, city, url, jd_text, status, status_reason, "
                "missed_polls, content_hash) "
                "VALUES (:id, :source, :company, :title, :city, :url, :jd_text, :status, "
                ":status_reason, 0, :content_hash) "
                "ON CONFLICT(id) DO UPDATE SET "
                "last_seen_at = datetime('now'), status = :status, status_reason = :status_reason, "
                "missed_polls = 0, content_hash = :content_hash",
                row,
            )
        self._conn.commit()

    def _save_scores(self, run_id: str, postings: list[dict]) -> None:
        # unit 12: a Posting the Score Sub-Agent scored carries a "score" key.
        for p in postings:
            if "score" not in p:
                continue
            self._conn.execute(
                "INSERT INTO app_score "
                "(posting_id, run_id, score, rationale, dimensions_json, criteria_version, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    p["id"],
                    run_id,
                    p["score"],
                    p["rationale"],
                    json.dumps(p["dimensions"]),
                    p.get("criteria_version"),
                    p.get("content_hash"),
                ),
            )
        self._conn.commit()

    # ---- queue ----------------------------------------------------------
    def get_queue(self) -> list[QueueEntry]:
        """Every Posting passing its Knockouts, sorted by Score (§6). A
        Posting the Knockout node excluded never got a Score, so the join
        alone leaves it out — nothing here is ever auto-dropped, and a
        rescored Posting shows only its latest Score, not one row per Run.
        `changed` flags a Posting scored more than once — a proxy for "the
        JD changed since she first saw it" (§11 unit 20); there's no
        separate "when did she look at this" tracking in this data model."""
        rows = self._conn.execute(
            "SELECT p.id, p.company, p.title, p.city, p.url, "
            "s.score, s.rationale, s.dimensions_json, "
            "(SELECT COUNT(*) FROM app_score WHERE posting_id = p.id) AS score_count "
            "FROM app_posting p "
            "JOIN app_score s ON s.id = "
            "  (SELECT MAX(id) FROM app_score WHERE posting_id = p.id) "
            "ORDER BY s.score DESC"
        ).fetchall()
        return [
            QueueEntry(
                posting_id=r["id"],
                company=r["company"],
                title=r["title"],
                city=r["city"],
                url=r["url"],
                score=r["score"],
                weak_fit=r["score"] < 50,
                changed=r["score_count"] > 1,
                rationale=r["rationale"],
                dimensions=json.loads(r["dimensions_json"]) if r["dimensions_json"] else [],
            )
            for r in rows
        ]

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
        self._checkpointer_conn.close()

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
