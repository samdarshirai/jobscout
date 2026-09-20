"""Eval harness (DESIGN §15, build-plan unit 33).

Outcome metric: Spearman rank correlation of Agent overall vs the
Candidate's overall Label on the frozen Seed Set — she is ground truth,
no LLM judge (§15). Target >= 0.6.

Secondary: cost per Posting; knockout false-exclusion count (excluding
a Posting she actually wanted — thumb up — is the worst failure, target
0).
"""

import contextvars
import hashlib
import json
import os
import queue
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from jobscout import spend
from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH, TargetCompany, load_companies
from jobscout.judge import (
    judge_divergence,
    judge_knockout_correctness,
    judge_matched_line_relevance,
    judge_score_rationale_consistency,
)
from jobscout.llm import BAKE_OFF_MODELS
from jobscout.retry import with_retry
from jobscout.score import _latest_criteria_version, _latest_resume_text, score_posting, score_postings

BAKE_OFF_MECHANISMS = ("none", "few-shot", "summary", "both")

# The judge model hit OpenRouter's new-account 20rpm cap running unit 35's
# ~100 calls back to back — retry.py's default 3 attempts (max ~7s backoff)
# doesn't clear a per-minute cap; a batch of judge calls needs to survive a
# full reset window.
_JUDGE_MAX_RETRIES = 6

DIVERGENCE_THRESHOLD = 25  # |agent - human| beyond this gets triaged (§15)


