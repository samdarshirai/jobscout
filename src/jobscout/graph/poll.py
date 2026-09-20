"""Poll graph (DESIGN §4).

Full backbone: plan_search --(gate)--> discover -> dedupe -> fetch_jd ->
staleness -> knockout -> score -> finish. Unit 13's Queue is a
service-layer read of persisted status/score, not a new graph node.
"""

import hashlib
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import TypedDict

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout import spend
from jobscout.criteria import KnockoutRule
from jobscout.dedupe import same_posting_cached
from jobscout.discovery.adzuna import discover_adzuna
from jobscout.discovery.arbeitnow import discover_arbeitnow
from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH
from jobscout.discovery.curated_ats import discover_curated_ats
from jobscout.discovery.fallback import fetch_page_text
from jobscout.knockout import decide_knockout, extract_knockout_facts
from jobscout.retry import with_retry
from jobscout.score import score_postings
from jobscout.search_plan import derive_search_plan

logger = logging.getLogger(__name__)


class PollState(TypedDict):
    criteria: dict
    search_plan: dict
    decision: str
    postings: list


def search_plan_gate(state: PollState) -> dict:
    """Approval Gate: the Run pauses here until the Operator approves the
    Search Plan (DESIGN §10). Resume value 'reject' aborts the Run."""
    decision = interrupt({"gate": "search_plan", "plan": state["search_plan"]})
    return {"decision": str(decision)}


def _after_gate(state: PollState) -> str:
    """Fail closed (DESIGN §10): only an explicit 'approve' proceeds.
    Anything else — 'reject', a typo, None — aborts the Run."""
    return "discover" if state["decision"] == "approve" else END


def _normalize(text: str) -> str:
    return text.strip().lower()


# Words to drop from a Search Plan query before treating what's left as a
# relevance keyword — generic job-title/location vocabulary AND business/
# domain buzzwords that show up in almost any company's marketing copy
# regardless of role (confirmed live: "platform"/"saas"/"fintech"/
# "enterprise" let Account Executive, Talent Acquisition, Data Scientist,
# and Cloud Engineer postings all through — none of those are a stack
# signal, they're just words a company's About section uses).
_GENERIC_QUERY_WORDS = {
    "senior", "lead", "staff", "principal", "junior", "mid", "engineer",
    "engineers", "developer", "architect", "frontend", "front-end", "backend",
    "back-end", "fullstack", "full-stack", "software", "germany", "remote",
    "hybrid", "onsite", "berlin", "munich", "hamburg", "cologne", "frankfurt",
    "enterprise", "saas", "fintech", "platform", "b2b", "b2c", "product",
    "entwickler", "digital", "cloud", "data", "talent", "team", "tech",
    "consulting", "sales", "marketing", "account", "executive", "manager",
    "acquisition", "administration", "scientist", "process", "design",
    "system", "component", "components", "signals", "legacy", "library",
    "modernization", "material", "architecture", "optimization", "performance",
    "accessibility", "api", "ci/cd", "english", "marketplace", "migration",
    "rest", "technical", "testing", "web", "frontends",
}


def _relevance_keywords(search_plan: dict) -> set[str]:
    """Plain-Python pre-filter signal (no LLM — DESIGN §17, `must_have_stack`
    etc. are hard constraints, not something to spend an LLM call deciding
    per Posting when a keyword already answers it): the content words left
    in the Search Plan's own queries after stripping generic job-title
    vocabulary. Empty when nothing distinctive is left — callers must treat
    that as "no filter," not "match nothing.\""""
    keywords: set[str] = set()
    for query in search_plan.get("queries", []):
        for word in _normalize(query).replace(",", " ").split():
            word = word.strip(".()")
            if len(word) > 2 and word not in _GENERIC_QUERY_WORDS:
                keywords.add(word)
    return keywords


def _is_relevant(posting: dict, keywords: set[str]) -> bool:
    """Whole-word match, not substring — confirmed live: substring match
    let "micro" (from a "Micro Frontends" query) match "microservices" in
    any backend JD, and would do the same for "system" in "ecosystem" etc.
    A stack term should match the term, not any word containing it."""
    if not keywords:
        return True
    text = _normalize(f"{posting.get('title', '')} {posting.get('jd_text') or ''}")
    return any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in keywords)


