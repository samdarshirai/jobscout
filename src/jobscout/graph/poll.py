"""Poll graph (DESIGN §4).

Full backbone: plan_search --(gate)--> discover -> dedupe -> fetch_jd ->
staleness -> knockout -> score -> finish. Unit 13's Queue is a
service-layer read of persisted status/score, not a new graph node.
"""

import hashlib
import sqlite3
from pathlib import Path
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout import spend
from jobscout.criteria import KnockoutRule
from jobscout.dedupe import same_posting_cached
from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH
from jobscout.discovery.curated_ats import discover_curated_ats
from jobscout.knockout import decide_knockout, extract_knockout_facts
from jobscout.score import score_postings
from jobscout.search_plan import derive_search_plan


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


def fetch_jd(state: PollState) -> dict:
    """A Posting missing JD text does not reach scoring (DESIGN §4).
    ponytail: Curated ATS Sources (unit 9) already return full JD text
    inline — this is a filter, not a fetch, until units 21-22 add
    summary-only Sources (Adzuna/arbeitnow) that need a real HTTP call
    added here. Stamps content_hash here too (DESIGN §11) — the score
    node uses a changed hash to decide a Posting needs re-scoring."""
    postings = []
    for p in state["postings"]:
        if not p.get("jd_text"):
            continue
        content_hash = hashlib.sha256(p["jd_text"].encode()).hexdigest()
        postings.append({**p, "content_hash": content_hash})
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
        # ponytail: Curated ATS Boards only. Units 21-22 add Adzuna/arbeitnow.
        return {"postings": discover_curated_ats(conn, companies_path)}

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

        def _run_tie_breaks() -> list[dict]:
            result = []
            for group in by_company_title.values():
                kept: list[dict] = []
                for candidate in group:
                    if any(same_posting_cached(conn, candidate, k) for k in kept):
                        continue
                    kept.append(candidate)
                result.extend(kept)
            return result

        deduped, rows = spend.run_and_track(_run_tie_breaks)
        spend.log_spend(conn, run_id, "dedupe", rows)
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
            for posting in state["postings"]:
                facts = extract_knockout_facts(posting["jd_text"], rules)
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
