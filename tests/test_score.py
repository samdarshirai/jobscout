from jobscout.discovery.companies import TargetCompany
from jobscout.score import (
    DimensionScore,
    ScoreResult,
    _cap_uncited,
    _company_lookup_text,
    _fill_missing_dimensions,
    _past_rejections_text,
    score_posting,
    score_postings,
    weighted_total,
)

_SCORED = [
    {"dimension": "stack fit", "weight": 0.6, "rubric": "5 = core tools"},
    {"dimension": "eng-culture signals", "weight": 0.4, "rubric": "testing/CI"},
]


def test_cap_uncited_leaves_a_well_cited_score_alone():
    dims = [DimensionScore(dimension="stack fit", score=5, jd_line="React", resume_line="Built with React")]
    capped, notes = _cap_uncited(dims, jd_text="Role needs React", resume_text="Built with React for 5 years")
    assert capped[0].score == 5
    assert notes == []


def test_cap_uncited_caps_a_score_missing_a_real_quote():
    dims = [DimensionScore(dimension="stack fit", score=5, jd_line="Rust", resume_line=None)]
    capped, notes = _cap_uncited(dims, jd_text="Role needs Rust", resume_text="Built with React")
    assert capped[0].score == 2
    assert "stack fit" in notes[0]


def test_cap_uncited_catches_a_fabricated_quote_not_actually_in_the_text():
    dims = [DimensionScore(dimension="stack fit", score=5, jd_line="Rust", resume_line="Shipped Rust for 5 years")]
    capped, notes = _cap_uncited(dims, jd_text="Role needs Rust", resume_text="Built with React, no Rust here")
    assert capped[0].score == 2
    assert notes


def test_cap_uncited_leaves_a_low_uncited_score_alone():
    dims = [DimensionScore(dimension="stack fit", score=1, jd_line=None, resume_line=None)]
    capped, notes = _cap_uncited(dims, jd_text="Role needs Rust", resume_text="Built with React")
    assert capped[0].score == 1
    assert notes == []


def test_cap_uncited_caps_two_real_but_unrelated_quotes():
    """Confirmed live (unit 35): two independently-real quotes aren't
    necessarily related to each other."""
    dims = [DimensionScore(
        dimension="domain/product interest", score=5,
        jd_line="We build AI voice agents for scheduling appointments.",
        resume_line="Developed high-performance Angular applications serving 100,000+ users.",
    )]
    capped, notes = _cap_uncited(
        dims,
        jd_text="We build AI voice agents for scheduling appointments.",
        resume_text="Developed high-performance Angular applications serving 100,000+ users.",
    )
    assert capped[0].score == 2
    assert "don't relate" in notes[0]


def test_cap_uncited_does_not_cap_related_quotes_with_different_word_forms():
    """The stem check should survive plain inflection (mentor/mentored),
    not just exact word matches."""
    dims = [DimensionScore(
        dimension="scope & seniority", score=5,
        jd_line="Mentor engineers and lead platform initiatives.",
        resume_line="Mentored developers and led a team of 4 engineers.",
    )]
    capped, notes = _cap_uncited(
        dims,
        jd_text="Mentor engineers and lead platform initiatives.",
        resume_text="Mentored developers and led a team of 4 engineers.",
    )
    assert capped[0].score == 5
    assert notes == []


def test_score_posting_applies_the_cap_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.score._run_scoring_agent",
        lambda jd_text, scored, tools: ScoreResult(
            dimensions=[DimensionScore(dimension="stack fit", score=5, jd_line=None, resume_line=None)],
            rationale="great fit",
        ),
    )
    posting = {"id": "p1", "jd_text": "JD", "content_hash": None}

    result = score_posting(posting, _SCORED, "resume", [], conn=None)

    assert result["dimensions"][0]["score"] == 2
    assert "capped" in result["rationale"]


def test_score_posting_skips_the_cap_when_matched_lines_required_is_false(tmp_path, monkeypatch):
    """Ablation switch (unit 37): MATCHED_LINES_REQUIRED=false measures
    the anti-inflation cap's delta by turning it off."""
    monkeypatch.setattr(
        "jobscout.score._run_scoring_agent",
        lambda jd_text, scored, tools: ScoreResult(
            dimensions=[DimensionScore(dimension="stack fit", score=5, jd_line=None, resume_line=None)],
            rationale="great fit",
        ),
    )
    monkeypatch.setenv("MATCHED_LINES_REQUIRED", "false")
    posting = {"id": "p1", "jd_text": "JD", "content_hash": None}

    result = score_posting(posting, _SCORED, "resume", [], conn=None)

    assert result["dimensions"][0]["score"] == 5
    assert result["rationale"] == "great fit"


