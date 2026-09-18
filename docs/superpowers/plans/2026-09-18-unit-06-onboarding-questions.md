# Unit 6: Onboarding Questions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The onboard graph grows two more real stages — static questions (a fixed catalog, DESIGN §5 stage 2) and dynamic questions (3-5 LLM-generated follow-ups from the resume + static answers, DESIGN §5 stage 3) — each pausing the graph at an `interrupt()` gate until the candidate answers. `CoreService` gets a `resume_onboarding` sibling to `resume_run` so callers can answer each gate in turn. All three stages (resume, static answers, dynamic answers) land in the same versioned `app_profile` row.

**Architecture:** A new leaf module, `jobscout.questions`, owns the static question catalog and the dynamic-question LLM call — mirrors `jobscout.resume`'s boundary (no graph, no store). The onboard graph grows from one node to four: `ingest -> static_questions_gate -> draft_dynamic_questions -> dynamic_questions_gate -> END`. Verified empirically during plan-writing: two sequential `interrupt()`s on the same thread work exactly like one does (each `Command(resume=...)` resolves the current pending interrupt and runs forward to the next interrupt or `END`) — the same mechanism unit 3 proved for the poll graph's single gate, just exercised twice. Because `_save_profile` (unit 5) only fires when `CoreService`'s `_handle` reports `status == "completed"`, and the graph no longer reaches `END` until *both* gates are answered, a single `INSERT` (not an `UPDATE`) still correctly captures all three stages merged into one `app_profile.data_json` — no update-aware persistence needed, unlike what unit 5's notes anticipated (that anticipation was based on an incomplete picture of how the gates would change *when* the graph completes; this plan supersedes it).

**Tech Stack:** Python ≥3.12, `jobscout.llm` (unit 4), `jobscout.resume` (unit 5), `langgraph` (units 2-3), stdlib `json`, pytest + `monkeypatch`. No new dependencies.

**Spec:** `docs/2026-09-01-build-plan.md` unit 6; `docs/DESIGN.md` §5 (Onboarding, stages 2-3 verbatim: static questions list, "LLM reads resume + static answers, asks 3-5 targeted follow-ups", "Storage: one versioned profile record ... parsed resume + static answers + dynamic Q&A").

## Global Constraints

- **CoreService remains the only thing that touches the graph or the store** (unit 3, permanent). `jobscout.questions` and every new node in `jobscout.graph.onboard` do LLM/pure-Python work only — no `sqlite3` import, no connection, anywhere in either module.
- **`run_onboarding`'s signature does not change** (`run_onboarding(self, resume_path: str | None = None) -> RunHandle`, unit 3/5). A new sibling, `resume_onboarding(self, decision: object) -> RunHandle`, is added — unit 3's own notes-for-later-units anticipated exactly this: "If mid-onboard resume becomes real (an `interrupt()` inside onboard), `run_onboarding` will need a `resume_onboarding` sibling — add it to `CoreService`, do not bypass."
- **`resume_onboarding` must reject an onboarding thread that was never started**, the same failure mode unit 3's final review found and fixed for `resume_run`: `Command(resume=...)` against a `thread_id` with no checkpoint is silently treated as fresh input from `START`, not an error. Guard with `if not self._onboard.get_state(cfg).created_at: raise ValueError(...)`, mirroring `resume_run`'s existing guard exactly. Resuming an *already-completed* onboarding thread must remain a harmless no-op (same as `resume_run` — verified in unit 3, not re-verified here since the mechanism is identical).
- **Only DESIGN §5 stages 2-3 are in scope.** No criteria derivation (unit 7), no `--reset` handling (unit 7). The onboard graph gains exactly two gate nodes and one compute node; it does not gain a rejection/abort path — unlike the poll graph's search-plan gate, there is no "reject" outcome for onboarding questions, only "answer and continue."
- **The profile write stays one `INSERT` + one `commit`** (unit 3/5 pattern) — not an `UPDATE`. Because the graph now only reaches `END` after both new gates resolve, `_save_profile` still fires exactly once per completed onboarding flow, same as unit 5. `data_json`'s shape changes to nest all three stages: `{"resume": <ExtractedProfile fields>, "static_answers": {...}, "dynamic_answers": {...}}`.
- **The static question catalog is fixed data, not LLM-generated**, and must cover exactly the topics build-plan unit 6 and DESIGN §5 stage 2 name: German level + whether to include German-required roles; salary floor (knockout-or-scored); work mode + acceptable cities; company size/stage; hard-exclude industries; must-have stack; contract type. Two of those bullets each combine two related asks — expressed here as 9 keyed prompts (`german_level` + `include_german_required_roles`; `work_mode` + `acceptable_cities`; the remaining 5 bullets as one key each).
- **Dynamic questions are 3-5 items**, enforced by a Pydantic `Field(min_length=3, max_length=5)` on the structured-output schema — verified against installed `pydantic` during plan-writing (list length constraints work as expected in pydantic v2).
- **No schema changes, no new dependencies.**
- **Tests make no real network/LLM calls.** Every test that would otherwise trigger `parse_profile` or `generate_dynamic_questions`'s real LLM calls monkeypatches them at the point they're imported into the calling module (`jobscout.graph.onboard.parse_profile`, `jobscout.graph.onboard.generate_dynamic_questions`), same pattern unit 5 established.
- Python ≥3.12; `uv` packaging; pytest; TDD (write failing test, see it fail, implement, see it pass, commit).

