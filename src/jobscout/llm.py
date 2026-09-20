"""OpenRouter model access (DESIGN §13).

The one place an LLM client is built. Provider routing denies training /
data-collecting providers on every call (DESIGN §2, §13). No LLM call
happens in this module — the first real call is unit 8's `plan_search`.
The `data_collection: deny` guarantee requires `OPENROUTER_BASE_URL` to point
at OpenRouter; repointing it elsewhere (e.g., for local testing) makes that
field meaningless, and DESIGN §2's data-handling promise stops applying.
"""

import os

from langchain_openai import ChatOpenAI

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# Pinned from OpenRouter's live catalogue 2026-09-18 (build-plan unit 4 open
# question — the idea doc's model names were unverified). Cheap, matches
# DESIGN §13's bake-off candidate list, supports tools + structured_outputs.
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
# The OpenAI SDK's own default is 600s with no explicit timeout — a stalled
# request then blocks a Poll for up to 10 minutes with no sign of life.
# 60s is generous for a structured-output call; retry.py's with_retry only
# catches 429s, not a hang, so this is the actual backstop against one.
DEFAULT_TIMEOUT_S = 60.0
# `request_timeout` is a per-chunk READ timeout, not a total-call-duration
# cap — a response that trickles in slowly (or a model that rambles/loops
# without stopping) never trips it. Confirmed live: a call sat for 7+
# minutes on an established connection with ~0 new bytes/10s, well past
# 60s, because SOME byte kept arriving just often enough to reset the read
# clock. Capping max_tokens bounds worst-case generation length directly.
# DEFAULT_MODEL is a REASONING model (OpenRouter's usage reports
# `reasoning_tokens`, spent BEFORE any visible answer) — confirmed live
# that a 4096 cap gets entirely consumed by reasoning alone on an ordinary
# structured call (search_plan: 4096/4096 reasoning tokens, zero left for
# the actual answer -> LengthFinishReasonError). 16384 leaves real headroom
# for reasoning + a genuinely large answer (search_plan's 30
# queries + 28 companies) while still being a real, finite cap, not
# unbounded — still cheap per call against the $20 total (§13).
DEFAULT_MAX_TOKENS = 16384
# The reasoning judge (build-plan unit 34, DESIGN §13/§15) must be a
# "cheap-frontier anchor" — Haiku/GPT-mini class, deliberately a
# different lab than DEFAULT_MODEL so it isn't judging the Score
# Sub-Agent's own family. Pinned from OpenRouter's live catalogue
# 2026-09-19 (same "verify, don't assume the name" rule as DEFAULT_MODEL).
JUDGE_MODEL = "anthropic/claude-haiku-4.5"
# Bake-Off candidates (DESIGN §13, §15; build-plan unit 36) — cheap chat
# models from labs other than DEFAULT_MODEL's, plus JUDGE_MODEL reused as
# the cheap-frontier-anchor correlation ceiling. Pinned from OpenRouter's
# live catalogue 2026-09-19, same rule as above.
#
# DESIGN §13's candidate list also names GLM (z-ai/glm-4.6) — dropped here.
# Confirmed live, 3/3 real score_posting attempts: it returns blank content
# for create_react_agent's structured final answer over OpenRouter (a
# ValueError or a Pydantic JSON-parse error, no usable ScoreResult either
# way) — a genuine integration failure with this model, not a flake (the
# frontier-anchor model hit the same blank-content shape once but then
# succeeded twice on retry; GLM never did).
BAKE_OFF_MODELS = {
    "deepseek-chat": "deepseek/deepseek-chat",
    "qwen": "qwen/qwen-2.5-72b-instruct",
    "kimi": "moonshotai/kimi-k2-0905",
    "frontier-anchor": JUDGE_MODEL,
}


def get_llm(model: str | None = None) -> ChatOpenAI:
    """A `ChatOpenAI` pointed at OpenRouter, provider routing denying data
    collection. Precedence: `model` arg > `OPENROUTER_MODEL` env var >
    `DEFAULT_MODEL` — switching models is always a string, never code.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set — see .env.example"
        )
    return ChatOpenAI(
        model=model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL,
        api_key=api_key,
        base_url=os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL,
        extra_body={"provider": {"data_collection": "deny"}},
        timeout=DEFAULT_TIMEOUT_S,
        max_tokens=DEFAULT_MAX_TOKENS,
    )
