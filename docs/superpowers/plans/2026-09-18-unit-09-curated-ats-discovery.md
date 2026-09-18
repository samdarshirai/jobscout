# Unit 9: Curated ATS Board Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Poll discovers Postings from the candidate's hand-curated `companies.yaml` Target Companies. For each company, auto-detect which ATS (Greenhouse / Lever / Ashby / Personio) hosts its careers page, cache that per company, fetch its open jobs (or fall back to a `trafilatura` extract of the plain careers page when no ATS is detected), and store each as an `app_posting` row with raw JD text and status `new`. The poll graph gains a real `discover` node between the search-plan gate and `finish`.

**Architecture:** New `jobscout.discovery` subpackage, same "no graph, no store" boundary as `jobscout.resume`/`jobscout.criteria`/`jobscout.search_plan` — plain functions that take an `httpx.Client` (and, only where the ATS-type cache needs it, a `sqlite3.Connection`) rather than owning either. Four leaf modules: `companies.py` (loads `companies.yaml`), `ats.py` (the four per-ATS fetchers + fixed-order detection), `fallback.py` (the `trafilatura` careers-page extract), `curated_ats.py` (orchestrates the three into normalized posting dicts, owns the dedupe-id scheme and the `app_company_ats` cache table's reads/writes). `jobscout.graph.poll.build_poll_graph` gains a required `conn` parameter (the `discover` node needs the ATS-type cache) and a `companies_path` parameter (test isolation, same pattern as `criteria_path`); `CoreService` passes its own connection and a new `companies_path` constructor param through. `discover`'s output (`state["postings"]`, plain dicts) is persisted by `CoreService.resume_run` on completion, mirroring how `_save_onboarding_results` persists `run_onboarding`'s output — graph nodes stay DB-free and `CoreService` remains the only thing that writes `app_posting`, with one narrow, explicit exception (see Global Constraints): the `discover` closure's two small ATS-type cache helpers, which take `conn` as a plain parameter to read/write `app_company_ats` only, never `app_posting`.

**Tech Stack:** Python ≥3.12, `httpx` (new *direct* dependency — already present transitively via `langchain-openai`, verified installed at 0.28.1; pinning it directly since this unit calls it directly for the first time), `trafilatura` (new direct dependency, not yet installed), stdlib `xml.etree.ElementTree` (Personio's feed is XML — no XML library dependency needed), `hashlib` (dedupe fallback key, same scheme `jobscout.storage.schema` already documents), `pyyaml` (already a direct dependency, unit 7), pytest + `monkeypatch` + `httpx.MockTransport` (stdlib-adjacent — ships with `httpx`, no network in tests, no new test dependency).

**Spec:** `docs/2026-09-01-build-plan.md` unit 9; `docs/DESIGN.md` §3 (Board discovery — the four-Source table, "ATS type auto-detected once per company and cached", "`companies.yaml` in the repo, hand-editable"), §4 (graph shape — `plan_search --(gate)--> discover --> ...`; this unit adds `discover`, unit 10 adds `dedupe/fetch_jd/staleness` after it), §11 (dedupe key scheme, `app_posting`'s columns). Vocabulary: `CONTEXT.md` Posting / JD / Source / Curated ATS Board / Target Company, used verbatim in this plan's field descriptions and module docstrings.

## Global Constraints

- **`jobscout.discovery.*` modules do no SQLite writes and hold no connection of their own**, except `curated_ats.py`'s two small helpers that read/write the `app_company_ats` cache table — those take a `sqlite3.Connection` as a plain parameter (same shape as `jobscout.criteria.derive_criteria` taking a `dict`), never store one on an object. Posting persistence into `app_posting` is `CoreService`'s job alone, done once the poll graph run completes — no module in `jobscout.discovery` touches `app_posting`.
- **Dedupe id is namespaced beyond the schema comment's literal wording.** `schema.sql`'s `app_posting.id` comment says "ATS job-id when the posting came from a known ATS" — used bare, two different companies on the same ATS vendor (e.g. two Greenhouse boards) could mint colliding native ids. **Ruling: `id = f"{source}:{company_slug}:{external_id}"` when an ATS/native id exists, else `sha256(company_slug|title|city)` (all lowercased)** — same collision-avoidance intent as the schema comment, safe across companies. `source` is exactly the format `schema.sql` already documents: `ats:<company>` or `careers:<company>` (using the company's display *name*, not its slug, to match that comment precisely).
- **ATS probing happens at most once per company, ever** (until something clears the cache — no unit does that yet, which is correct: `companies.yaml` entries are long-lived). `app_company_ats.ats_type` can be `greenhouse | lever | ashby | personio | none`; `"none"` is cached too, so a company with no detectable ATS isn't re-probed against all four vendors every poll — it goes straight to the `trafilatura` fallback (or is skipped, if it has no `careers_url`).
- **Detection order is fixed: greenhouse, lever, ashby, personio.** First fetcher that returns a 200 with a parseable body wins; this is deterministic, not "whichever answers fastest" — required for the cache to be stable and for tests to assert a specific winner when a test double could satisfy more than one.
- **The `trafilatura` fallback is one Posting per company, not one per job** — DESIGN §3 calls it "Fallback extract" for companies with *no* detectable ATS, the lowest-fidelity Source in the table; a generic careers page has no structured per-job boundary to split on without an LLM call, and this unit's `discover` node is plain Python (no LLM, per DESIGN §4's node table). Splitting it into real per-job postings, if ever wanted, is a future unit's job, not silently attempted here.
- **`build_poll_graph`'s new `conn` parameter is a breaking, not additive, signature change** (unlike unit 7's additive `criteria_path`) — the `discover` node cannot exist without a connection to read/write the ATS-type cache. Both call sites that build a poll graph (`CoreService.__init__`, `tests/graph/test_skeleton_graphs.py::_compile`) are updated in this plan; no other call site exists (grepped: only those two).
- **`companies.yaml` lives at the repo root**, not under `data/` — `DESIGN §3` says "in the repo, hand-editable," and `data/*` is gitignored except `data/example/` (`.gitignore`, candidate-derived files like `criteria.yaml`/`resume.pdf`). A target-company list isn't derived from the resume the way `criteria.yaml` is, and the design explicitly wants it committed. Shipped with zero entries (`companies: []`) — this plan does not fabricate real employer names; the candidate fills it in herself, same as she supplies her own resume.
- **Tests make no real network calls** — every HTTP interaction goes through `httpx.Client(transport=httpx.MockTransport(...))`; no `respx` or other new test dependency needed, `MockTransport` ships with the already-installed `httpx`.
- Python ≥3.12; `uv` packaging; pytest; TDD (write failing test, see it fail, implement, see it pass, commit).

## Interfaces Produced (later units consume these)

- `jobscout.discovery.companies.TargetCompany` — `pydantic.BaseModel`, fields `name: str`, `slug: str`, `careers_url: str | None = None`.
- `jobscout.discovery.companies.DEFAULT_COMPANIES_PATH: Path` — `Path("companies.yaml")`.
- `jobscout.discovery.companies.load_companies(path: Path = DEFAULT_COMPANIES_PATH) -> list[TargetCompany]` — `[]` if the file is missing or has an empty/absent `companies:` key.
- `jobscout.discovery.ats.ATS_TYPES: tuple[str, ...]` — `("greenhouse", "lever", "ashby", "personio")`, fixed probe order.
- `jobscout.discovery.ats.detect_and_fetch(client: httpx.Client, slug: str) -> tuple[str, list[dict]] | None` — probes in `ATS_TYPES` order, returns `(ats_type, jobs)` for the first hit or `None` if none answer. Each job dict: `external_id: str`, `title: str`, `city: str | None`, `url: str | None`, `jd_text: str`.
- `jobscout.discovery.ats.fetch_from_known_ats(client: httpx.Client, ats_type: str, slug: str) -> list[dict]` — fetch from an already-cached type; `[]` on failure (never raises for a non-200/malformed response).
- `jobscout.discovery.fallback.fetch_careers_page_posting(client: httpx.Client, careers_url: str) -> dict | None` — `{"external_id": None, "title": str, "city": None, "url": str, "jd_text": str}` or `None` if the page 404s or `trafilatura` extracts nothing.
- `jobscout.discovery.curated_ats.discover_curated_ats(conn: sqlite3.Connection, companies_path: Path = DEFAULT_COMPANIES_PATH, client: httpx.Client | None = None) -> list[dict]` — the unit's main entry point. Each returned dict has exactly the columns `CoreService._save_discovered_postings` inserts: `id`, `source`, `company`, `title`, `city`, `url`, `jd_text`.
- `jobscout.graph.poll.build_poll_graph(conn: sqlite3.Connection, companies_path: Path = DEFAULT_COMPANIES_PATH) -> StateGraph` — signature change (was `build_poll_graph()`); `PollState` gains no new keys (`postings: list` already existed, now actually populated).
- `jobscout.service.CoreService.__init__(self, db_path: Path = DEFAULT_DB_PATH, criteria_path: Path | None = None, companies_path: Path | None = None) -> None` — `companies_path` defaults to `DEFAULT_COMPANIES_PATH` when `None`.

---

## File Structure

- `companies.yaml` (repo root, new, tracked) — `{"companies": []}`, hand-edited by the candidate later.
- `src/jobscout/discovery/__init__.py` (new, empty) — marks the package.
- `src/jobscout/discovery/companies.py` (new) — `TargetCompany`, `load_companies`.
- `tests/discovery/__init__.py` (new, empty).
- `tests/discovery/test_companies.py` (new) — load round-trip, missing-file, empty-file.
- `src/jobscout/discovery/ats.py` (new) — the four per-ATS fetchers, `detect_and_fetch`, `fetch_from_known_ats`.
- `tests/discovery/test_ats.py` (new) — one fetcher test per ATS type (canned response via `MockTransport`), `detect_and_fetch` ordering + all-miss case.
- `src/jobscout/discovery/fallback.py` (new) — `fetch_careers_page_posting`.
- `tests/discovery/test_fallback.py` (new) — 200-with-extractable-text, 200-with-nothing-extractable, 404.
- `src/jobscout/discovery/curated_ats.py` (new) — dedupe-id scheme, ATS-type cache read/write, `discover_curated_ats`.
- `tests/discovery/test_curated_ats.py` (new) — cache-miss probes and caches; cache-hit skips probing; `"none"`-cached company uses fallback; company with `"none"` cached and no `careers_url` yields nothing; dedupe-id shape for both branches.
- Modify: `src/jobscout/storage/schema.sql` — new `app_company_ats` table.
- Modify: `src/jobscout/graph/poll.py` — `build_poll_graph` takes `conn`/`companies_path`; new `discover` node; gate's approve edge now routes to `discover`, which then routes to `finish`.
- Modify: `tests/graph/test_skeleton_graphs.py` — `_compile` threads a builder function that receives `conn`; poll tests stub `jobscout.graph.poll.discover_curated_ats`; new assertion that `discover`'s output lands in `state.values["postings"]`.
- Modify: `src/jobscout/service.py` — `companies_path` constructor param; `self._poll` built with `conn`/`companies_path`; new `_save_discovered_postings`, called from `resume_run` on completion.
- Modify: `tests/test_service.py` — `_svc` helper passes a `tmp_path`-scoped `companies_path`; existing `trigger_run`/`resume_run` tests stub `jobscout.graph.poll.discover_curated_ats`; new tests asserting discovered postings land in `app_posting` after an approved run, and that a rejected run persists nothing.
- Modify: `tests/test_repo_hygiene.py` — assert `companies.yaml` is tracked (not gitignored), mirroring the existing `data/example/` assertion.
- Modify: `pyproject.toml` — add `httpx>=0.28` and `trafilatura>=1.12` to `dependencies`.

---

## Task 1: `companies.yaml` + `jobscout.discovery.companies`

**Files:**
- Create: `companies.yaml`
- Create: `src/jobscout/discovery/__init__.py`
- Create: `src/jobscout/discovery/companies.py`
- Create: `tests/discovery/__init__.py`
- Create: `tests/discovery/test_companies.py`
- Modify: `tests/test_repo_hygiene.py`

**Interfaces:**
- Produces: `TargetCompany(name: str, slug: str, careers_url: str | None = None)`; `load_companies(path: Path = DEFAULT_COMPANIES_PATH) -> list[TargetCompany]`; `DEFAULT_COMPANIES_PATH = Path("companies.yaml")`.

- [ ] **Step 1: Write the failing tests**

`tests/discovery/__init__.py`: empty file.

`tests/discovery/test_companies.py`:

```python
from pathlib import Path

from jobscout.discovery.companies import TargetCompany, load_companies


def test_load_companies_round_trips(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text(
        "companies:\n"
        "  - name: Acme GmbH\n"
        "    slug: acme\n"
        "    careers_url: https://acme.example/careers\n"
        "  - name: Contoso\n"
        "    slug: contoso\n"
    )

    companies = load_companies(path)

    assert companies == [
        TargetCompany(name="Acme GmbH", slug="acme", careers_url="https://acme.example/careers"),
        TargetCompany(name="Contoso", slug="contoso", careers_url=None),
    ]


def test_load_companies_missing_file_returns_empty(tmp_path):
    assert load_companies(tmp_path / "nope.yaml") == []


def test_load_companies_empty_file_returns_empty(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text("companies: []\n")
    assert load_companies(path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/discovery/test_companies.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.discovery'`

- [ ] **Step 3: Write minimal implementation**

`src/jobscout/discovery/__init__.py`: empty.

`src/jobscout/discovery/companies.py`:

```python
"""Target Companies — the hand-curated list `discover` searches (DESIGN §3).

CONTEXT.md: Target Company — a company the Candidate would actually work
for, kept hand-listed in the repo.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel

DEFAULT_COMPANIES_PATH = Path("companies.yaml")


class TargetCompany(BaseModel):
    name: str
    slug: str
    careers_url: str | None = None


def load_companies(path: Path = DEFAULT_COMPANIES_PATH) -> list[TargetCompany]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return [TargetCompany(**c) for c in data.get("companies") or []]
```

`companies.yaml` (repo root):

```yaml
# Target Companies (CONTEXT.md) — hand-curated, ~20-30 companies the
# candidate would actually work for (DESIGN §3). ATS type is
# auto-detected from `slug`, cached, not listed here. `careers_url` is
# only used as the `trafilatura` fallback when no ATS is detected.
companies: []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/discovery/test_companies.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the hygiene assertion and verify it passes**

Add to `tests/test_repo_hygiene.py`, alongside `test_example_data_stays_tracked`:

```python
def test_companies_yaml_stays_tracked():
    assert not _is_ignored("companies.yaml"), "companies.yaml must be tracked (DESIGN §3)"
```

Run: `uv run pytest tests/test_repo_hygiene.py -v`
Expected: PASS — passes once `companies.yaml` is `git add`ed in Step 6 (`git check-ignore` returns 1/not-ignored for untracked-but-not-excluded files too, so this can pass pre-commit; re-run after adding to be sure).

- [ ] **Step 6: Commit**

```bash
git add companies.yaml src/jobscout/discovery/__init__.py src/jobscout/discovery/companies.py tests/discovery/__init__.py tests/discovery/test_companies.py tests/test_repo_hygiene.py
git commit -m "feat: companies.yaml and the Target Company loader"
```

---

## Task 2: `jobscout.discovery.ats` — the four ATS fetchers + detection

**Files:**
- Create: `src/jobscout/discovery/ats.py`
- Create: `tests/discovery/test_ats.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `ATS_TYPES`, `detect_and_fetch(client, slug) -> tuple[str, list[dict]] | None`, `fetch_from_known_ats(client, ats_type, slug) -> list[dict]`. Job dict shape: `{"external_id": str, "title": str, "city": str | None, "url": str | None, "jd_text": str}`.

- [ ] **Step 1: Add the dependency**

Add to `pyproject.toml`'s `dependencies`: `"httpx>=0.28",`

Run: `uv sync`

- [ ] **Step 2: Write the failing tests**

`tests/discovery/test_ats.py`:

```python
import httpx
import pytest

from jobscout.discovery.ats import detect_and_fetch, fetch_from_known_ats


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_detect_and_fetch_greenhouse():
    def handler(request):
        assert request.url.path == "/v1/boards/acme/jobs"
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 1,
                        "title": "Senior Frontend Engineer",
                        "location": {"name": "Berlin"},
                        "content": "<p>Do things</p>",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    }
                ]
            },
        )

    ats_type, jobs = detect_and_fetch(_client(handler), "acme")

    assert ats_type == "greenhouse"
    assert jobs == [
        {
            "external_id": "1",
            "title": "Senior Frontend Engineer",
            "city": "Berlin",
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "jd_text": "<p>Do things</p>",
        }
    ]


def test_detect_and_fetch_lever_when_greenhouse_misses():
    def handler(request):
        if "greenhouse" in request.url.host:
            return httpx.Response(404)
        assert "lever" in request.url.host
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Backend Engineer",
                    "categories": {"location": "Remote"},
                    "descriptionPlain": "Ship things",
                    "hostedUrl": "https://jobs.lever.co/acme/abc",
                }
            ],
        )

    ats_type, jobs = detect_and_fetch(_client(handler), "acme")

    assert ats_type == "lever"
    assert jobs == [
        {
            "external_id": "abc",
            "title": "Backend Engineer",
            "city": "Remote",
            "url": "https://jobs.lever.co/acme/abc",
            "jd_text": "Ship things",
        }
    ]


def test_detect_and_fetch_ashby_when_greenhouse_and_lever_miss():
    def handler(request):
        if "ashbyhq" in request.url.host:
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "xyz",
                            "title": "Platform Engineer",
                            "location": "Berlin, Germany",
                            "descriptionPlain": "Own the platform",
                            "jobUrl": "https://jobs.ashbyhq.com/acme/xyz",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    ats_type, jobs = detect_and_fetch(_client(handler), "acme")

    assert ats_type == "ashby"
    assert jobs[0]["external_id"] == "xyz"
    assert jobs[0]["city"] == "Berlin, Germany"


def test_detect_and_fetch_personio_when_all_others_miss():
    xml = (
        "<workzag-jobs>"
        "<position>"
        "<id>42</id><name>Data Engineer</name><office>Munich</office>"
        "<careerSiteUrl>https://acme.jobs.personio.de/job/42</careerSiteUrl>"
        "<jobDescriptions>"
        "<jobDescription><name>Role</name><value>Build pipelines</value></jobDescription>"
        "</jobDescriptions>"
        "</position>"
        "</workzag-jobs>"
    )

    def handler(request):
        if "personio" in request.url.host:
            return httpx.Response(200, text=xml)
        return httpx.Response(404)

    ats_type, jobs = detect_and_fetch(_client(handler), "acme")

    assert ats_type == "personio"
    assert jobs == [
        {
            "external_id": "42",
            "title": "Data Engineer",
            "city": "Munich",
            "url": "https://acme.jobs.personio.de/job/42",
            "jd_text": "Build pipelines",
        }
    ]


def test_detect_and_fetch_returns_none_when_all_miss():
    result = detect_and_fetch(_client(lambda request: httpx.Response(404)), "ghost-co")
    assert result is None


def test_fetch_from_known_ats_skips_probing_other_types():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(
            200, json=[{"id": "1", "text": "T", "categories": {}, "descriptionPlain": "", "hostedUrl": ""}]
        )

    jobs = fetch_from_known_ats(_client(handler), "lever", "acme")

    assert len(calls) == 1
    assert "lever" in calls[0]
    assert jobs[0]["external_id"] == "1"


def test_fetch_from_known_ats_returns_empty_list_on_failure():
    jobs = fetch_from_known_ats(_client(lambda request: httpx.Response(500)), "greenhouse", "acme")
    assert jobs == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/discovery/test_ats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.discovery.ats'`

- [ ] **Step 4: Write minimal implementation**

`src/jobscout/discovery/ats.py`:

```python
"""ATS auto-detection + per-type job fetch (DESIGN §3).

CONTEXT.md: Curated ATS Board — the ATS (Greenhouse / Lever / Ashby /
Personio) hosting a Target Company's careers page, auto-detected and
cached per company. Probes in a fixed order so the result is
deterministic and the cache (owned by `jobscout.discovery.curated_ats`)
is stable.
"""

from xml.etree import ElementTree

import httpx

ATS_TYPES = ("greenhouse", "lever", "ashby", "personio")


def _fetch_greenhouse(client: httpx.Client, slug: str) -> list[dict] | None:
    resp = client.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        params={"content": "true"},
    )
    if resp.status_code != 200:
        return None
    return [
        {
            "external_id": str(j["id"]),
            "title": j["title"],
            "city": (j.get("location") or {}).get("name"),
            "url": j.get("absolute_url"),
            "jd_text": j.get("content", ""),
        }
        for j in resp.json().get("jobs", [])
    ]


def _fetch_lever(client: httpx.Client, slug: str) -> list[dict] | None:
    resp = client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    if resp.status_code != 200:
        return None
    jobs = resp.json()
    if not isinstance(jobs, list):
        return None
    return [
        {
            "external_id": str(j["id"]),
            "title": j["text"],
            "city": (j.get("categories") or {}).get("location"),
            "url": j.get("hostedUrl"),
            "jd_text": j.get("descriptionPlain", ""),
        }
        for j in jobs
    ]


def _fetch_ashby(client: httpx.Client, slug: str) -> list[dict] | None:
    resp = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if resp.status_code != 200:
        return None
    return [
        {
            "external_id": str(j["id"]),
            "title": j["title"],
            "city": j.get("location"),
            "url": j.get("jobUrl"),
            "jd_text": j.get("descriptionPlain", ""),
        }
        for j in resp.json().get("jobs", [])
    ]


def _fetch_personio(client: httpx.Client, slug: str) -> list[dict] | None:
    resp = client.get(f"https://{slug}.jobs.personio.de/xml")
    if resp.status_code != 200:
        return None
    try:
        root = ElementTree.fromstring(resp.text)
    except ElementTree.ParseError:
        return None
    jobs = []
    for position in root.findall("position"):
        description = " ".join(
            (d.findtext("value") or "").strip()
            for d in position.findall("./jobDescriptions/jobDescription")
        )
        jobs.append(
            {
                "external_id": position.findtext("id"),
                "title": position.findtext("name"),
                "city": position.findtext("office"),
                "url": position.findtext("careerSiteUrl"),
                "jd_text": description,
            }
        )
    return jobs


_FETCHERS = {
    "greenhouse": _fetch_greenhouse,
    "lever": _fetch_lever,
    "ashby": _fetch_ashby,
    "personio": _fetch_personio,
}


def detect_and_fetch(client: httpx.Client, slug: str) -> tuple[str, list[dict]] | None:
    """Probe each ATS type in `ATS_TYPES` order; first hit wins."""
    for ats_type in ATS_TYPES:
        jobs = _FETCHERS[ats_type](client, slug)
        if jobs is not None:
            return ats_type, jobs
    return None


def fetch_from_known_ats(client: httpx.Client, ats_type: str, slug: str) -> list[dict]:
    """Fetch from an already-cached ATS type. Never raises on a bad
    response — a transient failure just yields no Postings this Poll."""
    return _FETCHERS[ats_type](client, slug) or []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/discovery/test_ats.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/jobscout/discovery/ats.py tests/discovery/test_ats.py
git commit -m "feat: ATS auto-detection and per-type job fetch"
```

---

## Task 3: `jobscout.discovery.fallback` — trafilatura careers-page extract

**Files:**
- Create: `src/jobscout/discovery/fallback.py`
- Create: `tests/discovery/test_fallback.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `fetch_careers_page_posting(client: httpx.Client, careers_url: str) -> dict | None`.

- [ ] **Step 1: Add the dependency**

Add to `pyproject.toml`'s `dependencies`: `"trafilatura>=1.12",`

Run: `uv sync`

- [ ] **Step 2: Write the failing tests**

`tests/discovery/test_fallback.py`:

```python
import httpx

from jobscout.discovery.fallback import fetch_careers_page_posting


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_careers_page_posting_extracts_text(monkeypatch):
    html = "<html><body><main><p>We are hiring engineers in Berlin.</p></main></body></html>"
    monkeypatch.setattr(
        "jobscout.discovery.fallback.trafilatura.extract",
        lambda downloaded: "We are hiring engineers in Berlin.",
    )

    result = fetch_careers_page_posting(
        _client(lambda request: httpx.Response(200, text=html)),
        "https://acme.example/careers",
    )

    assert result == {
        "external_id": None,
        "title": "Careers page",
        "city": None,
        "url": "https://acme.example/careers",
        "jd_text": "We are hiring engineers in Berlin.",
    }


def test_fetch_careers_page_posting_returns_none_when_nothing_extractable(monkeypatch):
    monkeypatch.setattr(
        "jobscout.discovery.fallback.trafilatura.extract", lambda downloaded: None
    )

    result = fetch_careers_page_posting(
        _client(lambda request: httpx.Response(200, text="<html></html>")),
        "https://acme.example/careers",
    )

    assert result is None


def test_fetch_careers_page_posting_returns_none_on_404():
    result = fetch_careers_page_posting(
        _client(lambda request: httpx.Response(404)), "https://acme.example/careers"
    )
    assert result is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/discovery/test_fallback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.discovery.fallback'`

- [ ] **Step 4: Write minimal implementation**

`src/jobscout/discovery/fallback.py`:

```python
"""`trafilatura` careers-page fallback (DESIGN §3) — Target Companies
with no detectable ATS. One Posting per company, the whole careers
page as JD text; no per-job splitting (plain Python, no LLM, DESIGN §4).
"""

import httpx
import trafilatura


def fetch_careers_page_posting(client: httpx.Client, careers_url: str) -> dict | None:
    resp = client.get(careers_url)
    if resp.status_code != 200:
        return None
    text = trafilatura.extract(resp.text)
    if not text:
        return None
    return {
        "external_id": None,
        "title": "Careers page",
        "city": None,
        "url": careers_url,
        "jd_text": text,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/discovery/test_fallback.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/jobscout/discovery/fallback.py tests/discovery/test_fallback.py
git commit -m "feat: trafilatura careers-page fallback for undetected ATS"
```

---

## Task 4: `jobscout.discovery.curated_ats` — orchestration, cache, dedupe id

**Files:**
- Create: `src/jobscout/discovery/curated_ats.py`
- Create: `tests/discovery/test_curated_ats.py`
- Modify: `src/jobscout/storage/schema.sql`

**Interfaces:**
- Consumes: `jobscout.discovery.companies.{TargetCompany, load_companies, DEFAULT_COMPANIES_PATH}` (Task 1), `jobscout.discovery.ats.{detect_and_fetch, fetch_from_known_ats}` (Task 2), `jobscout.discovery.fallback.fetch_careers_page_posting` (Task 3).
- Produces: `discover_curated_ats(conn: sqlite3.Connection, companies_path: Path = DEFAULT_COMPANIES_PATH, client: httpx.Client | None = None) -> list[dict]`. Each dict: `id, source, company, title, city, url, jd_text`.

- [ ] **Step 1: Add the cache table**

Append to `src/jobscout/storage/schema.sql` (after `app_posting`, before `app_score`):

```sql
-- CONTEXT: Curated ATS Board — auto-detected ATS type per Target
-- Company, cached so it's probed at most once (§3).
CREATE TABLE IF NOT EXISTS app_company_ats (
    company_slug  TEXT PRIMARY KEY,
    ats_type      TEXT NOT NULL,  -- greenhouse | lever | ashby | personio | none
    detected_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 2: Write the failing tests**

`tests/discovery/test_curated_ats.py`:

```python
from pathlib import Path

import httpx
import pytest

from jobscout.discovery.curated_ats import discover_curated_ats
from jobscout.storage.db import get_connection, init_db


def _conn(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    return conn


def _companies_file(tmp_path, yaml_text: str) -> Path:
    path = tmp_path / "companies.yaml"
    path.write_text(yaml_text)
    return path


def test_discover_curated_ats_probes_and_caches_a_new_company(tmp_path):
    conn = _conn(tmp_path)
    path = _companies_file(tmp_path, "companies:\n  - name: Acme GmbH\n    slug: acme\n")

    def handler(request):
        if "greenhouse" in request.url.host:
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 1,
                            "title": "Senior Frontend Engineer",
                            "location": {"name": "Berlin"},
                            "content": "JD text",
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    postings = discover_curated_ats(conn, path, client)

    assert postings == [
        {
            "id": "ats:Acme GmbH:acme:1",
            "source": "ats:Acme GmbH",
            "company": "Acme GmbH",
            "title": "Senior Frontend Engineer",
            "city": "Berlin",
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "jd_text": "JD text",
        }
    ]
    cached = conn.execute(
        "SELECT ats_type FROM app_company_ats WHERE company_slug = 'acme'"
    ).fetchone()
    assert cached["ats_type"] == "greenhouse"
    conn.close()


def test_discover_curated_ats_uses_the_cache_and_skips_other_probes(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO app_company_ats (company_slug, ats_type) VALUES ('acme', 'lever')"
    )
    conn.commit()
    path = _companies_file(tmp_path, "companies:\n  - name: Acme GmbH\n    slug: acme\n")

    calls = []

    def handler(request):
        calls.append(request.url.host)
        assert "lever" in request.url.host
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Backend Engineer",
                    "categories": {"location": "Remote"},
                    "descriptionPlain": "Ship things",
                    "hostedUrl": "https://jobs.lever.co/acme/abc",
                }
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    postings = discover_curated_ats(conn, path, client)

    assert len(calls) == 1
    assert postings[0]["id"] == "ats:Acme GmbH:acme:abc"
    conn.close()


def test_discover_curated_ats_falls_back_to_trafilatura_when_no_ats_detected(
    tmp_path, monkeypatch
):
    conn = _conn(tmp_path)
    path = _companies_file(
        tmp_path,
        "companies:\n  - name: Acme GmbH\n    slug: acme\n"
        "    careers_url: https://acme.example/careers\n",
    )
    monkeypatch.setattr(
        "jobscout.discovery.curated_ats.fetch_careers_page_posting",
        lambda client, careers_url: {
            "external_id": None,
            "title": "Careers page",
            "city": None,
            "url": careers_url,
            "jd_text": "We hire engineers.",
        },
    )

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    postings = discover_curated_ats(conn, path, client)

    assert postings == [
        {
            "id": __import__("hashlib").sha256(b"acme|careers page|").hexdigest(),
            "source": "careers:Acme GmbH",
            "company": "Acme GmbH",
            "title": "Careers page",
            "city": None,
            "url": "https://acme.example/careers",
            "jd_text": "We hire engineers.",
        }
    ]
    cached = conn.execute(
        "SELECT ats_type FROM app_company_ats WHERE company_slug = 'acme'"
    ).fetchone()
    assert cached["ats_type"] == "none"
    conn.close()


def test_discover_curated_ats_skips_company_with_no_ats_and_no_careers_url(tmp_path):
    conn = _conn(tmp_path)
    path = _companies_file(tmp_path, "companies:\n  - name: Acme GmbH\n    slug: acme\n")

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    postings = discover_curated_ats(conn, path, client)

    assert postings == []
    conn.close()


def test_discover_curated_ats_with_no_companies_returns_empty(tmp_path):
    conn = _conn(tmp_path)
    path = _companies_file(tmp_path, "companies: []\n")
    postings = discover_curated_ats(conn, path)
    assert postings == []
    conn.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/discovery/test_curated_ats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.discovery.curated_ats'`

- [ ] **Step 4: Write minimal implementation**

`src/jobscout/discovery/curated_ats.py`:

```python
"""Curated ATS Board discovery — orchestrates `companies.yaml` + the
ATS-type cache + the `trafilatura` fallback into normalized Postings
(DESIGN §3; CONTEXT.md: Posting, Curated ATS Board).

No LLM, plain Python (DESIGN §4's node table). Holds no SQLite
connection of its own — `conn` is a parameter, same shape as
`jobscout.criteria.derive_criteria` taking a plain `dict`.
"""

import hashlib
import sqlite3
from pathlib import Path

import httpx

from jobscout.discovery.ats import detect_and_fetch, fetch_from_known_ats
from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH, TargetCompany, load_companies
from jobscout.discovery.fallback import fetch_careers_page_posting


def _dedupe_id(
    source: str, slug: str, external_id: str | None, title: str, city: str | None
) -> str:
    """§11 dedupe key, namespaced by Source so two companies on the same
    ATS vendor can't collide on a bare native id (schema.sql's literal
    "ATS job-id" wording undershoots this — see plan Global Constraints)."""
    if external_id:
        return f"{source}:{slug}:{external_id}"
    raw = f"{slug.lower()}|{title.lower()}|{(city or '').lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cached_ats_type(conn: sqlite3.Connection, slug: str) -> str | None:
    row = conn.execute(
        "SELECT ats_type FROM app_company_ats WHERE company_slug = ?", (slug,)
    ).fetchone()
    return row["ats_type"] if row else None


def _cache_ats_type(conn: sqlite3.Connection, slug: str, ats_type: str) -> None:
    conn.execute(
        "INSERT INTO app_company_ats (company_slug, ats_type) VALUES (?, ?)", (slug, ats_type)
    )
    conn.commit()


def _discover_company(
    conn: sqlite3.Connection, client: httpx.Client, company: TargetCompany
) -> list[dict]:
    ats_type = _cached_ats_type(conn, company.slug)
    if ats_type is None:
        detected = detect_and_fetch(client, company.slug)
        ats_type = detected[0] if detected else "none"
        _cache_ats_type(conn, company.slug, ats_type)
        raw_jobs = detected[1] if detected else []
    elif ats_type == "none":
        raw_jobs = []
    else:
        raw_jobs = fetch_from_known_ats(client, ats_type, company.slug)

    if ats_type == "none":
        if not company.careers_url:
            return []
        posting = fetch_careers_page_posting(client, company.careers_url)
        if posting is None:
            return []
        source = f"careers:{company.name}"
        return [
            {
                "id": _dedupe_id(source, company.slug, None, posting["title"], posting["city"]),
                "source": source,
                "company": company.name,
                "title": posting["title"],
                "city": posting["city"],
                "url": posting["url"],
                "jd_text": posting["jd_text"],
            }
        ]

    source = f"ats:{company.name}"
    return [
        {
            "id": _dedupe_id(source, company.slug, j["external_id"], j["title"], j["city"]),
            "source": source,
            "company": company.name,
            "title": j["title"],
            "city": j["city"],
            "url": j["url"],
            "jd_text": j["jd_text"],
        }
        for j in raw_jobs
    ]


def discover_curated_ats(
    conn: sqlite3.Connection,
    companies_path: Path = DEFAULT_COMPANIES_PATH,
    client: httpx.Client | None = None,
) -> list[dict]:
    """All Target Companies' Postings for this Poll."""
    companies = load_companies(companies_path)
    owns_client = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        postings = []
        for company in companies:
            postings.extend(_discover_company(conn, client, company))
        return postings
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/discovery/test_curated_ats.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full suite (schema change touches shared fixtures)**

Run: `uv run pytest -q`
Expected: PASS, no regressions (new table is additive, `CREATE TABLE IF NOT EXISTS`)

- [ ] **Step 7: Commit**

```bash
git add src/jobscout/storage/schema.sql src/jobscout/discovery/curated_ats.py tests/discovery/test_curated_ats.py
git commit -m "feat: orchestrate curated ATS discovery with a per-company ATS-type cache"
```

---

## Task 5: Wire `discover` into the poll graph; `CoreService` persists Postings

**Files:**
- Modify: `src/jobscout/graph/poll.py`
- Modify: `tests/graph/test_skeleton_graphs.py`
- Modify: `src/jobscout/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: `jobscout.discovery.curated_ats.discover_curated_ats` (Task 4), `jobscout.discovery.companies.DEFAULT_COMPANIES_PATH` (Task 1).
- Produces: `build_poll_graph(conn, companies_path=DEFAULT_COMPANIES_PATH)` (signature change); `CoreService.__init__(..., companies_path=None)`; `CoreService._save_discovered_postings(postings: list[dict]) -> None`.

- [ ] **Step 1: Write the failing tests**

In `tests/graph/test_skeleton_graphs.py`, replace the `POLL_INIT`/`_compile` setup and the four poll tests:

```python
from pathlib import Path

from langgraph.types import Command

from jobscout.criteria import Criteria, KnockoutRule, ScoredDimension
from jobscout.graph.onboard import build_onboard_graph
from jobscout.graph.poll import build_poll_graph
from jobscout.resume import ExtractedProfile
from jobscout.search_plan import SearchPlan
from jobscout.storage.db import get_checkpointer, get_connection, init_db

POLL_INIT = {"criteria": {}, "search_plan": {}, "decision": "", "postings": []}
FIXTURE = Path(__file__).parent.parent.parent / "data" / "example" / "fake_resume.pdf"
_FAKE_PLAN = SearchPlan(queries=["senior frontend engineer"], sources=["adzuna"], companies=[])
_FAKE_POSTINGS = [
    {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    }
]


def _compile(build_fn, tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    graph = build_fn(conn).compile(checkpointer=get_checkpointer(conn))
    return graph, conn


def _stub_search_plan(monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )


def _stub_discovery(monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: _FAKE_POSTINGS
    )


def test_poll_graph_pauses_at_the_search_plan_gate(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r1"}}
    graph.invoke(POLL_INIT, cfg)
    state = graph.get_state(cfg)
    assert state.next == ("search_plan_gate",)
    assert state.values["search_plan"] == _FAKE_PLAN.model_dump()
    conn.close()


def test_poll_graph_approve_runs_discover_then_ends(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    _stub_discovery(monkeypatch)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r2"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["decision"] == "approve"
    assert state.values["postings"] == _FAKE_POSTINGS
    conn.close()


def test_poll_graph_reject_routes_straight_to_end(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r3"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="reject"), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["decision"] == "reject"
    assert state.values["postings"] == []
    conn.close()


def test_poll_graph_unrecognized_decision_fails_closed(tmp_path, monkeypatch):
    _stub_search_plan(monkeypatch)
    graph, conn = _compile(lambda conn: build_poll_graph(conn), tmp_path)
    cfg = {"configurable": {"thread_id": "r4"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="garbage"), cfg)
    state = graph.get_state(cfg)
    assert state.next == ()
    assert state.values["decision"] == "garbage"
    conn.close()
```

Leave `test_onboard_graph_walks_through_both_gates_and_derives_criteria` as-is, but change its `_compile` call to `_compile(lambda conn: build_onboard_graph(), tmp_path)` (signature now takes a builder function, not a bare builder).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py -v`
Expected: FAIL — `build_poll_graph() takes 0 positional arguments but 1 was given` (old signature) — confirms the tests are exercising the new contract before it exists.

- [ ] **Step 3: Update `poll.py`**

```python
"""Poll graph (DESIGN §4).

Real shape:  plan_search --(gate)--> discover -> dedupe -> fetch_jd -> staleness -> score -> finish
This unit:   plan_search --(gate)--> discover --> finish

Unit 10 inserts dedupe/fetch_jd/staleness between `discover` and
`finish`; unit 12 adds the `score` sub-agent after that.
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


def finish(state: PollState) -> dict:
    # Units 11+ insert knockouts/queueing/scoring before this node.
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
    g.add_node("finish", finish)
    g.add_edge(START, "plan_search")
    g.add_edge("plan_search", "search_plan_gate")
    g.add_conditional_edges(
        "search_plan_gate", _after_gate, {"discover": "discover", END: END}
    )
    g.add_edge("discover", "finish")
    g.add_edge("finish", END)
    return g
```

- [ ] **Step 4: Run the graph tests to verify they pass**

Run: `uv run pytest tests/graph/test_skeleton_graphs.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Update `service.py`**

Change the imports and `_POLL_INIT`/`__init__`/`trigger_run`/`resume_run`:

```python
from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH
```

```python
class CoreService:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        criteria_path: Path | None = None,
        companies_path: Path | None = None,
    ) -> None:
        self._conn: sqlite3.Connection = get_connection(db_path)
        init_db(self._conn)
        self._criteria_path = criteria_path or DEFAULT_CRITERIA_PATH
        self._companies_path = companies_path or DEFAULT_COMPANIES_PATH
        self._checkpointer = get_checkpointer(self._conn)
        self._poll = build_poll_graph(self._conn, self._companies_path).compile(
            checkpointer=self._checkpointer
        )
        self._onboard = build_onboard_graph().compile(
            checkpointer=self._checkpointer
        )
```

```python
    def resume_run(self, run_id: str, decision: object) -> RunHandle:
        cfg = {"configurable": {"thread_id": run_id}}
        if not self._poll.get_state(cfg).created_at:
            raise ValueError(f"no such run: {run_id!r}")
        self._poll.invoke(Command(resume=decision), cfg)
        handle = self._handle(self._poll, run_id)
        if handle.status == "completed":
            self._save_discovered_postings(handle.state.get("postings", []))
        return handle
```

Add near `_save_criteria`:

```python
    def _save_discovered_postings(self, postings: list[dict]) -> None:
        for p in postings:
            self._conn.execute(
                "INSERT INTO app_posting (id, source, company, title, city, url, jd_text) "
                "VALUES (:id, :source, :company, :title, :city, :url, :jd_text) "
                "ON CONFLICT(id) DO UPDATE SET last_seen_at = datetime('now')",
                p,
            )
        self._conn.commit()
```

- [ ] **Step 6: Update `tests/test_service.py`**

Thread `companies_path` through `_svc` (no new import needed — tests patch `jobscout.graph.poll.discover_curated_ats` by string path, never call it directly):

```python
def _svc(tmp_path) -> CoreService:
    return CoreService(
        db_path=tmp_path / "j.sqlite",
        criteria_path=tmp_path / "criteria.yaml",
        companies_path=tmp_path / "companies.yaml",
    )
```

Every existing test that calls `svc.trigger_run()` and does *not* already stub `jobscout.graph.poll.derive_search_plan` needs no further change for discovery — `discover_curated_ats` reads `companies_path`, which now points at a tmp file that doesn't exist, and `load_companies` returns `[]` for a missing file (Task 1), so `discover` naturally yields `[]` with **no monkeypatch needed** for tests that don't care about postings. Only add a `discover_curated_ats` stub where a test asserts specific posting content.

Update `test_trigger_run_pauses_at_the_search_plan_gate` and friends: no change needed beyond the `_svc` signature already covering `companies_path` — they never reach `discover` (paused at the gate) or reach it with an empty `companies.yaml` (harmless `[]`).

Add two new tests after `test_resume_run_reject_completes`:

```python
def test_resume_run_approve_persists_discovered_postings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    fake_posting = {
        "id": "ats:Acme:acme:1",
        "source": "ats:Acme",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://example.com/1",
        "jd_text": "JD",
    }
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats", lambda conn, path: [fake_posting]
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    h = svc.trigger_run()
    svc.resume_run(h.run_id, "approve")

    row = svc._conn.execute(
        "SELECT source, company, title, jd_text FROM app_posting WHERE id = ?",
        (fake_posting["id"],),
    ).fetchone()
    assert row["company"] == "Acme"
    assert row["jd_text"] == "JD"
    svc.close()


def test_resume_run_reject_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.derive_search_plan", lambda criteria: _FAKE_PLAN
    )
    svc = _svc(tmp_path)
    _seed_criteria(svc)
    h = svc.trigger_run()
    svc.resume_run(h.run_id, "reject")

    rows = svc._conn.execute("SELECT * FROM app_posting").fetchall()
    assert rows == []
    svc.close()
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, all tests (existing + new)

- [ ] **Step 8: Commit**

```bash
git add src/jobscout/graph/poll.py tests/graph/test_skeleton_graphs.py src/jobscout/service.py tests/test_service.py
git commit -m "feat: wire curated ATS discovery into the poll graph"
```

---

## Self-Review

**Spec coverage** (`docs/2026-09-01-build-plan.md` unit 9 acceptance criteria):
- "`companies.yaml` lists the Target Companies; hand-editable in the repo" → Task 1.
- "ATS type auto-detected once per company and cached" → Task 2 (`detect_and_fetch`) + Task 4 (`app_company_ats` cache, probed only on cache miss).
- "Companies with no detectable ATS fall back to `trafilatura`" → Task 3 + Task 4's `ats_type == "none"` branch.
- "Each discovered Posting is stored with raw JD text and status `new`" → Task 5's `_save_discovered_postings`; `status` defaults to `'new'` in `schema.sql` already (unit 2), not touched here — an `INSERT` naturally gets the default; the `ON CONFLICT` update path only ever touches `last_seen_at`, never resetting an already-progressed Posting back to `new`.
- Open question ("exact endpoints / rate limits verified during implementation") — endpoints used are documented inline in `ats.py`'s module docstring reasoning (this plan) and are the vendors' standard public job-board APIs; rate limits are not enforced in this unit (no volume yet — `companies.yaml` ships empty), left for whichever unit first hits a real 429 (§17's failure-isolation covers that generically already).

**Placeholder scan:** none — every step has real code, every test has concrete assertions.

**Type consistency:** `discover_curated_ats`'s return shape (`id, source, company, title, city, url, jd_text`) matches `app_posting`'s insertable columns exactly (`_save_discovered_postings`'s named parameters); `TargetCompany` fields match every place `company.slug`/`company.name`/`company.careers_url` is read (Task 4); `build_poll_graph(conn, companies_path)` matches both call sites updated in Task 5 (`CoreService.__init__`, `test_skeleton_graphs.py::_compile`).
