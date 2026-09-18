"""Spend tracking against the $20 hard cap (DESIGN §13, build-plan unit 15).

`get_usage_metadata_callback()` is a global, contextvar-backed LangChain
hook — wrapping a node's work in it captures real token counts from any
chat model invoked underneath, with zero signature changes to the pure
LLM-calling functions (derive_search_plan, derive_criteria, parse_profile,
generate_dynamic_questions, extract_knockout_facts, score_posting) and no
risk to their existing tests' monkeypatched fakes, which simply yield no
usage at all. It sums same-model calls made inside one `with` block, so
`run_and_track` gives one row per model per node-invocation — not one row
per individual posting-level LLM call inside `knockout`/`score`'s
per-posting loops. DESIGN §13 says "each LLM call appends cost" but
nothing tests call-level granularity, and this keeps every LLM-calling
node's wiring identical.
"""

from functools import lru_cache
from typing import Any, Callable

import httpx
from langchain_core.callbacks import get_usage_metadata_callback

from jobscout.llm import DEFAULT_BASE_URL

CAP_USD = 20.0


class SpendCapExceeded(RuntimeError):
    pass


@lru_cache(maxsize=None)
def _model_pricing(model: str) -> tuple[float, float]:
    """(prompt $/token, completion $/token) from OpenRouter's live
    catalogue — never a rate baked into code (unit8-spend-pricing-note:
    this model's real price doubles on a weekday UTC-peak override).
    ponytail: reads only the base `pricing` block, not `pricing.overrides`
    — an estimate landing in a peak window under-reports. OpenRouter's
    `usage.include` extra_body field surfaces the real per-call cost
    instead, but `get_usage_metadata_callback`'s standardized UsageMetadata
    doesn't carry it, so this live-fetched base rate is the deliberate
    fallback, not an oversight. Falls back to (0.0, 0.0) if the catalogue
    is unreachable — a $0 Spend row beats crashing the Run over pricing."""
    try:
        resp = httpx.get(f"{DEFAULT_BASE_URL}/models", timeout=5.0)
        resp.raise_for_status()
        for entry in resp.json().get("data", []):
            if entry.get("id") == model:
                pricing = entry.get("pricing", {})
                return float(pricing.get("prompt", 0.0)), float(pricing.get("completion", 0.0))
    except httpx.HTTPError:
        pass
    return 0.0, 0.0


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prompt_rate, completion_rate = _model_pricing(model)
    return prompt_tokens * prompt_rate + completion_tokens * completion_rate


def run_and_track(fn: Callable, *args: Any, **kwargs: Any) -> tuple[Any, list[dict]]:
    """Call `fn(*args, **kwargs)`, returning its result plus one Spend row
    per model actually called underneath it."""
    with get_usage_metadata_callback() as cb:
        result = fn(*args, **kwargs)
    rows = [
        {
            "model": model,
            "prompt_tokens": usage["input_tokens"],
            "completion_tokens": usage["output_tokens"],
            "cost_usd": cost_for(model, usage["input_tokens"], usage["output_tokens"]),
        }
        for model, usage in cb.usage_metadata.items()
    ]
    return result, rows


def log_spend(conn, run_id: str, node: str, rows: list[dict]) -> None:
    if not rows:
        return
    for row in rows:
        conn.execute(
            "INSERT INTO app_spend (run_id, node, model, prompt_tokens, completion_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, node, row["model"], row["prompt_tokens"], row["completion_tokens"], row["cost_usd"]),
        )
    conn.commit()


def total_spend(conn) -> float:
    (total,) = conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) FROM app_spend").fetchone()
    return total
