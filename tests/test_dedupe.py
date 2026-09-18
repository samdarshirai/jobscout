import sqlite3

from jobscout.dedupe import SamePostingResult, same_posting_cached
from jobscout.storage.db import init_db

_A = {"id": "ats:Acme:1", "company": "Acme", "title": "Engineer", "city": "Berlin", "jd_text": "JD A"}
_B = {"id": "adzuna:42", "company": "Acme", "title": "Engineer", "city": "Berlin", "jd_text": "JD B"}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_same_posting_cached_asks_the_llm_once_then_reuses_the_cache(monkeypatch):
    calls = []

    def _fake_ask(a, b):
        calls.append((a["id"], b["id"]))
        return True

    monkeypatch.setattr("jobscout.dedupe._ask_llm_same_posting", _fake_ask)
    conn = _conn()

    first = same_posting_cached(conn, _A, _B)
    second = same_posting_cached(conn, _A, _B)

    assert first is True
    assert second is True
    assert len(calls) == 1
    conn.close()


def test_same_posting_cached_is_order_independent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "jobscout.dedupe._ask_llm_same_posting", lambda a, b: calls.append(1) or False
    )
    conn = _conn()

    same_posting_cached(conn, _A, _B)
    same_posting_cached(conn, _B, _A)

    assert len(calls) == 1
    conn.close()


def test_ask_llm_same_posting_calls_structured_llm(monkeypatch):
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return SamePostingResult(same=True)

    class _FakeLLM:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.dedupe.get_llm", lambda: _FakeLLM())

    from jobscout.dedupe import _ask_llm_same_posting

    result = _ask_llm_same_posting(_A, _B)

    assert result is True
    assert captured["schema"] is SamePostingResult
    assert "JD A" in captured["prompt"]
    assert "JD B" in captured["prompt"]
