"""Faithfulness Critic (DESIGN §4, §8; build-plan unit 24).

CONTEXT.md: Faithfulness Critic — "the LLM-judge that rejects any Cover
Letter claim not traceable to the resume." Same split as `knockout.py`:
the LLM judges each claim individually as a structured fact; the pass/fail
aggregation (`is_faithful` / `untraceable_claims`) happens in code, not
trusted from the model's own summary.
"""

from pydantic import BaseModel, Field

from jobscout.llm import get_llm


class ClaimCheck(BaseModel):
    claim: str = Field(description="The exact claim/sentence from the letter being judged")
    traceable: bool = Field(description="Does the resume directly support this claim?")
    resume_evidence: str | None = Field(
        default=None, description="Quoted resume line supporting the claim, or null if untraceable"
    )


class FaithfulnessReport(BaseModel):
    claims: list[ClaimCheck]


_FAITHFULNESS_PROMPT = (
    "Break this Cover Letter into its individual factual claims about the "
    "candidate's skills, experience, or background. Ignore generic "
    "pleasantries or closing lines that assert nothing factual (e.g. "
    "\"I'm excited about this opportunity\" is not a claim; \"I led a team "
    "of 5 engineers\" is).\n\n"
    "For each claim, judge strictly whether the Resume below directly "
    "supports it — quote the exact resume line when it does. A claim only "
    "loosely implied, or that extrapolates beyond what the resume actually "
    "says, is NOT traceable.\n\n"
    "Cover Letter:\n{letter_body}\n\nResume:\n{resume_text}"
)


def check_faithfulness(letter_body: str, resume_text: str) -> FaithfulnessReport:
    """One structured LLM call (DESIGN §4) — an LLM-judge critic, not a
    ReAct agent."""
    structured_llm = get_llm().with_structured_output(FaithfulnessReport)
    return structured_llm.invoke(
        _FAITHFULNESS_PROMPT.format(letter_body=letter_body, resume_text=resume_text)
    )


def untraceable_claims(report: FaithfulnessReport) -> list[ClaimCheck]:
    """The "rejected" set (DESIGN §8, build-plan unit 24)."""
    return [c for c in report.claims if not c.traceable]


def is_faithful(report: FaithfulnessReport) -> bool:
    return not untraceable_claims(report)
