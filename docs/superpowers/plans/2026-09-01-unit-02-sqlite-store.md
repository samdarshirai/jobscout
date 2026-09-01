# Unit 2: SQLite Store — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One SQLite file holds both the LangGraph checkpointer tables and the nine `app_*` application tables, and a graph paused at an `interrupt()` resumes from that file after the connection is closed and reopened.

**Architecture:** A `jobscout.storage.db` module owns three functions: `get_connection(path)` (a configured `sqlite3.Connection`), `init_db(conn)` (applies `schema.sql`, sets `PRAGMA user_version`), and `get_checkpointer(conn)` (a `SqliteSaver` bound to the same connection). App tables and checkpoint tables are separated by name prefix — `app_*` vs LangGraph's `checkpoint*` — in one file, per DESIGN §11. Raw stdlib `sqlite3`, no ORM.

**Tech Stack:** Python ≥3.12, stdlib `sqlite3`, `langgraph` + `langgraph-checkpoint-sqlite` (`SqliteSaver`), pytest.

**Spec:** `docs/2026-09-01-build-plan.md` unit 2; `docs/DESIGN.md` §11 (persistence, data model), §4 (graph — LLM only at named nodes), §16 (process restart resumes from last checkpoint), §7 (Verdicts logged from week 1), §20 (no Postgres, no vector DB). Vocabulary: `CONTEXT.md`.

## Global Constraints

- **One SQLite file** carries the checkpointer AND the app tables, separated by table prefix (DESIGN §11).
- **App table set is exactly these nine** (DESIGN §11): `app_posting`, `app_score`, `app_feedback`, `app_decision`, `app_spend`, `app_seed_label`, `app_profile`, `app_preference_summary`, `app_criteria`.
- Column-level schema is "finalised in code" (DESIGN §11) — later units add columns. This unit fixes the **table set, prefixes, primary keys, and the columns units 3–17 need immediately**.
- Raw `sqlite3` only — no SQLAlchemy or other ORM. No Postgres, no vector DB (DESIGN §20).
- DB path default: `data/jobscout.sqlite` (git-ignored; `get_connection` creates `data/` if missing).
- `PRAGMA foreign_keys = ON` on every connection.
- Python ≥3.12; `uv` packaging; pytest; TDD (write failing test, see it fail, implement, see it pass, commit).

## Interfaces Produced (later units consume these)

- `jobscout.storage.db.DEFAULT_DB_PATH: pathlib.Path` — `Path("data/jobscout.sqlite")`.
- `jobscout.storage.db.SCHEMA_VERSION: int` — currently `1`.
- `get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection` — `row_factory = sqlite3.Row`, `foreign_keys` on, parent dir created, `check_same_thread=False`.
- `init_db(conn: sqlite3.Connection) -> None` — idempotent; applies `schema.sql`, sets `PRAGMA user_version = SCHEMA_VERSION`, commits.
- `get_checkpointer(conn: sqlite3.Connection) -> SqliteSaver` — calls `.setup()`, returns the saver. Pass its return value straight to `StateGraph.compile(checkpointer=...)`.

---

## File Structure

- `src/jobscout/storage/__init__.py` — empty package marker.
- `src/jobscout/storage/db.py` — the three functions above + module constants. One responsibility: opening and initialising the store.
- `src/jobscout/storage/schema.sql` — DDL for the nine `app_*` tables, all `CREATE TABLE IF NOT EXISTS`.
- `tests/storage/__init__.py` — (only if pytest needs it; it does not with the current src layout — skip).
- `tests/storage/test_db.py` — schema + connection tests (pure `sqlite3`, no langgraph).
- `tests/storage/test_checkpoint_resume.py` — shared-file test + the paused-graph-resumes-after-reopen test.

---

## Task 1: `app_*` schema and the connection/init functions

**Files:**
- Create: `src/jobscout/storage/__init__.py`
- Create: `src/jobscout/storage/db.py` (only `get_connection` + `init_db` + constants in this task; `get_checkpointer` is Task 2)
- Create: `src/jobscout/storage/schema.sql`
- Test: `tests/storage/test_db.py`

**Interfaces:**
- Consumes: nothing from earlier units beyond the package existing (Unit 1).
- Produces: `DEFAULT_DB_PATH`, `SCHEMA_VERSION`, `get_connection`, `init_db` (signatures above).

