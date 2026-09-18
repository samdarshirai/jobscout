"""Score Sub-Agent (DESIGN §4, §6; build-plan unit 12).

The one real agent loop: a bounded ReAct sub-agent decides what context it
needs — re-read the resume, look up the company, check past Rejections,
read the current Preference Summary — before committing a Score +
Rationale + Matched Lines. Anti-inflation (§6) is enforced here in code:
a dimension without a *real* quoted JD line and quoted resume line caps at
2, regardless of what the model claims.
"""

import sqlite3
from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH, TargetCompany, load_companies
from jobscout.llm import get_llm

_MAX_STEPS = 12  # "bounded" ReAct sub-agent (DESIGN §4)


class DimensionScore(BaseModel):
    dimension: str = Field(description="Must exactly match one of the given Scored Dimensions")
    score: int = Field(ge=0, le=5)
    jd_line: str | None = Field(
        default=None, description="Exact quoted JD line backing this score, or null if none"
    )
    resume_line: str | None = Field(
        default=None, description="Exact quoted resume line backing this score, or null if none"
    )


class ScoreResult(BaseModel):
    dimensions: list[DimensionScore]
    rationale: str


_SCORE_PROMPT = (
    "Score this Job Description against the candidate on each Scored Dimension "
    "below, 0-5. Every score above 2 needs an exact quoted JD line AND an exact "
    "quoted resume line to back it. Use the tools if you need to re-read the "
    "resume, check the company, see past Rejections, or read the current "
    "Preference Summary before scoring.\n\n"
    "Scored Dimensions:\n{dimensions}\n\nJob Description:\n{jd_text}"
)


def _dimensions_text(scored: list[dict]) -> str:
    return "\n".join(f"- {d['dimension']} (weight {d['weight']}): {d['rubric']}" for d in scored)


