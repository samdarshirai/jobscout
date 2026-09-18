# Unit 10: Graph Backbone (discover → dedupe → fetch_jd → staleness) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The poll graph's backbone grows from `plan_search --(gate)--> discover --> finish` to `plan_search --(gate)--> discover --> dedupe --> fetch_jd --> staleness --> finish` — three new plain-Python nodes, no LLM, each a placeholder for real logic a later unit fills in. `dedupe` collapses exact-id duplicates within one Poll's batch. `fetch_jd` drops any Posting with no JD text so it never reaches scoring. `staleness` is a no-op pass-through.

**Architecture:** All three nodes are pure functions of `PollState` — no new dependency on `conn`, `httpx`, or any I/O (unlike `discover`, which unit 9 gave a legitimate, narrow exception to touch the ATS-type cache). They live in `src/jobscout/graph/poll.py` alongside the existing nodes, not a new module — this is graph wiring, not a new leaf component with its own interface other units will import. `build_poll_graph` gains three `add_node` calls and re-routes the edge chain; its signature (`conn`, `companies_path`) is unchanged.

**Tech Stack:** Python ≥3.12, stdlib only (`dict.setdefault`-based dedupe, no new library). pytest + `monkeypatch`, same `httpx.MockTransport`-free style already used for `tests/graph/test_skeleton_graphs.py` (these tests exercise the graph directly with a stubbed `discover_curated_ats`, no HTTP at all).

**Spec:** `docs/2026-09-01-build-plan.md` unit 10; `docs/DESIGN.md` §4 (graph shape — `discover → dedupe → fetch_jd → staleness`, "no LLM," "a Posting missing JD text does not reach scoring"), §11 (dedupe key scheme — exact-key only here, "collision on company+title but not on key" is unit 17's LLM tie-break; staleness's real re-poll/stale-after-2-polls logic is units 18-19). Full error/retry lifecycle for a Posting that fails `fetch_jd` (marked `error`, retried, `dead` after 3 polls) is explicitly unit 30's job (`docs/2026-09-01-build-plan.md:397`, DESIGN §17) — this unit only keeps such a Posting out of the state that flows toward scoring, it does not implement status-tracking for it.

## Global Constraints

