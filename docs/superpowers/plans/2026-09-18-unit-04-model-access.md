# Unit 4: Model Access Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One module — `jobscout.llm` — is the only thing that builds an LLM client. It exposes `get_llm()`, a factory for a single `ChatOpenAI` pointed at OpenRouter, with provider routing locked to deny data-collecting/training providers on every call, and a model id that is always a plain string (env var, or a call-time override) — never a code change.

**Architecture:** `get_llm(model: str | None = None) -> ChatOpenAI` reads `OPENROUTER_API_KEY` (required), `OPENROUTER_BASE_URL` (defaults to OpenRouter's endpoint), and `OPENROUTER_MODEL` (defaults to a pinned model id) from the environment, and returns a `langchain_openai.ChatOpenAI` — OpenRouter is OpenAI-API-compatible, so this is a normal `ChatOpenAI` pointed at a different `base_url`, with OpenRouter's `provider.data_collection: "deny"` routing flag passed through `extra_body` (the OpenAI SDK's escape hatch for non-standard request-body fields). `ChatOpenAI` is a LangChain `BaseChatModel`: it is what later units' graph nodes need for `with_structured_output()` (unit 8's `plan_search`, unit 23's `draft_letter`) and `bind_tools()` / `create_react_agent` (unit 12's Score Sub-Agent). No LLM call happens in this unit — this only builds the client factory. Wiring `get_llm()` into a graph node is out of scope; the first real call is unit 8.

**Tech Stack:** Python ≥3.12, `langchain-openai` (new dependency — pulls in `langchain-core`, already present transitively via `langgraph`), stdlib `os`, pytest + `monkeypatch`.

**Spec:** `docs/2026-09-01-build-plan.md` unit 4; `docs/DESIGN.md` §2 (data handling: OpenRouter provider routing excludes training/retention providers), §13 (models & cost: one model everywhere via OpenRouter, model id is a single config string, bake-off candidates, exact ids pinned at implementation time from OpenRouter's live catalogue — the idea doc's names are not verified). No `CONTEXT.md` vocabulary entry needed — this is a technical seam, not a domain concept.

## Global Constraints

- **One base URL + one API key; the model id is a single config string** (build-plan unit 4). Switching model = changing that string — never a code change.
- **Provider routing must deny data collection on every call** (DESIGN §2, §13): the request body carries `"provider": {"data_collection": "deny"}`. Verified 2026-09-18 against OpenRouter's live provider-routing docs — this is a top-level request-body field, passed through the OpenAI SDK's `extra_body` kwarg (`extra_body={"provider": {"data_collection": "deny"}}`), not a header and not nested under `model_kwargs`.
- **Default model id is pinned from OpenRouter's live catalogue, not guessed** (build-plan unit 4 open question: "the idea doc's 'V3.2 / V4 Flash' names are not verified"). Verified 2026-09-18 via `GET https://openrouter.ai/api/v1/models` (no auth required, public catalogue): `deepseek/deepseek-v4.1-flash` exists, costs $0.15/M prompt + $0.60/M completion tokens, and its `supported_parameters` list includes `tools`, `tool_choice`, and `structured_outputs` — required by later units 8 and 12. It also matches DESIGN §13's bake-off candidate list (DeepSeek is named first). If this model id has been retired by the time this task runs, re-verify against the same live endpoint and use whatever cheap DeepSeek/Qwen/GLM/Kimi-class id is current — do not guess a name from training data.
- **One new dependency: `langchain-openai`** (pin `>=1.6`, matching this project's existing `>=` pinning style). No other new dependencies. No changes to `schema.sql` — this unit touches no storage.
- **Tests make no real network calls and require no real API key.** `ChatOpenAI(...)` does not contact OpenRouter at construction time (verified empirically) — tests assert on the constructed client's attributes (`model_name`, `openai_api_base`, `extra_body`, `openai_api_key`), never on a live completion.
- Python ≥3.12; `uv` packaging; pytest; TDD (write failing test, see it fail, implement, see it pass, commit).

## Interfaces Produced (later units consume these)

- `jobscout.llm.DEFAULT_BASE_URL: str` — `"https://openrouter.ai/api/v1"`.
- `jobscout.llm.DEFAULT_MODEL: str` — `"deepseek/deepseek-v4.1-flash"`.
- `jobscout.llm.get_llm(model: str | None = None) -> ChatOpenAI` — `model` overrides `OPENROUTER_MODEL` overrides `DEFAULT_MODEL`. Raises `RuntimeError` if `OPENROUTER_API_KEY` is unset. Units 8, 12, 23 call this from inside graph nodes to get a `BaseChatModel` for structured calls / tool binding; they never construct `ChatOpenAI` directly.

---

## File Structure

- `src/jobscout/llm.py` — `DEFAULT_BASE_URL`, `DEFAULT_MODEL`, `get_llm()`. One responsibility: the one place an LLM client gets built.
- `tests/test_llm.py` — client-construction behavior: default model/routing, model swap via env var, model swap via call-time arg, base-url override, missing-key error.
- Modify: `pyproject.toml` — add `langchain-openai>=1.6` to `dependencies`.

(No changes to `.env.example` — `OPENROUTER_API_KEY` and `OPENROUTER_BASE_URL` are already listed there from unit 1, and `tests/test_repo_hygiene.py::test_env_example_lists_every_secret` already asserts `OPENROUTER_API_KEY` is present.)

---

## Task 1: `get_llm()` client factory

**Files:**
- Modify: `pyproject.toml`
- Create: `src/jobscout/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: nothing from earlier units — this is a leaf module.
- Produces: `DEFAULT_BASE_URL`, `DEFAULT_MODEL`, `get_llm` (signatures above).

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`'s `dependencies` list to add `langchain-openai>=1.6` after the `langgraph-checkpoint-sqlite` line:

```toml
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "langgraph>=1.2.11",
    "langgraph-checkpoint-sqlite>=3.1.1",
    "langchain-openai>=1.6",
]
```

Run: `uv sync`
Expected: resolves and installs `langchain-openai` (and its own transitive deps) with no version conflicts against the already-pinned `langgraph`/`langgraph-checkpoint-sqlite`.

- [ ] **Step 2: Write the failing tests**

`tests/test_llm.py`:

```python
import pytest

from jobscout.llm import DEFAULT_BASE_URL, DEFAULT_MODEL, get_llm


def test_get_llm_uses_default_model_base_url_and_deny_routing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    llm = get_llm()

    assert llm.model_name == DEFAULT_MODEL
    assert llm.openai_api_base == DEFAULT_BASE_URL
    assert llm.extra_body == {"provider": {"data_collection": "deny"}}
    assert llm.openai_api_key.get_secret_value() == "test-key"


def test_get_llm_model_env_var_switches_model_with_no_code_change(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")

    llm = get_llm()

    assert llm.model_name == "anthropic/claude-haiku-4.5"


def test_get_llm_model_arg_overrides_env_var(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")

    llm = get_llm(model="qwen/qwen3.8-flash")

    assert llm.model_name == "qwen/qwen3.8-flash"


def test_get_llm_base_url_env_var_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test/v1")

    llm = get_llm()

    assert llm.openai_api_base == "https://example.test/v1"


def test_get_llm_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        get_llm()
```

- [ ] **Step 3: Run the tests, verify they fail**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.llm'`.

- [ ] **Step 4: Write `src/jobscout/llm.py`**

```python
"""OpenRouter model access (DESIGN §13).

The one place an LLM client is built. Provider routing denies training /
data-collecting providers on every call (DESIGN §2, §13). No LLM call
happens in this module — the first real call is unit 8's `plan_search`.
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
        model=model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
        api_key=api_key,
        base_url=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        extra_body={"provider": {"data_collection": "deny"}},
    )
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (31 existing + 5 here = 36).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/jobscout/llm.py tests/test_llm.py
git commit -m "feat: OpenRouter model-access layer with deny-data-collection routing"
```

---

## Self-Review

**1. Spec coverage (build-plan unit 4 acceptance criteria):**

| Criterion | Task / test |
|---|---|
| One base URL + key; the model id is a single config string (§13) | `get_llm()` reads `OPENROUTER_BASE_URL`/`OPENROUTER_API_KEY`/`OPENROUTER_MODEL` from env, one call site; `test_get_llm_uses_default_model_base_url_and_deny_routing` |
| OpenRouter provider routing excludes training/no-zero-retention providers — `provider.data_collection: "deny"` (§2) | `extra_body={"provider": {"data_collection": "deny"}}` in every `get_llm()` call; asserted directly in `test_get_llm_uses_default_model_base_url_and_deny_routing` |
| Switching model = changing the config string, no code change (§13) | `OPENROUTER_MODEL` env var and `model` arg both swap `model_name` with zero code edits; `test_get_llm_model_env_var_switches_model_with_no_code_change`, `test_get_llm_model_arg_overrides_env_var` |
| Exact model ids pinned at implementation time from OpenRouter's live catalogue, not the idea doc's unverified names (open question) | `DEFAULT_MODEL` verified 2026-09-18 against `GET https://openrouter.ai/api/v1/models`; rationale and re-verification instruction recorded in the Global Constraints and the module docstring/comment |

No gaps. Wiring `get_llm()` into an actual graph node (unit 8's `plan_search`) is correctly out of scope — build-plan unit 4 only requires the client to exist and be configured correctly.

**2. Placeholder scan:** No "TBD" / "add error handling" / bare "write tests". Every step has real code. The `RuntimeError` on a missing API key is real validation at a trust boundary (an external secret), not defensive noise.

**3. Type consistency:** `DEFAULT_BASE_URL`, `DEFAULT_MODEL`, `get_llm` — used identically in the Interfaces block, the source, and the test file. `get_llm`'s return type (`ChatOpenAI`) and its `model_name` / `openai_api_base` / `extra_body` / `openai_api_key` attributes were verified empirically against installed `langchain-openai` 1.6.2 before writing this plan (pydantic field introspection + a live construction check), not assumed from memory.

**4. Known risk carried into the plan, not hidden:** `ChatOpenAI`'s exact attribute names (`model_name` vs `model`, `openai_api_base` vs `base_url`, `extra_body`'s shape) are library-version-sensitive, same category of risk unit 3 hit with LangGraph. Unlike unit 3, this plan does not need a runtime verification step inside the task — the attribute names were already confirmed empirically while writing this plan (see Global Constraints), and the pinned `langchain-openai>=1.6` floor matches the version checked. If a future re-run installs a materially newer major version and a test fails on an attribute name, that is a real signal to re-check the installed API, not to loosen the test.

**Notes for later units:**
- Unit 8 (`plan_search`) is the first real caller: `get_llm().with_structured_output(SomeSchema)` inside the node, plus Spend logging (§13) and a scrubbed trace (§14) — neither of which exists yet; unit 8 adds them.
- Unit 12 (Score Sub-Agent, ReAct subgraph) will likely use `get_llm()` with `create_react_agent` or manual `bind_tools()` — `get_llm()` already returns a plain `BaseChatModel`, no adapter needed.
- Units 33-39 (bake-off, ablations) are the reason `get_llm(model=...)` takes a call-time override in addition to the env var — running several models in one eval process needs per-call swapping, not just a process-wide env var.