def fetch_jd(state: PollState) -> dict:
    """A Posting missing JD text does not reach scoring (DESIGN §4).
    Curated ATS / arbeitnow Postings (units 9, 22) already carry full JD
    text inline — this is a filter for those. Adzuna Postings (unit 21)
    carry only Adzuna's own truncated summary in `jd_text`, so for
    `source == "adzuna"` this follows the Posting's `url` (Adzuna's
    `redirect_url`) and re-extracts with the same whole-page
    trafilatura path `discovery/fallback.py` uses for careers pages,
    upgrading to the real JD text; a failed fetch or empty extraction
    (§17 — dead link, JS-only page, recruiter-spam page) falls back to
    the adapter's truncated summary rather than dropping the Posting.
    Stamps content_hash here too (DESIGN §11) — the score node uses a
    changed hash to decide a Posting needs re-scoring."""
    postings = []
    client = httpx.Client(timeout=10.0)
    try:
        for p in state["postings"]:
            jd_text = p.get("jd_text")
            if p.get("source") == "adzuna" and p.get("url"):
                upgraded = fetch_page_text(client, p["url"])
                if upgraded:
                    jd_text = upgraded
            if not jd_text:
                continue
            content_hash = hashlib.sha256(jd_text.encode()).hexdigest()
            postings.append({**p, "jd_text": jd_text, "content_hash": content_hash})
    finally:
        client.close()
    return {"postings": postings}


def finish(state: PollState) -> dict:
    return {}


