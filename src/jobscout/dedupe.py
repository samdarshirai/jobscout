"""Cross-Source dedupe tie-break (DESIGN §11, build-plan unit 18).

Same job appears via more than one Source (a company ATS, Adzuna,
arbeitnow — units 21-22). Exact-key dedupe (poll.py's `dedupe` node)
already collapses a same-id repeat within one batch; a company+title
collision with *different* keys needs one cheap LLM "same posting? y/n"
call, cached forever — no fuzzy-match thresholds.
"""

import sqlite3

from pydantic import BaseModel, Field

from jobscout.llm import get_llm


class SamePostingResult(BaseModel):
    same: bool = Field(description="Do these two listings describe the same job opening?")


_SAME_POSTING_PROMPT = (
    "Are these two job listings the same opening, posted more than once (e.g. via "
    "different job boards)? Answer strictly from the text below — a different city, "
    "team, or seniority framing means they are not the same.\n\n"
    "Listing A — {a_company} / {a_title} / {a_city}:\n{a_jd}\n\n"
    "Listing B — {b_company} / {b_title} / {b_city}:\n{b_jd}"
)


def _ask_llm_same_posting(a: dict, b: dict) -> bool:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes)."""
    structured_llm = get_llm().with_structured_output(SamePostingResult)
    prompt = _SAME_POSTING_PROMPT.format(
        a_company=a["company"], a_title=a["title"], a_city=a.get("city"), a_jd=a.get("jd_text"),
        b_company=b["company"], b_title=b["title"], b_city=b.get("city"), b_jd=b.get("jd_text"),
    )
    return structured_llm.invoke(prompt).same


def same_posting_cached(conn: sqlite3.Connection, a: dict, b: dict) -> bool:
    """Cached forever, keyed order-independently on the pair's dedupe ids."""
    key_a, key_b = sorted([a["id"], b["id"]])
    row = conn.execute(
        "SELECT same FROM app_dedupe_cache WHERE key_a = ? AND key_b = ?", (key_a, key_b)
    ).fetchone()
    if row is not None:
        return bool(row["same"])
    same = _ask_llm_same_posting(a, b)
    conn.execute(
        "INSERT INTO app_dedupe_cache (key_a, key_b, same) VALUES (?, ?, ?)",
        (key_a, key_b, int(same)),
    )
    conn.commit()
    return same
