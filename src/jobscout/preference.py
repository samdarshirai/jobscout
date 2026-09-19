"""Preference Feedback Loop (DESIGN §7, build-plan unit 26).

CONTEXT.md: Preference Feedback Loop — the cycle that turns accumulated
Verdicts into better future Scores, via two mechanisms:

- **Few-Shot Store**: past Verdicts kept as rows with local embeddings,
  queried by similarity to inject examples into a Score. Not a vector
  database — brute-force cosine over ~50 rows.
- **Preference Summary**: the versioned, LLM-written prose account of
  what the Candidate likes and rejects and why, rewritten from
  accumulated Verdicts. Separate from the Profile.

Same `dedupe.py`-style ownership: this module owns full read/write
access to `app_preference_summary`, a table that already existed in
schema.sql but was dormant until now. `app_feedback.embedding` is
WRITTEN by `service.py`'s `record_verdict` (not here) but READ here.
"""

import sqlite3
from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding
from pydantic import BaseModel, Field

from jobscout.llm import get_llm

_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_NO_VERDICTS_YET = "No Verdicts yet — nothing to summarize."


class _PreferenceSummary(BaseModel):
    summary: str = Field(description="Prose: what the candidate likes and rejects, and why")

_SUMMARY_PROMPT = (
    "Write a concise prose paragraph summarizing what this candidate likes and "
    "rejects in job postings, and why — grounded strictly in the Verdicts below, "
    "no speculation beyond them.\n\nVerdicts:\n{verdicts}"
)


@lru_cache(maxsize=1)
def _embedder() -> TextEmbedding:
    return TextEmbedding(model_name=_MODEL_NAME)


def embed(text: str) -> bytes:
    """A local embedding (fastembed/bge-small, DESIGN §7), serialized for
    storage in `app_feedback.embedding`."""
    (vec,) = _embedder().embed([text])
    return np.asarray(vec, dtype=np.float32).tobytes()


def cosine_similarity(a: bytes, b: bytes) -> float:
    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def top_k_similar_verdicts(conn: sqlite3.Connection, jd_text: str, k: int = 5) -> list[dict]:
    """Few-Shot Store lookup: brute-force cosine over every embedded
    Verdict row (DESIGN §7 — not a vector DB, a handful of rows)."""
    rows = conn.execute(
        "SELECT f.verdict, f.reason, f.embedding, p.title, p.company "
        "FROM app_feedback f JOIN app_posting p ON p.id = f.posting_id "
        "WHERE f.embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return []
    query = embed(jd_text)
    scored = [
        {
            "title": r["title"],
            "company": r["company"],
            "verdict": r["verdict"],
            "reason": r["reason"],
            "similarity": cosine_similarity(query, r["embedding"]),
        }
        for r in rows
    ]
    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return scored[:k]


def _verdict_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT f.verdict, f.reason, p.title, p.company "
        "FROM app_feedback f JOIN app_posting p ON p.id = f.posting_id "
        "ORDER BY f.id"
    ).fetchall()


def _verdict_count(conn: sqlite3.Connection) -> int:
    (count,) = conn.execute("SELECT COUNT(*) FROM app_feedback").fetchone()
    return count


def generate_preference_summary(conn: sqlite3.Connection) -> str:
    """One structured LLM call (DESIGN §4/§7) — no Verdicts, no call at all."""
    rows = _verdict_rows(conn)
    if not rows:
        return _NO_VERDICTS_YET
    verdicts = "\n".join(
        f"- {r['verdict']} on {r['title']!r} at {r['company']}: {r['reason'] or 'no reason given'}"
        for r in rows
    )
    structured_llm = get_llm().with_structured_output(_PreferenceSummary)
    return structured_llm.invoke(_SUMMARY_PROMPT.format(verdicts=verdicts)).summary


def should_regenerate_summary(conn: sqlite3.Connection) -> bool:
    """DESIGN §7: regenerated every 5 new Verdicts (or on `jobscout learn`,
    which just calls `generate_preference_summary` unconditionally)."""
    total = _verdict_count(conn)
    row = conn.execute(
        "SELECT verdict_count_at_write FROM app_preference_summary ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return total > 0
    written = row["verdict_count_at_write"] or 0
    return (total - written) >= 5


def record_preference_summary(conn: sqlite3.Connection, summary: str) -> int:
    (version,) = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM app_preference_summary"
    ).fetchone()
    conn.execute(
        "INSERT INTO app_preference_summary (version, summary, verdict_count_at_write) "
        "VALUES (?, ?, ?)",
        (version, summary, _verdict_count(conn)),
    )
    conn.commit()
    return version