- [ ] **Step 1: Write the failing schema tests**

`tests/storage/test_db.py`:

```python
import sqlite3

import pytest

from jobscout.storage.db import (
    SCHEMA_VERSION,
    get_connection,
    init_db,
)

APP_TABLES = {
    "app_posting",
    "app_score",
    "app_feedback",
    "app_decision",
    "app_spend",
    "app_seed_label",
    "app_profile",
    "app_preference_summary",
    "app_criteria",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def test_get_connection_creates_parent_dir(tmp_path):
    db_path = tmp_path / "nested" / "jobscout.sqlite"
    conn = get_connection(db_path)
    assert db_path.parent.is_dir()
    conn.close()


def test_get_connection_enables_foreign_keys(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    (fk,) = conn.execute("PRAGMA foreign_keys").fetchone()
    assert fk == 1
    conn.close()


def test_init_db_creates_exactly_the_nine_app_tables(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    names = _table_names(conn)
    assert APP_TABLES <= names
    assert {n for n in names if n.startswith("app_")} == APP_TABLES
    conn.close()


def test_init_db_sets_user_version(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    (v,) = conn.execute("PRAGMA user_version").fetchone()
    assert v == SCHEMA_VERSION
    conn.close()


def test_init_db_is_idempotent(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    init_db(conn)  # must not raise
    assert APP_TABLES <= _table_names(conn)
    conn.close()


def test_foreign_keys_are_enforced(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO app_score (posting_id, score) VALUES ('no-such-posting', 50)"
        )
        conn.commit()
    conn.close()


def test_row_factory_is_sqlite3_row(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    conn.execute(
        "INSERT INTO app_posting (id, source, company, title) "
        "VALUES ('p1', 'arbeitnow', 'ACME', 'Frontend Engineer')"
    )
    row = conn.execute("SELECT company FROM app_posting WHERE id='p1'").fetchone()
    assert row["company"] == "ACME"
    conn.close()
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.storage'`.

- [ ] **Step 3: Write `src/jobscout/storage/__init__.py`**

Empty file.

- [ ] **Step 4: Write `src/jobscout/storage/schema.sql`**