## Interfaces Produced (later units consume these)

- `jobscout.questions.STATIC_QUESTIONS: list[dict]` — 9 entries, each `{"key": str, "prompt": str}`.
- `jobscout.questions.DynamicQuestions` — `pydantic.BaseModel` with `questions: list[str]` (3-5 items).
- `jobscout.questions.generate_dynamic_questions(resume_text: str, static_answers: dict) -> list[str]` — one structured LLM call.
- `jobscout.graph.onboard.OnboardState` gains three fields: `static_answers: dict`, `dynamic_questions: list[str]`, `dynamic_answers: dict` (six total, alongside `resume_path`, `profile_draft`, `resume_text`).
- `jobscout.graph.onboard.static_questions_gate(state) -> dict` — `interrupt({"gate": "static_questions", "questions": STATIC_QUESTIONS})`, returns `{"static_answers": dict(answers)}`.
- `jobscout.graph.onboard.draft_dynamic_questions(state) -> dict` — calls `jobscout.questions.generate_dynamic_questions`, returns `{"dynamic_questions": [...]}`.
- `jobscout.graph.onboard.dynamic_questions_gate(state) -> dict` — `interrupt({"gate": "dynamic_questions", "questions": state["dynamic_questions"]})`, returns `{"dynamic_answers": dict(answers)}`.
- `jobscout.service.CoreService.resume_onboarding(self, decision: object) -> RunHandle` — resumes the onboard thread past its current gate; raises `ValueError` if no onboarding run is in progress; persists the profile via `_save_profile` when the graph reaches `END`.
- `jobscout.service.CoreService._save_profile` — `data_json` now `{"resume": ..., "static_answers": ..., "dynamic_answers": ...}` instead of unit 5's bare `profile_draft`.

---

## File Structure

- `src/jobscout/questions.py` — `STATIC_QUESTIONS`, `DynamicQuestions`, `generate_dynamic_questions`. One responsibility: onboarding's question catalog and LLM follow-up generation. Touches neither the graph nor the store.
- `tests/test_questions.py` — the static catalog's shape; `generate_dynamic_questions`'s LLM wiring (mocked, no network).
- Modify: `src/jobscout/graph/onboard.py` — `OnboardState` gains three fields; two new gate nodes plus one compute node; `build_onboard_graph` wires all four nodes.
- Modify: `tests/graph/test_skeleton_graphs.py` — the onboard test now walks through both gates.
- Modify: `src/jobscout/service.py` — `run_onboarding` seeds the three new state fields; new `resume_onboarding` method; `_save_profile`'s `data_json` shape changes.
- Modify: `tests/test_service.py` — the two onboarding-completion tests from unit 5 are replaced with gate-aware versions; two new tests for `resume_onboarding` (walks to the second gate; rejects an unstarted run).

---

## Task 1: `jobscout.questions` — static catalog and dynamic LLM follow-ups

**Files:**
- Create: `src/jobscout/questions.py`
- Test: `tests/test_questions.py`

**Interfaces:**
- Consumes: `jobscout.llm.get_llm` (unit 4).
- Produces: `STATIC_QUESTIONS`, `DynamicQuestions`, `generate_dynamic_questions` (signatures above).

- [ ] **Step 1: Write the failing tests**

`tests/test_questions.py`:

