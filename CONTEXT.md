# Jobscout

A personal job-hunt agent for one real candidate's search. It discovers postings, reads
each job description, scores fit against a resume with quoted evidence, drafts cover
letters, and never submits anything without human approval. Second purpose: measure where
the agent's judgment drifts from the candidate's real preferences.

## Language

### People

**Candidate**:
The person whose job hunt this is. She is the human-in-the-loop for every gate and the
ground-truth judge for the eval.
_Avoid_: user, applicant, wife, client

**Operator**:
The person who runs the CLI and the eval. Distinct from the Candidate.
_Avoid_: user, admin, developer

**Agent**:
The whole Jobscout system as the Candidate experiences it — "the agent proposes, the
human commits". Not any single LLM call.
_Avoid_: bot, assistant, AI

### Discovery

**Posting**:
One job listing from one company. The unit everything else attaches to.
_Avoid_: job, listing, ad, vacancy, opening, role

**JD**:
The full job-description text of a Posting.
_Avoid_: description, body, ad text

**Source**:
An origin of Postings: Adzuna, arbeitnow, a Curated ATS Board, or a `trafilatura` careers-page
extract. Not the hiring company.
_Avoid_: board, provider, feed, channel

**Curated ATS Board**:
The applicant-tracking system (Greenhouse / Lever / Ashby / Personio) hosting a Target
Company's own careers page. Hand-listed; ATS type auto-detected and cached per company.
_Avoid_: careers page, company board

**Target Company**:
A company the Candidate would actually work for. The hand-curated set, kept in the repo.
_Avoid_: employer, account, prospect

**Poll**:
One scheduled execution of the discovery-through-score pipeline. Runs twice a day once
hosted; run by hand before that.
_Avoid_: scan, sync, cycle, sweep

**Run**:
One checkpointed execution of a LangGraph graph. A Poll is a Run of the main graph. A Run
can pause at a gate and resume hours later.
_Avoid_: session, job, execution

### Profile and criteria

**Profile**:
The versioned record of who the Candidate is: parsed resume + static onboarding answers +
dynamic follow-up Q&A. Set once at onboarding, rarely changed.
_Avoid_: preferences, settings, persona

**Criteria**:
The editable object derived from the Profile, in three buckets: `knockout`, `scored`,
`learn`. Versioned. This is what a Poll evaluates a Posting against.
_Avoid_: filters, rules, requirements, config

**Knockout**:
A hard pass/fail axis (seniority band, location, work-auth language, German requirement vs
her level). Any fail excludes the Posting outright — no Score.
_Avoid_: filter, blocker, dealbreaker, gate

**Scored Dimension**:
One of the four weighted 0–5 axes: stack fit, domain/product interest, scope & seniority
signals, eng-culture signals.
_Avoid_: criterion, factor, metric, category