def build_poll_graph(
    conn: sqlite3.Connection, companies_path: Path = DEFAULT_COMPANIES_PATH
) -> StateGraph:
    def plan_search(state: PollState, config: RunnableConfig) -> dict:
        run_id = config["configurable"]["thread_id"]
        plan, rows = spend.run_and_track(derive_search_plan, state["criteria"])
        spend.log_spend(conn, run_id, "plan_search", rows)
        return {"search_plan": plan.model_dump()}

    def discover(state: PollState) -> dict:
        """Curated ATS Boards (unit 9) + Adzuna (unit 21) + arbeitnow
        (unit 22), DESIGN §3's three discovery Sources — one shared
        client. Adzuna needs a free app_id+key (`.env.example`); when
        unset, Adzuna discovery is skipped rather than failing the Poll
        (§17), since it's a supplementary Source, not the only one.

        Adzuna/arbeitnow return their whole unfiltered catalogue — neither
        adapter takes the Search Plan's own query terms, so most of what
        comes back can be trivially off-stack (confirmed live: arbeitnow
        alone returned 247 Postings, the overwhelming majority nowhere
        near this Criteria's stack). Sending every one of those through an
        LLM Knockout call just to reject "Steuerberater" is real, wasted
        spend for a decision a plain keyword check already answers — so a
        cheap, code-only relevance filter (DESIGN §17, no LLM) runs here
        against the Search Plan's own query keywords, before Knockout ever
        sees them. Curated ATS Boards are hand-picked Target Companies, but
        a company's own careers page still lists every open role, not just
        engineering ones (confirmed live: Celonis alone returned 476
        Postings, mostly sales/AE/consulting) — so the same filter applies
        to curated Postings too, only the company itself is hand-picked,
        not every role it happens to have open."""
        client = httpx.Client(timeout=10.0)
        try:
            curated = discover_curated_ats(conn, companies_path, client)
            broad = []
            app_id = os.environ.get("ADZUNA_APP_ID")
            app_key = os.environ.get("ADZUNA_APP_KEY")
            if app_id and app_key:
                broad += discover_adzuna(client, app_id, app_key)
            broad += discover_arbeitnow(client)

            keywords = _relevance_keywords(state["search_plan"])
            relevant_curated = [p for p in curated if _is_relevant(p, keywords)]
            relevant_broad = [p for p in broad if _is_relevant(p, keywords)]
            logger.info(
                "discover: %d/%d curated + %d/%d broad-Source Postings kept "
                "(relevance keywords: %s)",
                len(relevant_curated),
                len(curated),
                len(relevant_broad),
                len(broad),
                sorted(keywords) or "none — no filter applied",
            )
            return {"postings": relevant_curated + relevant_broad}
        finally:
            client.close()

    def dedupe(state: PollState, config: RunnableConfig) -> dict:
        """Exact-key dedupe first (DESIGN §11) — a same-id Posting
        discovered twice in one batch collapses to its first occurrence.
        Then a company+title collision with *different* keys (same job via
        two Sources — units 21-22) gets one cached LLM tie-break call per
        pair (build-plan unit 18); no fuzzy-match thresholds."""
        seen: dict[str, dict] = {}
        for posting in state["postings"]:
            seen.setdefault(posting["id"], posting)
        by_company_title: dict[tuple[str, str], list[dict]] = {}
        for posting in seen.values():
            key = (_normalize(posting["company"]), _normalize(posting["title"]))
            by_company_title.setdefault(key, []).append(posting)

        run_id = config["configurable"]["thread_id"]
        collisions = [g for g in by_company_title.values() if len(g) > 1]
        if collisions:
            logger.info(
                "dedupe: %d company+title collision group(s) need a tie-break call",
                len(collisions),
            )

        def _run_tie_breaks() -> list[dict]:
            result = []
            done = 0
            for group in by_company_title.values():
                kept: list[dict] = []
                for candidate in group:
                    same = False
                    for k in kept:
                        done += 1
                        logger.info(
                            "dedupe tie-break %d: %s — %s",
                            done,
                            candidate.get("company", "?"),
                            candidate.get("title", candidate.get("id", "?")),
                        )
                        if same_posting_cached(conn, candidate, k):
                            same = True
                            break
                    if same:
                        continue
                    kept.append(candidate)
                result.extend(kept)
            return result

        deduped, rows = spend.run_and_track(_run_tie_breaks)
        spend.log_spend(conn, run_id, "dedupe", rows)
        logger.info("dedupe: %d -> %d Postings", len(state["postings"]), len(deduped))
        return {"postings": deduped}

    def staleness(state: PollState) -> dict:
        """Gone from Source for 2 consecutive Polls -> stale (DESIGN §11).
        Compares this Poll's freshly-discovered ids against every Posting
        the DB still has as 'new'/'queued' from a PREVIOUS Poll — this
        Poll's own batch hasn't been persisted yet at this point in the
        graph, so the DB read here reflects only prior Polls. No LLM call,
        no Spend logging."""
        seen_ids = {p["id"] for p in state["postings"]}
        rows = conn.execute(
            "SELECT id, missed_polls FROM app_posting WHERE status IN ('new', 'queued')"
        ).fetchall()
        for row in rows:
            if row["id"] in seen_ids:
                continue  # still around — _save_discovered_postings resets the counter when it saves this Poll's batch
            missed = row["missed_polls"] + 1
            if missed >= 2:
                conn.execute(
                    "UPDATE app_posting SET missed_polls = ?, status = 'stale' WHERE id = ?",
                    (missed, row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE app_posting SET missed_polls = ? WHERE id = ?", (missed, row["id"])
                )
        conn.commit()
        return {}

    def knockout(state: PollState, config: RunnableConfig) -> dict:
        """An LLM extracts the fact, a rule decides, per Knockout axis (§6).
        A failing Posting gets status `excluded` + reason here but still
        flows through this Run's `postings` list — unit 13's Queue is what
        filters on `status` so an excluded Posting never lands there or
        gets a Score."""
        rules = [KnockoutRule(**r) for r in state["criteria"].get("knockout", [])]
        if not rules:
            return {}
        run_id = config["configurable"]["thread_id"]

        def _run_knockout() -> list[dict]:
            updated = []
            total = len(state["postings"])
            for i, posting in enumerate(state["postings"], 1):
                logger.info(
                    "knockout %d/%d: %s — %s",
                    i,
                    total,
                    posting.get("company", "?"),
                    posting.get("title", posting.get("id", "?")),
                )
                # §17 / unit 30: with_retry only absorbs a 429 WITHIN this
                # one call (intra-call backoff). A failure surviving that
                # bumps app_posting.error_count, a SEPARATE cross-Poll
                # counter (3 in a row -> dead) — not the same "3".
                try:
                    facts = with_retry(
                        extract_knockout_facts, posting["jd_text"], rules, posting.get("city")
                    )
                except Exception as exc:
                    row = conn.execute(
                        "SELECT error_count FROM app_posting WHERE id = ?", (posting["id"],)
                    ).fetchone()
                    new_count = (row["error_count"] if row else 0) + 1
                    status = "dead" if new_count >= 3 else "error"
                    logger.warning("knockout %d/%d: %s (error_count=%d)", i, total, exc, new_count)
                    updated.append(
                        {
                            **posting,
                            "status": status,
                            "status_reason": str(exc),
                            "error_count": new_count,
                        }
                    )
                    continue
                reason = decide_knockout(facts)
                if reason is not None:
                    posting = {**posting, "status": "excluded", "status_reason": reason}
                updated.append(posting)
            return updated

        updated, rows = spend.run_and_track(_run_knockout)
        spend.log_spend(conn, run_id, "knockout", rows)
        return {"postings": updated}

    def score(state: PollState, config: RunnableConfig) -> dict:
        """Score Sub-Agent (§4, §6) — skips Postings a Knockout excluded."""
        run_id = config["configurable"]["thread_id"]
        postings, rows = spend.run_and_track(
            score_postings, state["postings"], state["criteria"], conn, companies_path
        )
        spend.log_spend(conn, run_id, "score", rows)
        return {"postings": postings}

    g = StateGraph(PollState)
    g.add_node("plan_search", plan_search)
    g.add_node("search_plan_gate", search_plan_gate)
    g.add_node("discover", discover)
    g.add_node("dedupe", dedupe)
    g.add_node("fetch_jd", fetch_jd)
    g.add_node("staleness", staleness)
    g.add_node("knockout", knockout)
    g.add_node("score", score)
    g.add_node("finish", finish)
    g.add_edge(START, "plan_search")
    g.add_edge("plan_search", "search_plan_gate")
    g.add_conditional_edges(
        "search_plan_gate", _after_gate, {"discover": "discover", END: END}
    )
    g.add_edge("discover", "dedupe")
    g.add_edge("dedupe", "fetch_jd")
    g.add_edge("fetch_jd", "staleness")
    g.add_edge("staleness", "knockout")
    g.add_edge("knockout", "score")
    g.add_edge("score", "finish")
    g.add_edge("finish", END)
    return g
