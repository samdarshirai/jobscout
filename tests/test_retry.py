import openai
import pytest

from jobscout.retry import with_retry


def _rate_limit_error():
    return openai.RateLimitError(
        "rate limited", response=_FakeResponse(), body=None
    )


class _FakeResponse:
    status_code = 429
    headers = {}
    request = None


def test_with_retry_succeeds_first_try_with_no_retry():
    calls = []

    def fn(x):
        calls.append(x)
        return x * 2

    sleeps = []
    result = with_retry(fn, 3, sleep_fn=sleeps.append)

    assert result == 6
    assert calls == [3]
    assert sleeps == []


def test_with_retry_retries_on_rate_limit_then_succeeds():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _rate_limit_error()
        return "ok"

    sleeps = []
    result = with_retry(fn, sleep_fn=sleeps.append)

    assert result == "ok"
    assert attempts["n"] == 3
    assert sleeps == [1, 2]  # 2**0, 2**1 between the 3 attempts


def test_with_retry_exhausts_retries_and_reraises_rate_limit_error():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise _rate_limit_error()

    sleeps = []
    with pytest.raises(openai.RateLimitError):
        with_retry(fn, max_retries=3, sleep_fn=sleeps.append)

    assert attempts["n"] == 3
    assert sleeps == [1, 2]


def test_with_retry_does_not_retry_a_non_rate_limit_exception():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise ValueError("malformed JD")

    sleeps = []
    with pytest.raises(ValueError, match="malformed JD"):
        with_retry(fn, sleep_fn=sleeps.append)

    assert attempts["n"] == 1
    assert sleeps == []
