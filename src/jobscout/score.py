"""Score Sub-Agent (DESIGN §4, §6; build-plan unit 12).

The one real agent loop: a bounded ReAct sub-agent decides what context it
needs — re-read the resume, look up the company, check past Rejections,
read the current Preference Summary — before committing a Score +
Rationale + Matched Lines. Anti-inflation (§6) is enforced here in code:
a dimension without a *real* quoted JD line and quoted resume line caps at
2, regardless of what the model claims.
"""

import logging
import os
import re
import sqlite3
from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH, TargetCompany, load_companies
from jobscout.llm import get_llm
from jobscout.preference import top_k_similar_verdicts
from jobscout.retry import with_retry

logger = logging.getLogger(__name__)

_MAX_STEPS = 12  # "bounded" ReAct sub-agent (DESIGN §4)
# DESIGN §7: none | few-shot | summary | both — which Preference Feedback
# Loop mechanism(s) the Score Sub-Agent's tools expose. Default "summary"
# per DESIGN §7 ("cheaper per call, more demoable").
_FEEDBACK_MECHANISMS = ("none", "few-shot", "summary", "both")


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


def _few_shot_verdicts_text(conn: sqlite3.Connection, jd_text: str) -> str:
    matches = top_k_similar_verdicts(conn, jd_text)
    if not matches:
        return "No similar past Verdicts on file yet."
    return "\n".join(
        f"{m['verdict']} on {m['title']!r} at {m['company']} "
        f"(similarity {m['similarity']:.2f}): {m['reason'] or 'no reason given'}"
        for m in matches
    )


def _feedback_mechanism() -> str:
    mode = os.environ.get("FEEDBACK_MECHANISM", "summary")
    return mode if mode in _FEEDBACK_MECHANISMS else "summary"


def _matched_lines_required() -> bool:
    """Ablation switch (DESIGN §15, build-plan unit 37): the Matched
    Lines citation requirement (`_cap_uncited`'s anti-inflation cap, §6)
    on by default, off only for measuring its delta."""
    return os.environ.get("MATCHED_LINES_REQUIRED", "true").lower() != "false"


def _build_tools(
    conn: sqlite3.Connection, resume_text: str, companies: list[TargetCompany], jd_text: str
) -> list:
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

    def similar_past_verdicts() -> str:
        """Past Verdicts (thumbs up/down + reason) on Postings similar to this one — the Few-Shot Store (DESIGN §7)."""
        return _few_shot_verdicts_text(conn, jd_text)

    tools = [tool(read_resume), tool(company_lookup), tool(past_rejections)]
    mode = _feedback_mechanism()
    if mode in ("summary", "both"):
        tools.append(tool(preference_summary))
    if mode in ("few-shot", "both"):
        tools.append(tool(similar_past_verdicts))
    return tools


def _run_scoring_agent(jd_text: str, scored: list[dict], tools: list) -> ScoreResult:
    """The bounded ReAct loop + one structured final call (DESIGN §4)."""
    agent = create_react_agent(get_llm(), tools, response_format=ScoreResult)
    prompt = _SCORE_PROMPT.format(dimensions=_dimensions_text(scored), jd_text=jd_text)
    result = agent.invoke(
        {"messages": [("user", prompt)]}, config={"recursion_limit": _MAX_STEPS}
    )
    return result["structured_response"]


_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on", "at", "by",
    "from", "is", "are", "was", "were", "be", "this", "that", "it", "as", "we", "you",
    "your", "our", "their", "who", "will", "has", "have", "had",
}


def _content_stems(text: str) -> set[str]:
    """Crude 5-char-prefix stemming so e.g. "mentor"/"mentored" or
    "engineer"/"engineers" still match — good enough for a relatedness
    heuristic, not real stemming."""
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return {w[:5] for w in words if w not in _STOPWORDS and len(w) > 2}


def _cap_uncited(
    dims: list[DimensionScore], jd_text: str, resume_text: str
) -> tuple[list[DimensionScore], list[str]]:
    """Anti-inflation (§6): a dimension without a real quoted JD line and a
    real quoted resume line caps at 2 — verified here, not trusted from the
    model's own claim.

    Confirmed live (unit 35's Matched Lines relevance judge, 19/25 flagged):
    two independently-real quotes aren't necessarily related to EACH OTHER
    or to the dimension — e.g. a real "AI voice agent product" JD line paired
    with a real-but-generic "built Angular apps" resume line, cited for
    "domain/product interest" with nothing connecting them. A shared-word
    check between the two quotes is a cheap, code-only proxy for "these
    actually support each other" — imperfect (semantically related quotes
    that share no words still get capped; ceiling: swap for an LLM judge if
    that false-positive rate matters in practice), but catches the
    demonstrated failure mode without another LLM call.

    Also returns human-readable notes for each dimension it capped, so
    `score_posting` can append them to the Rationale — confirmed live
    (unit 35's Score/Rationale consistency judge, 22/32 flagged): the
    Rationale is written by the model BEFORE this cap runs, so it still
    describes the pre-cap claim unless something tells it otherwise."""
    capped = []
    notes = []
    for d in dims:
        jd_ok = bool(d.jd_line) and d.jd_line.strip().lower() in jd_text.lower()
        resume_ok = bool(d.resume_line) and d.resume_line.strip().lower() in resume_text.lower()
        related = jd_ok and resume_ok and bool(
            _content_stems(d.jd_line) & _content_stems(d.resume_line)
        )
        if not related and d.score > 2:
            if not jd_ok or not resume_ok:
                reason = "no real quoted JD line and resume line to back it"
            else:
                reason = "the cited JD line and resume line don't relate to each other"
            notes.append(f"'{d.dimension}' capped to 2/5 — {reason}.")
            d = d.model_copy(update={"score": 2})
        capped.append(d)
    return capped, notes


