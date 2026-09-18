from jobscout.discovery.companies import TargetCompany
from jobscout.score import (
    DimensionScore,
    _cap_uncited,
    _company_lookup_text,
    _past_rejections_text,
    score_postings,
    weighted_total,
)

_SCORED = [
    {"dimension": "stack fit", "weight": 0.6, "rubric": "5 = core tools"},
    {"dimension": "eng-culture signals", "weight": 0.4, "rubric": "testing/CI"},
]


def test_cap_uncited_leaves_a_well_cited_score_alone():
    dims = [DimensionScore(dimension="stack fit", score=5, jd_line="React", resume_line="Built with React")]
    capped = _cap_uncited(dims, jd_text="Role needs React", resume_text="Built with React for 5 years")
    assert capped[0].score == 5


def test_cap_uncited_caps_a_score_missing_a_real_quote():
    dims = [DimensionScore(dimension="stack fit", score=5, jd_line="Rust", resume_line=None)]
    capped = _cap_uncited(dims, jd_text="Role needs Rust", resume_text="Built with React")
    assert capped[0].score == 2


def test_cap_uncited_catches_a_fabricated_quote_not_actually_in_the_text():
    dims = [DimensionScore(dimension="stack fit", score=5, jd_line="Rust", resume_line="Shipped Rust for 5 years")]
    capped = _cap_uncited(dims, jd_text="Role needs Rust", resume_text="Built with React, no Rust here")
    assert capped[0].score == 2


def test_cap_uncited_leaves_a_low_uncited_score_alone():
    dims = [DimensionScore(dimension="stack fit", score=1, jd_line=None, resume_line=None)]
    capped = _cap_uncited(dims, jd_text="Role needs Rust", resume_text="Built with React")
    assert capped[0].score == 1


def test_weighted_total_scales_to_0_100():
    dims = [
        DimensionScore(dimension="stack fit", score=5, jd_line="x", resume_line="y"),
        DimensionScore(dimension="eng-culture signals", score=0),
    ]
    assert weighted_total(dims, _SCORED) == 60


def test_weighted_total_ignores_a_dimension_not_in_criteria():
    dims = [DimensionScore(dimension="not a real axis", score=5)]
    assert weighted_total(dims, _SCORED) == 0


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