def _latest_resume_text(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT resume_text FROM app_profile ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return (row["resume_text"] if row else None) or ""


def _latest_criteria_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(version) AS v FROM app_criteria").fetchone()
    return row["v"] if row else None


def _latest_score_content_hash(conn: sqlite3.Connection, posting_id: str) -> str | None:
    row = conn.execute(
        "SELECT content_hash FROM app_score WHERE posting_id = ? ORDER BY id DESC LIMIT 1",
        (posting_id,),
    ).fetchone()
    return row["content_hash"] if row else None


def _company_lookup_text(companies: list[TargetCompany], company: str) -> str:
    match = next((c for c in companies if c.name == company), None)
    if match is None:
        return f"{company!r} is not on the curated Target Companies list."
    return f"{match.name} (slug {match.slug}); careers page: {match.careers_url or 'unknown'}."


def _past_rejections_text(conn: sqlite3.Connection, company: str) -> str:
    """Rejection (CONTEXT.md): thumbs-down Verdict, or Excluded, or Skipped."""
    downs = conn.execute(
        "SELECT p.title, f.reason FROM app_feedback f "
        "JOIN app_posting p ON p.id = f.posting_id "
        "WHERE f.verdict = 'down' AND p.company = ?",
        (company,),
    ).fetchall()
    others = conn.execute(
        "SELECT title, status, status_reason FROM app_posting "
        "WHERE company = ? AND status IN ('excluded', 'skipped')",
        (company,),
    ).fetchall()
    if not downs and not others:
        return f"No past Rejections on file for {company}."
    lines = [f"Thumbs-down on {r['title']!r}: {r['reason'] or 'no reason given'}" for r in downs]
    lines += [
        f"{r['status'].capitalize()} {r['title']!r}: {r['status_reason'] or 'no reason given'}"
        for r in others
    ]
    return "\n".join(lines)


def _preference_summary_text(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT summary FROM app_preference_summary ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return row["summary"] if row else "No Preference Summary yet."


def _build_tools(conn: sqlite3.Connection, resume_text: str, companies: list[TargetCompany]) -> list:
    def read_resume() -> str:
        """Re-read the candidate's full resume text."""
        return resume_text or "No resume on file."

    def company_lookup(company: str) -> str:
        """Look up what's known about a Target Company by its exact name."""
        return _company_lookup_text(companies, company)

    def past_rejections(company: str) -> str:
        """Past Rejections for this company: thumbs-down Verdicts, Excluded, or Skipped Postings."""
        return _past_rejections_text(conn, company)

    def preference_summary() -> str:
        """The current Preference Summary distilled from past thumbs-up/down feedback."""
        return _preference_summary_text(conn)

    return [tool(read_resume), tool(company_lookup), tool(past_rejections), tool(preference_summary)]


def _run_scoring_agent(jd_text: str, scored: list[dict], tools: list) -> ScoreResult:
    """The bounded ReAct loop + one structured final call (DESIGN §4)."""
    agent = create_react_agent(get_llm(), tools, response_format=ScoreResult)
    prompt = _SCORE_PROMPT.format(dimensions=_dimensions_text(scored), jd_text=jd_text)
    result = agent.invoke(
        {"messages": [("user", prompt)]}, config={"recursion_limit": _MAX_STEPS}
    )
    return result["structured_response"]


def _cap_uncited(
    dims: list[DimensionScore], jd_text: str, resume_text: str
) -> list[DimensionScore]:
    """Anti-inflation (§6): a dimension without a real quoted JD line and a
    real quoted resume line caps at 2 — verified here, not trusted from the
    model's own claim."""
    capped = []
    for d in dims:
        jd_ok = bool(d.jd_line) and d.jd_line.strip().lower() in jd_text.lower()
        resume_ok = bool(d.resume_line) and d.resume_line.strip().lower() in resume_text.lower()
        if (not jd_ok or not resume_ok) and d.score > 2:
            d = d.model_copy(update={"score": 2})
        capped.append(d)
    return capped


def weighted_total(dims: list[DimensionScore], scored: list[dict]) -> int:
    """Weighted sum -> 0-100, weights from criteria (§6)."""
    weight_by_dim = {d["dimension"]: d["weight"] for d in scored}
    total = sum(d.score / 5 * weight_by_dim.get(d.dimension, 0) for d in dims)
    return round(total * 100)


def score_posting(
    posting: dict,
    scored: list[dict],
    resume_text: str,
    companies: list[TargetCompany],
    conn: sqlite3.Connection,
) -> dict:
    """Score one Posting: run the sub-agent, then apply anti-inflation
    before the weighted total (§6)."""
    tools = _build_tools(conn, resume_text, companies)
    result = _run_scoring_agent(posting["jd_text"], scored, tools)
    dims = _cap_uncited(result.dimensions, posting["jd_text"], resume_text)
    return {
        "score": weighted_total(dims, scored),
        "rationale": result.rationale,
        "dimensions": [d.model_dump() for d in dims],
        "content_hash": posting.get("content_hash"),
    }


def score_postings(
    postings: list[dict],
    criteria: dict,
    conn: sqlite3.Connection,
    companies_path: Path = DEFAULT_COMPANIES_PATH,
) -> list[dict]:
    """Score every Posting not already excluded by a Knockout (§6, §11);
    an excluded Posting passes through untouched — no Score (§6). A
    Posting whose content_hash matches its latest Score is skipped —
    unchanged since it was last scored, no LLM call (§11 unit 20)."""
    scored = criteria.get("scored", [])
    if not scored:
        return postings
    resume_text = _latest_resume_text(conn)
    companies = load_companies(companies_path)
    criteria_version = _latest_criteria_version(conn)
    updated = []
    for posting in postings:
        if posting.get("status") == "excluded":
            updated.append(posting)
            continue
        if posting.get("content_hash") == _latest_score_content_hash(conn, posting["id"]):
            updated.append({**posting, "status": "scored"})
            continue
        result = score_posting(posting, scored, resume_text, companies, conn)
        updated.append(
            {
                **posting,
                "status": "scored",
                "score": result["score"],
                "rationale": result["rationale"],
                "dimensions": result["dimensions"],
                "criteria_version": criteria_version,
                "content_hash": posting.get("content_hash"),
            }
        )
    return updated
