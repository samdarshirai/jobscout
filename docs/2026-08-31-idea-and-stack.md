# Jobscout — Idea and Stack

**Status:** decisions locked. Full design (graph shape, data model, eval harness) not written yet.
**Date:** 2026-08-31

## Why this project

Learning vehicle for LangChain/LangGraph. Two goals, in order:

1. **Portfolio / interview piece** — demoable in five minutes, explainable in depth.
2. **Find where agents break** — measure failure, don't hand-wave it.

Constraint: a few evenings across 2–3 weeks. Python. Cheapest capable models.

## The idea

A personal job-hunt agent. Mini version of Simplify / Teal, running on one person's search.

Loop:

- Watches job boards against a criteria set.
- Reads each job description properly — not keyword matching.
- Scores it against the resume, and says *why*, with the specific lines it matched on.
- Drafts a tailored cover letter.
- Flags where the fit is weak instead of papering over it.
- **Never submits anything without explicit approval.**

Long-horizon axis: it runs for weeks. It has to dedupe postings, notice when one goes
stale, and remember what was already rejected and why.

### Where it should break (the actual research question)

"Is this a good fit for me" is a judgment that cannot be specified in a prompt. The
agent's scoring will drift from the user's real preferences. The interesting work is
measuring that gap and closing it with a feedback loop — thumbs up/down on scored
postings, fed back as few-shot examples or a learned preference summary.

Secondary failure modes to instrument:

- Cover letters that hallucinate experience not in the resume.
- Score inflation — everything comes back a 7/10.
- Goal drift over a multi-week run.
- Context loss on resume across long sessions.

## Approval gates

The agent proposes; the human commits. Gates sit at:

- The search plan, before it burns tokens.
- Any outbound action — application, message, form fill.
- Scope expansion mid-run (agent wants to widen criteria).

Implemented with LangGraph `interrupt()` plus a checkpointer, so a run can be paused,
answered hours later, and resumed.

## Stack

| Layer | Choice | Reason |
|---|---|---|
| Core | LangGraph + LangChain | Graph, checkpointing, `interrupt()`. Approval gates and resume are built in, not hand-rolled. |
| Model (workhorse) | DeepSeek V3.2 | ~$0.21/M in, $0.31/M out. Real tool-calling. |
| Model (planner) | DeepSeek V4 Flash | Reasoning-heavier node. |
| Gateway | OpenRouter | See below. |
| Search | Tavily free tier (1k/mo) | Agent-shaped results. Brave API as backup. |
| Fetch / extract | `httpx` + `trafilatura` | Strips boilerplate. No headless browser. |
| State | SQLite — LangGraph `SqliteSaver` + own tables | No Postgres, no vector DB. |
| Traces | LangSmith free tier | A 40-step agent is not debuggable from stdout. |
| CLI | Typer + Rich | Week 1–2 interface. |
| Web | FastAPI + one HTML page, SSE | Week 3. No React. |
| Evals | pytest + seed set + LLM-judge | ~20 postings with human-assigned scores as ground truth. |
| Packaging | `uv` | |

**Deliberately skipped:** vector database, Postgres, Docker, React, any second agent
framework. Add when a measurement says one is needed.

### Why OpenRouter and not DeepSeek direct

Direct is cheaper — OpenRouter passes through provider rates plus a 5.5% credit fee,
which at this project's spend is cents. Three reasons it still wins:

1. **The eval harness is a model bake-off.** Holding the graph fixed while swapping
   DeepSeek → Qwen → GLM → Kimi → a frontier model is the core experiment. One key,
   one base URL, change a string. Direct means N accounts and N billing setups.
2. **Data.** DeepSeek direct processes and stores in the PRC, and its policy permits
   using data to improve models with no clean API-side opt-out. This agent holds a
   resume, employment history, and salary expectations. OpenRouter can route the same
   open weights to non-training providers.
3. **Flat pricing.** Direct DeepSeek runs peak/off-peak with a 2x swing, which muddies
   cost-per-run in eval output.

Both are OpenAI-compatible. Switching is a base URL and key change — config, not an
abstraction layer.

### Why not OpenClaw or Hermes Agent

Different category. LangGraph is a library — you write the loop. [OpenClaw](https://docs.openclaw.ai/start/openclaw)
and [Hermes Agent](https://hermes-agent.nousresearch.com/) are harnesses: a finished
runtime with memory, scheduling, and messaging gateways already wired.

Wrong tool for both goals. A harness owns the loop, so you cannot inject failures,
force a replan, or hold the graph fixed across models — which is the experiment. And
"I configured OpenClaw" is a weekend of YAML; "I built the graph, gated it, and
measured where it fails" is what gets asked about.

Where they fit: week 3, wrap the agent as an MCP server and let OpenClaw or Hermes
drive it for Telegram/WhatsApp delivery. Worth reading Hermes' memory architecture
(semantic + working + episodic, self-authored skills) for ideas — steal the design,
not the runtime.

## Rough shape of the three weeks

- **Week 1** — CLI. Fetch and parse postings, score against resume, one approval gate. Traces on from day one.
- **Week 2** — Persistence and long-horizon. Dedupe, staleness, resume-across-sessions, cover letter drafting, preference feedback loop.
- **Week 3** — Eval harness and the numbers. Thin web UI. Optional MCP wrapper.

## Open questions for the design pass

- Which boards, and via what — official APIs, RSS, or scraping (and its terms).
- Graph shape: single agent with tools, or planner/worker/critic split.
- What "score" means concretely — dimensions and rubric.
- Preference feedback: few-shot examples, a maintained preference doc, or both.
- Eval metrics: score correlation against human labels, cover-letter faithfulness rate, cost per posting.
