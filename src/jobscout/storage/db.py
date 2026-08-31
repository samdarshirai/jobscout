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