def spearman_correlation(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation = Pearson correlation of the ranks.
    Ties get the average rank of the positions they span (standard
    tie handling) — no scipy needed for one small, one-off eval run."""
    if len(x) != len(y):
        raise ValueError("x and y must be the same length")
    n = len(x)
    if n < 2:
        raise ValueError("need at least 2 pairs to correlate")

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: values[i])
        result = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-indexed
            for k in range(i, j + 1):
                result[order[k]] = avg_rank
            i = j + 1
        return result

    rx, ry = ranks(x), ranks(y)
    mean_rx, mean_ry = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((v - mean_rx) ** 2 for v in rx)
    var_y = sum((v - mean_ry) ** 2 for v in ry)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x**0.5 * var_y**0.5)


TARGET_SPEARMAN = 0.6


_EXPERIMENTAL_RUN_FILTER = "posting_id = l.posting_id AND run_id NOT LIKE 'bakeoff:%' AND run_id NOT LIKE 'ablation:%'"


def seed_set_eval(conn: sqlite3.Connection, round: int = 1) -> dict:
    """Join app_seed_label(round) with each Posting's latest PRODUCTION
    app_score, report the outcome metric against target plus the
    secondary knockout false-exclusion count.

    Confirmed live: units 36/37's Bake-Off/ablation sweeps write many more
    app_score rows for the same Seed Set Postings under experimental
    run_ids — "latest row overall" then silently picks up a stray sweep
    cell instead of the real production score (Spearman read 0.586
    instead of the correct ~0.70 the moment the Bake-Off ran). Latest
    among non-experimental run_ids only."""
    rows = conn.execute(
        f"SELECT l.posting_id, l.overall AS human_overall, l.thumb, "
        f"s.score AS agent_overall, p.status "
        f"FROM app_seed_label l "
        f"JOIN app_score s ON s.id = (SELECT MAX(id) FROM app_score WHERE {_EXPERIMENTAL_RUN_FILTER}) "
        f"LEFT JOIN app_posting p ON p.id = l.posting_id "
        f"WHERE l.round = ?",
        (round,),
    ).fetchall()

    agent = [r["agent_overall"] for r in rows]
    human = [r["human_overall"] for r in rows]
    correlation = spearman_correlation(agent, human)

    false_exclusions = [
        dict(r) for r in rows if r["thumb"] == "up" and r["status"] == "excluded"
    ]

    return {
        "n": len(rows),
        "spearman": correlation,
        "target": TARGET_SPEARMAN,
        "meets_target": correlation >= TARGET_SPEARMAN,
        "knockout_false_exclusions": false_exclusions,
        "knockout_false_exclusion_count": len(false_exclusions),
    }


def cost_per_posting(conn: sqlite3.Connection, run_id: str = "eval-seed") -> float:
    total = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM app_spend WHERE run_id = ?", (run_id,)
    ).fetchone()["total"]
    posting_count = conn.execute(
        "SELECT COUNT(DISTINCT posting_id) AS n FROM app_score WHERE run_id = ?", (run_id,)
    ).fetchone()["n"]
    if not posting_count:
        return 0.0
    return total / posting_count


# ---- unit 35: reasoning-quality checks over the eval batch ----------------


def _seed_scores(conn: sqlite3.Connection, round: int) -> list[sqlite3.Row]:
    """Same experimental-run exclusion as seed_set_eval — see its docstring."""
    return conn.execute(
        f"SELECT l.posting_id, l.overall AS human_overall, l.why AS human_why, "
        f"s.score AS agent_overall, s.rationale, s.dimensions_json, "
        f"p.jd_text, p.city, p.status, p.status_reason "
        f"FROM app_seed_label l "
        f"JOIN app_score s ON s.id = (SELECT MAX(id) FROM app_score WHERE {_EXPERIMENTAL_RUN_FILTER}) "
        f"JOIN app_posting p ON p.id = l.posting_id "
        f"WHERE l.round = ?",
        (round,),
    ).fetchall()


def matched_lines_real(conn: sqlite3.Connection, round: int = 1) -> dict:
    """Cheap code check, no LLM (DESIGN §15's "quotes real" half of Matched
    Lines validity) — every dimension scored above 2 must cite a genuine
    substring of the JD and resume, per score.py's own `_cap_uncited`
    invariant. A violation here means that invariant broke upstream, not
    something this check itself needs to reason about."""
    resume_text = _latest_resume_text(conn)
    cited = []
    violations = []
    for row in _seed_scores(conn, round):
        jd_text = (row["jd_text"] or "").lower()
        for d in json.loads(row["dimensions_json"]):
            if d["score"] <= 2:
                continue
            jd_ok = bool(d.get("jd_line")) and d["jd_line"].strip().lower() in jd_text
            resume_ok = bool(d.get("resume_line")) and d["resume_line"].strip().lower() in resume_text.lower()
            entry = {"posting_id": row["posting_id"], "dimension": d["dimension"], "score": d["score"],
                      "jd_line": d.get("jd_line"), "resume_line": d.get("resume_line")}
            if jd_ok and resume_ok:
                cited.append(entry)
            else:
                violations.append(entry)
    return {"checked": len(cited) + len(violations), "violations": violations, "all_real": not violations,
            "cited": cited}


def matched_lines_relevance(conn: sqlite3.Connection, round: int = 1) -> dict:
    """The "relevant" half of Matched Lines validity (DESIGN §15) — needs
    judgment, so it's the anchor judge, run only over quotes that
    `matched_lines_real` already confirmed are genuine substrings."""
    cited = matched_lines_real(conn, round)["cited"]
    irrelevant = []
    for c in cited:
        verdict = with_retry(judge_matched_line_relevance, c["dimension"], c["jd_line"], c["resume_line"],
                              max_retries=_JUDGE_MAX_RETRIES)
        if not verdict.relevant:
            irrelevant.append({**c, "reasoning": verdict.reasoning})
    return {"checked": len(cited), "irrelevant": irrelevant, "all_relevant": not irrelevant}


def score_rationale_consistency(conn: sqlite3.Connection, round: int = 1) -> dict:
    """Unit 34's judge, run over the full eval batch instead of just the
    ~8 calibration Postings it was checked against."""
    inconsistent = []
    rows = _seed_scores(conn, round)
    for row in rows:
        dims = json.loads(row["dimensions_json"])
        dims_text = "\n".join(f"- {d['dimension']}: {d['score']}/5" for d in dims)
        verdict = with_retry(judge_score_rationale_consistency, row["agent_overall"], row["rationale"], dims_text,
                              max_retries=_JUDGE_MAX_RETRIES)
        if not verdict.consistent:
            inconsistent.append({"posting_id": row["posting_id"], "reasoning": verdict.reasoning})
    return {"checked": len(rows), "inconsistent": inconsistent, "all_consistent": not inconsistent}


def knockout_correctness(conn: sqlite3.Connection, round: int = 1) -> dict:
    """Audits each Seed Set Posting's *stored* production knockout decision
    (DESIGN §6, §15) against the real JD — the latest criteria's rules,
    since that's what a re-run today would apply."""
    criteria_row = conn.execute(
        "SELECT data_json FROM app_criteria ORDER BY version DESC LIMIT 1"
    ).fetchone()
    rules = json.loads(criteria_row["data_json"])["knockout"]
    rules_text = "\n".join(f"- {r['axis']}: {r['rule']}" for r in rules)

    incorrect = []
    rows = _seed_scores(conn, round)
    for row in rows:
        verdict = with_retry(
            judge_knockout_correctness, row["jd_text"], row["city"], rules_text, row["status"], row["status_reason"],
            max_retries=_JUDGE_MAX_RETRIES,
        )
        if not verdict.correct:
            incorrect.append({"posting_id": row["posting_id"], "status": row["status"],
                               "reasoning": verdict.reasoning})
    return {"checked": len(rows), "incorrect": incorrect, "all_correct": not incorrect}


def divergence_triage(conn: sqlite3.Connection, round: int = 1, threshold: int = DIVERGENCE_THRESHOLD) -> dict:
    """Divergence Triage (DESIGN §15): categorise the large |agent - human|
    gaps as weak_rationale / she_is_outlier / genuinely_ambiguous."""
    triaged = []
    for row in _seed_scores(conn, round):
        diff = abs(row["agent_overall"] - row["human_overall"])
        if diff < threshold:
            continue
        verdict = with_retry(judge_divergence, row["agent_overall"], row["human_overall"], row["rationale"], row["human_why"],
                              max_retries=_JUDGE_MAX_RETRIES)
        triaged.append({
            "posting_id": row["posting_id"], "agent_overall": row["agent_overall"],
            "human_overall": row["human_overall"], "diff": diff,
            "category": verdict.category, "reasoning": verdict.reasoning,
        })
    by_category: dict[str, int] = {}
    for t in triaged:
        by_category[t["category"]] = by_category.get(t["category"], 0) + 1
    return {"threshold": threshold, "n_diverged": len(triaged), "by_category": by_category, "cases": triaged}


# ---- unit 36: the Bake-Off deliverable table -------------------------

# Hard wall-clock deadline per score_posting call, on top of
# llm.py's DEFAULT_TIMEOUT_S. Confirmed live: a real Bake-Off run hung for
# 2+ hours on a single call — DEFAULT_TIMEOUT_S is a per-CHUNK read
# timeout (llm.py's own docstring already flagged this), so a response
# trickling in just fast enough to keep resetting that clock never trips
# it. This deadline bounds the worst case regardless of how the HTTP
# client paces its own timeout.
_BAKE_OFF_CALL_TIMEOUT_S = 120.0
# Confirmed live: a sequential Bake-Off cell averaged ~80s/call, almost
# entirely idle network wait — a modest concurrency cap cuts wall-clock
# time roughly this many times over without changing $ cost (same number
# of calls) or risking a shared-account rate-limit cliff (we hit a strict
# 20rpm new-account cap on one model earlier this session).
BAKE_OFF_CONCURRENCY = 5


def _score_with_deadline(
    posting: dict, scored_dims: list[dict], resume_text: str, companies: list[TargetCompany],
    conn: sqlite3.Connection, timeout: float | None = None,
):
    """Run score_posting (via with_retry, via spend.run_and_track) in a
    background thread with a real deadline. A plain daemon thread, not
    ThreadPoolExecutor — ThreadPoolExecutor registers an atexit hook that
    JOINS every worker thread before the process can exit, which would
    just move the 2-hour hang from mid-run to process-exit. A daemon
    thread never blocks exit. ponytail: abandoning the thread doesn't
    kill the underlying blocked network call, it keeps running invisibly
    until the process exits — acceptable for a one-shot batch script, not
    for a long-lived server reusing this."""
    timeout = timeout if timeout is not None else _BAKE_OFF_CALL_TIMEOUT_S
    ctx = contextvars.copy_context()
    result_q: queue.Queue = queue.Queue(maxsize=1)

    def _run():
        try:
            result_q.put(("ok", ctx.run(
                spend.run_and_track, with_retry, score_posting,
                posting, scored_dims, resume_text, companies, conn,
            )))
        except Exception as exc:
            result_q.put(("error", exc))

    threading.Thread(target=_run, daemon=True).start()
    try:
        status, payload = result_q.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(f"score_posting exceeded {timeout}s wall-clock deadline")
    if status == "error":
        raise payload
    return payload


def bake_off_cell(
    conn: sqlite3.Connection,
    postings: list[dict],
    scored_dims: list[dict],
    resume_text: str,
    companies: list[TargetCompany],
    model: str,
    mechanism: str,
    run_id: str,
) -> dict:
    """Score every given Posting once under one (model, feedback mechanism)
    combination — a single cell of the Bake-Off matrix (DESIGN §15, unit
    36). Bypasses `score_postings`' content_hash dedupe (unit 20) on
    purpose: the JD hasn't changed between cells, only the model/mechanism
    has, so that skip-if-unchanged logic would wrongly skip every cell
    after the first. `score.py` reads both knobs from the environment
    (`OPENROUTER_MODEL`, `FEEDBACK_MECHANISM`) — no new plumbing needed.

    Resumable by (posting_id, run_id): a Posting already scored under
    THIS exact cell is skipped, no LLM call. Confirmed live: a real run
    hung 2+ hours into a 480-call matrix and had to be killed — without
    this, restarting would double-insert every already-scored Posting
    under the same run_id and silently corrupt that cell's Spearman
    (duplicate points, not a rerun).

    Scores up to BAKE_OFF_CONCURRENCY Postings in parallel — confirmed
    live: one sequential cell averaged ~80s/call, on pace for 6-9 more
    hours across the full matrix, all of it spent idle on network I/O,
    not compute. `_score_with_deadline` already puts each call's real work
    on its own thread, so running several of THOSE concurrently is the
    same pattern one level up. Every DB write still happens on this
    (single) calling thread, inside `as_completed`'s loop — only the
    read-only tool calls inside `score_posting` run concurrently on
    `conn`, the same shared-connection-from-multiple-threads pattern
    already relied on for the ReAct agent's own internal tool-thread-pool
    (`storage/db.get_connection`'s `check_same_thread=False`)."""
    os.environ["OPENROUTER_MODEL"] = model
    os.environ["FEEDBACK_MECHANISM"] = mechanism
    criteria_version = _latest_criteria_version(conn)
    already_scored = {
        r["posting_id"] for r in conn.execute(
            "SELECT posting_id FROM app_score WHERE run_id = ?", (run_id,)
        ).fetchall()
    }
    to_score = [p for p in postings if p["id"] not in already_scored]

    def _attempt(posting: dict):
        # Failure isolation (§17), same posture as a real Poll: one bad
        # call for one Posting under one cell shouldn't sink the whole
        # matrix. `with_retry` only absorbs a 429; a model that returns
        # unparseable structured output (confirmed live: happens even for
        # an otherwise-reliable model on an off night) gets one bare
        # re-attempt here, then this Posting is just missing from this
        # cell rather than crashing `run_bake_off`.
        try:
            return posting, _score_with_deadline(posting, scored_dims, resume_text, companies, conn), None
        except Exception:
            try:
                return posting, _score_with_deadline(posting, scored_dims, resume_text, companies, conn), None
            except Exception as exc:
                return posting, None, exc

    if to_score:
        with ThreadPoolExecutor(max_workers=BAKE_OFF_CONCURRENCY) as pool:
            futures = [pool.submit(_attempt, p) for p in to_score]
            for future in as_completed(futures):
                posting, outcome, exc = future.result()
                if exc is not None:
                    print(f"bake_off_cell {run_id}: skipping {posting['id']} — {type(exc).__name__}: {exc}")
                    continue
                result, rows = outcome
                spend.log_spend(conn, run_id, "score", rows)
                conn.execute(
                    "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json, "
                    "criteria_version, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        posting["id"], run_id, result["score"], result["rationale"],
                        json.dumps(result["dimensions"]), criteria_version, posting.get("content_hash"),
                    ),
                )
                conn.commit()

    rows = conn.execute(
        "SELECT l.overall AS human_overall, s.score AS agent_overall "
        "FROM app_seed_label l "
        "JOIN app_score s ON s.posting_id = l.posting_id AND s.run_id = ? "
        "WHERE l.round = 1",
        (run_id,),
    ).fetchall()
    agent = [r["agent_overall"] for r in rows]
    human = [r["human_overall"] for r in rows]
    correlation = spearman_correlation(agent, human) if len(rows) >= 2 else None
    cost = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM app_spend WHERE run_id = ?", (run_id,)
    ).fetchone()["total"]

    return {
        "model": model, "mechanism": mechanism, "n": len(rows),
        "spearman": correlation, "cost_total": cost,
        "cost_per_posting": cost / len(rows) if rows else 0.0,
    }


