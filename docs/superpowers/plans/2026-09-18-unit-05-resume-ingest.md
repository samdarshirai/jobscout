# Unit 5: Resume Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The onboard graph's `ingest` node stops being a pass-through stub. Pointed at a resume PDF, it extracts raw text, has an LLM turn that text into a structured `ExtractedProfile` (roles, years, stack, seniority signals), and `CoreService.run_onboarding` persists the result as one versioned `app_profile` row (§5 stage 1).

**Architecture:** A new leaf module, `jobscout.resume`, owns PDF-text extraction (`extract_resume_text`, via `pypdf`) and the structured LLM call (`parse_profile`, via `jobscout.llm.get_llm().with_structured_output(ExtractedProfile)`) — it touches neither the graph nor the store. `jobscout.graph.onboard.ingest` calls both and returns plain state (`resume_text`, `profile_draft`); it still touches neither the LLM client's config nor the store directly — it just calls the leaf module. `CoreService.run_onboarding` (unit 3, signature unchanged) is extended to do one more thing after the onboard graph completes: `INSERT` the profile into `app_profile` — the only place in the codebase that does, per unit 3's Global Constraint. Only stage 1 of onboarding (resume ingest) is built here; static questions, dynamic questions (unit 6), and criteria derivation (unit 7) are out of scope — the graph stays a single `ingest` node to `END`.

**Tech Stack:** Python ≥3.12, `pypdf` (new dependency — pure-Python PDF text extraction), `langchain-openai` / `jobscout.llm` (unit 4), `langgraph` (units 2-3), stdlib `json`, `pathlib`, pytest + `monkeypatch`.

**Spec:** `docs/2026-09-01-build-plan.md` unit 5; `docs/DESIGN.md` §5 (Onboarding, stage 1: "candidate points at the PDF. LLM extraction → structured JSON (roles, years, stack, seniority signals). Raw text kept alongside." and "Storage: one versioned `profile` record in SQLite ... parsed resume + static answers + dynamic Q&A"). Vocabulary: `CONTEXT.md` ([[profile]], [[onboarding]]) if those entries exist — this plan does not depend on them existing yet.

## Global Constraints

