"""Retry/backoff for per-posting LLM calls (DESIGN §17; build-plan unit 30).

CONTEXT.md: Error / Dead. A model API 429 gets exponential backoff, up to
`max_retries` attempts, before the caller gives up and marks the Posting
`error` (DESIGN §17) — an intra-call concept, NOT the separate "3
consecutive Polls -> dead" cross-Poll counter tracked in
`app_posting.error_count` (see `graph/poll.py`'s `knockout` node and
`score.py`'s `score_postings`, which apply that rule using this module's
result). Two different "3"s, not to be conflated.
"""

import time
from typing import Any, Callable

import openai


def with_retry(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> Any:
    """Call `fn(*args, **kwargs)`, retrying only on `openai.RateLimitError`
    (a 429) with exponential backoff, up to `max_retries` total attempts.
    Any other exception propagates immediately — a malformed JD,
    extraction failure, or model 500 is `error` on the first failure, not
    retried within a Poll (DESIGN §17)."""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except openai.RateLimitError:
            if attempt == max_retries - 1:
                raise
            sleep_fn(2**attempt)
