# Unit 1: Project Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A clean checkout installs, exposes a `jobscout` CLI whose `--help` runs, passes a pytest smoke test, and never tracks real candidate data.

**Architecture:** A `uv`-managed Python package under `src/jobscout/`. The CLI is a Typer app in `src/jobscout/cli.py` with no real commands yet — later units add subcommands. A `.gitignore` keeps `data/` and `.env` out of git except `data/example/`.

**Tech Stack:** Python ≥3.12, `uv` (packaging + runner), Typer + Rich (CLI), pytest (tests), hatchling (build backend).

**Spec:** `docs/2026-09-01-build-plan.md` unit 1; `docs/DESIGN.md` §19 (deliverables), §16 (interfaces), §2 (data handling), §12 (CLI = Typer + Rich).

## Global Constraints

- Python packaging with `uv`; tests with `pytest` (DESIGN §19).
- `data/` git-ignored in full **except** `data/example/` (DESIGN §19).
- Never track: real resume PDF, `.env`, the SQLite DB, `criteria`, `voice.md` (DESIGN §2, §19).
- CLI is Typer + Rich (DESIGN §12).
- No vector DB, Postgres, Docker, React, or a second agent framework — not now, not as deps (DESIGN §20).
- `requires-python = ">=3.12"`.

---

## File Structure

- `pyproject.toml` — project metadata, deps, `jobscout` console script, hatchling wheel config.
- `src/jobscout/__init__.py` — package marker, `__version__`.
- `src/jobscout/cli.py` — Typer `app`, a `run()` entry point, a `--version` callback. One responsibility: the CLI surface.
- `tests/test_cli_smoke.py` — CLI help + installed-console-script smoke tests.
- `tests/test_repo_hygiene.py` — `.gitignore` and `.env.example` assertions.
- `.gitignore` — secrets, local data, Python/IDE/OS cruft.
- `.env.example` — every secret key, blank, with a DESIGN-section comment each.
- `data/example/.gitkeep` — keeps the demo-data dir present in a clean checkout.

---

## Task 1: uv package + `jobscout` CLI skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/jobscout/__init__.py`
- Create: `src/jobscout/cli.py`
- Test: `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: nothing (first task in the repo).
- Produces:
  - `jobscout.__version__: str`
  - `jobscout.cli.app: typer.Typer` — the CLI app object later units add subcommands to via `@app.command()`.
  - `jobscout.cli.run() -> None` — console-script entry point; calls `app()`.
  - Console script `jobscout` (installed by `uv sync`).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "jobscout"
version = "0.1.0"
description = "A personal job-hunt agent for one real candidate's search."
requires-python = ">=3.12"
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
]

[project.scripts]
jobscout = "jobscout.cli:run"

[dependency-groups]
dev = [
    "pytest>=8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/jobscout"]
```

- [ ] **Step 2: Write the package marker**

`src/jobscout/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Write the failing smoke test**

`tests/test_cli_smoke.py`:

```python
import subprocess

from typer.testing import CliRunner

from jobscout.cli import app

runner = CliRunner()