def run_bake_off(
    conn: sqlite3.Connection,
    postings: list[dict],
    models: dict[str, str] = BAKE_OFF_MODELS,
    mechanisms: tuple = BAKE_OFF_MECHANISMS,
    companies_path=DEFAULT_COMPANIES_PATH,
) -> list[dict]:
    """The Bake-Off (DESIGN §15, unit 36): 30 Postings x {feedback
    mechanism} x {model} -> one table, the deliverable for goal 2. Stops
    early if cumulative spend reaches the $20 cap (§13) — the remaining
    cells are simply missing from the table, not silently zeroed."""
    resume_text = _latest_resume_text(conn)
    companies = load_companies(companies_path)
    criteria_row = conn.execute(
        "SELECT data_json FROM app_criteria ORDER BY version DESC LIMIT 1"
    ).fetchone()
    scored_dims = json.loads(criteria_row["data_json"])["scored"]

    table = []
    for model_name, model_id in models.items():
        for mechanism in mechanisms:
            if spend.total_spend(conn) >= spend.CAP_USD:
                return table
            run_id = f"bakeoff:{model_name}:{mechanism}"
            cell = bake_off_cell(
                conn, postings, scored_dims, resume_text, companies, model_id, mechanism, run_id
            )
            table.append({"model_name": model_name, **cell})
    return table


