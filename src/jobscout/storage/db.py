"""Opening and initialising the one SQLite file (DESIGN §11)."""

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

DEFAULT_DB_PATH = Path("data/jobscout.sqlite")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = 4  # bumped for app_score.content_hash (build-plan unit 20)


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
    """Apply schema.sql and stamp the schema version. Idempotent for a
    matching or fresh DB; raises on a DB written by a different schema version
    (migration is a later unit's job — see module notes).
    """
    (found,) = conn.execute("PRAGMA user_version").fetchone()
    if found not in (0, SCHEMA_VERSION):
        raise RuntimeError(
            f"DB is schema v{found}, code expects v{SCHEMA_VERSION}; migrate first"
        )
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def get_checkpointer(conn: sqlite3.Connection) -> SqliteSaver:
    """A SqliteSaver bound to the same connection as the app tables.

    Its checkpoint* tables land in the one file alongside app_* (DESIGN §11).

    Call this ONCE per process and share the result. Each SqliteSaver has its
    own lock; two savers on one connection do not mutually exclude, so their
    commits can interleave with each other and with pending app-level writes.
    The Core Service Layer (unit 3) owns the single connection + single
    checkpointer for the process lifetime. Corollary: with a shared connection
    there are no meaningful multi-statement transactions — keep app writes
    single-statement and idempotent.
    """
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