- **CoreService remains the only thing that touches the graph or the store** (unit 3, permanent). `jobscout.resume` and `jobscout.graph.onboard.ingest` do PDF/LLM work only — no `sqlite3` import, no connection, anywhere in either module. The one `INSERT` into `app_profile` lives in `CoreService`, called after `run_onboarding`'s graph call completes.
- **`run_onboarding`'s signature does not change** (unit 3: "method signatures here do not change"). Its *behavior* grows — it now also persists a profile row on completion — exactly what unit 3's own notes-for-later-units anticipated ("later units extend this layer... they never bypass it").
- **The profile write is one `INSERT` + one `commit`**, matching unit 3's "app writes stay single-statement" pattern for `record_verdict`. A `SELECT COALESCE(MAX(version), 0) + 1` read precedes it to compute the next version — this is a read, not a second write, so it does not violate the single-statement-write rule. `# ponytail:` comment required at that call site: this version-number read-then-insert is not race-safe under concurrent callers; acceptable because nothing calls `CoreService` concurrently yet (single CLI process, one command at a time) — revisit if/when a concurrent surface (unit 41's web app, unit 40's bot) calls `run_onboarding` from more than one place at once.
- **Only stage 1 (resume ingest) is in scope.** Do not add static-question nodes, dynamic-question nodes, an approval gate, or a `criteria` derivation step — those are units 6 and 7. The onboard graph stays `ingest -> END`.
- **No schema changes.** `app_profile` (version, data_json, resume_text, pre_reset, created_at) already exists from unit 2 — used as-is. No `ALTER TABLE`.
- **One new dependency: `pypdf>=6.0`.** No other new dependencies.
- **Tests make no real network / LLM calls.** Every test that would otherwise trigger `parse_profile`'s real LLM call monkeypatches it (either `jobscout.resume.get_llm` directly, or `jobscout.graph.onboard.parse_profile` at the call site, per which module is under test). The only real, unmocked I/O in any test in this unit is local PDF-text extraction against a fixture file committed to the repo.
- **New fixture: `data/example/fake_resume.pdf`.** Already expected by the existing (currently vacuously passing, since the path didn't exist) `tests/test_repo_hygiene.py::test_example_data_stays_tracked`, which asserts this exact path is *not* git-ignored (DESIGN §19: demo data ships in the repo). Generated once from a verified script (given verbatim in Task 1) — not hand-authored binary, not sourced from a real person's resume.
- Python ≥3.12; `uv` packaging; pytest; TDD (write failing test, see it fail, implement, see it pass, commit).

## Interfaces Produced (later units consume these)

- `jobscout.resume.ExtractedProfile` — `pydantic.BaseModel` with fields `roles: list[str]`, `years_experience: float`, `stack: list[str]`, `seniority_signals: list[str]`.
- `jobscout.resume.extract_resume_text(pdf_path: pathlib.Path) -> str` — raw text, pages joined with `"\n"`.
- `jobscout.resume.parse_profile(resume_text: str) -> ExtractedProfile` — one structured LLM call via `jobscout.llm.get_llm()`.
- `jobscout.graph.onboard.OnboardState` gains a third field: `resume_text: str` (was `resume_path: str`, `profile_draft: dict`).
- `jobscout.graph.onboard.ingest(state: OnboardState) -> dict` — now returns `{"resume_text": ..., "profile_draft": <ExtractedProfile.model_dump()>}` instead of the unit-3 stub's `{"profile_draft": {"resume_path": ...}}`.
- `jobscout.service.CoreService.run_onboarding` — same signature as unit 3; on `status == "completed"`, also inserts one `app_profile` row (`version` = next integer, `data_json` = `json.dumps(profile_draft)`, `resume_text` = the raw extracted text). Units 6-7 will `UPDATE` this same row (not insert a new version) once static/dynamic answers and criteria exist — that update path is out of scope here.

---

## File Structure

- `data/example/fake_resume.pdf` — new binary fixture, generated once by a verified script (Task 1 Step 1), then committed as-is.
- `src/jobscout/resume.py` — `ExtractedProfile`, `extract_resume_text`, `parse_profile`. One responsibility: turn a resume PDF into structured data. Touches neither the graph nor the store.
- `tests/test_resume.py` — `extract_resume_text` against the real fixture (no mocking — deterministic, no network); `parse_profile`'s LLM wiring (mocked `get_llm`, no network).
- Modify: `src/jobscout/graph/onboard.py` — `OnboardState` gains `resume_text`; `ingest` calls `jobscout.resume`.
- Modify: `tests/graph/test_skeleton_graphs.py` — the onboard test now points at the real fixture and monkeypatches `jobscout.graph.onboard.parse_profile`.
- Modify: `src/jobscout/service.py` — `run_onboarding` seeds `resume_text: ""` in the initial state and persists `app_profile` on completion; add `import json`.
- Modify: `tests/test_service.py` — the two existing `run_onboarding` tests (real fixture path + monkeypatch instead of the unit-3 stub assertions), a new test asserting the persisted `app_profile` row, and the checkpointer-sharing test's bare `svc.run_onboarding()` call (which would otherwise try to open `""` as a PDF path).
- Modify: `pyproject.toml` — add `pypdf>=6.0` to `dependencies`.

---

## Task 1: `jobscout.resume` — PDF extraction and structured LLM parse

**Files:**
- Create: `data/example/fake_resume.pdf`
- Create: `src/jobscout/resume.py`
- Test: `tests/test_resume.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `jobscout.llm.get_llm` (unit 4).
- Produces: `ExtractedProfile`, `extract_resume_text`, `parse_profile` (signatures above).

- [ ] **Step 1: Add the dependency and generate the fixture PDF**

Edit `pyproject.toml`'s `dependencies` list, adding `pypdf>=6.0` after `langchain-openai`:

```toml
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "langgraph>=1.2.11",
    "langgraph-checkpoint-sqlite>=3.1.1",
    "langchain-openai>=1.6",
    "pypdf>=6.0",
]
```

Run: `uv sync`
Expected: resolves and installs `pypdf` with no conflicts.

Run this script **once** to generate the fixture (verified against installed `pypdf` 6.19.0 during plan-writing — it round-trips exactly):

```python
# /tmp/make_fixture_pdf.py — run once, then delete; only the output PDF is committed.
import pathlib

