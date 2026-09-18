"""Opening and initialising the one SQLite file (DESIGN §11)."""

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

DEFAULT_DB_PATH = Path("data/jobscout.sqlite")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = 4  # bumped for app_score.content_hash (build-plan unit 20)


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """A configured connection: foreign keys on, Row factory, parent dir created.

    check_same_thread=False because LangGraph's own executor may touch a
    connection from a worker thread. This alone does NOT make one
    connection object safe to share across threads — CoreService opens
    a separate connection per role (app tables vs. the checkpointer) for
    exactly that reason; see get_checkpointer's docstring.
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
    """A SqliteSaver bound to its OWN connection, not the app tables' one.

    Its checkpoint* tables land in the same file alongside app_* (DESIGN
    §11), but on a separate `sqlite3.Connection` object — pass this a
    connection nothing else writes through. LangGraph's own executor can
    run a graph node's body and a checkpoint write on different threads
    within one invoke(); two writers sharing one connection OBJECT is
    unsafe even with check_same_thread=False (confirmed: intermittent
    "cannot commit - no transaction is active" when app code and the
    checkpointer shared a connection). Two separate connections to the
    same file is fine — that's what WAL mode (schema.sql) is for.

    Call this ONCE per process and share the result — the SqliteSaver
    itself still isn't meant to be handed a second one.
    """
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
