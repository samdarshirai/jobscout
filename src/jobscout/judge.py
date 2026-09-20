"""Reasoning judge (DESIGN §15, build-plan units 34-35).

A cheap-frontier anchor model (`llm.JUDGE_MODEL` — deliberately a
different lab than the Score Sub-Agent's own model, so it isn't grading
its own family's work) checks whether a Posting's Score is actually
consistent with what its own Rationale + per-dimension citations
describe (unit 34, calibrated against ~8 of the Candidate's independent
hand-checks), plus unit 35's remaining reasoning-quality checks that
need judgment rather than a code check: matched-line relevance,
knockout correctness, and divergence triage. Matched-line *realness*
(is the quote an actual substring) stays a cheap code check in
`eval.py` — no LLM call needed to test string containment.
"""

from typing import Literal

from pydantic import BaseModel, Field

from jobscout.llm import JUDGE_MODEL, get_llm


class ConsistencyVerdict(BaseModel):
    consistent: bool = Field(
        description="Does the numeric score plausibly follow from what the rationale and "
        "dimension scores actually describe?"
    )
    reasoning: str = Field(description="One or two sentences explaining the verdict")


_JUDGE_PROMPT = (
    "A scoring agent gave this job Posting a final score of {score}/100 and wrote the "
    "rationale and per-dimension scores below. Judge only whether the final score is "
    "internally consistent with what the rationale and dimensions actually describe — "
    "not whether the score itself is correct. A rationale calling something a strong "
    "match but landing on a low score, or vice versa, is inconsistent; a low-scoring "
    "dimension driving a low overall despite one strong dimension is consistent (that's "
    "how a weighted average works).\n\n"
    "Rationale:\n{rationale}\n\nDimensions:\n{dimensions}"
)


def judge_score_rationale_consistency(
    score: int, rationale: str, dimensions_text: str
) -> ConsistencyVerdict:
    """One structured judge call (DESIGN §4: LLM calls only at named nodes)."""
    judge_llm = get_llm(JUDGE_MODEL).with_structured_output(ConsistencyVerdict)
    return judge_llm.invoke(
        _JUDGE_PROMPT.format(score=score, rationale=rationale, dimensions=dimensions_text)
    )


class MatchedLineVerdict(BaseModel):
    relevant: bool = Field(
        description="Does the quoted JD line and resume line actually support THIS dimension, "
        "not just appear somewhere in the JD/resume?"
    )
    reasoning: str = Field(description="One sentence explaining the verdict")


_MATCHED_LINE_PROMPT = (
    "A scoring agent cited these two quotes as evidence for the Dimension below. Both quotes "
    "are confirmed to be real, verbatim lines from the JD and resume — judge only whether they "
    "are actually RELEVANT to this specific dimension, not a real-but-irrelevant quote used just "
    "to pass a citation requirement.\n\n"
    "Dimension: {dimension}\n\nJD line: {jd_line}\n\nResume line: {resume_line}"
)


def judge_matched_line_relevance(dimension: str, jd_line: str, resume_line: str) -> MatchedLineVerdict:
    """Matched Lines validity has two parts (DESIGN §15): the quotes are
    *real* (a cheap substring check in `eval.py`, no LLM needed) and the
    quotes are *relevant* to the dimension they're cited for, which needs
    judgment."""
    judge_llm = get_llm(JUDGE_MODEL).with_structured_output(MatchedLineVerdict)
    return judge_llm.invoke(
        _MATCHED_LINE_PROMPT.format(dimension=dimension, jd_line=jd_line, resume_line=resume_line)
    )


class KnockoutVerdict(BaseModel):
    correct: bool = Field(
        description="Given the JD and job-board location field, is the knockout decision correct?"
    )
    reasoning: str = Field(description="One or two sentences explaining the verdict")


_KNOCKOUT_JUDGE_PROMPT = (
    "A knockout filter judged this Posting against the Knockout rules below, giving status "
    "{status} (reason: {status_reason}).\n\n"
    "Judge ONLY whether that decision is a literal, mechanical application of the rule text as "
    "written — do not relitigate it with your own judgment about fairness, seniority nuance, or "
    "ambiguity beyond what a rule's own text explicitly carves out as an exception. If a rule says "
    "'FAIL if X is entirely absent' and X really is absent from the JD, that FAIL is correct, full "
    "stop — the presence of related-but-different things doesn't override an explicit absence "
    "condition unless the rule's own wording says so. Likewise, if a rule allows a range (e.g. "
    "'Senior, Lead, or Staff... open to IC or lead'), a posting anywhere in that stated range passes "
    "it, even if you'd personally read it as skewing toward one end.\n\n"
    "Knockout rules:\n{rules_text}\n\nJob board location field: {city}\n\nJob Description:\n{jd_text}"
)


def judge_knockout_correctness(
    jd_text: str, city: str | None, rules_text: str, status: str, status_reason: str | None
) -> KnockoutVerdict:
    """Audits a Posting's *stored* knockout decision (DESIGN §6, §15) against
    the real JD — not a re-extraction, a correctness check of what actually
    ran in production."""
    judge_llm = get_llm(JUDGE_MODEL).with_structured_output(KnockoutVerdict)
    return judge_llm.invoke(
        _KNOCKOUT_JUDGE_PROMPT.format(
            status=status,
            status_reason=status_reason or "n/a — passed every axis",
            rules_text=rules_text,
            city=city or "not given",
            jd_text=jd_text,
        )
    )


class DivergenceVerdict(BaseModel):
    category: Literal["weak_rationale", "she_is_outlier", "genuinely_ambiguous"] = Field(
        description="weak_rationale: the agent's reasoning doesn't hold up under its own logic. "
        "she_is_outlier: the agent's reasoning is sound and her verdict looks like the unusual "
        "call. genuinely_ambiguous: both readings are defensible."
    )
    reasoning: str = Field(description="One or two sentences explaining the categorisation")


_DIVERGENCE_PROMPT = (
    "The Agent scored this Posting {agent_score}/100. The Candidate independently scored it "
    "{human_score}/100 and gave this one-line why: \"{human_why}\"\n\n"
    "The Agent's rationale:\n{rationale}\n\n"
    "Categorise this divergence: weak_rationale, she_is_outlier, or genuinely_ambiguous."
)


def judge_divergence(
    agent_score: int, human_score: int, rationale: str, human_why: str | None
) -> DivergenceVerdict:
    """Divergence Triage (DESIGN §15): when |agent - human| is large, is it
    the agent's reasoning that's weak, is she the outlier, or is the call
    genuinely ambiguous either way."""
    judge_llm = get_llm(JUDGE_MODEL).with_structured_output(DivergenceVerdict)
    return judge_llm.invoke(
        _DIVERGENCE_PROMPT.format(
            agent_score=agent_score,
            human_score=human_score,
            rationale=rationale,
            human_why=human_why or "(no reason given)",
        )
    )
