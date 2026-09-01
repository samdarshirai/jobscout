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
    first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at   TEXT NOT NULL DEFAULT (datetime('now'))
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

-- CONTEXT: Preference Summary — LLM prose from accumulated Verdicts, versioned (§7).
CREATE TABLE IF NOT EXISTS app_preference_summary (
    version                 INTEGER PRIMARY KEY,
    summary                 TEXT NOT NULL,
    verdict_count_at_write  INTEGER,   -- regenerated every 5 new Verdicts (§7)
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