def test_weighted_total_scales_to_0_100():
    dims = [
        DimensionScore(dimension="stack fit", score=5, jd_line="x", resume_line="y"),
        DimensionScore(dimension="eng-culture signals", score=0),
    ]
    assert weighted_total(dims, _SCORED) == 60


def test_weighted_total_ignores_a_dimension_not_in_criteria():
    dims = [DimensionScore(dimension="not a real axis", score=5)]
    assert weighted_total(dims, _SCORED) == 0


def test_weighted_total_matches_a_dimension_name_regardless_of_formatting():
    """Confirmed live: the model returned "stack_fit" for the criteria's
    "stack fit" — an exact-string match zeroed that dimension's weight
    silently, tanking a Posting narratively rationalized at ~92/100 down
    to a stored 0."""
    dims = [
        DimensionScore(dimension="Stack_Fit", score=5, jd_line="x", resume_line="y"),
        DimensionScore(dimension="ENG-CULTURE SIGNALS", score=0),
    ]
    assert weighted_total(dims, _SCORED) == 60


def test_fill_missing_dimensions_adds_an_uncited_0_for_a_dimension_the_model_skipped():
    """Confirmed live: a real Posting's model call returned only 1 of 4
    scored dimensions; the other 3 silently contributed 0 weight with no
    record they were ever missing. An explicit, uncited 0 stub makes the
    gap visible instead of an invisible side effect of a weight lookup
    finding nothing."""
    dims = [DimensionScore(dimension="stack fit", score=2, jd_line="x", resume_line="y")]
    filled = _fill_missing_dimensions(dims, _SCORED)
    assert len(filled) == 2
    missing = next(d for d in filled if d.dimension == "eng-culture signals")
    assert missing.score == 0
    assert missing.jd_line is None
    assert missing.resume_line is None


def test_fill_missing_dimensions_adds_nothing_when_every_dimension_is_present():
    dims = [
        DimensionScore(dimension="stack fit", score=2, jd_line="x", resume_line="y"),
        DimensionScore(dimension="eng-culture signals", score=3, jd_line="x", resume_line="y"),
    ]
    filled = _fill_missing_dimensions(dims, _SCORED)
    assert len(filled) == 2


def test_fill_missing_dimensions_matches_a_reformatted_name_and_does_not_duplicate():
    """A dimension name the model reformats (underscores, dropped words)
    must still count as present — otherwise this fix would double-count
    it instead of the weight-matching bug it's meant to complement."""
    dims = [DimensionScore(dimension="stack_fit", score=2, jd_line="x", resume_line="y")]
    filled = _fill_missing_dimensions(dims, _SCORED)
    assert len(filled) == 2  # stack_fit matched, only eng-culture signals added


def test_weighted_total_matches_a_dimension_name_missing_a_word():
    """Confirmed live: even after case/punctuation normalization, the
    model's "scope_seniority" didn't match the criteria's real
    "scope & seniority signals" — it dropped the word "signals"
    entirely, not just reformatted it — silently zeroing that
    dimension's weight (22 stored instead of the correct 40 for a
    Posting whose dims were uniformly 2/5). Fuzzy word-overlap matching
    survives a dropped word, not just reformatting."""
    scored = [
        {"dimension": "scope & seniority signals", "weight": 0.6, "rubric": "x"},
        {"dimension": "eng-culture signals", "weight": 0.4, "rubric": "x"},
    ]
    dims = [
        DimensionScore(dimension="scope_seniority", score=5, jd_line="x", resume_line="y"),
        DimensionScore(dimension="eng_culture", score=0),
    ]
    assert weighted_total(dims, scored) == 60


def test_weighted_total_averages_duplicate_entries_for_the_same_dimension():
    """Confirmed live: the model can cite the same dimension twice (two
    separate JD/resume line pairs) — each entry must not count as a
    separate dimension's worth of weight, or the total gets inflated."""
    dims = [
        DimensionScore(dimension="stack fit", score=5, jd_line="x", resume_line="y"),
        DimensionScore(dimension="stack fit", score=5, jd_line="x2", resume_line="y2"),
        DimensionScore(dimension="eng-culture signals", score=0),
    ]
    # same as a single stack-fit=5, eng-culture=0 — NOT double-counted to 120
    assert weighted_total(dims, _SCORED) == 60


