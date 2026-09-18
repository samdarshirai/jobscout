# Unit 7: Criteria Derivation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The onboard graph grows a fifth stage — an LLM derives a `Criteria` object (three buckets: `knockout`, `scored`, `learn`, CONTEXT.md) from the completed Profile. `CoreService` persists it as a versioned `app_criteria` row *and* writes a hand-editable `data/criteria.yaml` file (DESIGN §12: "criteria stays file-edited, no form-building"). A hand-edit gets picked back up and version-bumped via `sync_criteria_from_file`. `jobscout onboard --reset`'s primitive, `reset_onboarding`, tags existing profile/criteria rows `pre_reset` and clears the onboard thread's checkpoint history so a fresh `run_onboarding` genuinely restarts — this is also the fix for the re-run gap unit 5/6 deferred ("a genuinely repeated full onboarding is unit 7's job to guard").

**Architecture:** A new leaf module, `jobscout.criteria`, owns the `Criteria`/`KnockoutRule`/`ScoredDimension` schema, the LLM derivation call, and YAML file I/O — same boundary as `jobscout.resume`/`jobscout.questions` (no graph, no store). The onboard graph gains one more node, `draft_criteria`, after `dynamic_questions_gate`, before `END` — no new gate, criteria derivation doesn't pause for approval in this unit (DESIGN's Approval Gates, §10, are search-plan/outbound-letter/scope-expansion; onboarding criteria isn't one of them — it's *hand-editable after the fact* via the file, not gated during onboarding). `CoreService.__init__` gains an optional `criteria_path` parameter (additive, mirrors `db_path`) so tests don't write into the real `data/` directory. `_save_profile` and a new `_save_criteria` both fire once, together, when the onboard graph reaches `END` — verified in this plan (see Global Constraints) that this still happens exactly once per completed flow, including after unit 6's already-completed-thread guard.