```sql
-- Jobscout application tables. All names prefixed `app_` to stay separate from
-- LangGraph's `checkpoint*` tables in the same file (DESIGN §11).
-- Columns are minimal for now — later units ALTER TABLE to add fields
-- (DESIGN §11: "finalised in code"). Bump PRAGMA user_version + add migration
-- handling in db.init_db when that starts.

-- CONTEXT: Posting — one job listing from one company.
CREATE TABLE IF NOT EXISTS app_posting (
    id             TEXT PRIMARY KEY,   -- dedupe key: ATS job-id, else sha256(company+title+city) (§11)
    source         TEXT NOT NULL,      -- CONTEXT: Source (adzuna | arbeitnow | ats:<company> | careers:<company>)
    company        TEXT NOT NULL,
    title          TEXT NOT NULL,
    city           TEXT,
    url            TEXT,
    jd_text        TEXT,               -- CONTEXT: JD; NULL until fetch_jd (§4)
    content_hash   TEXT,               -- staleness detection (§11)
    status         TEXT NOT NULL DEFAULT 'new',
        -- new | queued | scored | excluded | stale | error | dead | package_ready | applied | skipped (CONTEXT)
    status_reason  TEXT,
    first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Score / Rationale / Matched Lines — Score Sub-Agent output (§6).
CREATE TABLE IF NOT EXISTS app_score (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id        TEXT NOT NULL REFERENCES app_posting(id),
    run_id            TEXT,             -- the Run that produced this Score
    score             INTEGER,          -- weighted sum 0-100 (§6)
    rationale         TEXT,
    dimensions_json   TEXT,             -- per Scored Dimension: 0-5 + quoted JD line + quoted resume line (§6)
    criteria_version  INTEGER,          -- app_criteria.version scored against
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Verdict — Candidate thumb + reason on a scored live Posting (§7).
-- Logged from week 1, before the Preference Feedback Loop consumes it (§7).
CREATE TABLE IF NOT EXISTS app_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id  TEXT NOT NULL REFERENCES app_posting(id),
    verdict     TEXT NOT NULL,          -- 'up' | 'down'
    reason      TEXT,                   -- one-line why
    embedding   BLOB,                   -- Few-Shot Store vector; NULL until unit 26 (§7)
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Approval Gate outcomes — search plan / outbound letter / scope expansion (§10).
CREATE TABLE IF NOT EXISTS app_decision (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT,
    gate         TEXT NOT NULL,         -- 'search_plan' | 'outbound_letter' | 'scope_expansion'
    posting_id   TEXT REFERENCES app_posting(id),  -- NULL for search_plan
    outcome      TEXT NOT NULL,         -- 'approved' | 'rejected'
    payload_json TEXT,                  -- the proposal that was decided on
    decided_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Spend — token cost per LLM call, against the $20 cap (§13).
CREATE TABLE IF NOT EXISTS app_spend (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT,
    node               TEXT,            -- graph node that made the call
    model              TEXT,
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER,
    cost_usd           REAL NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Label — Candidate's eval ground truth on a frozen Seed Set Posting (§15).
-- Never feeds the Preference Feedback Loop.
CREATE TABLE IF NOT EXISTS app_seed_label (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id  TEXT NOT NULL,          -- seed JD text lives in-repo; id need not be in app_posting
    overall     INTEGER NOT NULL,       -- 0-100
    thumb       TEXT NOT NULL,          -- 'up' | 'down'
    why         TEXT,
    round       INTEGER NOT NULL DEFAULT 1,  -- 1 = initial, 2 = end-of-project re-label for her Drift (§15)
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Profile — parsed resume + static answers + dynamic Q&A, versioned (§5).
CREATE TABLE IF NOT EXISTS app_profile (
    version      INTEGER PRIMARY KEY,
    data_json    TEXT NOT NULL,
    resume_text  TEXT,
    pre_reset    INTEGER NOT NULL DEFAULT 0,  -- tagged when `onboard --reset` runs (§5)
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Criteria — knockout / scored / learn buckets derived from a Profile, versioned (§5).
CREATE TABLE IF NOT EXISTS app_criteria (
    version          INTEGER PRIMARY KEY,
    data_json        TEXT NOT NULL,
    profile_version  INTEGER,
    pre_reset        INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Preference Summary — LLM prose from accumulated Verdicts, versioned (§7).
CREATE TABLE IF NOT EXISTS app_preference_summary (
    version                 INTEGER PRIMARY KEY,
    summary                 TEXT NOT NULL,
    verdict_count_at_write  INTEGER,   -- regenerated every 5 new Verdicts (§7)
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 5: Write `src/jobscout/storage/db.py` (Task 1 scope)**

```python
"""Opening and initialising the one SQLite file (DESIGN §11)."""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/jobscout.sqlite")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = 1


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """A configured connection: foreign keys on, Row factory, parent dir created.

    check_same_thread=False because LangGraph's checkpointer may touch the
    connection from a worker thread. ponytail: one shared connection is fine
    for a single long-running process; move to a pool or the async saver if
    real concurrency shows up.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply schema.sql and stamp the schema version. Idempotent."""
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
```

- [ ] **Step 6: Run the tests, verify they pass**

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (13 tests — 6 from Unit 1 + 7 here).

- [ ] **Step 8: Commit**

```bash
git add src/jobscout/storage/__init__.py src/jobscout/storage/db.py src/jobscout/storage/schema.sql tests/storage/test_db.py
git commit -m "feat: app_* SQLite schema and connection/init helpers"
```

---

## Task 2: LangGraph checkpointer in the same file, resume-after-reopen

**Files:**
- Modify: `src/jobscout/storage/db.py` (add `get_checkpointer`)
- Modify: `pyproject.toml` (deps — via `uv add`, do not hand-edit)
- Test: `tests/storage/test_checkpoint_resume.py`

**Interfaces:**
- Consumes: `get_connection`, `init_db` from Task 1.
- Produces: `get_checkpointer(conn: sqlite3.Connection) -> SqliteSaver`.

- [ ] **Step 1: Add the LangGraph dependencies**

Run: `uv add langgraph langgraph-checkpoint-sqlite`
This writes pinned versions into `pyproject.toml` `[project.dependencies]` and `uv.lock`.

- [ ] **Step 2: Verify the checkpointer + interrupt API shape**

Run this one-liner to confirm the import paths this task assumes:

```bash
uv run python -c "from langgraph.checkpoint.sqlite import SqliteSaver; from langgraph.graph import StateGraph, START, END; from langgraph.types import interrupt, Command; print('api ok')"
```

Expected: `api ok`.

**If any import fails** (the library reorganised since this plan was written): report BLOCKED with the actual `ImportError`. Do not guess replacement paths — the controller will supply the corrected API. Known likely alternatives to mention in the report: `SqliteSaver` may need `from langgraph.checkpoint.sqlite import SqliteSaver` vs a `.from_conn_string` context manager; `interrupt`/`Command` may live in `langgraph.graph` in some versions.

- [ ] **Step 3: Write the failing tests**

`tests/storage/test_checkpoint_resume.py`:

```python
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
```

- [ ] **Step 4: Run the tests, verify they fail**

Run: `uv run pytest tests/storage/test_checkpoint_resume.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_checkpointer'` (the graph/langgraph imports themselves resolve after Step 1).