def test_company_lookup_text_reports_unknown_company():
    text = _company_lookup_text([TargetCompany(name="Acme", slug="acme")], "Other Co")
    assert "not on the curated" in text


def test_company_lookup_text_reports_a_known_company():
    text = _company_lookup_text(
        [TargetCompany(name="Acme", slug="acme", careers_url="https://acme.example/careers")], "Acme"
    )
    assert "acme" in text
    assert "acme.example" in text


def test_past_rejections_text_reports_nothing_on_file(tmp_path):
    import sqlite3

    from jobscout.storage.db import init_db

    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    assert "No past Rejections" in _past_rejections_text(conn, "Acme")
    conn.close()


def test_score_postings_skips_excluded_postings_without_calling_the_agent(tmp_path, monkeypatch):
    import sqlite3

    from jobscout.storage.db import init_db

    def _boom(*args, **kwargs):
        raise AssertionError("score_posting should not be called for an excluded Posting")

    monkeypatch.setattr("jobscout.score.score_posting", _boom)
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    postings = [{"id": "p1", "status": "excluded", "status_reason": "too junior"}]
    result = score_postings(postings, {"scored": _SCORED}, conn, tmp_path / "companies.yaml")
    assert result == postings
    conn.close()


def test_score_postings_is_a_noop_with_no_scored_dimensions():
    postings = [{"id": "p1", "jd_text": "JD"}]
    assert score_postings(postings, {}, conn=None) == postings


def test_score_postings_scores_a_never_scored_posting_and_records_its_hash(tmp_path, monkeypatch):
    import sqlite3

    from jobscout.storage.db import init_db

    monkeypatch.setattr(
        "jobscout.score.score_posting",
        lambda posting, scored, resume_text, companies, conn: {
            "score": 80,
            "rationale": "great fit",
            "dimensions": [],
            "content_hash": posting.get("content_hash"),
        },
    )
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    postings = [{"id": "p1", "jd_text": "JD", "content_hash": "hash-v1"}]
    [result] = score_postings(postings, {"scored": _SCORED}, conn, tmp_path / "companies.yaml")
    assert result["status"] == "scored"
    assert result["score"] == 80
    assert result["content_hash"] == "hash-v1"
    conn.close()


def test_score_postings_skips_a_posting_whose_content_hash_is_unchanged(tmp_path, monkeypatch):
    import sqlite3

    from jobscout.storage.db import init_db

    def _boom(*args, **kwargs):
        raise AssertionError("score_posting should not be called for an unchanged Posting")

    monkeypatch.setattr("jobscout.score.score_posting", _boom)
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO app_score (posting_id, score, rationale, dimensions_json, content_hash) "
        "VALUES ('p1', 70, 'ok', '[]', 'hash-v1')"
    )
    conn.commit()
    postings = [{"id": "p1", "jd_text": "JD", "content_hash": "hash-v1"}]
    [result] = score_postings(postings, {"scored": _SCORED}, conn, tmp_path / "companies.yaml")
    assert result["status"] == "scored"
    assert "score" not in result
    conn.close()


def test_score_postings_marks_a_posting_error_when_scoring_raises(tmp_path, monkeypatch):
    import sqlite3

    from jobscout.storage.db import init_db

    def _boom(posting, scored, resume_text, companies, conn):
        raise ValueError("malformed JD")

    monkeypatch.setattr("jobscout.score.score_posting", _boom)
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.commit()
    postings = [{"id": "p1", "jd_text": "JD", "content_hash": "hash-v1"}]
    [result] = score_postings(postings, {"scored": _SCORED}, conn, tmp_path / "companies.yaml")
    assert result["status"] == "error"
    assert result["status_reason"] == "malformed JD"
    assert result["error_count"] == 1
    conn.close()


def test_score_postings_marks_a_posting_dead_after_three_consecutive_errors(tmp_path, monkeypatch):
    import sqlite3

    from jobscout.storage.db import init_db

    def _boom(posting, scored, resume_text, companies, conn):
        raise ValueError("model 500")

    monkeypatch.setattr("jobscout.score.score_posting", _boom)
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO app_posting (id, source, company, title, error_count) "
        "VALUES ('p1', 'arbeitnow', 'Acme', 'Engineer', 2)"
    )
    conn.commit()
    postings = [{"id": "p1", "jd_text": "JD", "content_hash": "hash-v1"}]
    [result] = score_postings(postings, {"scored": _SCORED}, conn, tmp_path / "companies.yaml")
    assert result["status"] == "dead"
    assert result["error_count"] == 3
    conn.close()