# Fault injection (DESIGN §15, build-plan unit 38): stress the long-horizon
# machinery (dedupe/staleness §11, memory §7) that only ever gets exercised
# by real time passing over real Polls -- these two flags force a bad-input
# scenario the eval harness can hit in one process instead of waiting weeks.

_CONTEXT_TRUNCATION_CHARS = 400  # short enough to starve the scorer, not just shrink it


def _inject_context_truncation(resume_text: str) -> str:
    return resume_text[:_CONTEXT_TRUNCATION_CHARS]


def run_context_truncation_check(
    conn: sqlite3.Connection,
    postings: list[dict],
    scored_dims: list[dict],
    resume_text: str,
    companies: list[TargetCompany],
) -> dict:
    """`--inject context-truncation`: scores every given Posting against a
    chopped resume (§15). A starved resume SHOULD score worse -- that's not
    the failure mode under test. What's under test is whether scoring still
    completes and returns a well-formed result for every Posting instead of
    crashing (§17's one-bad-posting-never-blocks-the-batch posture, stressed
    by a universally-bad input instead of a one-off)."""
    truncated = _inject_context_truncation(resume_text)
    scored, crashed = [], []
    for posting in postings:
        try:
            score_posting(posting, scored_dims, truncated, companies, conn)
            scored.append(posting["id"])
        except Exception as exc:
            crashed.append({"posting_id": posting["id"], "error": f"{type(exc).__name__}: {exc}"})
    return {"inject": "context-truncation", "n": len(postings), "scored": scored, "crashed": crashed}