```python
from jobscout.questions import STATIC_QUESTIONS, DynamicQuestions, generate_dynamic_questions


def test_static_questions_cover_every_design_topic():
    keys = {q["key"] for q in STATIC_QUESTIONS}
    assert keys == {
        "german_level",
        "include_german_required_roles",
        "salary_floor",
        "work_mode",
        "acceptable_cities",
        "company_size_stage",
        "hard_exclude_industries",
        "must_have_stack",
        "contract_type",
    }
    for q in STATIC_QUESTIONS:
        assert q["prompt"]


def test_generate_dynamic_questions_calls_structured_llm(monkeypatch):
    expected = DynamicQuestions(questions=["Q1?", "Q2?", "Q3?"])
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.questions.get_llm", lambda: _FakeLLM())

    result = generate_dynamic_questions("Jane Doe, React dev", {"work_mode": "remote"})

    assert result == ["Q1?", "Q2?", "Q3?"]
    assert captured["schema"] is DynamicQuestions
    assert "Jane Doe" in captured["prompt"]
    assert "remote" in captured["prompt"]
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `uv run pytest tests/test_questions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.questions'`.

- [ ] **Step 3: Write `src/jobscout/questions.py`**

```python
"""Onboarding questions — static catalog and dynamic LLM follow-ups (DESIGN §5 stages 2-3).

Touches neither the graph nor the store, same boundary as jobscout.resume.
"""

from pydantic import BaseModel, Field

from jobscout.llm import get_llm

STATIC_QUESTIONS = [
    {
        "key": "german_level",
        "prompt": "What is your German language level? (none, A1-A2, B1-B2, C1-C2, native)",
    },
    {
        "key": "include_german_required_roles",
        "prompt": "Include roles that require German, even above your stated level? (yes/no)",
    },
    {
        "key": "salary_floor",
        "prompt": "What is your minimum acceptable salary (annual, EUR)? Is this a "
        "hard floor (knockout) or just preferred (scored)?",
    },
    {"key": "work_mode", "prompt": "Preferred work mode: remote, hybrid, or onsite?"},
    {
        "key": "acceptable_cities",
        "prompt": "Which cities are acceptable for hybrid/onsite roles?",
    },
    {
        "key": "company_size_stage",
        "prompt": "Preferred company size/stage (startup, scale-up, mid-size, enterprise)?",
    },
    {"key": "hard_exclude_industries", "prompt": "Any industries to hard-exclude?"},
    {"key": "must_have_stack", "prompt": "Any must-have technologies/stack?"},
    {
        "key": "contract_type",
        "prompt": "Preferred contract type (permanent, contract, freelance)?",
    },
]


class DynamicQuestions(BaseModel):
    """3-5 targeted follow-ups from the resume + static answers (DESIGN §5 stage 3)."""

    questions: list[str] = Field(
        min_length=3,
        max_length=5,
        description=(
            "Targeted follow-up questions, e.g. resolving stack ambiguity "
            "('6y React but also Vue - are Vue-only roles ok?') or seniority "
            "preference ('you've led a team - IC only or open to lead?')"
        ),
    )


_DYNAMIC_PROMPT = (
    "Based on this resume and these static answers, ask 3-5 targeted follow-up "
    "questions that would help score job postings for this candidate. Good "
    "examples: resolving ambiguity in their stack, or seniority/leadership "
    "preference.\n\nResume text:\n{resume_text}\n\nStatic answers:\n{static_answers}"
)


def generate_dynamic_questions(resume_text: str, static_answers: dict) -> list[str]:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes)."""
    structured_llm = get_llm().with_structured_output(DynamicQuestions)
    result = structured_llm.invoke(
        _DYNAMIC_PROMPT.format(resume_text=resume_text, static_answers=static_answers)
    )
    return result.questions
```

- [ ] **Step 4: Run the tests, verify they pass**