- [ ] **Step 5: Add `get_checkpointer` to `src/jobscout/storage/db.py`**

Add the import at the top:

```python
from langgraph.checkpoint.sqlite import SqliteSaver
```

Add the function:

```python
def get_checkpointer(conn: sqlite3.Connection) -> SqliteSaver:
    """A SqliteSaver bound to the same connection as the app tables.

    Its checkpoint* tables land in the one file alongside app_* (DESIGN §11).
    """
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
```

- [ ] **Step 6: Run the tests, verify they pass**

Run: `uv run pytest tests/storage/test_checkpoint_resume.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (15 tests — 6 Unit 1 + 7 Task 1 + 2 here).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/jobscout/storage/db.py tests/storage/test_checkpoint_resume.py
git commit -m "feat: LangGraph SqliteSaver sharing the app DB file"
```

---

## Self-Review

**1. Spec coverage (build-plan unit 2 acceptance criteria):**

| Criterion | Task / test |
|---|---|
| One SQLite file carries checkpointer + app tables, separated by prefix (§11) | Task 2 `test_checkpoint_and_app_tables_live_in_one_file` |
| App tables exist for the nine named concepts (§11) | Task 1 `test_init_db_creates_exactly_the_nine_app_tables` |
| A graph interrupted mid-Run resumes from its last checkpoint after process restart (§11, §16) | Task 2 `test_graph_paused_at_interrupt_resumes_after_connection_reopen` |
| Column-level schema deferred; table set + prefixes fixed | `schema.sql` header comment + `SCHEMA_VERSION`/`user_version` groundwork |
| Verdicts logged from week 1 (§7) | `app_feedback` table present now, before the loop (unit 26) uses it |

No gaps.

**2. Placeholder scan:** No TBD / "add error handling" / bare "write tests". Step 2 of Task 2 is a real verification command with a concrete BLOCKED path, not a placeholder.

**3. Type consistency:** `get_connection` / `init_db` / `get_checkpointer` / `DEFAULT_DB_PATH` / `SCHEMA_VERSION` — used identically in the interfaces block, `db.py`, and both test files. `SqliteSaver` return type consistent between the interfaces block and the function signature. Table names in `schema.sql` match `APP_TABLES` in `test_db.py` exactly (checked name by name: posting, score, feedback, decision, spend, seed_label, profile, criteria, preference_summary).

**4. Known risk carried into the plan, not hidden:** the exact LangGraph import paths and the `interrupt()`/`Command(resume=)` API are version-sensitive (flagged as a deferred unknown in the build plan). Task 2 Step 2 verifies them before any code depends on them and routes a mismatch to BLOCKED rather than a guess.

**Notes for later units:**
- Column additions: `ALTER TABLE app_x ADD COLUMN ...` in `schema.sql` is not automatically applied to an existing DB by `CREATE TABLE IF NOT EXISTS`. The first unit that needs a new column also adds a migration step to `init_db` keyed off `PRAGMA user_version`, and bumps `SCHEMA_VERSION`.
- `python-dotenv` / config loading is still unassigned (deferred from Unit 1) — Unit 4 should own `DEFAULT_DB_PATH` becoming configurable via `.env`.