def _persist_scored(conn: sqlite3.Connection, run_id: str, postings: list[dict]) -> None:
    """`run_stale_jd_check` calls `score_postings` directly (no graph, no
    service layer), so it has to do the same app_score bookkeeping the real
    `score` node's caller (`service._save_scores`) normally does -- otherwise
    the content_hash gate under test has no prior Score row to compare the
    next simulated pass against."""
    for p in postings:
        if "score" not in p:
            continue
        conn.execute(
            "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (p["id"], run_id, p["score"], p["rationale"], json.dumps(p["dimensions"]), p.get("content_hash")),
        )
    conn.commit()


def _fresh_postings(jd_by_id: dict[str, str]) -> list[dict]:
    """A minimal (id, jd_text, content_hash) dict per Posting -- what
    `fetch_jd` actually hands `score_postings` each real Poll (§4), not the
    enriched score/rationale/dimensions dict a *previous* pass produced.
    `run_stale_jd_check` must rebuild this shape itself each simulated Poll
    or `score_postings`' `{**posting, "status": "scored"}` skip-path would
    keep leaking the PRIOR round's own "score" key forward, masking a real
    skip as a rescore."""
    return [
        {"id": pid, "jd_text": jd_text, "content_hash": hashlib.sha256(jd_text.encode()).hexdigest()}
        for pid, jd_text in jd_by_id.items()
    ]


