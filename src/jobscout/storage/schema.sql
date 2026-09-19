-- Jobscout application tables. All names prefixed `app_` to stay separate from
-- LangGraph's `checkpoint*` tables in the same file (DESIGN §11).
-- Columns are minimal for now — later units ALTER TABLE to add fields
-- (DESIGN §11: "finalised in code"). Bump PRAGMA user_version + add migration
-- handling in db.init_db when that starts.
-- LangGraph also creates an unprefixed `writes` table — do not name an app
-- table `writes`. Our collision protection is the `app_` prefix, not theirs.
-- All timestamps are UTC (SQLite `datetime('now')`); surfaces must convert.

-- Durability posture: WAL lets a reader (a CLI poll) run without being blocked
-- by a writer (jobscout serve) in the same file. Persistent once set.
PRAGMA journal_mode = WAL;

-- CONTEXT: Posting — one job listing from one company.
CREATE TABLE IF NOT EXISTS app_posting (
    id             TEXT PRIMARY KEY,   -- dedupe key: ATS job-id, else sha256(company+title+city) (§11)
    source         TEXT NOT NULL,      -- CONTEXT: Source (adzuna | arbeitnow | ats:<company> | careers:<company>)
    company        TEXT NOT NULL,
    title          TEXT NOT NULL,
    city           TEXT,
    url            TEXT,
    jd_text        TEXT,               -- CONTEXT: JD; NULL until fetch_jd (§4)
    content_hash   TEXT,               -- staleness detection (§11)
    status         TEXT NOT NULL DEFAULT 'new',
        -- new | queued | scored | excluded | stale | error | dead | package_ready | applied | skipped (CONTEXT)
    status_reason  TEXT,
    missed_polls   INTEGER NOT NULL DEFAULT 0,  -- consecutive Polls missing from Source; 2 -> stale (§11)
    error_count    INTEGER NOT NULL DEFAULT 0,  -- consecutive Polls ending in 'error'; 3 -> dead (§17 unit 30)
    applied_at     TEXT,                        -- set only by the Candidate's explicit "mark applied" (§9 unit 29)
    first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Curated ATS Board — auto-detected ATS type per Target
-- Company, cached so it's probed at most once (§3).
CREATE TABLE IF NOT EXISTS app_company_ats (
    company_slug  TEXT PRIMARY KEY,
    ats_type      TEXT NOT NULL,  -- greenhouse | lever | ashby | personio | none
    detected_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Score / Rationale / Matched Lines — Score Sub-Agent output (§6).
CREATE TABLE IF NOT EXISTS app_score (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id        TEXT NOT NULL REFERENCES app_posting(id),
    run_id            TEXT,             -- the Run that produced this Score
    score             INTEGER,          -- weighted sum 0-100 (§6)
    rationale         TEXT,
    dimensions_json   TEXT,             -- per Scored Dimension: 0-5 + quoted JD line + quoted resume line (§6)
    criteria_version  INTEGER,          -- app_criteria.version scored against
    content_hash      TEXT,             -- the Posting's content_hash when this Score was computed (§11 unit 20)
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Verdict — Candidate thumb + reason on a scored live Posting (§7).
-- Logged from week 1, before the Preference Feedback Loop consumes it (§7).
CREATE TABLE IF NOT EXISTS app_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id  TEXT NOT NULL REFERENCES app_posting(id),
    verdict     TEXT NOT NULL,          -- 'up' | 'down'
    reason      TEXT,                   -- one-line why
    embedding   BLOB,                   -- Few-Shot Store vector; NULL until unit 26 (§7)
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Approval Gate outcomes — search plan / outbound letter / scope expansion (§10).
CREATE TABLE IF NOT EXISTS app_decision (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT,
    gate         TEXT NOT NULL,         -- 'search_plan' | 'outbound_letter' | 'scope_expansion'
    posting_id   TEXT REFERENCES app_posting(id),  -- NULL for search_plan
    outcome      TEXT NOT NULL,         -- 'approved' | 'rejected'
    payload_json TEXT,                  -- the proposal that was decided on
    decided_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Spend — token cost per LLM call, against the $20 cap (§13).
CREATE TABLE IF NOT EXISTS app_spend (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT,
    node               TEXT,            -- graph node that made the call
    model              TEXT,
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER,
    cost_usd           REAL NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Label — Candidate's eval ground truth on a frozen Seed Set Posting (§15).
-- Never feeds the Preference Feedback Loop.
CREATE TABLE IF NOT EXISTS app_seed_label (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id  TEXT NOT NULL,          -- seed JD text lives in-repo; id need not be in app_posting
    overall     INTEGER NOT NULL,       -- 0-100
    thumb       TEXT NOT NULL,          -- 'up' | 'down'
    why         TEXT,
    round       INTEGER NOT NULL DEFAULT 1,  -- 1 = initial, 2 = end-of-project re-label for her Drift (§15)
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Profile — parsed resume + static answers + dynamic Q&A, versioned (§5).
CREATE TABLE IF NOT EXISTS app_profile (
    version      INTEGER PRIMARY KEY,
    data_json    TEXT NOT NULL,
    resume_text  TEXT,
    pre_reset    INTEGER NOT NULL DEFAULT 0,  -- tagged when `onboard --reset` runs (§5)
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: Criteria — knockout / scored / learn buckets derived from a Profile, versioned (§5).
CREATE TABLE IF NOT EXISTS app_criteria (
    version          INTEGER PRIMARY KEY,
    data_json        TEXT NOT NULL,
    profile_version  INTEGER,
    pre_reset        INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONTEXT: cross-source dedupe tie-break cache — "same posting? y/n" is
-- asked at most once per pair, cached forever (§11, build-plan unit 18).
CREATE TABLE IF NOT EXISTS app_dedupe_cache (
    key_a       TEXT NOT NULL,
    key_b       TEXT NOT NULL,
    same        INTEGER NOT NULL,  -- 0 | 1
    checked_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (key_a, key_b)
);

-- CONTEXT: Preference Summary — LLM prose from accumulated Verdicts, versioned (§7).
CREATE TABLE IF NOT EXISTS app_preference_summary (
    version                 INTEGER PRIMARY KEY,
    summary                 TEXT NOT NULL,
    verdict_count_at_write  INTEGER,   -- regenerated every 5 new Verdicts (§7)
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-Poll Knockout-pass count, feeding the Scope Expansion trigger: 3
-- consecutive Polls under 2 passes (§10, build-plan unit 27). One row per
-- completed poll Run.
CREATE TABLE IF NOT EXISTS app_poll_run (
    run_id                TEXT PRIMARY KEY,
    passed_knockout_count INTEGER NOT NULL,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