def test_score_postings_still_scores_other_postings_when_one_errors(tmp_path, monkeypatch):
    import sqlite3

    from jobscout.storage.db import init_db

    def _flaky(posting, scored, resume_text, companies, conn):
        if posting["id"] == "bad":
            raise ValueError("malformed JD")
        return {"score": 80, "rationale": "great fit", "dimensions": [], "content_hash": None}

    monkeypatch.setattr("jobscout.score.score_posting", _flaky)
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.commit()
    postings = [
        {"id": "bad", "jd_text": "JD", "content_hash": "h1"},
        {"id": "good", "jd_text": "JD", "content_hash": "h2"},
    ]
    results = score_postings(postings, {"scored": _SCORED}, conn, tmp_path / "companies.yaml")
    by_id = {r["id"]: r for r in results}
    assert by_id["bad"]["status"] == "error"
    assert by_id["good"]["status"] == "scored"
    assert by_id["good"]["score"] == 80
    conn.close()


def test_score_postings_rescores_a_posting_whose_content_hash_changed(tmp_path, monkeypatch):
    import sqlite3

    from jobscout.storage.db import init_db

    monkeypatch.setattr(
        "jobscout.score.score_posting",
        lambda posting, scored, resume_text, companies, conn: {
            "score": 90,
            "rationale": "even better now",
            "dimensions": [],
            "content_hash": posting.get("content_hash"),
        },
    )
    conn = sqlite3.connect(tmp_path / "j.sqlite")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO app_score (posting_id, score, rationale, dimensions_json, content_hash) "
        "VALUES ('p1', 70, 'ok', '[]', 'hash-v1')"
    )
    conn.commit()
    postings = [{"id": "p1", "jd_text": "JD edited", "content_hash": "hash-v2"}]
    [result] = score_postings(postings, {"scored": _SCORED}, conn, tmp_path / "companies.yaml")
    assert result["score"] == 90
    assert result["content_hash"] == "hash-v2"
    conn.close()


# ---- Preference Feedback Loop tool gating (unit 26) -------------------------

import jobscout.score as score_module


def _tool_names(tools):
    return {t.name for t in tools}


def test_build_tools_default_mode_is_summary_only(monkeypatch):
    monkeypatch.delenv("FEEDBACK_MECHANISM", raising=False)
    tools = score_module._build_tools(None, "resume", [], "jd")
    names = _tool_names(tools)
    assert "preference_summary" in names
    assert "similar_past_verdicts" not in names


def test_build_tools_few_shot_mode_swaps_the_tool(monkeypatch):
    monkeypatch.setenv("FEEDBACK_MECHANISM", "few-shot")
    tools = score_module._build_tools(None, "resume", [], "jd")
    names = _tool_names(tools)
    assert "similar_past_verdicts" in names
    assert "preference_summary" not in names


def test_build_tools_both_mode_includes_both(monkeypatch):
    monkeypatch.setenv("FEEDBACK_MECHANISM", "both")
    tools = score_module._build_tools(None, "resume", [], "jd")
    names = _tool_names(tools)
    assert {"preference_summary", "similar_past_verdicts"} <= names


def test_build_tools_none_mode_includes_neither(monkeypatch):
    monkeypatch.setenv("FEEDBACK_MECHANISM", "none")
    tools = score_module._build_tools(None, "resume", [], "jd")
    names = _tool_names(tools)
    assert "preference_summary" not in names
    assert "similar_past_verdicts" not in names


def test_build_tools_falls_back_to_summary_on_an_unknown_mode(monkeypatch):
    monkeypatch.setenv("FEEDBACK_MECHANISM", "bogus")
    tools = score_module._build_tools(None, "resume", [], "jd")
    names = _tool_names(tools)
    assert "preference_summary" in names
    assert "similar_past_verdicts" not in names


def test_few_shot_verdicts_text_formats_matches(monkeypatch):
    monkeypatch.setattr(
        "jobscout.score.top_k_similar_verdicts",
        lambda conn, jd_text, k=5: [
            {"title": "Backend Eng", "company": "Acme", "verdict": "up", "reason": "great stack", "similarity": 0.91}
        ],
    )
    text = score_module._few_shot_verdicts_text(None, "jd text")
    assert "Backend Eng" in text
    assert "Acme" in text
    assert "0.91" in text


def test_few_shot_verdicts_text_handles_no_matches(monkeypatch):
    monkeypatch.setattr("jobscout.score.top_k_similar_verdicts", lambda conn, jd_text, k=5: [])
    text = score_module._few_shot_verdicts_text(None, "jd text")
    assert "No similar past Verdicts" in text
