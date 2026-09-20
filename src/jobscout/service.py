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

from jobscout import preference, spend
from jobscout.answer_sheet import generate_answer_sheet
from jobscout.criteria import Criteria, DEFAULT_CRITERIA_PATH, read_criteria_file, write_criteria_file
from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH, load_companies
from jobscout.fit_notes import generate_fit_notes
from jobscout.graph.letter import build_letter_graph
from jobscout.graph.onboard import build_onboard_graph
from jobscout.graph.poll import build_poll_graph
from jobscout.graph.scope_expansion import build_scope_expansion_graph
from jobscout.score import score_posting
from jobscout.scope_expansion import is_suppressed, trigger_met
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
_LETTER_INIT = {"draft": {}, "decision": "", "faithfulness": {}}
_SCOPE_EXPANSION_INIT = {"proposal": {}, "decision": ""}
DEFAULT_VOICE_NOTES_PATH = Path("voice.md")  # optional, DESIGN §8


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
        voice_notes_path: Path | None = None,
    ) -> None:
        configure_tracing()  # §16: tracing on from the first Run
        self._conn: sqlite3.Connection = get_connection(db_path)
        init_db(self._conn)
        self._criteria_path = criteria_path or DEFAULT_CRITERIA_PATH
        self._companies_path = companies_path or DEFAULT_COMPANIES_PATH
        self._voice_notes_path = voice_notes_path or DEFAULT_VOICE_NOTES_PATH
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
        self._letter = build_letter_graph(self._conn).compile(
            checkpointer=self._checkpointer
        )
        self._scope_expansion = build_scope_expansion_graph(self._conn).compile(
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
            self._save_poll_run(run_id, postings)
        return handle

    def _save_poll_run(self, run_id: str, postings: list[dict]) -> None:
        """Feeds the Scope Expansion trigger (§10 unit 27): 3 consecutive
        Polls under 2 Postings passing Knockouts. A Posting that errored or
        died this Poll didn't reach a real pass/fail Knockout decision, so
        it doesn't count as passing either."""
        passed = sum(
            1 for p in postings if p.get("status") not in ("excluded", "error", "dead")
        )
        self._conn.execute(
            "INSERT INTO app_poll_run (run_id, passed_knockout_count) VALUES (?, ?)",
            (run_id, passed),
        )
        self._conn.commit()

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

    def _latest_profile_data(self) -> dict:
        row = self._conn.execute(
            "SELECT data_json FROM app_profile ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("no profile found — run onboarding first")
        return json.loads(row["data_json"])

    def _latest_resume_text(self) -> str:
        row = self._conn.execute(
            "SELECT resume_text FROM app_profile ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return (row["resume_text"] if row else None) or ""

    def _posting_jd_text(self, posting_id: str) -> str:
        row = self._conn.execute(
            "SELECT jd_text FROM app_posting WHERE id = ?", (posting_id,)
        ).fetchone()
        if row is None or not row["jd_text"]:
            raise ValueError(f"no such Posting with JD text: {posting_id!r}")
        return row["jd_text"]

    def _read_voice_notes(self) -> str | None:
        """Optional voice.md, folded into the Cover Letter when present (§8)."""
        return self._voice_notes_path.read_text() if self._voice_notes_path.exists() else None

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
        # unit 30: error_count is NOT force-reset like missed_polls — a
        # posting the knockout/score node marked error/dead carries its own
        # incremented error_count in its dict; one that processed cleanly
        # carries no error_count key at all, defaulting to 0 here, which is
        # exactly how it resets on a clean Poll after a prior error.
        for p in postings:
            row = {
                "status": "new",
                "status_reason": None,
                "content_hash": None,
                "error_count": 0,
                **p,
            }
            self._conn.execute(
                "INSERT INTO app_posting "
                "(id, source, company, title, city, url, jd_text, status, status_reason, "
                "missed_polls, content_hash, error_count) "
                "VALUES (:id, :source, :company, :title, :city, :url, :jd_text, :status, "
                ":status_reason, 0, :content_hash, :error_count) "
                "ON CONFLICT(id) DO UPDATE SET "
                "last_seen_at = datetime('now'), status = :status, status_reason = :status_reason, "
                "missed_polls = 0, content_hash = :content_hash, error_count = :error_count",
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
        """Every Posting *currently* passing its Knockouts, sorted by Score
        (§6). A Posting the Knockout node excluded never got a Score, so
        the join alone leaves it out on its first Run — but a Posting can
        be re-discovered in a later Poll and knocked out THEN (confirmed
        live: a Criteria/prompt fix started correctly excluding Postings
        that a past Run had scored), so `p.status = 'scored'` is required
        too — an old app_score row from before that must not keep a now-
        excluded Posting in the Queue. A rescored Posting shows only its
        latest Score, not one row per Run. `changed` flags a Posting
        scored more than once — a proxy for "the JD changed since she
        first saw it" (§11 unit 20); there's no separate "when did she
        look at this" tracking in this data model."""
        rows = self._conn.execute(
            "SELECT p.id, p.company, p.title, p.city, p.url, "
            "s.score, s.rationale, s.dimensions_json, "
            "(SELECT COUNT(*) FROM app_score WHERE posting_id = p.id) AS score_count "
            "FROM app_posting p "
            "JOIN app_score s ON s.id = "
            "  (SELECT MAX(id) FROM app_score WHERE posting_id = p.id) "
            "WHERE p.status = 'scored' "
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
        # unit 26: embed the Posting's JD text so the Few-Shot Store can
        # later find "past Verdicts on postings similar to THIS one" by
        # cosine similarity (preference.top_k_similar_verdicts) — NULL when
        # the Posting has no JD text on file (defensive, shouldn't happen
        # for anything that reached her Queue).
        jd_row = self._conn.execute(
            "SELECT jd_text FROM app_posting WHERE id = ?", (posting_id,)
        ).fetchone()
        embedding = preference.embed(jd_row["jd_text"]) if jd_row and jd_row["jd_text"] else None
        self._conn.execute(
            "INSERT INTO app_feedback (posting_id, verdict, reason, embedding) "
            "VALUES (?, ?, ?, ?)",
            (posting_id, verdict, reason, embedding),
        )
        self._conn.commit()

    # ---- cover letter (units 23-24) ------------------------------------
    def draft_letter_for_posting(self, posting_id: str) -> RunHandle:
        """On demand only (DESIGN §8) — never auto-run for the Queue.
        Every letter stops at the outbound-letter Approval Gate before it
        could ever enter a Package (§10, unit 28)."""
        self._check_spend_cap()
        run_id = uuid4().hex
        cfg = {"configurable": {"thread_id": run_id}}
        init = dict(
            _LETTER_INIT,
            posting_id=posting_id,
            jd_text=self._posting_jd_text(posting_id),
            resume_text=self._latest_resume_text(),
            profile=self._latest_profile_data(),
            voice_notes=self._read_voice_notes(),
        )
        self._letter.invoke(init, cfg)
        return self._handle(self._letter, run_id)

    def resume_letter(self, run_id: str, decision: str) -> RunHandle:
        self._check_spend_cap()
        cfg = {"configurable": {"thread_id": run_id}}
        if not self._letter.get_state(cfg).created_at:
            raise ValueError(f"no such run: {run_id!r}")
        self._letter.invoke(Command(resume=decision), cfg)
        return self._handle(self._letter, run_id)

    # ---- fit notes (unit 25) --------------------------------------------
    def get_fit_notes(self, posting_id: str) -> dict:
        """On demand, ungated, unpersisted — DESIGN §8's Digest/Telegram
        delivery surfaces don't exist yet (units 40-41); this ships
        dormant until then, same precedent as unit 18's cross-source
        dedupe tie-break shipping before a second live Source existed."""
        self._check_spend_cap()
        jd_text = self._posting_jd_text(posting_id)
        notes, rows = spend.run_and_track(
            generate_fit_notes, jd_text, self._latest_resume_text()
        )
        spend.log_spend(self._conn, f"fit_notes:{posting_id}", "fit_notes", rows)
        return notes.model_dump()

    # ---- preference feedback loop (unit 26) ------------------------------
    def learn(self) -> str:
        """`jobscout learn` (§7): force-regenerate the Preference Summary
        now, regardless of the every-5-new-Verdicts cadence."""
        self._check_spend_cap()
        summary, rows = spend.run_and_track(preference.generate_preference_summary, self._conn)
        spend.log_spend(self._conn, "learn", "generate_preference_summary", rows)
        preference.record_preference_summary(self._conn, summary)
        return summary

    def rescore(self) -> int:
        """`jobscout rescore` (§7): "no retroactive re-scoring of the
        existing queue unless asked" — this is that ask. Force re-scores
        every current, non-terminal Posting against the latest Criteria +
        Preference Feedback Loop context, bypassing unit 20's
        content-hash skip (a deliberate re-score, not a routine Poll)."""
        self._check_spend_cap()
        scored_dims = self._latest_criteria_data().get("scored", [])
        if not scored_dims:
            return 0
        resume_text = self._latest_resume_text()
        companies = load_companies(self._companies_path)
        (criteria_version,) = self._conn.execute(
            "SELECT MAX(version) FROM app_criteria"
        ).fetchone()
        rows = self._conn.execute(
            "SELECT id, jd_text, content_hash FROM app_posting "
            "WHERE status NOT IN ('excluded', 'stale', 'dead', 'error') "
            "AND jd_text IS NOT NULL"
        ).fetchall()
        run_id = f"rescore:{uuid4().hex}"
        for r in rows:
            posting = {"id": r["id"], "jd_text": r["jd_text"], "content_hash": r["content_hash"]}
            result, spend_rows = spend.run_and_track(
                score_posting, posting, scored_dims, resume_text, companies, self._conn
            )
            spend.log_spend(self._conn, run_id, "rescore", spend_rows)
            self._conn.execute(
                "INSERT INTO app_score "
                "(posting_id, run_id, score, rationale, dimensions_json, criteria_version, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    r["id"],
                    run_id,
                    result["score"],
                    result["rationale"],
                    json.dumps(result["dimensions"]),
                    criteria_version,
                    r["content_hash"],
                ),
            )
        self._conn.commit()
        return len(rows)

    # ---- scope expansion (unit 27) ---------------------------------------
    def _recent_pass_counts(self, n: int = 3) -> list[int]:
        rows = self._conn.execute(
            "SELECT passed_knockout_count FROM app_poll_run ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [r["passed_knockout_count"] for r in reversed(rows)]  # oldest-first

    def _recent_exclusions(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT title, company, status_reason FROM app_posting "
            "WHERE status = 'excluded' ORDER BY last_seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _recent_scope_rejections(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT payload_json, decided_at FROM app_decision "
            "WHERE gate = 'scope_expansion' AND outcome = 'rejected'"
        ).fetchall()
        out = []
        for r in rows:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
            if payload.get("axis"):
                out.append({"axis": payload["axis"], "decided_at": r["decided_at"]})
        return out

    def maybe_trigger_scope_expansion(self) -> RunHandle | None:
        """DESIGN §10: 3 consecutive thin Polls -> exactly one proposed
        Criteria change, held at its own Approval Gate. `None` when the
        trigger isn't met, or when the resulting proposal's axis is still
        suppressed from a rejection within the last 2 weeks — in which
        case it's auto-rejected and never shown to her (§10)."""
        self._check_spend_cap()
        if not trigger_met(self._recent_pass_counts()):
            return None
        run_id = uuid4().hex
        cfg = {"configurable": {"thread_id": run_id}}
        init = dict(
            _SCOPE_EXPANSION_INIT,
            criteria=self._latest_criteria_data(),
            recent_exclusions=self._recent_exclusions(),
        )
        self._scope_expansion.invoke(init, cfg)
        handle = self._handle(self._scope_expansion, run_id)
        axis = handle.state.get("proposal", {}).get("axis")
        if axis and is_suppressed(axis, self._recent_scope_rejections()):
            self.resume_scope_expansion(run_id, "reject")
            return None
        return handle

    def resume_scope_expansion(self, run_id: str, decision: str) -> RunHandle:
        self._check_spend_cap()
        cfg = {"configurable": {"thread_id": run_id}}
        if not self._scope_expansion.get_state(cfg).created_at:
            raise ValueError(f"no such run: {run_id!r}")
        self._scope_expansion.invoke(Command(resume=decision), cfg)
        handle = self._handle(self._scope_expansion, run_id)
        proposal = handle.state.get("proposal", {})
        self._conn.execute(
            "INSERT INTO app_decision (run_id, gate, outcome, payload_json) "
            "VALUES (?, 'scope_expansion', ?, ?)",
            (run_id, "approved" if decision == "approve" else "rejected", json.dumps(proposal)),
        )
        self._conn.commit()
        if decision == "approve" and proposal.get("axis"):
            self._apply_scope_expansion(proposal)
        return handle

    def _apply_scope_expansion(self, proposal: dict) -> None:
        # ponytail: only rewrites a matching Knockout axis's rule text with
        # the proposal's `change` string — DESIGN's own scope-expansion
        # example is knockout-axis-shaped (a salary floor), and a
        # `scored`-bucket weight change isn't something this mechanism
        # supports yet. Add it if that ever becomes the common case.
        row = self._latest_criteria_row()
        criteria = json.loads(row["data_json"])
        for rule in criteria.get("knockout", []):
            if rule["axis"] == proposal["axis"]:
                rule["rule"] = proposal["change"]
                break
        profile_version = self._conn.execute(
            "SELECT profile_version FROM app_criteria ORDER BY version DESC LIMIT 1"
        ).fetchone()["profile_version"]
        (version,) = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM app_criteria"
        ).fetchone()
        self._conn.execute(
            "INSERT INTO app_criteria (version, data_json, profile_version) VALUES (?, ?, ?)",
            (version, json.dumps(criteria), profile_version),
        )
        self._conn.commit()
        write_criteria_file(Criteria(**criteria), self._criteria_path)

    # ---- package / apply (units 28-29) -----------------------------------
    def approve_application(self, posting_id: str, letter_run_id: str) -> dict:
        """DESIGN §9: she approves an application -> a Package is built and
        the Posting becomes `package_ready`. Never sets `applied` — that's
        unit 29's job, only on her later explicit confirmation of a real
        submission."""
        self._check_spend_cap()
        letter_cfg = {"configurable": {"thread_id": letter_run_id}}
        letter_snap = self._letter.get_state(letter_cfg)
        if not letter_snap.created_at:
            raise ValueError(f"no such letter run: {letter_run_id!r}")
        draft = letter_snap.values.get("draft") or {}
        if not draft.get("body"):
            raise ValueError(f"letter run {letter_run_id!r} has no drafted letter")
        posting = self._conn.execute(
            "SELECT company, url, jd_text FROM app_posting WHERE id = ?", (posting_id,)
        ).fetchone()
        if posting is None:
            raise ValueError(f"no such Posting: {posting_id!r}")
        resume_text = self._latest_resume_text()
        answers, rows = spend.run_and_track(
            generate_answer_sheet,
            posting["jd_text"] or "",
            resume_text,
            self._latest_profile_data(),
            posting["company"],
        )
        spend.log_spend(self._conn, letter_run_id, "answer_sheet", rows)
        package = {
            "letter": draft["body"],
            # ponytail: the original resume PDF's path isn't retained past
            # onboarding ingest — its extracted text is the closest thing
            # this data model keeps; add real file retention if a Package
            # ever needs to hand her back the actual PDF.
            "resume_text": resume_text,
            "jd_url": posting["url"],
            "answer_sheet": answers.model_dump(),
        }
        self._conn.execute(
            "UPDATE app_posting SET status = 'package_ready' WHERE id = ?", (posting_id,)
        )
        self._conn.commit()
        return package

    def mark_applied(self, posting_id: str) -> None:
        """DESIGN §9: set ONLY by her explicit confirmation after a real
        submission on the company site — never by Package generation."""
        row = self._conn.execute(
            "SELECT id FROM app_posting WHERE id = ?", (posting_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no such Posting: {posting_id!r}")
        self._conn.execute(
            "UPDATE app_posting SET status = 'applied', applied_at = datetime('now') WHERE id = ?",
            (posting_id,),
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
