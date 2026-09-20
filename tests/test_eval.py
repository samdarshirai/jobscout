import json
import os
import sqlite3
import time

import pytest

from jobscout.eval import (
    _score_with_deadline,
    bake_off_cell,
    cost_per_posting,
    divergence_triage,
    knockout_correctness,
    matched_lines_real,
    matched_lines_relevance,
    run_bake_off,
    score_rationale_consistency,
    seed_set_eval,
    spearman_correlation,
)
from jobscout.judge import ConsistencyVerdict, DivergenceVerdict, KnockoutVerdict, MatchedLineVerdict
from jobscout.storage.db import init_db


def test_spearman_correlation_is_1_for_identical_rank_order():
    assert spearman_correlation([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_spearman_correlation_is_minus_1_for_reversed_rank_order():
    assert spearman_correlation([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_correlation_is_0_for_no_rank_relationship():
    # a permutation whose rank deviations cancel to exactly zero covariance
    assert spearman_correlation([1, 2, 3, 4], [2, 4, 1, 3]) == pytest.approx(0.0)


def test_spearman_correlation_handles_tied_values():
    # ties get average rank; still strongly positive since order is preserved
    assert spearman_correlation([1, 1, 2, 3], [10, 10, 20, 30]) == pytest.approx(1.0)


def test_spearman_correlation_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        spearman_correlation([1, 2], [1, 2, 3])


def _seed_labeled_posting(
    conn, posting_id, agent_score, human_overall, thumb, status="scored",
    jd_text="We need Angular.", dimensions=None, city="Munich", status_reason=None,
):
    conn.execute(
        "INSERT INTO app_posting (id, source, company, title, status, jd_text, city, status_reason) "
        "VALUES (?, 'arbeitnow', 'Acme', 'Eng', ?, ?, ?, ?)",
        (posting_id, status, jd_text, city, status_reason),
    )
    conn.execute(
        "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json) "
        "VALUES (?, 'eval-seed', ?, 'why', ?)",
        (posting_id, agent_score, json.dumps(dimensions or [])),
    )
    conn.execute(
        "INSERT INTO app_seed_label (posting_id, overall, thumb, why, round) VALUES (?, ?, ?, 'why', 1)",
        (posting_id, human_overall, thumb),
    )


def test_seed_set_eval_reports_correlation_against_target(tmp_path):
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed_labeled_posting(conn, "p1", 90, 95, "up")
    _seed_labeled_posting(conn, "p2", 10, 5, "down")
    _seed_labeled_posting(conn, "p3", 50, 55, "down")
    conn.commit()

    result = seed_set_eval(conn)

    assert result["n"] == 3
    assert result["spearman"] == pytest.approx(1.0)
    assert result["meets_target"] is True
    assert result["knockout_false_exclusion_count"] == 0


def test_seed_set_eval_ignores_experimental_bake_off_and_ablation_scores(tmp_path):
    """Confirmed live (units 36/37): a Bake-Off/ablation sweep writes many
    more app_score rows for the same Seed Set Postings under experimental
    run_ids — "latest row overall" then silently picks up a stray sweep
    cell instead of the real production score. Only a later PRODUCTION
    score (a real eval-seed rerun) should ever override the number this
    reports; a later experimental row must not."""
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed_labeled_posting(conn, "p1", 90, 95, "up")
    _seed_labeled_posting(conn, "p2", 10, 5, "down")
    conn.commit()
    # a later Bake-Off/ablation sweep scores p1 very differently — if
    # "latest row overall" won, this would invert the rank order
    conn.execute(
        "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json) "
        "VALUES ('p1', 'bakeoff:qwen:none', 1, 'why', '[]')"
    )
    conn.execute(
        "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json) "
        "VALUES ('p1', 'ablation:matched-lines-off', 1, 'why', '[]')"
    )
    conn.commit()

    result = seed_set_eval(conn)

    assert result["n"] == 2
    # the PRODUCTION score (90, ranked above p2's 10) still wins
    assert result["spearman"] == pytest.approx(1.0)


def test_seed_set_eval_flags_a_knockout_false_exclusion(tmp_path):
    """A Posting she thumbed up that knockout excluded is the worst
    failure (§15) — must be surfaced, not silently averaged away."""
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed_labeled_posting(conn, "p1", 0, 90, "up", status="excluded")
    _seed_labeled_posting(conn, "p2", 10, 5, "down", status="excluded")
    conn.commit()

    result = seed_set_eval(conn)

    assert result["knockout_false_exclusion_count"] == 1
    assert result["knockout_false_exclusions"][0]["posting_id"] == "p1"


def test_cost_per_posting_divides_eval_spend_by_distinct_postings_scored(tmp_path):
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed_labeled_posting(conn, "p1", 50, 50, "down")
    _seed_labeled_posting(conn, "p2", 50, 50, "down")
    conn.execute(
        "INSERT INTO app_spend (run_id, node, model, prompt_tokens, completion_tokens, cost_usd) "
        "VALUES ('eval-seed', 'score', 'm', 100, 50, 0.02)"
    )
    conn.execute(
        "INSERT INTO app_spend (run_id, node, model, prompt_tokens, completion_tokens, cost_usd) "
        "VALUES ('eval-seed', 'score', 'm', 100, 50, 0.02)"
    )
    conn.commit()

    assert cost_per_posting(conn) == pytest.approx(0.02)


def test_cost_per_posting_is_0_with_no_matching_spend(tmp_path):
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    assert cost_per_posting(conn) == 0.0


# ---- unit 35: reasoning-quality checks -------------------------------


def _conn_with_resume(tmp_path, resume_text="Frontend engineer skilled in Angular and TypeScript."):
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO app_profile (version, resume_text, data_json) VALUES (1, ?, '{}')", (resume_text,)
    )
    return conn


def test_matched_lines_real_passes_a_genuine_citation(tmp_path):
    conn = _conn_with_resume(tmp_path)
    dims = [{"dimension": "stack fit", "score": 4,
             "jd_line": "We need Angular.", "resume_line": "Frontend engineer skilled in Angular and TypeScript."}]
    _seed_labeled_posting(conn, "p1", 80, 80, "up", jd_text="We need Angular.", dimensions=dims)
    conn.commit()

    result = matched_lines_real(conn)

    assert result["checked"] == 1
    assert result["all_real"] is True
    assert result["violations"] == []


def test_matched_lines_real_flags_a_hallucinated_citation(tmp_path):
    conn = _conn_with_resume(tmp_path)
    dims = [{"dimension": "stack fit", "score": 4,
             "jd_line": "This line is not in the JD.", "resume_line": "Frontend engineer skilled in Angular and TypeScript."}]
    _seed_labeled_posting(conn, "p1", 80, 80, "up", jd_text="We need Angular.", dimensions=dims)
    conn.commit()

    result = matched_lines_real(conn)

    assert result["all_real"] is False
    assert result["violations"][0]["posting_id"] == "p1"


def test_matched_lines_real_ignores_low_scored_uncited_dimensions(tmp_path):
    """score.py's _cap_uncited already floors an uncited dimension at 2 —
    only dimensions above that cap are asserting a real citation."""
    conn = _conn_with_resume(tmp_path)
    dims = [{"dimension": "stack fit", "score": 2, "jd_line": None, "resume_line": None}]
    _seed_labeled_posting(conn, "p1", 20, 20, "down", dimensions=dims)
    conn.commit()

    result = matched_lines_real(conn)

    assert result["checked"] == 0


def test_matched_lines_relevance_calls_the_judge_only_on_real_citations(tmp_path, monkeypatch):
    conn = _conn_with_resume(tmp_path)
    dims = [{"dimension": "stack fit", "score": 4,
             "jd_line": "We need Angular.", "resume_line": "Frontend engineer skilled in Angular and TypeScript."}]
    _seed_labeled_posting(conn, "p1", 80, 80, "up", jd_text="We need Angular.", dimensions=dims)
    conn.commit()
    calls = []

    def _fake_judge(dimension, jd_line, resume_line):
        calls.append((dimension, jd_line, resume_line))
        return MatchedLineVerdict(relevant=False, reasoning="off-topic quote")

    monkeypatch.setattr("jobscout.eval.judge_matched_line_relevance", _fake_judge)

    result = matched_lines_relevance(conn)

    assert len(calls) == 1
    assert result["all_relevant"] is False
    assert result["irrelevant"][0]["posting_id"] == "p1"


def test_score_rationale_consistency_flags_an_inconsistent_verdict(tmp_path, monkeypatch):
    conn = _conn_with_resume(tmp_path)
    _seed_labeled_posting(conn, "p1", 80, 80, "up")
    conn.commit()
    monkeypatch.setattr(
        "jobscout.eval.judge_score_rationale_consistency",
        lambda score, rationale, dims_text: ConsistencyVerdict(consistent=False, reasoning="mismatch"),
    )

    result = score_rationale_consistency(conn)

    assert result["checked"] == 1
    assert result["all_consistent"] is False
    assert result["inconsistent"][0]["posting_id"] == "p1"


def test_knockout_correctness_uses_the_latest_criteria_version(tmp_path, monkeypatch):
    conn = _conn_with_resume(tmp_path)
    _seed_labeled_posting(conn, "p1", 0, 90, "up", status="excluded", status_reason="location: remote-only outside Germany")
    conn.execute(
        "INSERT INTO app_criteria (version, data_json) VALUES (1, ?)",
        (json.dumps({"scored": [], "knockout": [{"axis": "location", "rule": "Must be Germany."}]}),),
    )
    conn.commit()
    captured = {}

    def _fake_judge(jd_text, city, rules_text, status, status_reason):
        captured.update(status=status, status_reason=status_reason, rules_text=rules_text)
        return KnockoutVerdict(correct=False, reasoning="JD is Berlin-based, this was a bad exclude")

    monkeypatch.setattr("jobscout.eval.judge_knockout_correctness", _fake_judge)

    result = knockout_correctness(conn)

    assert captured["status"] == "excluded"
    assert "location" in captured["rules_text"]
    assert result["all_correct"] is False
    assert result["incorrect"][0]["posting_id"] == "p1"


def test_divergence_triage_only_triages_gaps_past_the_threshold(tmp_path, monkeypatch):
    conn = _conn_with_resume(tmp_path)
    _seed_labeled_posting(conn, "small-gap", 60, 65, "up")
    _seed_labeled_posting(conn, "big-gap", 30, 90, "up")
    conn.commit()
    monkeypatch.setattr(
        "jobscout.eval.judge_divergence",
        lambda agent, human, rationale, why: DivergenceVerdict(category="she_is_outlier", reasoning="agent's logic holds"),
    )

    result = divergence_triage(conn, threshold=25)

    assert result["n_diverged"] == 1
    assert result["cases"][0]["posting_id"] == "big-gap"
    assert result["by_category"] == {"she_is_outlier": 1}


# ---- unit 36: the Bake-Off deliverable table --------------------------


def test_bake_off_cell_sets_the_env_knobs_score_posting_reads(tmp_path, monkeypatch):
    conn = _conn_with_resume(tmp_path)
    _seed_labeled_posting(conn, "p1", 0, 80, "up")
    conn.execute("INSERT INTO app_criteria (version, data_json) VALUES (1, ?)", (json.dumps({"scored": []}),))
    conn.commit()
    posting = dict(conn.execute("SELECT * FROM app_posting WHERE id='p1'").fetchone())
    captured_env = {}

    def _fake_score_posting(posting, scored, resume_text, companies, conn):
        captured_env["model"] = os.environ.get("OPENROUTER_MODEL")
        captured_env["mechanism"] = os.environ.get("FEEDBACK_MECHANISM")
        return {"score": 77, "rationale": "why", "dimensions": [], "content_hash": None}

    monkeypatch.setattr("jobscout.eval.score_posting", _fake_score_posting)

    bake_off_cell(conn, [posting], [], "resume", [], "some/model", "few-shot", "bakeoff:some-model:few-shot")

    assert captured_env == {"model": "some/model", "mechanism": "few-shot"}


def test_bake_off_cell_computes_spearman_and_cost_for_its_own_run_id(tmp_path, monkeypatch):
    conn = _conn_with_resume(tmp_path)
    _seed_labeled_posting(conn, "p1", 0, 20, "down")
    _seed_labeled_posting(conn, "p2", 0, 80, "up")
    conn.commit()
    postings = [dict(r) for r in conn.execute("SELECT * FROM app_posting")]
    scores = {"p1": 25, "p2": 85}

    monkeypatch.setattr(
        "jobscout.eval.score_posting",
        lambda posting, scored, resume_text, companies, conn: {
            "score": scores[posting["id"]], "rationale": "why", "dimensions": [], "content_hash": None,
        },
    )

    result = bake_off_cell(conn, postings, [], "resume", [], "m/x", "none", "bakeoff:m-x:none")

    assert result == {
        "model": "m/x", "mechanism": "none", "n": 2,
        "spearman": pytest.approx(1.0), "cost_total": 0.0, "cost_per_posting": 0.0,
    }
    # the OLD app_score rows (agent_overall=0, from _seed_labeled_posting)
    # must not leak into this cell's own result via seed_set_eval's usual
    # "latest score" join — bake_off_cell filters by its own run_id.
    stored = conn.execute(
        "SELECT score FROM app_score WHERE posting_id='p1' AND run_id='bakeoff:m-x:none'"
    ).fetchone()
    assert stored["score"] == 25


def test_run_bake_off_stops_once_the_spend_cap_is_reached(tmp_path, monkeypatch):
    conn = _conn_with_resume(tmp_path)
    conn.execute("INSERT INTO app_criteria (version, data_json) VALUES (1, ?)", (json.dumps({"scored": []}),))
    conn.commit()
    monkeypatch.setattr("jobscout.eval.spend.total_spend", lambda conn: 20.0)
    calls = []
    monkeypatch.setattr("jobscout.eval.bake_off_cell", lambda *a, **k: calls.append(a) or {})

    table = run_bake_off(conn, postings=[], models={"m1": "a/b"}, mechanisms=("none",))

    assert table == []
    assert calls == []


def test_run_bake_off_builds_one_row_per_model_and_mechanism_cell(tmp_path, monkeypatch):
    conn = _conn_with_resume(tmp_path)
    conn.execute("INSERT INTO app_criteria (version, data_json) VALUES (1, ?)", (json.dumps({"scored": []}),))
    conn.commit()
    monkeypatch.setattr("jobscout.eval.spend.total_spend", lambda conn: 0.0)
    monkeypatch.setattr(
        "jobscout.eval.bake_off_cell",
        lambda conn, postings, scored_dims, resume_text, companies, model, mechanism, run_id:
            {"model": model, "mechanism": mechanism, "n": 0, "spearman": None, "cost_total": 0.0, "cost_per_posting": 0.0},
    )

    table = run_bake_off(
        conn, postings=[], models={"m1": "a/b", "m2": "c/d"}, mechanisms=("none", "summary"),
    )

    assert len(table) == 4
    assert {(row["model_name"], row["mechanism"]) for row in table} == {
        ("m1", "none"), ("m1", "summary"), ("m2", "none"), ("m2", "summary"),
    }


def test_bake_off_cell_skips_a_posting_already_scored_under_its_own_run_id(tmp_path, monkeypatch):
    """Resumability (unit 36): a killed-and-restarted run must not
    double-insert a Posting already scored under this exact cell, or its
    Spearman gets duplicate points instead of a clean rerun."""
    conn = _conn_with_resume(tmp_path)
    _seed_labeled_posting(conn, "p1", 0, 80, "up")
    conn.execute(
        "INSERT INTO app_score (posting_id, run_id, score, rationale, dimensions_json) "
        "VALUES ('p1', 'bakeoff:m-x:none', 90, 'already scored', '[]')"
    )
    conn.commit()
    posting = dict(conn.execute("SELECT * FROM app_posting WHERE id='p1'").fetchone())
    calls = []
    monkeypatch.setattr(
        "jobscout.eval.score_posting",
        lambda posting, scored, resume_text, companies, conn: calls.append(posting["id"]) or {
            "score": 10, "rationale": "new call", "dimensions": [], "content_hash": None,
        },
    )

    bake_off_cell(conn, [posting], [], "resume", [], "m/x", "none", "bakeoff:m-x:none")

    assert calls == []
    rows = conn.execute("SELECT score FROM app_score WHERE run_id='bakeoff:m-x:none'").fetchall()
    assert len(rows) == 1
    assert rows[0]["score"] == 90


def test_score_with_deadline_raises_timeout_error_instead_of_hanging(tmp_path, monkeypatch):
    """Confirmed live (unit 36): a real score_posting call hung for 2+
    hours — DEFAULT_TIMEOUT_S is a per-chunk read timeout, not a total-call
    cap. This deadline must actually bound the wait, not just document
    intent to."""
    conn = _conn_with_resume(tmp_path)
    monkeypatch.setattr(
        "jobscout.eval.score_posting",
        lambda posting, scored, resume_text, companies, conn: time.sleep(5),
    )

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        _score_with_deadline({"id": "p1", "jd_text": "x"}, [], "resume", [], conn, timeout=0.2)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0


def test_bake_off_cell_skips_a_posting_that_times_out_on_both_attempts(tmp_path, monkeypatch):
    conn = _conn_with_resume(tmp_path)
    _seed_labeled_posting(conn, "p1", 0, 80, "up")
    conn.commit()
    posting = dict(conn.execute("SELECT * FROM app_posting WHERE id='p1'").fetchone())
    monkeypatch.setattr("jobscout.eval._BAKE_OFF_CALL_TIMEOUT_S", 0.1)
    monkeypatch.setattr(
        "jobscout.eval.score_posting",
        lambda posting, scored, resume_text, companies, conn: time.sleep(5),
    )

    result = bake_off_cell(conn, [posting], [], "resume", [], "m/x", "none", "bakeoff:m-x:none")

    assert result["n"] == 0
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM app_score WHERE run_id='bakeoff:m-x:none'"
    ).fetchone()["n"] == 0
