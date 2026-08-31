# Jobscout — Build Plan

**Status:** sliced from `DESIGN.md` (2026-09-01) + `CONTEXT.md`. No ADRs exist yet;
`DESIGN.md` is the decision record and is cited directly. Vocabulary is `CONTEXT.md`'s.
**Date:** 2026-09-01

Units are vertical slices, each buildable / testable / demoable without waiting on unrelated
work. Numbered in dependency order — building in list order never needs a later unit's
internals. Grouped by the three phases in `DESIGN.md` §18.

Recurring acceptance criteria (apply to every unit that produces the relevant event, not
repeated below except where load-bearing):

- Every LLM call's token cost is logged to the spend table and counted against the $20 cap
  (§13, [[spend]]).
- Every graph node emits a LangSmith trace; payloads pass the Scrubber first (§14, §16,
  [[scrubber]]).
- Unredacted data stays in local SQLite only (§2, §14).
- New operator actions are reachable as `jobscout` subcommands (§12).

---

## Phase 1 — Foundation

### 1. The project runs from a clean checkout

**Depends on:** none
**Refs:** §19, §16

Acceptance criteria:
- `uv` packaging; `uv run jobscout --help` prints the command list.
- `pytest` runs and passes on a smoke test.
- `data/` is git-ignored in full except `data/example/` (§19); `.env.example` documents
  every secret key (§19) — OpenRouter, LangSmith, Adzuna `app_id`+key, Telegram token,
  web bearer token.
- No real resume, `.env`, DB, `criteria`, or `voice.md` is tracked (§2).

### 2. The SQLite store holds graph checkpoints and app tables in one file

**Depends on:** 1
**Refs:** §11, §4

Acceptance criteria:
- One SQLite file carries the LangGraph checkpointer and the app tables, separated by table
  prefix (§11).
- App tables exist for: postings, scores, feedback (Verdicts), decisions, spend, seed
  labels, versioned profile, versioned preference summary, versioned criteria (§11).
- A graph interrupted mid-Run resumes from its last checkpoint after process restart (§11,
  §16).

Open questions: column-level schema is "finalised in code" (§11) — this unit fixes the
table set and prefixes, not every field.

### 3. Every surface calls one Core Service Layer

**Depends on:** 2
**Refs:** §12

Acceptance criteria:
- A single module exposes: trigger a Run, resume a Run at an Approval Gate, record a
  Verdict, run onboarding ([[core-service-layer]]).
- The layer is the only thing that touches the graph or the store; callers hold no graph
  objects (§12).
- Built now, not stubbed for later refactor (§12) — later units extend this layer, never
  bypass it.

### 4. Model access is one OpenRouter client with provider routing locked

**Depends on:** 1
**Refs:** §2, §13

Acceptance criteria:
- One base URL + key; the model id is a single config string (§13).
- OpenRouter provider routing is set to exclude any provider that trains on data or lacks
  zero-retention — `provider.data_collection: "deny"` or an explicit allowlist (§2).
- Switching model = changing the config string, no code change (§13).

Open questions: exact model ids are "pinned at implementation time from OpenRouter's live
catalogue" (§13) — the idea doc's "V3.2 / V4 Flash" names are not verified.

### 5. Resume ingest produces a structured Profile

**Depends on:** 2, 4
**Refs:** §5 stage 1

Acceptance criteria:
- The Candidate points the CLI at a resume PDF; an LLM extraction yields structured JSON —
  roles, years, stack, seniority signals (§5).
- Raw resume text is stored alongside the parsed JSON (§5).
- The result is one versioned `profile` record (§5).
- `onboard` is a checkpointed subgraph — interrupting and resuming mid-ingest does not
  restart it (§5).

### 6. Onboarding questions complete the Profile

**Depends on:** 5
**Refs:** §5 stages 2–3

Acceptance criteria:
- Static questions cover: German level + whether to include German-required roles; salary
  floor (knockout or scored); work mode + acceptable cities; company size/stage; hard-
  exclude industries; must-have stack; contract type (§5).
- 3–5 dynamic follow-ups are generated from resume + static answers (§5).
- All answers land in the same versioned `profile` record (§5).

### 7. Criteria are derived from the Profile and are hand-editable

**Depends on:** 6
**Refs:** §5, §6

Acceptance criteria:
- An LLM derives a `criteria` object with three buckets: `knockout`, `scored`, `learn`
  ([[criteria]], §5).
- `criteria` is file-editable and versioned; a bump creates a new version, old versions
  kept (§5, §10).