def run_stale_jd_check(
    conn: sqlite3.Connection,
    postings: list[dict],
    criteria: dict,
    run_id: str,
    companies_path=DEFAULT_COMPANIES_PATH,
) -> dict:
    """`--inject stale-jd`: feeds `score_postings` the OLD jd_text/content_hash
    of an edited Posting (§15) -- exactly what a stale/cached re-fetch would
    hand it. Three simulated Polls: first score, a stale re-fetch (identical
    jd_text/content_hash), then the true edit (genuinely new jd_text) landing
    on a later Poll. Reports whether unit 20's re-score gate (§11) still
    catches the true edit once it actually arrives, rather than staying
    poisoned by the stale round in between."""
    jd_by_id = {p["id"]: p["jd_text"] for p in postings}

    first_pass = score_postings(_fresh_postings(jd_by_id), criteria, conn, companies_path)
    _persist_scored(conn, run_id, first_pass)

    stale_result = score_postings(_fresh_postings(jd_by_id), criteria, conn, companies_path)
    stale_skipped = [p["id"] for p in stale_result if "score" not in p]

    edited_jd = {pid: text + " (edited)" for pid, text in jd_by_id.items()}
    edited_result = score_postings(_fresh_postings(edited_jd), criteria, conn, companies_path)
    edited_rescored = [p["id"] for p in edited_result if "score" in p]

    return {
        "inject": "stale-jd", "n": len(postings),
        "stale_correctly_skipped": stale_skipped,
        "true_edit_still_rescored": edited_rescored,
    }


# ---- unit 39: FINDINGS.md, generated from everything already run above ----


def _read_only_cell(conn: sqlite3.Connection, label: str, mechanism: str, run_id: str) -> dict:
    """`bake_off_cell` read-only: `postings=[]` means `to_score` is always
    empty, so it never calls a model or spends -- it just reads whatever a
    PAST run already wrote to `app_score`/`app_spend` under `run_id` and
    computes that cell's spearman/cost from it. Reused here (Bake-Off table
    + ablation deltas) instead of re-running the sweep to build FINDINGS.md."""
    return bake_off_cell(conn, [], [], "", [], label, mechanism, run_id)


def bake_off_table_from_db(conn: sqlite3.Connection) -> list[dict]:
    """The Bake-Off table (§15, unit 36), reconstructed read-only from
    whatever `bakeoff:<model>:<mechanism>` run_ids are already in `app_score`
    -- no re-scoring, no new spend."""
    run_ids = [
        r["run_id"] for r in conn.execute(
            "SELECT DISTINCT run_id FROM app_score WHERE run_id LIKE 'bakeoff:%' ORDER BY run_id"
        ).fetchall()
    ]
    table = []
    for run_id in run_ids:
        _, model_name, mechanism = run_id.split(":", 2)
        cell = _read_only_cell(conn, model_name, mechanism, run_id)
        table.append({"model_name": model_name, **cell})
    return table


def ablation_deltas_from_db(conn: sqlite3.Connection, baseline_spearman: float) -> list[dict]:
    """Ablation deltas (§15, unit 37), reconstructed read-only from whatever
    `ablation:<name>` run_ids are already in `app_score` -- delta is that
    variant's spearman minus the real production spearman (`baseline_spearman`,
    from `seed_set_eval`), same sign convention as unit 37: negative means the
    switched-off mechanism was helping."""
    run_ids = [
        r["run_id"] for r in conn.execute(
            "SELECT DISTINCT run_id FROM app_score WHERE run_id LIKE 'ablation:%' ORDER BY run_id"
        ).fetchall()
    ]
    deltas = []
    for run_id in run_ids:
        name = run_id.split(":", 1)[1]
        cell = _read_only_cell(conn, name, "", run_id)
        deltas.append({
            "ablation": name, "n": cell["n"], "spearman": cell["spearman"],
            "baseline_spearman": baseline_spearman,
            "delta": None if cell["spearman"] is None else cell["spearman"] - baseline_spearman,
        })
    return deltas