- **No LLM calls in any of the three new nodes** — DESIGN §4 is explicit that `discover`/`dedupe`/`fetch_jd`/`staleness` are "Plain Python. No LLM." this is a correctness requirement, not a style preference: unit 15's Spend cap and the eval's per-node fault injection both assume LLM calls happen only at the named nodes in DESIGN §4's table, and `dedupe`/`fetch_jd`/`staleness` are not in that table.
- **`dedupe` is exact-key only.** It collapses postings sharing the same `id` (computed by unit 9's `discover_curated_ats`) within the current batch, keeping the first occurrence. It does **not** attempt to catch two different ids that are "probably the same job" (matching company+title) — DESIGN §11 puts that behind a cached LLM call, and the build plan assigns it to unit 17 by name. Implementing any fuzzy/company+title matching here would be building ahead of spec.
- **`fetch_jd` does not fetch anything yet.** Every Posting `discover` (unit 9) currently produces already carries full JD text — Greenhouse/Lever/Ashby/Personio APIs return it inline, and the `trafilatura` fallback extracts it directly. `fetch_jd` in this unit is a filter: drop any Posting whose `jd_text` is falsy (missing, `None`, or empty string) so it never proceeds toward scoring, matching DESIGN §4's node-table entry verbatim ("a Posting missing JD text does not reach scoring"). The *fetching* half of this node's eventual job arrives with units 21-22 (Adzuna/arbeitnow return summaries, not full JD text, and will need a real HTTP call here) — noted as a `ponytail:` comment, not implemented now.
- **`staleness` is a true no-op** — `return {}`, touching no state key. DESIGN §11's real staleness rules ("gone from source or 404 for 2 polls → `stale`", "`content_hash` changed → re-score") are units 18-19's job and need persistence across polls this unit's `PollState` (reset fresh every `trigger_run`) has no way to track.
- **A dropped Posting (duplicate or missing-JD-text) is simply absent from `state["postings"]` at `finish`** — it is not persisted with an `error` status, not logged, and not retried. That lifecycle (DESIGN §17: "logged, marked `error` with the reason, run continues... after 3 failed polls → `dead`") is unit 30's job by name (`docs/2026-09-01-build-plan.md:397`). This unit's job is narrower: keep such Postings out of what reaches scoring, full stop.
- **`build_poll_graph`'s signature does not change** — `(conn: sqlite3.Connection, companies_path: Path = DEFAULT_COMPANIES_PATH) -> StateGraph`, same as unit 9 left it. No new parameter is needed since none of the three new nodes touch `conn`, `httpx`, or the filesystem.
- **`CoreService`'s persistence call (`_save_discovered_postings`, called from `resume_run` on completion) is unchanged** — it already persists whatever ends up in `state["postings"]` at graph completion; this unit only changes what that list contains by the time the graph reaches `finish`, not how it's saved.
- Python ≥3.12; `uv` packaging; pytest; TDD (write failing test, see it fail, implement, see it pass, commit).

## Interfaces Produced (later units consume these)

- `jobscout.graph.poll.dedupe(state: PollState) -> dict` — new node, returns `{"postings": [...]}` with duplicate `id`s collapsed to their first occurrence.
- `jobscout.graph.poll.fetch_jd(state: PollState) -> dict` — new node, returns `{"postings": [...]}` with every Posting lacking `jd_text` removed.
- `jobscout.graph.poll.staleness(state: PollState) -> dict` — new node, always returns `{}`.
- `jobscout.graph.poll.build_poll_graph`'s compiled graph now runs `discover → dedupe → fetch_jd → staleness → finish` on approval — unit 11 (knockout exclusion) inserts its node between `staleness` and `finish`; unit 12 (Score Sub-Agent) goes after that.

---

## File Structure

- Modify: `src/jobscout/graph/poll.py` — three new node functions, `build_poll_graph`'s edge chain re-routed through them.
- Modify: `tests/graph/test_skeleton_graphs.py` — new tests for `dedupe` (collapses a same-id duplicate) and `fetch_jd` (drops a no-JD-text Posting) exercised through the full compiled graph (not unit-testing the bare functions in isolation, to match this file's existing style of driving the graph end-to-end); existing approve/reject/fail-closed tests keep passing unchanged since the one fake posting they use already has a unique `id` and non-empty `jd_text`.

No new files — this unit only reroutes existing graph wiring, matching the plan's own "no new leaf module" architecture call.

---

## Task 1: Wire `dedupe` → `fetch_jd` → `staleness` into the poll graph

**Files:**
- Modify: `src/jobscout/graph/poll.py`
- Modify: `tests/graph/test_skeleton_graphs.py`

**Interfaces:**
- Consumes: nothing new — operates only on `PollState["postings"]`, already produced by unit 9's `discover` node.
- Produces: `dedupe`, `fetch_jd`, `staleness` (see Interfaces Produced above).

- [ ] **Step 1: Write the failing tests**

Add to `tests/graph/test_skeleton_graphs.py`, after the existing `_FAKE_POSTINGS` constant and before `test_poll_graph_pauses_at_the_search_plan_gate`:

```python
_DUPLICATE_POSTINGS = [
    {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    },
    {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer (dupe)",
        "city": "Berlin",
        "url": "https://example.com/1-dupe",
        "jd_text": "JD (dupe copy)",
    },
]
_MIXED_JD_POSTINGS = [
    {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Has JD",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    },
    {
        "id": "ats:Acme:acme:2",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Missing JD",
        "city": "Berlin",
        "url": "https://example.com/2",
        "jd_text": "",
    },
]
```

Then add these two tests after `test_poll_graph_approve_runs_discover_then_ends`:

```python
def test_poll_graph_dedupes_same_id_within_one_batch(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: _DUPLICATE_POSTINGS
    )
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r5"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.values["postings"] == [_DUPLICATE_POSTINGS[0]]
    conn.close()


def test_poll_graph_drops_postings_with_no_jd_text(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: _MIXED_JD_POSTINGS
    )
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r6"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.values["postings"] == [_MIXED_JD_POSTINGS[0]]
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py -v -k "dedupes or drops_postings"`
Expected: FAIL — both new tests assert on a single deduped/filtered Posting but the current graph (no `dedupe`/`fetch_jd` nodes) passes both raw entries straight through, so `state.values["postings"]` still has 2 items, not 1.

- [ ] **Step 3: Update `src/jobscout/graph/poll.py`**

Replace the module docstring and add the three nodes plus re-wire `build_poll_graph`:

```python
"""Poll graph (DESIGN §4).

Real shape:  plan_search --(gate)--> discover -> dedupe -> fetch_jd -> staleness -> score -> finish
This unit:   plan_search --(gate)--> discover -> dedupe -> fetch_jd -> staleness -> finish

Unit 11 inserts knockout exclusion between `staleness` and `finish`;
unit 12 adds the `score` sub-agent after that.
"""

import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH
from jobscout.discovery.curated_ats import discover_curated_ats
from jobscout.search_plan import derive_search_plan


class PollState(TypedDict):
    criteria: dict
    search_plan: dict
    decision: str
    postings: list


def plan_search(state: PollState) -> dict:
    # ponytail: no Spend logging yet (§13) — same reasoning as onboard's LLM
    # nodes (units 5-7); the cap-enforcement machinery (unit 15) doesn't exist yet.
    plan = derive_search_plan(state["criteria"])
    return {"search_plan": plan.model_dump()}


def search_plan_gate(state: PollState) -> dict:
    """Approval Gate: the Run pauses here until the Operator approves the
    Search Plan (DESIGN §10). Resume value 'reject' aborts the Run."""
    decision = interrupt({"gate": "search_plan", "plan": state["search_plan"]})
    return {"decision": str(decision)}


def _after_gate(state: PollState) -> str:
    """Fail closed (DESIGN §10): only an explicit 'approve' proceeds.
    Anything else — 'reject', a typo, None — aborts the Run."""
    return "discover" if state["decision"] == "approve" else END


def dedupe(state: PollState) -> dict:
    """Exact-key dedupe only (DESIGN §11) — a same-id Posting discovered
    twice in one batch collapses to its first occurrence. Cross-Source
    fuzzy matching (same job, different id) is unit 17's cached LLM call."""
    seen: dict[str, dict] = {}
    for posting in state["postings"]:
        seen.setdefault(posting["id"], posting)
    return {"postings": list(seen.values())}


def fetch_jd(state: PollState) -> dict:
    """A Posting missing JD text does not reach scoring (DESIGN §4).
    ponytail: Curated ATS Sources (unit 9) already return full JD text
    inline — this is a filter, not a fetch, until units 21-22 add
    summary-only Sources (Adzuna/arbeitnow) that need a real HTTP call
    added here."""
    return {"postings": [p for p in state["postings"] if p.get("jd_text")]}


def staleness(state: PollState) -> dict:
    """Pass-through placeholder — real logic is units 18-19."""
    return {}


def finish(state: PollState) -> dict:
    # Unit 11+ insert knockout exclusion/queueing/scoring before this node.
    return {}


def build_poll_graph(
    conn: sqlite3.Connection, companies_path: Path = DEFAULT_COMPANIES_PATH
) -> StateGraph:
    def discover(state: PollState) -> dict:
        # ponytail: Curated ATS Boards only. Units 21-22 add Adzuna/arbeitnow.
        return {"postings": discover_curated_ats(conn, companies_path)}

    g = StateGraph(PollState)
    g.add_node("plan_search", plan_search)
    g.add_node("search_plan_gate", search_plan_gate)
    g.add_node("discover", discover)
    g.add_node("dedupe", dedupe)
    g.add_node("fetch_jd", fetch_jd)
    g.add_node("staleness", staleness)
    g.add_node("finish", finish)
    g.add_edge(START, "plan_search")
    g.add_edge("plan_search", "search_plan_gate")
    g.add_conditional_edges(
        "search_plan_gate", _after_gate, {"discover": "discover", END: END}
    )
    g.add_edge("discover", "dedupe")
    g.add_edge("dedupe", "fetch_jd")
    g.add_edge("fetch_jd", "staleness")
    g.add_edge("staleness", "finish")
    g.add_edge("finish", END)
    return g
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py -v`
Expected: PASS — all poll-graph tests, including the two new ones and the pre-existing approve/reject/fail-closed/gate tests (unchanged, since `_FAKE_POSTINGS`'s one entry already has a unique id and non-empty `jd_text`, so it survives `dedupe`+`fetch_jd` untouched).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions (`tests/test_service.py`'s poll-related tests use the same `_FAKE_PLAN`/single-fake-posting pattern and are unaffected by the new nodes for the same reason).

- [ ] **Step 6: Commit**

```bash
git add src/jobscout/graph/poll.py tests/graph/test_skeleton_graphs.py
git commit -m "feat: wire dedupe/fetch_jd/staleness into the poll graph backbone"
```

---

## Self-Review

**Spec coverage** (`docs/2026-09-01-build-plan.md` unit 10 acceptance criteria):
- "`discover → dedupe → fetch_jd → staleness` run as plain Python, no LLM" → Task 1's three nodes are pure functions, no `jobscout.llm` import.
- "`dedupe` here is exact-key only... LLM tie-break is unit 17" → `dedupe`'s docstring and implementation only compare `id`, nothing else.
- "`staleness` here is a pass-through placeholder — real logic is units 18-19" → `staleness` returns `{}` unconditionally.
- "`fetch_jd` populates full JD text; a Posting missing JD text does not reach scoring" → `fetch_jd` filters on `jd_text` truthiness; "populates" is explicitly not yet implemented (no Source needs it yet) and called out as a `ponytail:` deferral to units 21-22, not silently skipped.

**Placeholder scan:** none — every step has real code and real test assertions.

**Type consistency:** `dedupe`/`fetch_jd`/`staleness` all take `PollState` and return `dict`, matching every other node in `poll.py`; `build_poll_graph`'s signature is unchanged from unit 9, so `CoreService.__init__`'s existing call site (`build_poll_graph(self._conn, self._companies_path)`) needs no changes.