Run: `uv run pytest tests/test_questions.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — all existing tests plus these 2 new ones, no failures.

- [ ] **Step 6: Commit**

```bash
git add src/jobscout/questions.py tests/test_questions.py
git commit -m "feat: static question catalog and dynamic-follow-up LLM generation"
```

---

## Task 2: Wire both question gates into the onboard graph

**Files:**
- Modify: `src/jobscout/graph/onboard.py`
- Modify: `tests/graph/test_skeleton_graphs.py`

**Interfaces:**
- Consumes: `STATIC_QUESTIONS`, `generate_dynamic_questions` (Task 1).
- Produces: updated `OnboardState`, `static_questions_gate`, `draft_dynamic_questions`, `dynamic_questions_gate`, updated `build_onboard_graph` (signatures above).

- [ ] **Step 1: Update the onboard graph test to expect both gates**

Replace `test_onboard_graph_runs_to_end_and_is_checkpointed` in `tests/graph/test_skeleton_graphs.py` with:

```python
def test_onboard_graph_walks_through_both_gates_and_persists_answers(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React", "TypeScript"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Vue ok?", "IC or lead?", "Remote only?"],
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
        },
        cfg,
    )
    state = graph.get_state(cfg)
    assert state.next == ("static_questions_gate",)
    assert state.interrupts[0].value["gate"] == "static_questions"

    static_answers = {"german_level": "B2", "work_mode": "remote"}
    graph.invoke(Command(resume=static_answers), cfg)
    state = graph.get_state(cfg)
    assert state.next == ("dynamic_questions_gate",)
    assert state.interrupts[0].value == {
        "gate": "dynamic_questions",
        "questions": ["Vue ok?", "IC or lead?", "Remote only?"],
    }
    assert state.values["static_answers"] == static_answers

    dynamic_answers = {"Vue ok?": "yes", "IC or lead?": "open to lead"}
    graph.invoke(Command(resume=dynamic_answers), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["dynamic_answers"] == dynamic_answers
    assert state.values["profile_draft"] == fake_profile.model_dump()
    conn.close()
```

`FIXTURE`, `ExtractedProfile`, and `Command` are already imported at the top of this file from unit 5 and unit 3 respectively — do not duplicate those imports. Leave every other test in this file (the four poll-graph tests) untouched.

- [ ] **Step 2: Run it, verify it fails against the still-two-stage `ingest`-only graph**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py::test_onboard_graph_walks_through_both_gates_and_persists_answers -v`
Expected: FAIL — `state.next` after the first `invoke` is `()` (the graph still runs straight to `END` after `ingest`), not `("static_questions_gate",)`. Confirms the old graph shape is still in place.

- [ ] **Step 3: Rewrite `src/jobscout/graph/onboard.py`**

```python
"""Onboard graph — resume ingest, static questions, dynamic questions (DESIGN §5).

`onboard` is a checkpointed subgraph so each stage can pause and resume later.
Unit 7 adds criteria derivation after this graph reaches END.
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout.questions import STATIC_QUESTIONS, generate_dynamic_questions
from jobscout.resume import extract_resume_text, parse_profile


class OnboardState(TypedDict):
    resume_path: str
    profile_draft: dict
    resume_text: str
    static_answers: dict
    dynamic_questions: list[str]
    dynamic_answers: dict


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


def build_onboard_graph() -> StateGraph:
    g = StateGraph(OnboardState)
    g.add_node("ingest", ingest)
    g.add_node("static_questions_gate", static_questions_gate)
    g.add_node("draft_dynamic_questions", draft_dynamic_questions)
    g.add_node("dynamic_questions_gate", dynamic_questions_gate)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "static_questions_gate")
    g.add_edge("static_questions_gate", "draft_dynamic_questions")
    g.add_edge("draft_dynamic_questions", "dynamic_questions_gate")
    g.add_edge("dynamic_questions_gate", END)
    return g
```

- [ ] **Step 4: Run the graph tests, verify they pass**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py -v`
Expected: PASS (5 tests — the 4 unchanged poll-graph tests + the rewritten onboard test).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — no regressions. `tests/test_service.py`'s onboarding tests are expected to start failing here (Task 3 fixes them) — confirm any failures are confined to that file and are about onboarding no longer completing in one `run_onboarding` call, not about anything else breaking.

- [ ] **Step 6: Commit**

```bash
git add src/jobscout/graph/onboard.py tests/graph/test_skeleton_graphs.py
git commit -m "feat: static and dynamic question gates in the onboard graph"
```

---

## Task 3: `CoreService.resume_onboarding` and merged Profile persistence

**Files:**
- Modify: `src/jobscout/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: the four-node onboard graph (Task 2).
- Produces: `CoreService.resume_onboarding`, updated `run_onboarding` and `_save_profile` (signatures above).

- [ ] **Step 1: Update the failing service tests**

In `tests/test_service.py`, replace `test_run_onboarding_completes` and `test_run_onboarding_persists_one_versioned_profile_row` (both from unit 5) with:

```python
def test_run_onboarding_pauses_at_the_static_questions_gate(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React", "TypeScript", "GraphQL"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    svc = _svc(tmp_path)
    h = svc.run_onboarding(str(FIXTURE))
    assert h.status == "paused"
    assert h.pending_gate["gate"] == "static_questions"
    svc.close()


def test_resume_onboarding_walks_to_the_dynamic_questions_gate(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Vue ok?", "IC or lead?", "Remote only?"],
    )
    svc = _svc(tmp_path)
    svc.run_onboarding(str(FIXTURE))
    h = svc.resume_onboarding({"work_mode": "remote"})
    assert h.status == "paused"
    assert h.pending_gate == {
        "gate": "dynamic_questions",
        "questions": ["Vue ok?", "IC or lead?", "Remote only?"],
    }
    svc.close()


def test_resume_onboarding_completes_and_persists_merged_profile(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.generate_dynamic_questions",
        lambda resume_text, static_answers: ["Vue ok?"],
    )
    svc = _svc(tmp_path)
    svc.run_onboarding(str(FIXTURE))
    svc.resume_onboarding({"work_mode": "remote"})
    h = svc.resume_onboarding({"Vue ok?": "yes"})

    assert h.status == "completed"
    assert h.state["static_answers"] == {"work_mode": "remote"}
    assert h.state["dynamic_answers"] == {"Vue ok?": "yes"}

    rows = svc._conn.execute(
        "SELECT version, data_json, resume_text FROM app_profile"
    ).fetchall()
    assert len(rows) == 1
    data = json.loads(rows[0]["data_json"])
    assert data["resume"] == fake_profile.model_dump()
    assert data["static_answers"] == {"work_mode": "remote"}
    assert data["dynamic_answers"] == {"Vue ok?": "yes"}
    assert "Jane Doe" in rows[0]["resume_text"]
    svc.close()


def test_resume_onboarding_rejects_when_no_run_in_progress(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError, match="no onboarding run"):
        svc.resume_onboarding("anything")
    svc.close()
```

`json`, `Path`, `ExtractedProfile`, `pytest`, and the `FIXTURE` constant are already imported/defined at the top of this file from unit 5 — do not duplicate. Leave `test_one_connection_and_one_checkpointer_for_the_service` and `test_run_onboarding_requires_a_resume_path` untouched — neither depends on onboarding reaching completion in a single call, both are unaffected by this task's changes (verify this yourself by reading them before assuming it, rather than trusting this note alone). Leave every other test in the file untouched.

- [ ] **Step 2: Run the service tests, verify the new/changed ones fail correctly**

Run: `uv run pytest tests/test_service.py -v`
Expected: FAIL on `test_resume_onboarding_walks_to_the_dynamic_questions_gate`, `test_resume_onboarding_completes_and_persists_merged_profile`, and `test_resume_onboarding_rejects_when_no_run_in_progress` — `CoreService` has no `resume_onboarding` method yet (`AttributeError`). `test_run_onboarding_pauses_at_the_static_questions_gate` should already PASS at this point (it only exercises `run_onboarding`, whose behavior already changed correctly in Task 2 since the graph itself now pauses there) — confirm this before moving on; if it fails too, something in Task 2 didn't land as expected.

- [ ] **Step 3: Update `src/jobscout/service.py`**

Replace `run_onboarding` and `_save_profile`, and add `resume_onboarding` between them:

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
            },
            cfg,
        )
        handle = self._handle(self._onboard, _ONBOARD_THREAD)
        if handle.status == "completed":
            self._save_profile(handle.state)
        return handle

    def resume_onboarding(self, decision: object) -> RunHandle:
        cfg = {"configurable": {"thread_id": _ONBOARD_THREAD}}
        if not self._onboard.get_state(cfg).created_at:
            raise ValueError("no onboarding run in progress")
        self._onboard.invoke(Command(resume=decision), cfg)
        handle = self._handle(self._onboard, _ONBOARD_THREAD)
        if handle.status == "completed":
            self._save_profile(handle.state)
        return handle

    def _save_profile(self, state: dict) -> None:
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
```

`Command` is already imported at the top of this file (unit 3, used by `resume_run`) — do not duplicate. `json` is already imported (unit 5).

- [ ] **Step 4: Run the service tests, verify they pass**

Run: `uv run pytest tests/test_service.py -v`
Expected: PASS — every test in the file, including the three new ones and the one carried over from Step 2's confirmation.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — no failures anywhere.

- [ ] **Step 6: Commit**

```bash
git add src/jobscout/service.py tests/test_service.py
git commit -m "feat: CoreService.resume_onboarding; merged profile persists after both gates"
```

---

## Self-Review

**1. Spec coverage (build-plan unit 6 acceptance criteria):**

| Criterion | Task / test |
|---|---|
| Static questions cover: German level + whether to include German-required roles; salary floor (knockout or scored); work mode + acceptable cities; company size/stage; hard-exclude industries; must-have stack; contract type (§5) | Task 1 `STATIC_QUESTIONS` — 9 keyed prompts covering all 7 DESIGN bullets; `test_static_questions_cover_every_design_topic` asserts the exact key set |
| 3-5 dynamic follow-ups are generated from resume + static answers (§5) | Task 1 `DynamicQuestions` (`Field(min_length=3, max_length=5)`, verified against installed pydantic), `generate_dynamic_questions(resume_text, static_answers)`; Task 2's `draft_dynamic_questions` node passes both real values from state |
| All answers land in the same versioned `profile` record (§5) | Task 3 `_save_profile`'s merged `data_json = {"resume": ..., "static_answers": ..., "dynamic_answers": ...}`, one `INSERT`; `test_resume_onboarding_completes_and_persists_merged_profile` asserts exactly one row containing all three |

No gaps. Criteria derivation (unit 7) is correctly out of scope.

**2. Placeholder scan:** No "TBD" / "add error handling" / bare "write tests". Every step has complete, real code. The `# ponytail:` comment on `_save_profile`'s version read is carried forward verbatim from unit 5 since the same non-atomicity risk still applies unchanged.