**Rubric**:
The anchored definition of what each 0–5 level means for a Scored Dimension ("stack 5 = JD
names 3+ of her primary tools as core").
_Avoid_: guide, scale, key

### Scoring output

**Score**:
The weighted sum of Scored Dimensions, 0–100. Absent on an excluded Posting.
_Avoid_: rating, rank, match score, grade

**Rationale**:
The Score Sub-Agent's written justification for a Score.
_Avoid_: explanation, reasoning, notes

**Matched Lines**:
The quoted JD line plus quoted resume line backing one Scored Dimension. Required, or that
dimension caps at 2.
_Avoid_: evidence, citations, quotes, highlights

**Score Sub-Agent**:
The one bounded ReAct loop in the system, at the `score` node. Decides what context it
needs, then commits Score + Rationale + Matched Lines.
_Avoid_: scorer, the agent, evaluator

**Queue**:
The set of Postings that passed all Knockouts, shown to the Candidate sorted by Score.
Nothing here is ever auto-dropped.
_Avoid_: inbox, list, shortlist, pipeline

**Weak-Fit Flag**:
The marker on a queued Posting whose Score is below 50.
_Avoid_: low-score warning, caution

**Fit Notes**:
The honest account of where the Candidate falls short of a Posting, delivered to her
separately — never folded into the cover letter.
_Avoid_: weaknesses, gaps, caveats

### Feedback loop

**Verdict**:
The Candidate's thumbs up / thumbs down on a scored live Posting, plus a one-line reason.
Feeds the Preference Feedback Loop.
_Avoid_: feedback, rating, vote, label

**Preference Feedback Loop**:
The cycle that turns accumulated Verdicts into better future Scores. Has two mechanisms,
switchable by config.
_Avoid_: learning, training, tuning

**Preference Summary**:
The versioned, LLM-written prose account of what the Candidate likes and rejects and why,
rewritten from accumulated Verdicts. Separate from the Profile.
_Avoid_: learned preferences, memory, notes

**Few-Shot Store**:
Past Verdicts kept as rows with local embeddings, queried by similarity to inject examples
into a Score. Not a vector database — brute-force cosine over ~50 rows.
_Avoid_: example DB, vector store, index

**Feedback Mechanism**:
Which Verdict-injection method is active: `none`, `few-shot`, `summary`, or `both`. A
config switch and an eval axis.
_Avoid_: mode, strategy

**Rejection**:
Any prior Posting from a given company that the Candidate thumbs-downed, that was Excluded
on a Knockout, or that she skipped without a Verdict. What the Score Sub-Agent's
"past rejections" tool returns.
_Avoid_: pass, no

### Lifecycle

**Excluded**:
Posting status: failed a Knockout. No Score, never reaches the queue. The only automatic
drop.
_Avoid_: rejected, filtered, dropped

**Stale**:
Posting status: gone from its Source or 404 for two Polls, or superseded by an edited
version.
_Avoid_: expired, old, dead

**Error**:
Posting status: hit a processing fault (malformed JD, extraction failure, model 500).
Retried next Poll.
_Avoid_: failed, broken

**Dead**:
Posting status: an Error Posting that failed three consecutive Polls. Surfaced in the web
view only.
_Avoid_: stale, failed, abandoned

**Package**:
The bundle generated when the Candidate approves applying: final cover letter + resume file
+ JD link + filled Answer Sheet.
_Avoid_: application, submission, kit

**Answer Sheet**:
The filled responses to common application questions (visa status, notice period, salary
expectation, why this company), part of a Package.
_Avoid_: form, questionnaire, FAQ

**package_ready**:
Posting status: Package generated and delivered, awaiting the Candidate's manual
submission.

**Applied**:
Posting status: set only by the Candidate's explicit confirmation after she submitted on
the company site. Never set by Package generation.
_Avoid_: submitted, sent, done

**Skipped**:
Posting status: the Candidate dismissed a queued Posting without a Verdict. Counts toward
Rejection context but carries no reason.
_Avoid_: ignored, passed, dismissed

### Cover letter

**Cover Letter**:
A modern three-paragraph, ~250-word tailored letter in the JD's language. Not a formal
German *Anschreiben*.
_Avoid_: Anschreiben, motivation letter, application letter

**Faithfulness Critic**:
The LLM-judge that rejects any Cover Letter claim not traceable to the resume.
_Avoid_: fact-checker, validator, verifier

**Voice Notes**:
Optional Candidate-supplied tone guidance and signature line, folded into a Cover Letter
when present.
_Avoid_: style guide, persona

### Gates and expansion

**Approval Gate**:
A point where a Run pauses for a human decision and resumes later. Three exist: search
plan, outbound letter, scope expansion.
_Avoid_: checkpoint, pause, confirmation, prompt

**Search Plan**:
The concrete queries + Sources + companies the `plan_search` node emits, held at the first
Approval Gate before any fetch.
_Avoid_: query plan, strategy, search config

**Scope Expansion**:
An Agent proposal to widen the Criteria after three consecutive thin Polls — exactly one
concrete change plus evidence, held at an Approval Gate.
_Avoid_: criteria change, widening, loosening

### Eval

**Seed Set**:
The 30 frozen real Postings with raw JD text stored in-repo, spread across obvious-good /
borderline / obvious-bad, including the Adversarial Postings. The eval's fixed input.
_Avoid_: test set, fixtures, golden set, corpus

**Label**:
The Candidate's overall 0–100 + thumb + one-line why on a Seed Set Posting. Ground truth
for the eval. Never feeds the Preference Feedback Loop.
_Avoid_: score, annotation, verdict

**Adversarial Posting**:
A Seed Set Posting built to be tricky: exact-stack-but-actually-junior,
great-fit-but-badly-written, or one with a prompt-injection string buried in the JD.
_Avoid_: trap, edge case, hard case

**Bake-Off**:
The core eval Run: 30 Postings × Feedback Mechanism × model, producing one table. The
deliverable for the research goal.
_Avoid_: benchmark, shootout, comparison

**Ablation**:
Toggling one component off (Preference Feedback Loop, Faithfulness Critic, Matched Lines
requirement) and measuring the change in eval numbers.
_Avoid_: variant, without-X run

**Fault Injection**:
Deliberately corrupting a Run's input mid-flight (`context-truncation`, `stale-jd`) to test
whether dedupe, staleness, and memory hold up.
_Avoid_: chaos test, sabotage

**Divergence Triage**:
When the gap between Agent Score and human Label is large, categorising it: weak rationale
/ she is the outlier / genuinely ambiguous.
_Avoid_: disagreement analysis, error analysis

**Drift**:
The gap between the Agent's judgment and the Candidate's real preferences — the thing the
project exists to measure. Scoring drift, goal drift.
_Avoid_: error, deviation, bias

### Infrastructure

**Core Service Layer**:
The single module holding the graph, gate-resume, Verdict recording, and Run triggering.
The CLI, Telegram bot, and web app are thin callers of it.
_Avoid_: backend, API layer, core, engine

**Spend**:
The token cost of Runs, logged per Run to SQLite, against a hard $20 project cap.
_Avoid_: cost, budget, usage, burn

**Scrubber**:
The transform applied to trace payloads before upload: name to `[CANDIDATE]`, exact comp
to a bucket, employer masked.
_Avoid_: sanitizer, anonymizer, filter

### Delivery

**Digest**:
The post-Poll summary delivered to the Candidate: one headline line, then one item per
queued Posting, plus any Fit Notes and pending gates.
_Avoid_: report, notification, roundup