TEXT_LINES = [
    "Jane Doe",
    "Senior Frontend Engineer",
    "6 years React, TypeScript, GraphQL",
    "Led a team of 4 engineers at Acme Corp",
]


def make_minimal_pdf(lines: list[str]) -> bytes:
    content_ops = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for i, line in enumerate(lines):
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        if i == 0:
            content_ops.append(f"({escaped}) Tj")
        else:
            content_ops.append("T*")
            content_ops.append(f"({escaped}) Tj")
    content_ops.append("ET")
    content_stream = "\n".join(content_ops).encode("latin-1")

    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(
        f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1")
        + content_stream
        + b"\nendstream"
    )

    out = bytearray()
    out += b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("latin-1")
        out += obj
        out += b"\nendobj\n"

    xref_offset = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += b"trailer\n"
    out += f"<< /Size {n} /Root 1 0 R >>\n".encode("latin-1")
    out += b"startxref\n"
    out += f"{xref_offset}\n".encode("latin-1")
    out += b"%%EOF"
    return bytes(out)


pdf_bytes = make_minimal_pdf(TEXT_LINES)
out_path = pathlib.Path("data/example/fake_resume.pdf")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_bytes(pdf_bytes)
print(f"wrote {len(pdf_bytes)} bytes to {out_path}")
```

Run: `uv run python /tmp/make_fixture_pdf.py` (from the repo root, so the relative output path lands at `data/example/fake_resume.pdf`), then `rm /tmp/make_fixture_pdf.py`.
Expected: `data/example/fake_resume.pdf` exists, ~700 bytes.

Verify it round-trips before moving on:
```bash
uv run python -c "
from pypdf import PdfReader
print(PdfReader('data/example/fake_resume.pdf').pages[0].extract_text())
"
```
Expected output (exact):
```
Jane Doe
Senior Frontend Engineer
6 years React, TypeScript, GraphQL
Led a team of 4 engineers at Acme Corp
```
If this doesn't match exactly, do not proceed — report BLOCKED with what you got.

- [ ] **Step 2: Write the failing tests**

`tests/test_resume.py`:

```python
from pathlib import Path

from jobscout.resume import ExtractedProfile, extract_resume_text, parse_profile

FIXTURE = Path(__file__).parent.parent / "data" / "example" / "fake_resume.pdf"


def test_extract_resume_text_reads_real_pdf_content():
    text = extract_resume_text(FIXTURE)

    assert "Jane Doe" in text
    assert "React" in text
    assert "Led a team of 4 engineers" in text


def test_parse_profile_calls_structured_llm_with_resume_text(monkeypatch):
    expected = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React", "TypeScript", "GraphQL"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.resume.get_llm", lambda: _FakeLLM())

    result = parse_profile("Jane Doe\nSenior Frontend Engineer")

    assert result == expected
    assert captured["schema"] is ExtractedProfile
    assert "Jane Doe" in captured["prompt"]
```

- [ ] **Step 3: Run the tests, verify they fail**

Run: `uv run pytest tests/test_resume.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.resume'`.

- [ ] **Step 4: Write `src/jobscout/resume.py`**

```python
"""Resume ingest — PDF to structured Profile (DESIGN §5 stage 1).

Touches neither the graph nor the store: `extract_resume_text` and
`parse_profile` are pure functions over their arguments plus one LLM call.
`jobscout.graph.onboard.ingest` calls both; `CoreService` (unit 3) is the
only thing that persists the result.
"""

from pathlib import Path

from pydantic import BaseModel, Field
from pypdf import PdfReader

from jobscout.llm import get_llm


class ExtractedProfile(BaseModel):
    """Structured resume extraction (build-plan unit 5)."""

    roles: list[str] = Field(description="Job titles/roles held, most recent first")
    years_experience: float = Field(description="Total years of professional experience")
    stack: list[str] = Field(description="Languages, frameworks, and tools used")
    seniority_signals: list[str] = Field(
        description="Phrases indicating seniority: team size led, scope of ownership, title"
    )


def extract_resume_text(pdf_path: Path) -> str:
    """Raw text of every page, joined with newlines."""
    reader = PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


_EXTRACTION_PROMPT = (
    "Extract a structured profile from this resume text. Identify roles "
    "held, total years of professional experience, the technical stack "
    "(languages/frameworks/tools), and any signals of seniority (team "
    "size led, scope of ownership, title).\n\nResume text:\n{resume_text}"
)


def parse_profile(resume_text: str) -> ExtractedProfile:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes)."""
    structured_llm = get_llm().with_structured_output(ExtractedProfile)
    return structured_llm.invoke(_EXTRACTION_PROMPT.format(resume_text=resume_text))
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `uv run pytest tests/test_resume.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (37 existing + 2 here = 39).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock data/example/fake_resume.pdf src/jobscout/resume.py tests/test_resume.py
git commit -m "feat: resume PDF extraction and structured-profile LLM parse"
```

---

## Task 2: Wire `ingest` into the onboard graph; `CoreService` persists the Profile

**Files:**
- Modify: `src/jobscout/graph/onboard.py`
- Modify: `tests/graph/test_skeleton_graphs.py`
- Modify: `src/jobscout/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: `ExtractedProfile`, `extract_resume_text`, `parse_profile` (Task 1).
- Produces: updated `OnboardState`, `ingest`, `CoreService.run_onboarding` behavior (signatures above).

