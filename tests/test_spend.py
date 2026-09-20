import sqlite3

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from jobscout.spend import _model_pricing, cost_for, log_spend, run_and_track, total_spend
from jobscout.storage.db import init_db


class _FakeUsageModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake-usage"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = AIMessage(
            content="hi",
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            response_metadata={"model_name": "fake/model-x"},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_model_pricing_returns_zero_on_network_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise httpx.HTTPError("no network")

    monkeypatch.setattr("jobscout.spend.httpx.get", _boom)
    monkeypatch.setattr("jobscout.spend._pricing_cache", {})
    assert _model_pricing("some/model") == (0.0, 0.0)


def test_model_pricing_does_not_cache_a_network_failure(monkeypatch):
    """Confirmed live: caching the (0.0, 0.0) fallback let one transient
    network error zero every Spend row for the rest of the process. A
    failure must be retried on the next call, not remembered forever."""
    import jobscout.spend as spend_module

    calls = {"n": 0}

    def _flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.HTTPError("transient")
        req = httpx.Request("GET", "https://example.test/models")
        return httpx.Response(
            200, json={"data": [{"id": "m", "pricing": {"prompt": "0.001", "completion": "0.002"}}]}, request=req
        )

    monkeypatch.setattr("jobscout.spend.httpx.get", _flaky)
    monkeypatch.setattr(spend_module, "_pricing_cache", {})

    assert _model_pricing("m") == (0.0, 0.0)  # first call: transient failure, not cached
    assert _model_pricing("m") == (0.001, 0.002)  # second call: retried, succeeds


def test_cost_for_multiplies_tokens_by_rate(monkeypatch):
    monkeypatch.setattr("jobscout.spend._model_pricing", lambda model: (0.001, 0.002))
    assert cost_for("some/model", prompt_tokens=100, completion_tokens=50) == pytest.approx(0.2)


def test_run_and_track_returns_no_rows_for_a_plain_function():
    result, rows = run_and_track(lambda x: x + 1, 41)
    assert result == 42
    assert rows == []


def test_run_and_track_captures_a_real_chat_model_call(monkeypatch):
    monkeypatch.setattr("jobscout.spend._model_pricing", lambda model: (0.001, 0.002))
    llm = _FakeUsageModel()

    result, rows = run_and_track(llm.invoke, "hi")

    assert result.content == "hi"
    assert rows == [
        {
            "model": "fake/model-x",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cost_usd": pytest.approx(0.2),
        }
    ]


def test_log_spend_is_a_noop_for_an_empty_list():
    conn = _conn()
    log_spend(conn, "r1", "score", [])
    assert conn.execute("SELECT * FROM app_spend").fetchall() == []
    conn.close()


def test_log_spend_inserts_one_row_per_call_and_total_spend_sums_them():
    conn = _conn()
    log_spend(
        conn,
        "r1",
        "score",
        [
            {"model": "a/model", "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 1.5},
            {"model": "b/model", "prompt_tokens": 20, "completion_tokens": 10, "cost_usd": 2.5},
        ],
    )
    rows = conn.execute("SELECT run_id, node, model, cost_usd FROM app_spend ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"run_id": "r1", "node": "score", "model": "a/model", "cost_usd": 1.5},
        {"run_id": "r1", "node": "score", "model": "b/model", "cost_usd": 2.5},
    ]
    assert total_spend(conn) == pytest.approx(4.0)
    conn.close()


def test_total_spend_is_zero_with_no_rows():
    conn = _conn()
    assert total_spend(conn) == 0.0
    conn.close()