def test_help_exits_zero_and_names_the_tool():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Jobscout" in result.output


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_console_script_help_runs():
    result = subprocess.run(
        ["uv", "run", "jobscout", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Jobscout" in result.stdout
```

- [ ] **Step 4: Run the test, verify it fails**

Run: `uv run pytest tests/test_cli_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobscout.cli'`.

- [ ] **Step 5: Write the CLI**

`src/jobscout/cli.py`:

```python
import typer

from jobscout import __version__

app = typer.Typer(
    name="jobscout",
    help="Jobscout — a personal job-hunt agent for one real candidate's search.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"jobscout {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Jobscout command-line interface. Subcommands are added by later units."""


def run() -> None:
    app()


if __name__ == "__main__":
    run()
```

- [ ] **Step 6: Sync and run the test, verify it passes**

Run: `uv sync && uv run pytest tests/test_cli_smoke.py -v`
Expected: PASS (3 tests). `uv sync` installs the `jobscout` console script into the project venv.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/jobscout/__init__.py src/jobscout/cli.py tests/test_cli_smoke.py
git commit -m "feat: uv package skeleton with jobscout CLI"
```

---

## Task 2: Repo hygiene — `.gitignore`, `.env.example`, `data/example/`

**Files:**
- Modify: `.gitignore` (already exists with a `.superpowers/` line from SDD setup — overwrite with the full content below, keeping `.superpowers/`)
- Create: `.env.example`
- Create: `data/example/.gitkeep`
- Test: `tests/test_repo_hygiene.py`

**Interfaces:**
- Consumes: nothing.
- Produces: no importable symbols — deliverable is the tracked-file boundary, verified by `git check-ignore`.

- [ ] **Step 1: Write the failing hygiene test**

`tests/test_repo_hygiene.py`:

```python
import subprocess
from pathlib import Path


def _is_ignored(path: str) -> bool:
    result = subprocess.run(["git", "check-ignore", "-q", path])
    return result.returncode == 0


def test_secrets_and_local_data_are_ignored():
    for path in [".env", "data/jobscout.sqlite", "data/resume.pdf", "data/criteria.yaml", "data/voice.md"]:
        assert _is_ignored(path), f"{path} must be git-ignored (DESIGN §2, §19)"


def test_example_data_stays_tracked():
    assert not _is_ignored("data/example/fake_resume.pdf"), "data/example/ must not be ignored (DESIGN §19)"


def test_env_example_lists_every_secret():
    text = Path(".env.example").read_text()
    for key in [
        "OPENROUTER_API_KEY",
        "LANGCHAIN_API_KEY",
        "ADZUNA_APP_ID",
        "ADZUNA_APP_KEY",
        "TELEGRAM_BOT_TOKEN",
        "JOBSCOUT_WEB_TOKEN",
    ]:
        assert key in text, f"{key} missing from .env.example"
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `uv run pytest tests/test_repo_hygiene.py -v`
Expected: FAIL — `.env.example` does not exist / paths not ignored.

- [ ] **Step 3: Overwrite `.gitignore` with the full content**

```gitignore
# Secrets
.env

# Local candidate data — everything except the demo fixtures (DESIGN §2, §19)
data/*
!data/example/

# SQLite
*.sqlite
*.sqlite3
*.db

# Python
__pycache__/
*.py[cod]
.venv/
*.egg-info/
.pytest_cache/

# SDD scratch (superpowers subagent-driven-development)
.superpowers/

# IDE / OS
.idea/
.DS_Store
```

- [ ] **Step 4: Write `.env.example`**

```dotenv
# Model gateway — OpenRouter (DESIGN §13). Provider routing excludes training/retention providers (§2).
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Tracing — LangSmith free tier (DESIGN §14, §16). Payloads are scrubbed before upload (§14).
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=jobscout

# Board discovery — Adzuna free key (DESIGN §3)
ADZUNA_APP_ID=
ADZUNA_APP_KEY=

# Candidate surface — Telegram bot (DESIGN §12)
TELEGRAM_BOT_TOKEN=

# Web surface — optional bearer token for tunnelled access (DESIGN §12)
JOBSCOUT_WEB_TOKEN=
```

- [ ] **Step 5: Create the demo-data directory marker**

Run: `mkdir -p data/example && touch data/example/.gitkeep`

- [ ] **Step 6: Run the test, verify it passes**

Run: `uv run pytest tests/test_repo_hygiene.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS (6 tests total — 3 smoke + 3 hygiene).

- [ ] **Step 8: Commit**

```bash
git add .gitignore .env.example data/example/.gitkeep tests/test_repo_hygiene.py
git commit -m "chore: gitignore, .env.example, demo-data dir"
```

---

## Self-Review

**1. Spec coverage (build-plan unit 1 acceptance criteria):**

| Criterion | Task |
|---|---|
| `uv` packaging; `uv run jobscout --help` prints command list | Task 1 (`pyproject.toml`, `test_console_script_help_runs`) |
| `pytest` runs and passes on a smoke test | Task 1 + Task 2 step 7 |
| `data/` git-ignored except `data/example/` | Task 2 (`.gitignore`, `test_example_data_stays_tracked`) |
| `.env.example` documents every secret key | Task 2 (`test_env_example_lists_every_secret`) |
| No real resume, `.env`, DB, `criteria`, `voice.md` tracked | Task 2 (`test_secrets_and_local_data_are_ignored`) |

No gaps.

**2. Placeholder scan:** No TBD / "handle errors" / bare "write tests". Every code step has real content.

**3. Type consistency:** `app` (`typer.Typer`), `run()`, `__version__` — used identically in the interfaces block, `cli.py`, and the tests. `_is_ignored` / `_version_callback` are file-local helpers, defined where used.

**Note for later units:** `.gitignore` uses `data/*` + `!data/example/`. A future `data/example/` subdirectory needs no extra rule; a new *top-level* ignore under `data/` also needs none. Adding a tracked file directly at `data/` root (not under `example/`) would be blocked — intended.