def _findings_markdown(
    baseline: dict, bake_off_table: list[dict], ablations: list[dict], triage: dict,
) -> str:
    lines = ["# FINDINGS", ""]

    lines += ["## Outcome metric", ""]
    lines += [
        f"Spearman rank correlation (Agent overall vs the Candidate's overall label), "
        f"production run, n={baseline['n']}: **{baseline['spearman']:.3f}** "
        f"(target ≥ {baseline['target']}, {'met' if baseline['meets_target'] else 'NOT met'}).",
        "",
        f"Knockout false-exclusion count: **{baseline['knockout_false_exclusion_count']}** "
        "(a Posting she thumbed up that a Knockout excluded -- the worst failure mode, target 0).",
        "",
    ]
    for fe in baseline["knockout_false_exclusions"]:
        lines.append(f"- `{fe['posting_id']}`: her overall {fe['human_overall']}, thumb up, excluded")
    lines.append("")

    lines += ["## Bake-Off", ""]
    lines += ["| Model | Mechanism | n | Spearman | Cost/posting |", "|---|---|---|---|---|"]
    for row in bake_off_table:
        spearman = "n/a" if row["spearman"] is None else f"{row['spearman']:.3f}"
        lines.append(
            f"| {row['model_name']} | {row['mechanism']} | {row['n']} | {spearman} | "
            f"${row['cost_per_posting']:.4f} |"
        )
    lines.append("")

    lines += ["## Ablation deltas", ""]
    lines += ["| Ablation | n | Spearman | Delta vs. production | |", "|---|---|---|---|---|"]
    for row in ablations:
        spearman = "n/a" if row["spearman"] is None else f"{row['spearman']:.3f}"
        delta = "n/a" if row["delta"] is None else f"{row['delta']:+.3f}"
        note = "helps (removing it hurt)" if (row["delta"] or 0) < 0 else "costs correlation, by design" if row["delta"] else ""
        lines.append(f"| {row['ablation']} | {row['n']} | {spearman} | {delta} | {note} |")
    lines.append("")

    lines += ["## Drift-closure curve", ""]
    lines += [
        "Pending -- needs the Candidate's round-2 end-of-project re-label "
        "(~15 Postings, DESIGN §15) to measure her own drift and whether the "
        "Preference Feedback Loop closed the gap. No round-2 labels exist yet.",
        "",
    ]

    lines += ["## Failure-mode writeups", ""]
    lines += [
        f"{triage['n_diverged']} of {baseline['n']} Postings diverged "
        f"(|agent − human| ≥ {triage['threshold']}) and were triaged by the calibrated judge:",
        "",
    ]
    for category, count in sorted(triage["by_category"].items()):
        lines.append(f"- **{category}**: {count}")
    lines.append("")
    for case in triage["cases"]:
        lines.append(
            f"- `{case['posting_id']}` (agent {case['agent_overall']} vs her {case['human_overall']}, "
            f"diff {case['diff']}) -- *{case['category']}*: {case['reasoning']}"
        )
    lines.append("")

    return "\n".join(lines)


def generate_findings(
    conn: sqlite3.Connection, out_path: Path = Path("FINDINGS.md"), round: int = 1,
) -> str:
    """Assembles FINDINGS.md (DESIGN §19, build-plan unit 39) from
    everything units 31-38 already produced in `conn`: the Bake-Off table
    and ablation deltas are read-only reconstructions (no re-scoring, no
    new spend, §15); the failure-mode triage runs the calibrated judge live
    over whatever Postings currently diverge (small real spend -- typically
    a handful of calls, §15). The Drift-closure curve is reported as
    pending until round-2 labels exist -- there is no data to fabricate it
    from."""
    baseline = seed_set_eval(conn, round)
    bake_off_table = bake_off_table_from_db(conn)
    ablations = ablation_deltas_from_db(conn, baseline["spearman"])
    triage = divergence_triage(conn, round)
    markdown = _findings_markdown(baseline, bake_off_table, ablations, triage)
    out_path.write_text(markdown)
    return markdown
