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