**Tech Stack:** Python ≥3.12, `pyyaml` (new *direct* dependency — already present transitively via `langchain-core`/friends, verified installed at 6.0.3; pinning it directly rather than relying on someone else's transitive pin), `jobscout.llm` (unit 4), `langgraph` (units 2-3, including `SqliteSaver.delete_thread`, verified against the installed version — see Global Constraints), stdlib `json`, pytest + `monkeypatch`.

**Spec:** `docs/2026-09-01-build-plan.md` unit 7; `docs/DESIGN.md` §5 (Storage: "An LLM then derives an editable `criteria` object with three buckets... `--reset` wipes `profile` + `criteria` only"), §6 (Scoring: the four fixed Scored Dimensions, the four fixed base Knockout axes), §10 (Scope-expansion: "approved → `criteria` version bumped" — confirms `app_criteria` is the versioned source of truth, not the YAML file alone), §12 ("criteria stays file-edited — no form-building"). Vocabulary: `CONTEXT.md` Criteria/Knockout/Scored Dimension definitions, used verbatim in this plan's field descriptions.

## Global Constraints

- **CoreService remains the only thing that touches the graph or the store** (unit 3, permanent). `jobscout.criteria` does LLM calls and plain file I/O only — no `sqlite3` import, no connection. (File I/O for the hand-editable YAML is not "the store"; it's the same category of I/O `jobscout.resume.extract_resume_text` already does for the PDF.)
- **`CoreService.__init__`'s new `criteria_path: Path | None = None` parameter is additive**, not a breaking change to unit 3's `__init__(self, db_path: Path = DEFAULT_DB_PATH)` signature — existing callers (including `get_service()`, unchanged) are unaffected. `run_onboarding`/`resume_onboarding`'s public signatures stay exactly as unit 3/5/6 left them.
- **A ruling on `--reset`'s scope, made now rather than guessed at implementation time:** DESIGN §5 says `--reset` "wipes `profile` + `criteria` only. Posting / score / feedback history is kept but tagged pre-reset." The committed `schema.sql` (unit 2) only has a `pre_reset` column on `app_profile` and `app_criteria` — `app_posting`, `app_score`, `app_feedback` have no such column. **Ruling: `reset_onboarding` tags existing `app_profile`/`app_criteria` rows `pre_reset = 1` (an `UPDATE`, not a `DELETE` — matches the schema's soft-tag design, not a literal "wipe") and does not touch `app_posting`/`app_score`/`app_feedback` at all**, since no column exists there to tag them and adding one is a schema migration outside this unit's scope (`schema.sql` changes are explicitly excluded below). If a future eval unit genuinely needs Posting/Score/Feedback split by reset-generation, that unit adds the migration — this plan does not silently guess at one.
- **`SqliteSaver.delete_thread(thread_id)` genuinely clears checkpoint history** — verified empirically during plan-writing: after `delete_thread`, `get_state(cfg).created_at` is `None` (same signal `resume_run`/`resume_onboarding`'s "no run in progress" guards already check), and a fresh `invoke` restarts cleanly from `START`. `reset_onboarding` calls this on `_ONBOARD_THREAD` after tagging the SQLite rows.
- **`draft_criteria` is a plain compute node, not a gate.** It runs automatically right after `dynamic_questions_gate` resolves, in the same `invoke`/`resume` call — no third `interrupt()`. Criteria review happens via the hand-editable file afterward, not a graph-level approval pause.
- **`_save_profile` and a new `_save_criteria` both fire together, exactly once, when the graph reaches `END`** — same "fires once per completed flow" invariant unit 6 established and then had to fix for the already-completed-thread case. This plan reuses that exact fix (`if not snap.next: return ...` before invoking) unchanged; `_save_criteria`'s call is gated by the same `if handle.status == "completed"` check as `_save_profile`, so the already-completed-thread guard protects both together, not just one.
- **`app_criteria`'s `data_json` and the YAML file are the same data, two representations.** SQLite is the versioned source of truth (DESIGN §10: "approved → `criteria` version bumped" — a scope-expansion approval, unit 27, bumps *the SQLite version*). The YAML file is the hand-editable copy; `sync_criteria_from_file` is how a hand-edit becomes a new SQLite version. Whichever one the CLI/web/Telegram surfaces show a human is out of scope here (unit 17/40/41).
- **No `schema.sql` changes** — `app_profile`/`app_criteria` (unit 2) already have every column this unit needs (`app_criteria`: `version`, `data_json`, `profile_version`, `pre_reset`, `created_at`).
- **One new direct dependency: `pyyaml>=6.0`.** No other new dependencies.
- **Tests make no real network/LLM calls, and do not write into the real `data/` directory** — `CoreService`'s test helper passes a `tmp_path`-scoped `criteria_path`, same pattern already established for `db_path`.
- Python ≥3.12; `uv` packaging; pytest; TDD (write failing test, see it fail, implement, see it pass, commit).

## Interfaces Produced (later units consume these)

- `jobscout.criteria.DEFAULT_CRITERIA_PATH: Path` — `Path("data/criteria.yaml")`.
- `jobscout.criteria.KnockoutRule` — `pydantic.BaseModel`, fields `axis: str`, `rule: str`.
- `jobscout.criteria.ScoredDimension` — `pydantic.BaseModel`, fields `dimension: str`, `weight: float`, `rubric: str`.
- `jobscout.criteria.Criteria` — `pydantic.BaseModel`, fields `knockout: list[KnockoutRule]`, `scored: list[ScoredDimension]` (exactly 4, `Field(min_length=4, max_length=4)`), `learn: list[str]`.
- `jobscout.criteria.derive_criteria(profile: dict) -> Criteria` — one structured LLM call.
- `jobscout.criteria.write_criteria_file(criteria: Criteria, path: Path = DEFAULT_CRITERIA_PATH) -> None`.
- `jobscout.criteria.read_criteria_file(path: Path = DEFAULT_CRITERIA_PATH) -> Criteria`.
- `jobscout.graph.onboard.OnboardState` gains a seventh field: `criteria_draft: dict`.
- `jobscout.graph.onboard.draft_criteria(state) -> dict` — new node, calls `jobscout.criteria.derive_criteria`, returns `{"criteria_draft": criteria.model_dump()}`.
- `jobscout.service.CoreService.__init__(self, db_path: Path = DEFAULT_DB_PATH, criteria_path: Path | None = None) -> None` — `criteria_path` defaults to `DEFAULT_CRITERIA_PATH` when `None`.
- `jobscout.service.CoreService.sync_criteria_from_file(self, path: Path | None = None) -> int | None` — reloads the hand-editable file; bumps a new `app_criteria` version if it differs from the latest, returns the new version number, or `None` if unchanged.
- `jobscout.service.CoreService.reset_onboarding(self) -> None` — tags existing `app_profile`/`app_criteria` rows `pre_reset = 1`, clears the onboard thread's checkpoint history.

---

## File Structure

- `src/jobscout/criteria.py` — `Criteria`, `KnockoutRule`, `ScoredDimension`, `derive_criteria`, `write_criteria_file`, `read_criteria_file`. One responsibility: the criteria object and its two persistence-adjacent representations (LLM-derived, file-editable).
- `tests/test_criteria.py` — YAML round-trip (real, unmocked file I/O); `derive_criteria`'s LLM wiring (mocked, no network).
- Modify: `src/jobscout/graph/onboard.py` — `OnboardState` gains `criteria_draft`; new `draft_criteria` node; `build_onboard_graph` wires it in.
- Modify: `tests/graph/test_skeleton_graphs.py` — the onboard test now also drives through `draft_criteria`.
- Modify: `src/jobscout/service.py` — `criteria_path` constructor param; `run_onboarding`/`resume_onboarding` seed the new state field and call a combined save step; new `_save_criteria`, `sync_criteria_from_file`, `reset_onboarding`.
- Modify: `tests/test_service.py` — `_svc` helper passes a `tmp_path`-scoped `criteria_path`; the completion test gains criteria assertions; new tests for `sync_criteria_from_file` (bump-on-change, no-op-when-unchanged) and `reset_onboarding` (tags rows, clears the thread so a fresh `run_onboarding` genuinely restarts).
- Modify: `pyproject.toml` — add `pyyaml>=6.0` to `dependencies`.

---

## Task 1: `jobscout.criteria` — schema, LLM derivation, file I/O

**Files:**
- Create: `src/jobscout/criteria.py`
- Test: `tests/test_criteria.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `jobscout.llm.get_llm` (unit 4).
- Produces: `Criteria`, `KnockoutRule`, `ScoredDimension`, `derive_criteria`, `write_criteria_file`, `read_criteria_file`, `DEFAULT_CRITERIA_PATH` (signatures above).

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`'s `dependencies` list, adding `pyyaml>=6.0` after `pypdf`:

```toml
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "langgraph>=1.2.11",
    "langgraph-checkpoint-sqlite>=3.1.1",
    "langchain-openai>=1.6",
    "pypdf>=6.0",
    "pyyaml>=6.0",
]
```

Run: `uv sync`
Expected: resolves with no conflicts (`pyyaml` is already present transitively).

- [ ] **Step 2: Write the failing tests**

`tests/test_criteria.py`:

```python
from jobscout.criteria import (
    Criteria,
    KnockoutRule,
    ScoredDimension,
    derive_criteria,
    read_criteria_file,
    write_criteria_file,
)


def _sample_criteria() -> Criteria:
    return Criteria(
        knockout=[KnockoutRule(axis="seniority_band", rule="senior or mid only")],
        scored=[
            ScoredDimension(dimension="stack fit", weight=0.4, rubric="5 = 3+ core tools"),
            ScoredDimension(dimension="domain/product interest", weight=0.2, rubric="soft axis"),
            ScoredDimension(dimension="scope & seniority signals", weight=0.2, rubric="ownership"),
            ScoredDimension(dimension="eng-culture signals", weight=0.2, rubric="testing/CI"),
        ],
        learn=["ambiguous stack preference"],
    )


def test_write_and_read_criteria_file_round_trips(tmp_path):
    path = tmp_path / "criteria.yaml"
    criteria = _sample_criteria()

    write_criteria_file(criteria, path)
    loaded = read_criteria_file(path)

    assert loaded == criteria
    assert "seniority_band" in path.read_text()


def test_derive_criteria_calls_structured_llm(monkeypatch):
    expected = _sample_criteria()
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.criteria.get_llm", lambda: _FakeLLM())

    result = derive_criteria(
        {"resume": {"stack": ["React"]}, "static_answers": {"work_mode": "remote"}}
    )

    assert result == expected
    assert captured["schema"] is Criteria
    assert "React" in captured["prompt"]
```

- [ ] **Step 3: Run the tests, verify they fail**

Run: `uv run pytest tests/test_criteria.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.criteria'`.

- [ ] **Step 4: Write `src/jobscout/criteria.py`**

```python
"""Criteria derivation and the hand-editable file (DESIGN §5, §10, §12).

`Criteria` is the editable object derived from the Profile, in three buckets
(CONTEXT.md: Criteria — knockout/scored/learn). Touches neither the graph
nor the store; CoreService (unit 3) persists the SQLite version and calls
write_criteria_file for the hand-editable copy.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from jobscout.llm import get_llm

DEFAULT_CRITERIA_PATH = Path("data/criteria.yaml")


class KnockoutRule(BaseModel):
    """A hard pass/fail axis (CONTEXT.md: Knockout). Any fail excludes the
    Posting outright, no Score."""

    axis: str = Field(
        description="Named axis, e.g. seniority_band, location, german_required"
    )
    rule: str = Field(description="Plain-language pass/fail rule for this axis")


class ScoredDimension(BaseModel):
    """One of the four fixed weighted 0-5 axes (CONTEXT.md: Scored Dimension)."""

    dimension: str = Field(
        description=(
            "One of: stack fit, domain/product interest, scope & seniority "
            "signals, eng-culture signals"
        )
    )
    weight: float = Field(
        description="Relative weight, 0-1, the four should sum to roughly 1.0"
    )
    rubric: str = Field(
        description=(
            "Concrete anchor for this candidate, e.g. 'stack 5 = JD names 3+ "
            "of React/TypeScript/GraphQL as core'"
        )
    )


class Criteria(BaseModel):
    """The editable object derived from the Profile (CONTEXT.md: Criteria)."""

    knockout: list[KnockoutRule]
    scored: list[ScoredDimension] = Field(min_length=4, max_length=4)
    learn: list[str] = Field(
        description="Soft signals with insufficient info yet, left to the Preference Feedback Loop"
    )


_CRITERIA_PROMPT = (
    "Derive job-search criteria from this candidate's Profile, in three buckets:\n\n"
    "knockout: hard pass/fail axes, each a rule a Posting either meets or fails "
    "outright. Always include seniority_band, location, work_auth_language, and "
    "german_required, derived from the static answers. Add hard_exclude_industries "
    "and must_have_stack if the candidate named any. Add salary_floor here only if "
    "the candidate said their salary floor is a hard knockout, not scored.\n\n"
    "scored: exactly these four dimensions, each with a 0-1 weight (roughly "
    "summing to 1.0) and a concrete rubric anchor tailored to this candidate's "
    "actual resume: 'stack fit', 'domain/product interest', 'scope & seniority "
    "signals', 'eng-culture signals'. If salary is scored (not knockout), fold it "
    "into whichever dimension's rubric text fits best rather than inventing a "
    "fifth dimension.\n\n"
    "learn: soft signals with insufficient information yet, left for the "
    "preference feedback loop to refine from future thumbs up/down (can be "
    "empty).\n\nProfile:\n{profile}"
)


def derive_criteria(profile: dict) -> Criteria:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes)."""
    structured_llm = get_llm().with_structured_output(Criteria)
    return structured_llm.invoke(_CRITERIA_PROMPT.format(profile=profile))


def write_criteria_file(criteria: Criteria, path: Path = DEFAULT_CRITERIA_PATH) -> None:
    """Human-editable copy (DESIGN §12: "criteria stays file-edited")."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(criteria.model_dump(), sort_keys=False, allow_unicode=True)
    )


def read_criteria_file(path: Path = DEFAULT_CRITERIA_PATH) -> Criteria:
    return Criteria(**yaml.safe_load(path.read_text()))
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `uv run pytest tests/test_criteria.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — all existing tests plus these 2 new ones.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/jobscout/criteria.py tests/test_criteria.py
git commit -m "feat: criteria schema, LLM derivation, and hand-editable YAML file"
```

---

## Task 2: Wire `draft_criteria` into the onboard graph

**Files:**
- Modify: `src/jobscout/graph/onboard.py`
- Modify: `tests/graph/test_skeleton_graphs.py`

**Interfaces:**
- Consumes: `Criteria`, `derive_criteria` (Task 1).
- Produces: updated `OnboardState`, `draft_criteria`, updated `build_onboard_graph` (signatures above).

- [ ] **Step 1: Update the onboard graph test to expect criteria derivation**

Replace `test_onboard_graph_walks_through_both_gates_and_persists_answers` in `tests/graph/test_skeleton_graphs.py` with:

```python
def test_onboard_graph_walks_through_both_gates_and_derives_criteria(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React", "TypeScript"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    fake_criteria = Criteria(
        knockout=[KnockoutRule(axis="seniority_band", rule="senior or mid only")],
        scored=[
            ScoredDimension(dimension="stack fit", weight=0.4, rubric="5 = 3+ core tools"),
            ScoredDimension(dimension="domain/product interest", weight=0.2, rubric="soft"),
            ScoredDimension(dimension="scope & seniority signals", weight=0.2, rubric="own"),
            ScoredDimension(dimension="eng-culture signals", weight=0.2, rubric="testing"),
        ],
        learn=[],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Vue ok?", "IC or lead?", "Remote only?"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.derive_criteria", lambda profile: fake_criteria
    )
    graph, conn = _compile(build_onboard_graph, tmp_path)
    cfg = {"configurable": {"thread_id": "onboard"}}

    graph.invoke(
        {
            "resume_path": str(FIXTURE),
            "profile_draft": {},
            "resume_text": "",
            "static_answers": {},
            "dynamic_questions": [],
            "dynamic_answers": {},
            "criteria_draft": {},
        },
        cfg,
    )
    state = graph.get_state(cfg)
    assert state.next == ("static_questions_gate",)

    static_answers = {"german_level": "B2", "work_mode": "remote"}
    graph.invoke(Command(resume=static_answers), cfg)
    state = graph.get_state(cfg)
    assert state.next == ("dynamic_questions_gate",)

    dynamic_answers = {"Vue ok?": "yes", "IC or lead?": "open to lead"}
    graph.invoke(Command(resume=dynamic_answers), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["criteria_draft"] == fake_criteria.model_dump()
    assert state.values["static_answers"] == static_answers
    assert state.values["dynamic_answers"] == dynamic_answers
    assert state.values["profile_draft"] == fake_profile.model_dump()
    conn.close()
```

Add `from jobscout.criteria import Criteria, KnockoutRule, ScoredDimension` to the file's imports, alongside the existing `from jobscout.resume import ExtractedProfile`. Leave every other test in this file (the four poll-graph tests) untouched.

- [ ] **Step 2: Run it, verify it fails against the still-4-node graph**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py::test_onboard_graph_walks_through_both_gates_and_derives_criteria -v`
Expected: FAIL — `state.values` has no `criteria_draft` key (`KeyError`), since the graph reaches `END` right after `dynamic_questions_gate` today.

- [ ] **Step 3: Rewrite `src/jobscout/graph/onboard.py`**

```python
"""Onboard graph — resume ingest, static/dynamic questions, criteria (DESIGN §5).

`onboard` is a checkpointed subgraph so each stage can pause and resume later.
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout.criteria import derive_criteria
from jobscout.questions import STATIC_QUESTIONS, generate_dynamic_questions
from jobscout.resume import extract_resume_text, parse_profile


class OnboardState(TypedDict):
    resume_path: str
    profile_draft: dict
    resume_text: str
    static_answers: dict
    dynamic_questions: list[str]
    dynamic_answers: dict
    criteria_draft: dict


def ingest(state: OnboardState) -> dict:
    resume_text = extract_resume_text(Path(state["resume_path"]))
    profile = parse_profile(resume_text)
    return {"resume_text": resume_text, "profile_draft": profile.model_dump()}


def static_questions_gate(state: OnboardState) -> dict:
    """Pauses until the candidate answers the static questions (DESIGN §5 stage 2)."""
    answers = interrupt({"gate": "static_questions", "questions": STATIC_QUESTIONS})
    return {"static_answers": dict(answers)}


def draft_dynamic_questions(state: OnboardState) -> dict:
    # ponytail: no Spend logging yet (§13) — same reasoning as parse_profile
    # (unit 5); the cap-enforcement machinery (unit 15) doesn't exist yet.
    questions = generate_dynamic_questions(state["resume_text"], state["static_answers"])
    return {"dynamic_questions": questions}


def dynamic_questions_gate(state: OnboardState) -> dict:
    """Pauses until the candidate answers the LLM-generated follow-ups
    (DESIGN §5 stage 3)."""
    answers = interrupt(
        {"gate": "dynamic_questions", "questions": state["dynamic_questions"]}
    )
    return {"dynamic_answers": dict(answers)}


def draft_criteria(state: OnboardState) -> dict:
    # ponytail: no Spend logging yet (§13) — same reasoning as draft_dynamic_questions.
    profile = {
        "resume": state["profile_draft"],
        "static_answers": state["static_answers"],
        "dynamic_answers": state["dynamic_answers"],
    }
    criteria = derive_criteria(profile)
    return {"criteria_draft": criteria.model_dump()}


def build_onboard_graph() -> StateGraph:
    g = StateGraph(OnboardState)
    g.add_node("ingest", ingest)
    g.add_node("static_questions_gate", static_questions_gate)
    g.add_node("draft_dynamic_questions", draft_dynamic_questions)
    g.add_node("dynamic_questions_gate", dynamic_questions_gate)
    g.add_node("draft_criteria", draft_criteria)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "static_questions_gate")
    g.add_edge("static_questions_gate", "draft_dynamic_questions")
    g.add_edge("draft_dynamic_questions", "dynamic_questions_gate")
    g.add_edge("dynamic_questions_gate", "draft_criteria")
    g.add_edge("draft_criteria", END)
    return g
```

- [ ] **Step 4: Run the graph tests, verify they pass**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py -v`
Expected: PASS (5 tests — 4 unchanged poll-graph tests + the rewritten onboard test).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS everywhere except `tests/test_service.py`'s onboarding-completion tests, which are expected to fail here — `run_onboarding`/`resume_onboarding` don't yet seed `criteria_draft` in their initial state dict, so the graph will `KeyError` on `state["static_answers"]`... no — confirm precisely: the new state field `criteria_draft` isn't read by any node before `draft_criteria` itself, so nothing should `KeyError` before that point; the actual failure mode is that `_save_profile`'s completion path in `service.py` doesn't know about criteria yet, and any test asserting on `handle.state["criteria_draft"]` or expecting a specific `app_profile`/`app_criteria` row count will fail. Confirm the failures you see are confined to `tests/test_service.py` and are about the *service* layer not yet knowing about criteria — Task 3 fixes them.

- [ ] **Step 6: Commit**

```bash
git add src/jobscout/graph/onboard.py tests/graph/test_skeleton_graphs.py
git commit -m "feat: derive criteria as the onboard graph's fifth stage"
```

---

## Task 3: `CoreService` persists Criteria, syncs hand-edits, resets onboarding

**Files:**
- Modify: `src/jobscout/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: the five-node onboard graph (Task 2); `Criteria`, `write_criteria_file`, `read_criteria_file`, `DEFAULT_CRITERIA_PATH` (Task 1).
- Produces: updated `CoreService.__init__`, `run_onboarding`, `resume_onboarding`; new `_save_criteria`, `sync_criteria_from_file`, `reset_onboarding` (signatures above).

- [ ] **Step 1: Update the failing service tests**

In `tests/test_service.py`, add to the imports:

```python
from jobscout.criteria import Criteria, KnockoutRule, ScoredDimension, write_criteria_file
```

Update the `_svc` helper:

```python
def _svc(tmp_path) -> CoreService:
    return CoreService(
        db_path=tmp_path / "j.sqlite", criteria_path=tmp_path / "criteria.yaml"
    )
```

Add a module-level helper next to any existing sample-data helpers (or near the top of the file, wherever fits the file's existing organization):

```python
def _sample_criteria() -> Criteria:
    return Criteria(
        knockout=[KnockoutRule(axis="seniority_band", rule="senior or mid only")],
        scored=[
            ScoredDimension(dimension="stack fit", weight=0.4, rubric="5 = 3+ core tools"),
            ScoredDimension(dimension="domain/product interest", weight=0.2, rubric="soft"),
            ScoredDimension(dimension="scope & seniority signals", weight=0.2, rubric="own"),
            ScoredDimension(dimension="eng-culture signals", weight=0.2, rubric="testing"),
        ],
        learn=[],
    )
```

Replace `test_resume_onboarding_completes_and_persists_merged_profile` with:

```python
def test_resume_onboarding_completes_and_persists_merged_profile(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    fake_criteria = _sample_criteria()
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Vue ok?"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.derive_criteria", lambda profile: fake_criteria
    )
    svc = _svc(tmp_path)
    svc.run_onboarding(str(FIXTURE))
    svc.resume_onboarding({"work_mode": "remote"})
    h = svc.resume_onboarding({"Vue ok?": "yes"})

    assert h.status == "completed"
    assert h.state["static_answers"] == {"work_mode": "remote"}
    assert h.state["dynamic_answers"] == {"Vue ok?": "yes"}
    assert h.state["criteria_draft"] == fake_criteria.model_dump()

    profile_rows = svc._conn.execute(
        "SELECT version, data_json, resume_text FROM app_profile"
    ).fetchall()
    assert len(profile_rows) == 1
    data = json.loads(profile_rows[0]["data_json"])
    assert data["resume"] == fake_profile.model_dump()
    assert data["static_answers"] == {"work_mode": "remote"}
    assert data["dynamic_answers"] == {"Vue ok?": "yes"}

    criteria_rows = svc._conn.execute(
        "SELECT version, data_json, profile_version FROM app_criteria"
    ).fetchall()
    assert len(criteria_rows) == 1
    assert criteria_rows[0]["version"] == 1
    assert criteria_rows[0]["profile_version"] == 1
    assert json.loads(criteria_rows[0]["data_json"]) == fake_criteria.model_dump()

    assert svc._criteria_path.exists()
    assert "seniority_band" in svc._criteria_path.read_text()
    svc.close()
```

Update `test_resume_onboarding_after_completion_is_a_harmless_no_op` (from unit 6's final-review fix) so it also monkeypatches `jobscout.graph.onboard.derive_criteria` (the onboard graph won't reach completion without it now) and additionally asserts `app_criteria` stays at exactly one row across the repeat call, the same way it already asserts this for `app_profile`. Read the test's current body in this file before editing it — carry its existing structure and assertions forward, only adding the `derive_criteria` monkeypatch and the `app_criteria` row-count check.

Add three new tests:

```python
def test_sync_criteria_from_file_bumps_a_new_version_on_change(tmp_path):
    svc = _svc(tmp_path)
    criteria_v1 = _sample_criteria()
    write_criteria_file(criteria_v1, svc._criteria_path)

    version1 = svc.sync_criteria_from_file()
    assert version1 == 1

    criteria_v2 = criteria_v1.model_copy(update={"learn": ["new signal"]})
    write_criteria_file(criteria_v2, svc._criteria_path)

    version2 = svc.sync_criteria_from_file()
    assert version2 == 2

    rows = svc._conn.execute("SELECT version FROM app_criteria ORDER BY version").fetchall()
    assert [r["version"] for r in rows] == [1, 2]
    svc.close()


def test_sync_criteria_from_file_is_a_noop_when_unchanged(tmp_path):
    svc = _svc(tmp_path)
    criteria = _sample_criteria()
    write_criteria_file(criteria, svc._criteria_path)
    svc.sync_criteria_from_file()

    result = svc.sync_criteria_from_file()

    assert result is None
    rows = svc._conn.execute("SELECT version FROM app_criteria").fetchall()
    assert len(rows) == 1
    svc.close()


def test_reset_onboarding_tags_pre_reset_and_clears_the_thread(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    fake_criteria = _sample_criteria()
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Q1?"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.derive_criteria", lambda profile: fake_criteria
    )
    svc = _svc(tmp_path)
    svc.run_onboarding(str(FIXTURE))
    svc.resume_onboarding({"work_mode": "remote"})
    svc.resume_onboarding({"Q1?": "answer"})

    svc.reset_onboarding()

    profile_rows = svc._conn.execute("SELECT pre_reset FROM app_profile").fetchall()
    assert all(r["pre_reset"] == 1 for r in profile_rows)
    criteria_rows = svc._conn.execute("SELECT pre_reset FROM app_criteria").fetchall()
    assert all(r["pre_reset"] == 1 for r in criteria_rows)

    h = svc.run_onboarding(str(FIXTURE))
    assert h.status == "paused"
    assert h.pending_gate["gate"] == "static_questions"
    svc.close()
```

Leave every other test in the file (poll-graph-related, `record_verdict`, singleton, no-graph-leak, `resume_onboarding` rejects unstarted run) untouched.

- [ ] **Step 2: Run the service tests, verify the new/changed ones fail correctly**

Run: `uv run pytest tests/test_service.py -v`
Expected: FAIL on every test that touches criteria (`AttributeError: 'CoreService' object has no attribute 'sync_criteria_from_file'` / `'reset_onboarding'`, or a `TypeError` on `CoreService(criteria_path=...)` since the constructor doesn't accept that yet, or `KeyError: 'criteria_draft'`). Confirm each failure is for one of these expected reasons before moving on.

- [ ] **Step 3: Update `src/jobscout/service.py`**

Add to the imports:

```python
from jobscout.criteria import Criteria, DEFAULT_CRITERIA_PATH, read_criteria_file, write_criteria_file
```

Update `CoreService.__init__`:

```python
    def __init__(
        self, db_path: Path = DEFAULT_DB_PATH, criteria_path: Path | None = None
    ) -> None:
        self._conn: sqlite3.Connection = get_connection(db_path)
        init_db(self._conn)
        self._criteria_path = criteria_path or DEFAULT_CRITERIA_PATH
        # ponytail: one connection + one checkpointer for the whole process.
        # Single-process app; move to a pool / async saver only if real
        # concurrency shows up (Unit 2 final review).
        self._checkpointer = get_checkpointer(self._conn)
        self._poll = build_poll_graph().compile(checkpointer=self._checkpointer)
        self._onboard = build_onboard_graph().compile(
            checkpointer=self._checkpointer
        )
```

Replace `run_onboarding` and `resume_onboarding`, and add `_save_criteria`, `sync_criteria_from_file`, `reset_onboarding` (placed near `run_onboarding`/`resume_onboarding`/`_save_profile`, following this file's existing section-comment style):

```python
    def run_onboarding(self, resume_path: str | None = None) -> RunHandle:
        if not resume_path:
            raise ValueError("run_onboarding needs a resume PDF path")
        cfg = {"configurable": {"thread_id": _ONBOARD_THREAD}}
        self._onboard.invoke(
            {
                "resume_path": resume_path,
                "profile_draft": {},
                "resume_text": "",
                "static_answers": {},
                "dynamic_questions": [],
                "dynamic_answers": {},
                "criteria_draft": {},
            },
            cfg,
        )
        handle = self._handle(self._onboard, _ONBOARD_THREAD)
        if handle.status == "completed":
            self._save_onboarding_results(handle.state)
        return handle

    def resume_onboarding(self, decision: object) -> RunHandle:
        cfg = {"configurable": {"thread_id": _ONBOARD_THREAD}}
        snap = self._onboard.get_state(cfg)
        if not snap.created_at:
            raise ValueError("no onboarding run in progress")
        if not snap.next:  # already completed — nothing to resume
            return self._handle(self._onboard, _ONBOARD_THREAD)
        self._onboard.invoke(Command(resume=decision), cfg)
        handle = self._handle(self._onboard, _ONBOARD_THREAD)
        if handle.status == "completed":
            self._save_onboarding_results(handle.state)
        return handle

    def reset_onboarding(self) -> None:
        # Ruling: tag, don't delete (matches schema.sql's pre_reset design);
        # app_posting/app_score/app_feedback have no pre_reset column and are
        # intentionally left untouched — see plan Global Constraints.
        self._conn.execute("UPDATE app_profile SET pre_reset = 1")
        self._conn.execute("UPDATE app_criteria SET pre_reset = 1")
        self._conn.commit()
        self._checkpointer.delete_thread(_ONBOARD_THREAD)

    def sync_criteria_from_file(self, path: Path | None = None) -> int | None:
        criteria = read_criteria_file(path or self._criteria_path)
        new_data = json.dumps(criteria.model_dump())
        latest = self._conn.execute(
            "SELECT data_json FROM app_criteria ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if latest is not None and latest["data_json"] == new_data:
            return None
        (profile_version,) = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM app_profile"
        ).fetchone()
        (version,) = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM app_criteria"
        ).fetchone()
        self._conn.execute(
            "INSERT INTO app_criteria (version, data_json, profile_version) "
            "VALUES (?, ?, ?)",
            (version, new_data, profile_version),
        )
        self._conn.commit()
        return version

    def _save_onboarding_results(self, state: dict) -> None:
        profile_version = self._save_profile(state)
        self._save_criteria(state, profile_version)

    def _save_criteria(self, state: dict, profile_version: int) -> None:
        data = state["criteria_draft"]
        (version,) = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM app_criteria"
        ).fetchone()
        self._conn.execute(
            "INSERT INTO app_criteria (version, data_json, profile_version) "
            "VALUES (?, ?, ?)",
            (version, json.dumps(data), profile_version),
        )
        self._conn.commit()
        write_criteria_file(Criteria(**data), self._criteria_path)
```

`_save_profile` (unit 5/6, unchanged in body) must now **return the version it inserted** instead of returning nothing — change only its `return` statement:

```python
    def _save_profile(self, state: dict) -> int:
        # ponytail: version read-then-insert isn't race-safe under concurrent
        # callers. Fine today — one CLI process, one command at a time. Add
        # locking / a unique constraint retry if a concurrent surface
        # (units 40-41) ever calls run_onboarding from more than one place.
        (version,) = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM app_profile"
        ).fetchone()
        data = {
            "resume": state["profile_draft"],
            "static_answers": state["static_answers"],
            "dynamic_answers": state["dynamic_answers"],
        }
        self._conn.execute(
            "INSERT INTO app_profile (version, data_json, resume_text) "
            "VALUES (?, ?, ?)",
            (version, json.dumps(data), state["resume_text"]),
        )
        self._conn.commit()
        return version
```

`Path`, `Command`, `json` are already imported in this file from earlier units — do not duplicate.

- [ ] **Step 4: Run the service tests, verify they pass**

Run: `uv run pytest tests/test_service.py -v`
Expected: PASS — every test in the file.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — no failures anywhere.

- [ ] **Step 6: Commit**

```bash
git add src/jobscout/service.py tests/test_service.py
git commit -m "feat: CoreService persists Criteria, syncs hand-edits, resets onboarding"
```

---

## Self-Review

**1. Spec coverage (build-plan unit 7 acceptance criteria):**

| Criterion | Task / test |
|---|---|
| An LLM derives a `criteria` object with three buckets: `knockout`, `scored`, `learn` (§5) | Task 1 `Criteria`/`KnockoutRule`/`ScoredDimension`, `derive_criteria`; Task 2's `draft_criteria` node wires it into the graph with the real Profile |
| `criteria` is file-editable and versioned; a bump creates a new version, old versions kept (§5, §10) | Task 1 `write_criteria_file`/`read_criteria_file`; Task 3 `sync_criteria_from_file` (bump-on-change, `test_sync_criteria_from_file_bumps_a_new_version_on_change`), never deletes old `app_criteria` rows |
| `jobscout onboard --reset` wipes `profile` + `criteria` only; Posting/Score/Verdict history is kept but tagged pre-reset (§5) | Task 3 `reset_onboarding`; Ruling recorded in Global Constraints for the schema/build-plan-text mismatch on which tables actually have a `pre_reset` column; `test_reset_onboarding_tags_pre_reset_and_clears_the_thread` |

No gaps. Wiring an actual `jobscout onboard --reset` CLI flag is unit 17's job — this unit correctly stops at providing the `reset_onboarding` primitive.

**2. Placeholder scan:** No "TBD" / "add error handling" / bare "write tests". Every step has complete code. The `# ponytail:` comments (version-read-then-insert race, no Spend logging yet) are carried forward from units 5/6 verbatim where the same reasoning still applies.

**3. Type consistency:** `Criteria`/`KnockoutRule`/`ScoredDimension`/`derive_criteria`/`write_criteria_file`/`read_criteria_file` used identically across Task 1's source/tests, Task 2's `onboard.py`/graph test, and Task 3's `service.py`/service tests. `OnboardState`'s seven keys match every dict literal seeding/asserting onboard state across both modified test files. `_save_profile`'s return-type change (`None` → `int`) is a private-method change with exactly one caller (`_save_onboarding_results`, added in this same task) — no other code depends on its old `None` return.

**4. Known risk carried into the plan, not hidden:** `SqliteSaver.delete_thread`'s existence and exact clearing behavior were verified empirically during plan-writing (a throwaway two-invoke-then-delete-then-fresh-invoke script), not assumed from the method's name. The `--reset` scope mismatch between DESIGN's prose and the actual committed schema is recorded as an explicit Ruling, not silently resolved one way or the other.

**Notes for later units:**
- Unit 8 (search-plan gate, build-yourself candidate) is the first real consumer of `get_llm()` inside the *poll* graph rather than the onboard graph — it does not depend on anything this unit built, but it's the next unit in the build order.
- Unit 11 (Knockout evaluation) and unit 12 (Score Sub-Agent) are the actual consumers of the `Criteria` object this unit derives — they read the latest `app_criteria` row (via a `CoreService` method neither exists yet) and evaluate a Posting against `knockout`/`scored`. This unit only produces and versions the object; it does not evaluate anything against it.
- Unit 26 (Preference Feedback Loop) is what eventually populates the `learn` bucket's soft signals into real weight adjustments — this unit's `learn` list is just LLM-flagged text, not yet actionable.
- `sync_criteria_from_file`'s `profile_version` uses whatever `app_profile`'s current latest version is at sync time — if a hand-edit happens between two different Profile versions (unlikely in v1, single-profile), the FK would point at the profile that existed *when synced*, not necessarily the one the edit was conceptually "about." Not a bug for this unit's scope (single profile, single criteria lineage) — worth a second look if multi-profile ever becomes real.