- `jobscout onboard --reset` wipes `profile` + `criteria` only; Posting / Score / Verdict
  history is kept but tagged pre-reset (§5).

### 8. An operator approves the Search Plan before any fetch

**Depends on:** 3, 7
**Refs:** §4, §10

Acceptance criteria:
- `plan_search` emits one structured LLM call producing concrete queries + Sources +
  companies ([[search-plan]], §4).
- The Run halts at an Approval Gate via `interrupt()` + checkpointer before any network
  fetch (§4, §10).
- The gate is answerable later from a marked-up file; the Run resumes on approval and
  aborts on rejection (§4, §16 week 1–2).

### 9. A Poll discovers Postings from the Curated ATS Boards

**Depends on:** 8
**Refs:** §3, §4

Acceptance criteria:
- `companies.yaml` lists the Target Companies; it is hand-editable in the repo (§3).
- ATS type (Greenhouse / Lever / Ashby / Personio) is auto-detected once per company and
  cached ([[curated-ats-board]], §3).
- Companies with no detectable ATS fall back to `trafilatura` careers-page extraction (§3).
- Each discovered Posting is stored with raw JD text and status `new` ([[jd]], §11).

Open questions: exact endpoints / rate limits "verified during implementation" (§3).

### 10. The graph backbone carries a Posting from discovery to the scoring node

**Depends on:** 9
**Refs:** §4

Acceptance criteria:
- `discover → dedupe → fetch_jd → staleness` run as plain Python, no LLM (§4).
- `dedupe` here is exact-key only: ATS job-id, else
  `sha256(normalized_company + normalized_title + city)` (§11) — the LLM tie-break is unit 17.
- `staleness` here is a pass-through placeholder — real logic is units 18–19.
- `fetch_jd` populates full JD text; a Posting missing JD text does not reach scoring (§4).

### 11. Postings failing a Knockout are Excluded with a reason

**Depends on:** 10
**Refs:** §6

Acceptance criteria:
- An LLM extracts the fact, a rule decides, for each Knockout axis: seniority band
  (senior/mid vs staff/EM/junior), location/remote vs Germany, visa/work-auth language,
  "German required" vs her level ([[knockout]], §6).
- Any fail sets status `excluded` + reason, no Score, and the Posting never reaches the
  Queue ([[excluded]], §6).
- Auto-drop happens **only** on a hard Knockout — nothing else auto-drops (§6).

### 12. The Score Sub-Agent scores a Posting with quoted evidence

**Depends on:** 11
**Refs:** §4, §6

Acceptance criteria:
- The `score` node is a bounded ReAct sub-agent (subgraph + tool node) — the one real agent
  loop ([[score-sub-agent]], §4).
- Tools: re-read a resume section, company lookup, past Rejections for this company
  ([[rejection]] — thumbs-down + Excluded + Skipped), current preference summary (§4).
- It commits a Score + Rationale + Matched Lines ([[matched-lines]], §4).
- Each of the four Scored Dimensions is scored 0–5; a dimension without a quoted JD line +
  quoted resume line caps at 2 (§6).
- Weighted sum → 0–100, weights from config ([[score]], §6).
- Rubric anchors are concrete per level (§6).

### 13. Postings passing Knockouts land in the Queue, sorted by Score

**Depends on:** 12
**Refs:** §6

Acceptance criteria:
- Every Posting passing Knockouts enters the Queue, sorted by Score ([[queue]], §6).
- A Weak-Fit Flag is shown on any queued Posting scoring below 50 ([[weak-fit-flag]], §6).
- Nothing in the Queue is ever auto-dropped (§6).
- No retroactive re-scoring of the existing Queue (§7) — this unit only adds new Postings.

### 14. The Scrubber redacts trace payloads before upload

**Depends on:** 3
**Refs:** §14, §16

Acceptance criteria:
- Given a payload with the Candidate's name, contact info, exact comp, and current
  employer, the Scrubber outputs: name → `[CANDIDATE]`, contact stripped, comp → bucket
  (e.g. `€80–90k`), employer masked ([[scrubber]], §14).
- Graph structure and reasoning text survive redaction (§14).
- Tracing is on from the first Run (§16); no self-hosting (§14).

### 15. Every Run records Spend and halts at the $20 cap

**Depends on:** 3, 4
**Refs:** §13

Acceptance criteria:
- Each LLM call appends cost to the spend table ([[spend]], §13).
- `jobscout` shows the running total (§13).
- Reaching $20 total stops the Run rather than continuing (§13).

### 16. The Candidate's thumb on a queued Posting is recorded as a Verdict

**Depends on:** 13
**Refs:** §7

