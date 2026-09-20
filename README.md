# jobscout

A personal job-hunt agent, built for one real candidate's search (Senior/Mid Frontend
Engineer, Germany). It polls a curated set of company ATS boards plus Adzuna/arbeitnow, knocks
out postings that fail hard requirements, scores the rest against a Rubric derived from her
resume, and queues the survivors for her thumbs up/down — feeding a Preference Feedback Loop
that keeps tuning the score. On request it drafts a cover letter, checked against the resume so
it can't invent a claim.

Two goals: a five-minute-demoable, explainable-in-depth portfolio piece, and a small research
project measuring where an agent's judgment drifts from a real user's stated preferences.

Full design: [`docs/DESIGN.md`](docs/DESIGN.md). Eval results: [`FINDINGS.md`](FINDINGS.md).
Build order: [`docs/2026-09-01-build-plan.md`](docs/2026-09-01-build-plan.md).

## Architecture

One SQLite file holds everything — app tables plus LangGraph's own checkpoints. Every surface
(CLI, Telegram, web) calls a single `CoreService` (`src/jobscout/service.py`); none of them
touch the graph or the DB directly. Four LangGraph graphs — onboarding, poll, letter, scope
expansion — each pause at an Approval Gate before anything irreversible happens.

## Setup

```bash
uv sync
cp .env.example .env   # fill in OPENROUTER_API_KEY at minimum
```

`.env.example` documents every variable and which surface needs it. Nothing under `data/` is
tracked except `data/example/` — that's real candidate data (resume, DB, criteria) and it stays
local.

## 5-minute demo script

```bash
# 1. Onboard from a resume PDF — extracts a Profile, asks a few clarifying questions.
uv run jobscout onboard data/example/fake_resume.pdf
# edit data/gates/onboard.yaml with the answers, then:
uv run jobscout resume onboard

# 2. Trigger a Poll — proposes a Search Plan, pauses for approval.
uv run jobscout poll
# edit data/gates/poll_<run_id>.yaml: decision: approve, then:
uv run jobscout resume poll <run_id>

# 3. See what it found.
uv run jobscout queue
uv run jobscout spend

# 4. Record a Verdict.
uv run jobscout thumb <posting_id> up --reason "great stack fit"

# 5. Browse it — Run/Posting explainers, Preference history, Eval numbers, and the same
#    actions as the CLI, all from one page.
uv run uvicorn jobscout.web:app --reload
# open http://127.0.0.1:8000
```

Everything the web UI does routes through the same `CoreService` as the CLI — a Verdict
recorded from the browser is the same call `jobscout thumb` makes.

The Telegram surface (`src/jobscout/telegram_bot.py`) is built and unit-tested
(`tests/test_telegram_bot.py`) but not yet wired to a standalone launch command — start it by
calling `build_application(...)` and `.run_polling()` yourself with a bot token, or wait for the
`jobscout serve` wiring noted as a deliberate follow-up in the build plan.

## Tests

```bash
uv run pytest
```

## Screenshots

Queue view, a Posting explainer, and the Eval view — run the demo script above and open the web
UI to see them live; screenshots aren't checked in here since the data underneath (real scored
postings) belongs to a real candidate's search.