def _fill_missing_dimensions(
    dims: list[DimensionScore], scored: list[dict]
) -> list[DimensionScore]:
    """The model doesn't always score every asked-for dimension — confirmed
    live: a real Posting got back only 1 of 4 (`stack fit`), and
    `weighted_total` silently treated the other 3 as zero-weight simply
    because nothing in `dims` matched them, with no record that they were
    ever missing rather than genuinely scored 0. Filling an explicit,
    uncited 0 for each dimension the model skipped keeps the same
    anti-inflation-floor number but makes the gap visible and inspectable
    in the stored `dimensions`, instead of an invisible side effect of a
    lookup finding nothing."""
    covered = [_dim_words(d.dimension) for d in dims]
    filled = list(dims)
    for criterion in scored:
        crit_words = _dim_words(criterion["dimension"])
        if not any(crit_words & dw for dw in covered):
            filled.append(
                DimensionScore(dimension=criterion["dimension"], score=0, jd_line=None, resume_line=None)
            )
    return filled


def _dim_words(name: str) -> set[str]:
    """Word set for fuzzy dimension-name matching — confirmed live: exact
    normalized-string matching still missed cases like the model's
    "scope_seniority" against the criteria's "scope & seniority signals"
    (it drops the word "signals" entirely, not just reformats
    punctuation), silently zeroing that dimension's weight while a
    "stack_fit"/"stack fit" case right next to it matched fine — the
    same failure mode as the original exact-match bug, just one level
    subtler. Matching on shared words instead of the full cleaned string
    survives reordering, punctuation, underscores, AND a dropped word."""
    return set(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def _best_matching_weight(dim_name: str, criteria_words: list[tuple[set[str], float]]) -> float:
    dim_words = _dim_words(dim_name)
    best_weight, best_overlap = 0.0, 0
    for words, weight in criteria_words:
        overlap = len(dim_words & words)
        if overlap > best_overlap:
            best_overlap, best_weight = overlap, weight
    return best_weight


def weighted_total(dims: list[DimensionScore], scored: list[dict]) -> int:
    """Weighted sum -> 0-100, weights from criteria (§6).

    The model can return more than one DimensionScore for the same named
    dimension (e.g. two separate JD/resume line citations backing one
    axis) — confirmed live: a Posting with duplicated "stack fit" entries
    scored 78 here despite the model's own rationale calling it "~2.05/5,
    below apply threshold" (~41/100), because summing every entry gave
    that dimension's weight twice. Averaging same-named entries before
    weighting fixes the double count."""
    criteria_words = [(_dim_words(d["dimension"]), d["weight"]) for d in scored]
    scores_by_dim: dict[str, list[int]] = {}
    for d in dims:
        scores_by_dim.setdefault(d.dimension, []).append(d.score)
    total = sum(
        (sum(s) / len(s)) / 5 * _best_matching_weight(dim, criteria_words)
        for dim, s in scores_by_dim.items()
    )
    return round(total * 100)


def score_posting(
    posting: dict,
    scored: list[dict],
    resume_text: str,
    companies: list[TargetCompany],
    conn: sqlite3.Connection,
) -> dict:
    """Score one Posting: run the sub-agent, then apply anti-inflation
    before the weighted total (§6). MATCHED_LINES_REQUIRED=false (unit 37
    ablation) skips the cap entirely — every other step runs the same."""
    tools = _build_tools(conn, resume_text, companies, posting["jd_text"])
    result = _run_scoring_agent(posting["jd_text"], scored, tools)
    dims = _fill_missing_dimensions(result.dimensions, scored)
    rationale = result.rationale
    if _matched_lines_required():
        dims, cap_notes = _cap_uncited(dims, posting["jd_text"], resume_text)
        if cap_notes:
            rationale += "\n\n" + "\n".join(cap_notes)
    return {
        "score": weighted_total(dims, scored),
        "rationale": rationale,
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
    total = len(postings)
    for i, posting in enumerate(postings, 1):
        if posting.get("status") == "excluded":
            updated.append(posting)
            continue
        if posting.get("content_hash") == _latest_score_content_hash(conn, posting["id"]):
            updated.append({**posting, "status": "scored"})
            continue
        logger.info(
            "score %d/%d: %s — %s",
            i,
            total,
            posting.get("company", "?"),
            posting.get("title", posting.get("id", "?")),
        )
        # §17 / unit 30: with_retry only absorbs a 429 WITHIN this one
        # call (intra-call backoff). A failure surviving that bumps
        # app_posting.error_count, a SEPARATE cross-Poll counter (3 in a
        # row -> dead) — not the same "3".
        try:
            result = with_retry(score_posting, posting, scored, resume_text, companies, conn)
        except Exception as exc:
            row = conn.execute(
                "SELECT error_count FROM app_posting WHERE id = ?", (posting["id"],)
            ).fetchone()
            new_count = (row["error_count"] if row else 0) + 1
            status = "dead" if new_count >= 3 else "error"
            logger.warning("score %d/%d: %s (error_count=%d)", i, total, exc, new_count)
            updated.append(
                {**posting, "status": status, "status_reason": str(exc), "error_count": new_count}
            )
            continue
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
