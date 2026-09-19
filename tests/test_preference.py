import numpy as np

from jobscout.preference import (
    cosine_similarity,
    embed,
    generate_preference_summary,
    record_preference_summary,
    should_regenerate_summary,
    top_k_similar_verdicts,
)
from jobscout.storage.db import get_connection, init_db


def _conn(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    return conn


def _vec_bytes(*vals: float) -> bytes:
    return np.asarray(vals, dtype=np.float32).tobytes()


def _seed_posting_and_feedback(conn, posting_id, title, company, verdict, reason, embedding):
    conn.execute(
        "INSERT INTO app_posting (id, source, company, title) VALUES (?, 'ats:X', ?, ?)",
        (posting_id, company, title),
    )
    conn.execute(
        "INSERT INTO app_feedback (posting_id, verdict, reason, embedding) VALUES (?, ?, ?, ?)",
        (posting_id, verdict, reason, embedding),
    )
    conn.commit()


class _FakeStructuredLLM:
    def __init__(self, response):
        self.response = response
        self.prompt = None

    def invoke(self, prompt):
        self.prompt = prompt
        return self.response


class _FakeLLM:
    def __init__(self, response):
        self.structured = _FakeStructuredLLM(response)

    def with_structured_output(self, schema):
        self.schema = schema
        return self.structured


# ---- cosine_similarity -----------------------------------------------------


def test_cosine_similarity_identical_vectors_is_one():
    v = _vec_bytes(1.0, 2.0, 3.0)
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal_vectors_is_zero():
    a = _vec_bytes(1.0, 0.0)
    b = _vec_bytes(0.0, 1.0)
    assert abs(cosine_similarity(a, b)) < 1e-6


def test_cosine_similarity_opposite_vectors_is_negative_one():
    a = _vec_bytes(1.0, 0.0)
    b = _vec_bytes(-1.0, 0.0)
    assert cosine_similarity(a, b) == -1.0


def test_cosine_similarity_zero_vector_is_defensively_zero():
    a = _vec_bytes(0.0, 0.0)
    b = _vec_bytes(1.0, 1.0)
    assert cosine_similarity(a, b) == 0.0


# ---- top_k_similar_verdicts -------------------------------------------------


def test_top_k_similar_verdicts_ranks_by_similarity(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _seed_posting_and_feedback(
        conn, "p1", "Backend Engineer", "Acme", "up", "loved the stack", _vec_bytes(1.0, 0.0)
    )
    _seed_posting_and_feedback(
        conn, "p2", "Sales Rep", "Acme", "down", "not technical enough", _vec_bytes(0.0, 1.0)
    )
    monkeypatch.setattr("jobscout.preference.embed", lambda text: _vec_bytes(1.0, 0.0))

    results = top_k_similar_verdicts(conn, "some JD text", k=5)

    assert len(results) == 2
    assert results[0]["title"] == "Backend Engineer"
    assert results[0]["verdict"] == "up"
    assert results[0]["similarity"] > results[1]["similarity"]


def test_top_k_similar_verdicts_respects_k(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    for i in range(3):
        _seed_posting_and_feedback(
            conn, f"p{i}", f"Role {i}", "Acme", "up", None, _vec_bytes(1.0, float(i))
        )
    monkeypatch.setattr("jobscout.preference.embed", lambda text: _vec_bytes(1.0, 0.0))

    results = top_k_similar_verdicts(conn, "JD", k=2)

    assert len(results) == 2


def test_top_k_similar_verdicts_empty_when_no_embeddings(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO app_posting (id, source, company, title) VALUES ('p1', 'ats:X', 'Acme', 'Eng')"
    )
    conn.execute(
        "INSERT INTO app_feedback (posting_id, verdict, reason) VALUES ('p1', 'up', 'good fit')"
    )
    conn.commit()

    assert top_k_similar_verdicts(conn, "JD text") == []


def test_embed_returns_a_384_dim_float32_vector(tmp_path):
    vec = embed("senior backend engineer, python, distributed systems")
    arr = np.frombuffer(vec, dtype=np.float32)
    assert arr.shape == (384,)


# ---- generate_preference_summary -------------------------------------------


def test_generate_preference_summary_calls_the_llm_with_verdict_data(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _seed_posting_and_feedback(
        conn, "p1", "Backend Engineer", "Acme", "up", "great stack fit", None
    )
    fake = _FakeLLM(type("R", (), {"summary": "She likes backend roles at Acme-like companies."})())
    monkeypatch.setattr("jobscout.preference.get_llm", lambda: fake)

    result = generate_preference_summary(conn)

    assert result == "She likes backend roles at Acme-like companies."
    assert "Backend Engineer" in fake.structured.prompt
    assert "great stack fit" in fake.structured.prompt


def test_generate_preference_summary_skips_llm_when_no_verdicts(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    called = []
    monkeypatch.setattr(
        "jobscout.preference.get_llm", lambda: called.append(True) or _FakeLLM(None)
    )

    result = generate_preference_summary(conn)

    assert result == "No Verdicts yet — nothing to summarize."
    assert called == []


# ---- should_regenerate_summary ---------------------------------------------


def test_should_regenerate_summary_false_when_no_summary_and_no_verdicts(tmp_path):
    conn = _conn(tmp_path)
    assert should_regenerate_summary(conn) is False


def test_should_regenerate_summary_true_when_no_summary_but_has_verdicts(tmp_path):
    conn = _conn(tmp_path)
    _seed_posting_and_feedback(conn, "p1", "Eng", "Acme", "up", None, None)
    assert should_regenerate_summary(conn) is True


def test_should_regenerate_summary_false_under_five_new_verdicts(tmp_path):
    conn = _conn(tmp_path)
    for i in range(3):
        _seed_posting_and_feedback(conn, f"p{i}", "Eng", "Acme", "up", None, None)
    record_preference_summary(conn, "summary v1")
    _seed_posting_and_feedback(conn, "p3", "Eng", "Acme", "up", None, None)

    assert should_regenerate_summary(conn) is False


def test_should_regenerate_summary_true_at_five_new_verdicts(tmp_path):
    conn = _conn(tmp_path)
    for i in range(3):
        _seed_posting_and_feedback(conn, f"p{i}", "Eng", "Acme", "up", None, None)
    record_preference_summary(conn, "summary v1")
    for i in range(3, 8):
        _seed_posting_and_feedback(conn, f"p{i}", "Eng", "Acme", "up", None, None)

    assert should_regenerate_summary(conn) is True


# ---- record_preference_summary ---------------------------------------------


def test_record_preference_summary_versions_across_successive_calls(tmp_path):
    conn = _conn(tmp_path)
    _seed_posting_and_feedback(conn, "p1", "Eng", "Acme", "up", None, None)

    v1 = record_preference_summary(conn, "first summary")
    _seed_posting_and_feedback(conn, "p2", "Eng", "Acme", "down", None, None)
    v2 = record_preference_summary(conn, "second summary")

    assert v1 == 1
    assert v2 == 2
    rows = conn.execute(
        "SELECT version, summary, verdict_count_at_write FROM app_preference_summary ORDER BY version"
    ).fetchall()
    assert rows[0]["verdict_count_at_write"] == 1
    assert rows[1]["verdict_count_at_write"] == 2
    assert rows[1]["summary"] == "second summary"