**3. Type consistency:** `OnboardState`'s six keys are used identically across Task 2's `onboard.py`, Task 2's graph test, Task 3's service code, and Task 3's service tests. `STATIC_QUESTIONS`, `DynamicQuestions`, `generate_dynamic_questions` used identically between Task 1's source/tests and Task 2's `onboard.py` import. The gate payload shapes (`{"gate": "static_questions", "questions": STATIC_QUESTIONS}`, `{"gate": "dynamic_questions", "questions": [...]}`) are produced in `onboard.py` and asserted identically in both the Task 2 graph test and the Task 3 service tests (via `pending_gate`).

**4. Known risk carried into the plan, not hidden:** whether two sequential `interrupt()`s on one thread actually work as expected with the installed `langgraph`/`langgraph-checkpoint-sqlite` versions was empirically verified during plan-writing (a throwaway two-gate graph, run through both resumes, checkpointer state inspected at each step) rather than assumed from unit 3's single-gate precedent. Pydantic's `min_length`/`max_length` on a `list[str]` field was likewise verified directly, not assumed from general pydantic-v1-era knowledge (the field names changed between v1 and v2).

**Notes for later units:**
- Unit 7 (criteria derivation) reads the completed `app_profile` row — `data_json`'s three top-level keys (`resume`, `static_answers`, `dynamic_answers`) are the contract it parses. Unit 7 also owns `jobscout onboard --reset`, which should address the still-open question of what happens if `run_onboarding` is called again on an already-fully-onboarded thread (today: `graph.invoke` with fresh input on a completed thread restarts `ingest` from `START`, re-running the real LLM calls and creating a second, separate `app_profile` version via a second `_save_profile` call — this unit's gate structure prevents *duplicate rows from partial re-runs*, but a genuinely repeated full `run_onboarding` call is still unit 7's job to guard, via `--reset` semantics).
- `resume_onboarding`'s `decision: object` stays untyped/generic on purpose — later gates elsewhere in the codebase (the poll graph's search-plan/outbound-letter/scope-expansion gates) already established that `interrupt()` payloads and their resume values are free-form; onboarding's two gates both resume with a `dict` of answers, but nothing enforces that at the type level, matching the existing project convention.
- Telegram (unit 40) and web (unit 41) surfaces will need to render `pending_gate["questions"]` (a list of `{"key", "prompt"}` dicts for static, a bare list of strings for dynamic) — the two gates intentionally have different `questions` shapes since static questions have stable keys to store answers under, while dynamic questions don't need keys (their answers are collected as free-form Q→A pairs, as the tests show).