- [ ] **Step 1: Update the onboard graph test to expect real ingest behavior**

Replace the existing `test_onboard_graph_runs_to_end_and_is_checkpointed` in `tests/graph/test_skeleton_graphs.py` (keep the file's other three poll-graph tests untouched) with:

```python
from pathlib import Path

from jobscout.resume import ExtractedProfile

FIXTURE = Path(__file__).parent.parent.parent / "data" / "example" / "fake_resume.pdf"


def test_onboard_graph_runs_to_end_and_is_checkpointed(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React", "TypeScript"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    graph, conn = _compile(build_onboard_graph, tmp_path)
    cfg = {"configurable": {"thread_id": "onboard"}}
    graph.invoke(
        {"resume_path": str(FIXTURE), "profile_draft": {}, "resume_text": ""}, cfg
    )
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["profile_draft"] == fake_profile.model_dump()
    assert "Jane Doe" in state.values["resume_text"]
    assert state.config["configurable"]["thread_id"] == "onboard"
    conn.close()
```

Add the `from pathlib import Path` and `from jobscout.resume import ExtractedProfile` imports and the `FIXTURE` constant at the top of the file, alongside the existing `from langgraph.types import Command` / graph imports — do not duplicate an existing import line.

- [ ] **Step 2: Run it, verify it fails against the still-stubbed `ingest`**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py::test_onboard_graph_runs_to_end_and_is_checkpointed -v`
Expected: FAIL — `state.values["profile_draft"]` is `{"resume_path": ...}` (the unit-3 stub), not `fake_profile.model_dump()`; or a `KeyError`/`FileNotFoundError` if the stub tries to treat the fixture path as something to store verbatim. Either failure mode confirms the stub is still in place.

- [ ] **Step 3: Rewrite `src/jobscout/graph/onboard.py`**

```python
"""Onboard graph — resume ingest is real; later stages are units 6-7 (DESIGN §5).

`onboard` is a checkpointed subgraph so it can stop halfway and resume.
This unit's `ingest` does the real stage-1 work: PDF -> text -> structured
Profile. Units 6-7 add static questions, dynamic questions, and criteria
derivation as further nodes between `ingest` and `END`.
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from jobscout.resume import extract_resume_text, parse_profile


class OnboardState(TypedDict):
    resume_path: str
    profile_draft: dict
    resume_text: str


def ingest(state: OnboardState) -> dict:
    resume_text = extract_resume_text(Path(state["resume_path"]))
    profile = parse_profile(resume_text)
    return {"resume_text": resume_text, "profile_draft": profile.model_dump()}


def build_onboard_graph() -> StateGraph:
    g = StateGraph(OnboardState)
    g.add_node("ingest", ingest)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", END)
    return g
```

- [ ] **Step 4: Run the graph test, verify it passes**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py -v`
Expected: PASS (4 tests — 3 unchanged poll-graph tests + the rewritten onboard test).

- [ ] **Step 5: Write the failing service tests**

In `tests/test_service.py`, add `import json` and `from pathlib import Path` and `from jobscout.resume import ExtractedProfile` to the imports, and a `FIXTURE` constant:

```python
FIXTURE = Path(__file__).parent / "data" / "example" / "fake_resume.pdf"
```

Wait — `tests/test_service.py` lives directly under `tests/`, so the fixture (at `data/example/fake_resume.pdf` from the repo root) is one level up from `tests/graph/`'s `FIXTURE` constant. Use:

```python
FIXTURE = Path(__file__).parent.parent / "data" / "example" / "fake_resume.pdf"
```

Replace the existing `test_run_onboarding_completes` with:

```python
def test_run_onboarding_completes(tmp_path, monkeypatch):
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
    assert h.status == "completed"
    assert h.state["profile_draft"] == fake_profile.model_dump()
    svc.close()
```

Add a new test right after it:

```python
def test_run_onboarding_persists_one_versioned_profile_row(tmp_path, monkeypatch):
    fake_profile = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React"],
        seniority_signals=["Led a team of 4 engineers"],
    )
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile", lambda resume_text: fake_profile
    )
    svc = _svc(tmp_path)
    svc.run_onboarding(str(FIXTURE))
    rows = svc._conn.execute(
        "SELECT version, data_json, resume_text FROM app_profile"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    assert json.loads(rows[0]["data_json"]) == fake_profile.model_dump()
    assert "Jane Doe" in rows[0]["resume_text"]
    svc.close()
```

Update `test_one_connection_and_one_checkpointer_for_the_service` (it currently calls `svc.run_onboarding()` with no path, which would now try to open `""` as a PDF and fail) to:

```python
def test_one_connection_and_one_checkpointer_for_the_service(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.onboard.parse_profile",
        lambda resume_text: ExtractedProfile(
            roles=[], years_experience=0.0, stack=[], seniority_signals=[]
        ),
    )
    svc = _svc(tmp_path)
    svc.trigger_run()
    svc.run_onboarding(str(FIXTURE))
    assert svc._poll.checkpointer is svc._checkpointer
    assert svc._onboard.checkpointer is svc._checkpointer
    assert svc._checkpointer.conn is svc._conn
    svc.close()
```

Leave every other test in `tests/test_service.py` (the `trigger_run`/`resume_run`/`record_verdict`/singleton/no-leak tests) untouched.

- [ ] **Step 6: Run the service tests, verify the new/changed ones fail correctly**

Run: `uv run pytest tests/test_service.py -v`
Expected: FAIL on `test_run_onboarding_completes` (still asserts old stub shape until Step 5's edits are in, or fails with a path error), FAIL on `test_run_onboarding_persists_one_versioned_profile_row` (no such behavior yet — `sqlite3.OperationalError` or empty `rows`), FAIL on `test_one_connection_and_one_checkpointer_for_the_service` if `run_onboarding()`'s old no-arg call path is still exercised elsewhere. Confirm each failure is for the expected reason before moving on.

- [ ] **Step 7: Update `src/jobscout/service.py`**

Add `import json` to the top-of-file imports (alongside the existing `sqlite3`/`dataclasses`/etc. imports).

Replace `run_onboarding` and add `_save_profile` right after it:

```python
    def run_onboarding(self, resume_path: str | None = None) -> RunHandle:
        cfg = {"configurable": {"thread_id": _ONBOARD_THREAD}}
        self._onboard.invoke(
            {"resume_path": resume_path or "", "profile_draft": {}, "resume_text": ""},
            cfg,
        )
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
        self._conn.execute(
            "INSERT INTO app_profile (version, data_json, resume_text) "
            "VALUES (?, ?, ?)",
            (version, json.dumps(state["profile_draft"]), state["resume_text"]),
        )
        self._conn.commit()
```

Insert `_save_profile` in the `# ---- verdict ----` / lifecycle region of the file wherever it reads most naturally next to `run_onboarding` — this is a small file, follow its existing section-comment style (`# ---- runs ----`, `# ---- verdict ----`).

- [ ] **Step 8: Run the service tests, verify they pass**

Run: `uv run pytest tests/test_service.py -v`
Expected: PASS (10 tests — unit 3 left 9; this task rewrites 2 of them in place, same count, and adds 1 new one: `test_run_onboarding_persists_one_versioned_profile_row`).

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (40 — 37 from before this unit, +2 from Task 1's new `tests/test_resume.py`, +1 from Task 2's new persistence test; the other Task 2 changes rewrite existing tests in place and don't change the count).

- [ ] **Step 10: Commit**

```bash
git add src/jobscout/graph/onboard.py tests/graph/test_skeleton_graphs.py src/jobscout/service.py tests/test_service.py
git commit -m "feat: wire resume ingest into onboard graph; CoreService persists the Profile"
```

---

## Self-Review

**1. Spec coverage (build-plan unit 5 acceptance criteria):**

| Criterion | Task / test |
|---|---|
| Candidate points CLI at a resume PDF; LLM extraction yields structured JSON — roles, years, stack, seniority signals (§5) | Task 1 `ExtractedProfile` (exact four fields), `parse_profile`; Task 2 `ingest` wires it into the graph via `state["resume_path"]`. CLI itself is unit 17 — out of scope here, `run_onboarding(resume_path)` is the seam it will call. |
| Raw resume text is stored alongside the parsed JSON (§5) | `extract_resume_text` + `OnboardState.resume_text` threaded through `ingest`; `CoreService._save_profile` writes `resume_text` into the same `app_profile` row as `data_json`; `test_run_onboarding_persists_one_versioned_profile_row` asserts both columns on one row |
| The result is one versioned `profile` record (§5) | `_save_profile`'s `SELECT COALESCE(MAX(version),0)+1` + single `INSERT`; `test_run_onboarding_persists_one_versioned_profile_row` asserts exactly one row with `version == 1` |
| `onboard` is a checkpointed subgraph — interrupting and resuming mid-ingest does not restart it (§5) | Structurally already true from unit 3 (the onboard graph compiles against `CoreService`'s shared checkpointer); `test_onboard_graph_runs_to_end_and_is_checkpointed` re-confirms this holds with real `ingest` logic, not just the stub |

No gaps. Static questions, dynamic questions, and criteria derivation are correctly out of scope (units 6, 7).

**2. Placeholder scan:** No "TBD" / "add error handling" / bare "write tests". Every step has real, complete code, including the fixture-generation script (already verified byte-for-byte during plan-writing, not left for the implementer to improvise).

**3. Type consistency:** `ExtractedProfile`, `extract_resume_text`, `parse_profile` used identically across Task 1's source, Task 1's tests, Task 2's `onboard.py`, and Task 2's test files. `OnboardState`'s three keys (`resume_path`, `profile_draft`, `resume_text`) match every dict literal that constructs or asserts against onboard state in both modified test files. `FIXTURE`'s relative path depth is correct per file: `tests/test_resume.py` and `tests/test_service.py` both sit directly under `tests/` (`parent.parent`), `tests/graph/test_skeleton_graphs.py` sits one level deeper (`parent.parent.parent`) — verified against the actual paths, not assumed.

**4. Known risk carried into the plan, not hidden:** the version-read-then-insert in `_save_profile` is not atomic under concurrency; explicitly ponytail-flagged with the upgrade trigger (a concurrent surface calling `run_onboarding`), not silently shipped. The fixture PDF's exact extracted text was verified against installed `pypdf` 6.19.0 before this plan was written — if a materially different `pypdf` major version is installed at implementation time and extraction differs, treat that as a real signal to re-verify, not to loosen an assertion.

**Notes for later units:**
- Unit 6 (static + dynamic questions) will add more nodes to the onboard graph between `ingest` and `END`, and will need to `UPDATE` (not re-`INSERT`) the same `app_profile.version` row `_save_profile` created here, merging static/dynamic answers into `data_json` — `CoreService` will need a sibling to `_save_profile` for that, or `_save_profile` itself will need to become update-aware once there's a "resume in progress vs. complete" signal in state.
- Unit 7 (criteria derivation) reads the completed `app_profile` row to derive `app_criteria` — `data_json`'s shape (the `ExtractedProfile` fields today, plus static/dynamic answers after unit 6) is the contract unit 7 will parse.
- The pricing-override note from unit 4's final review applies again here: `parse_profile`'s call is a billable event once unit 8+ wires in real Spend logging (§13) — this unit doesn't log spend because DESIGN §13's cap-enforcement machinery doesn't exist yet (build-plan unit 15), same "recurring criteria don't apply yet" reasoning unit 3 used for its skeleton nodes.