Acceptance criteria:
- A thumbs up / thumbs down + one-line reason on a scored Posting is stored as a `feedback`
  row ([[verdict]], §7).
- Verdicts are recorded from week 1, before any loop consumes them (§7).
- Recording a Verdict does not re-score anything (§7).

### 17. The operator drives the full Foundation loop from the CLI

**Depends on:** 8, 13, 16
**Refs:** §12, §16

Acceptance criteria:
- `jobscout` (Typer + Rich) exposes: run a Poll, resume a Run at a gate, run/reset
  onboarding, show the Queue, show Spend, thumb a Posting (§12).
- Gates are answered via a marked-up file in week 1–2 (§16).
- The CLI calls only the Core Service Layer (§12).

---

## Phase 2 — Persistence & long-horizon

### 18. The same Posting from multiple Sources is stored once

**Depends on:** 10
**Refs:** §11

Acceptance criteria:
- Primary key: ATS job-id when the Posting came from a known ATS; else
  `sha256(normalized_company + normalized_title + city)` (§11).
- A collision on company+title but not on key triggers exactly one cheap LLM
  "same posting? y/n" call, cached forever (§11).
- No fuzzy-match thresholds (§11).

### 19. A Posting that disappears from its Source becomes Stale

**Depends on:** 10
**Refs:** §11

Acceptance criteria:
- Queued / `new` Postings are re-polled each Run (§11).
- Gone from Source or 404 for two consecutive Polls → status `stale` ([[stale]], §11).

### 20. An edited Posting is re-scored and flagged as changed

**Depends on:** 12, 19
**Refs:** §11

Acceptance criteria:
- A changed `content_hash` triggers a re-score (§11).
- The Posting is flagged "changed since she saw it" in the Queue (§11).

### 21. Postings from Adzuna appear in the pipeline

**Depends on:** 10, 18
**Refs:** §3

Acceptance criteria:
- The Adzuna adapter (free `app_id` + key) pulls Germany postings with structured salary +
  location (§3).
- Adzuna results feed dedupe (unit 18) like any other Source (§3).

Open questions: endpoints / rate limits verified in implementation (§3); recruiter-spam
handling is noted as a known Adzuna trait (§3) but no filter is specified.

### 22. Postings from arbeitnow appear in the pipeline

**Depends on:** 10, 18
**Refs:** §3

Acceptance criteria:
- The arbeitnow adapter (no key) pulls EU tech postings (§3).
- Results feed dedupe (unit 18) (§3).

### 23. On request, a gated Cover Letter draft is produced

**Depends on:** 12
**Refs:** §8, §10

Acceptance criteria:
- `draft_letter` runs on demand only — a thumbs-up or an explicit "draft letter" — never
  auto-run for the Queue ([[cover-letter]], §4, §8).
- Output is a modern 3-paragraph ~250-word letter in the JD's language (§8).
- JD is German-only and the Profile shows German below B2 → generate English + flag
  "JD implies German — decide before sending" (§8).
- Sources are resume + Profile + JD only, plus Voice Notes if `voice.md` is present (§8).
- Every letter stops at the outbound-letter Approval Gate before entering a Package (§10).
- The Agent never sends a letter under any path (§8).

### 24. A Cover Letter claim not traceable to the resume is rejected

**Depends on:** 23
**Refs:** §4, §8

Acceptance criteria:
- `check_faithfulness` is an LLM-judge critic checking the letter against the resume,
  per-claim ([[faithfulness-critic]], §4).
- A claim asserting a skill or experience not in the resume is rejected (§8).
- The letter stays honest-but-positive and never asserts a missing skill (§8).

### 25. Where the Candidate falls short of a Posting is delivered as Fit Notes

**Depends on:** 12
**Refs:** §8

Acceptance criteria:
- Gaps between resume and JD are reported separately as Fit Notes in the Digest / Telegram
  ([[fit-notes]], §8).
- Fit Notes are never folded into the Cover Letter (§8).

### 26. Scoring incorporates Verdict history per the configured Feedback Mechanism

**Depends on:** 12, 16
**Refs:** §7

Acceptance criteria:
- Both mechanisms exist behind a config switch: `none` / `few-shot` / `summary` / `both`
  ([[feedback-mechanism]], §7).
- **Few-Shot Store:** each Verdict + reason is a row with a local embedding (fastembed /
  bge-small); similarity is brute-force cosine over ~50 rows in SQLite — not a vector DB;
  top-K is injected into the `score` prompt ([[few-shot-store]], §7).
