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
    )