- **Preference Summary:** an LLM rewrites a prose "what she likes / rejects and why" doc
  from accumulated Verdicts, injected into every `score` call; regenerated every 5 new
  Verdicts or on `jobscout learn` ([[preference-summary]], §7).
- Runtime default is `summary` (§7).
- Few-Shot Store rows update live (§7).
- Feedback shapes future Scores only — the existing Queue is untouched unless
  `jobscout rescore` is run (§7).
- The Preference Summary is stored separately from the `profile` (§7).

### 27. The Agent proposes a Scope Expansion when the Queue runs thin

**Depends on:** 13
**Refs:** §10

Acceptance criteria:
- Trigger: 3 consecutive Polls with fewer than 2 Postings passing Knockouts ([[scope-expansion]], §10).
- The proposal is exactly one concrete change + evidence (e.g. "11 postings failed only on
  the salary floor; dropping €X→€Y surfaces these 4: [titles]") — not "let's widen the
  search" (§10).
- It halts at an Approval Gate (§10).
- Approved → `criteria` version bumped. Rejected → logged, same proposal suppressed for 2
  weeks (§10).

### 28. On approval, a Package is generated and the Posting becomes package_ready

**Depends on:** 23, 24
**Refs:** §9

Acceptance criteria:
- The Candidate approving an application produces a Package: final letter + resume file +
  JD link + a filled Answer Sheet (visa status, notice period, salary expectation, why this
  company) ([[package]], [[answer-sheet]], §9).
- Status → `package_ready`; the Package is delivered to her ([[package_ready]], §9).
- Package generation never sets `applied` (§9).

### 29. The Candidate confirming a real submission sets the Posting to applied

**Depends on:** 28
**Refs:** §9

Acceptance criteria:
- "Mark applied" from the Candidate sets status `applied` + timestamp ([[applied]], §9).
- `applied` is set only by her explicit confirmation after real submission — no other path
  (§9).
- Long-horizon tracking (don't re-surface, watch for a response) keys off `applied` (§9).
- Browser form-fill / auto-submit is out of scope (§9) — not built.

### 30. A single Posting's failure never blocks the Poll

**Depends on:** 10
**Refs:** §17

Acceptance criteria:
- A malformed JD, extraction failure, or model 500 on one Posting → logged, status `error`
  + reason, Poll continues ([[error]], §17).
- Model API 429 → exponential backoff, 3 retries, then park the Posting for next Poll (§17).
- A Source being down → skip that Source this Poll, use the others, note it in the Digest
  (§17).
- An `error` Posting failing 3 consecutive Polls → status `dead`, surfaced in the web view
  only ([[dead]], §17).

---

## Phase 3 — Numbers & delivery

### 31. Thirty real Postings are frozen as the Seed Set

**Depends on:** 12
**Refs:** §15

Acceptance criteria:
- 30 Postings pulled from the live pipeline once it runs; raw JD text stored in-repo so the
  eval reproduces after Postings 404 ([[seed-set]], §15).
- Spread across obvious-good / borderline / obvious-bad (§15).
- ~6 Adversarial Postings, tagged: exact-stack-but-actually-junior,
  great-fit-but-badly-written, one with a buried prompt-injection string
  ([[adversarial-posting]], §15).

### 32. The Candidate Labels the Seed Set

**Depends on:** 31
**Refs:** §15

Acceptance criteria:
- She gives each Seed Set Posting an overall 0–100 + thumb + one-line why — not
  per-dimension ([[label]], §15).
- Labels are stored in the seed labels table (§11).
- ~15 Postings are re-labeled at the end to measure her own Drift (§15).
- Labels never feed the Preference Feedback Loop ([[label]], `CONTEXT.md`).

### 33. The eval reports Spearman correlation of Agent Score vs Label

**Depends on:** 32
**Refs:** §15

Acceptance criteria:
- Spearman rank correlation, Agent overall vs her overall — no LLM judge, she is ground
  truth (§15).
- Target ≥ 0.6, reported against target (§15).
- Secondary: cost per Posting; knockout false-exclusion count, target 0 — excluding a job
  she wanted is the worst failure (§15).

### 34. The reasoning judge is calibrated against the Candidate's hand-checks

**Depends on:** 32
**Refs:** §15

Acceptance criteria:
- The LLM judge is the cheap-frontier anchor model (§13, §15).
- It is calibrated against ~8 of her hand-checks before scoring anything (§15).

### 35. Reasoning-quality checks run over an eval batch

**Depends on:** 34, 24
**Refs:** §15

Acceptance criteria:
- Checks: Cover Letter faithfulness (per-claim traceable); Matched Lines validity (JD and
  resume quotes real + relevant); Score↔Rationale consistency; Knockout correctness;
  Divergence Triage — when |Agent − human| is large, categorise as weak rationale /
  she's the outlier / genuinely ambiguous ([[divergence-triage]], §15).

### 36. The Bake-Off produces the deliverable table

**Depends on:** 26, 33
**Refs:** §15

Acceptance criteria:
- `eval run` = 30 Postings × Feedback Mechanism {none / few-shot / summary / both} ×
  {model} → one table ([[bake-off]], §15).
- Bake-off candidates: DeepSeek chat / Qwen / GLM / Kimi + one cheap-frontier anchor as the
  correlation ceiling (§13).
- Eval output has a cost column; past $20 → stop and look (§13).

### 37. Ablation switches report a delta per component

**Depends on:** 36
**Refs:** §15

Acceptance criteria:
- Switches: Preference Feedback Loop on/off, Faithfulness Critic on/off, Matched Lines
  requirement on/off, model swap ([[ablation]], §15).
- The eval reports the delta for each (§15).

### 38. Fault-injection flags stress the long-horizon machinery

**Depends on:** 18, 19, 26
**Refs:** §15

Acceptance criteria:
- `--inject context-truncation` chops the resume before `score` ([[fault-injection]], §15).
- `--inject stale-jd` feeds the old version of an edited Posting (§15).
- The eval measures whether dedupe / staleness / memory hold up over a simulated multi-week
  Run (§15).

### 39. FINDINGS.md is generated from eval output

**Depends on:** 36, 37, 38
**Refs:** §15, §19

Acceptance criteria:
- `FINDINGS.md` contains: the Bake-Off table, the Drift-closure curve, ablation deltas,
  failure-mode writeups (§19).
- It is the deliverable for goal 2 (§15).

### 40. The Candidate operates the Queue from Telegram

**Depends on:** 17, 23, 25
**Refs:** §12

Acceptance criteria:
- `python-telegram-bot`, long-polling, no public webhook (§12).
- Hard-locked to the Candidate's + Operator's chat IDs; all other senders ignored (§12).
- After each Poll: one summary line, then one message per queued Posting with inline buttons
  👍 / 👎 / draft letter / skip (§12).
- Burst cap: >5 in a Poll → summary + top 3 by Score, rest "see web" (§12).
- Gate prompts (search plan, letter, scope expansion) are sent immediately (§12).
- Quiet hours 22:00–08:00 — queue and send at 08:00 (§12).

Deferred (not this unit): `/ask` free-text mode is an optional stretch (§12).

### 41. The web app explains a Run and a Posting

**Depends on:** 17
**Refs:** §12

Acceptance criteria:
- FastAPI, localhost, optional env bearer token for tunnelled access (§12).
- Run view: graph path taken, per-node cost / timing, Postings scored in that Run (§12).
- Posting view: JD text, Rubric breakdown, Matched Lines, Score history, her Verdict (§12).
- Preference view: current Preference Summary + version history (§12).
- Eval view: Bake-Off table, Spearman numbers, ablation deltas (§12).
- Live status via polling every 2s, not SSE (§12).
- Config (Rubric weights, `companies.yaml`, `criteria`) stays file-edited — no form-building
  (§12).

### 42. The web app takes the same actions as Telegram

**Depends on:** 41, 40
**Refs:** §12

Acceptance criteria:
- Actions: trigger a Run, thumb, approve/reject letter, approve/reject Search Plan,
  approve/reject Scope Expansion, skip a Posting, mark applied (§12).
- Actions route through the Core Service Layer, same as Telegram and CLI (§12).

### 43. The repo is demo-ready

**Depends on:** 39
**Refs:** §19

Acceptance criteria:
- `data/example/` holds fake data for the demo; nothing real is tracked (§19).
- `README.md`: what it is, a 5-minute demo script, GIF / screenshots (§19).
- Secrets documented in `.env.example`; `DESIGN.md`, `FINDINGS.md`, `README.md` all present
  (§19).
- Public GitHub repo (§19).

---

## Not sliced (explicitly out of scope — `DESIGN.md` §20)

Vector database · Postgres · Docker · React · second agent framework · MCP-server wrapper ·
browser form-fill / auto-submit · resume generation · multi-resume selection · OpenClaw /
Hermes as a runtime · `/ask` free-text chat (optional stretch only).

## Scheduling (§16) — not a unit

`jobscout serve` (Telegram bot + APScheduler poll 2×/day + FastAPI, one launchd-kept
process) is wiring over units 40–42, added once those land.
