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
    "candidate's skills, experience, or background — a claim is a concrete, "
    "checkable assertion (\"I led a team of 5 engineers\"). NONE of the "
    "following are claims, do not extract them:\n"
    "- a pleasantry, greeting, or closing line (\"I'm excited about this "
    "opportunity\", \"I'd welcome the chance to discuss further\")\n"
    "- a statement about the COMPANY, role, or mission, not the candidate's "
    "own background (\"your mission resonates with me\")\n"
    "- a self-assessment, opinion, or admitted gap about the candidate "
    "(\"Java is not my strongest area\", \"I have a system-design mindset\", "
    "\"I can contribute while growing into it\")\n"
    "- a forward-looking intention (\"I'd welcome the chance...\")\n\n"
    "Extract claims at the sentence or clause level — a sentence naming "
    "several resume-backed tools or projects together is ONE claim, not "
    "one per tool. Under-counting a compound sentence as one claim is fine; "
    "splitting it into five is not.\n\n"
    "For each real claim, judge whether the Resume OR the Profile below "
    "supports its SUBSTANCE — a different phrasing, tense, or combining "
    "two lines into one sentence still counts as traceable. A claim about "
    "language level, location preference, or work authorization only the "
    "Profile would state (not a resume section) is traceable if the "
    "Profile supports it. Only mark untraceable when neither the Resume "
    "nor the Profile supports what the letter asserts, or one of them "
    "meaningfully contradicts it. Quote the exact supporting line when it "
    "does support the claim.\n\n"
    "Cover Letter:\n{letter_body}\n\nResume:\n{resume_text}\n\nProfile:\n{profile}"
)


def check_faithfulness(letter_body: str, resume_text: str, profile: dict | None = None) -> FaithfulnessReport:
    """One structured LLM call (DESIGN §4) — an LLM-judge critic, not a
    ReAct agent.

    Confirmed live (unit 37 ablation): draft_letter is given BOTH resume
    and profile (language level, location preference, work authorization
    aren't resume content), but check_faithfulness only ever saw the
    resume — every legitimate profile-sourced claim in a real letter came
    back "untraceable" purely because the checker never had the data to
    trace it against. `profile` is optional (default None -> "n/a") so
    existing calls don't break."""
    structured_llm = get_llm().with_structured_output(FaithfulnessReport)
    return structured_llm.invoke(
        _FAITHFULNESS_PROMPT.format(
            letter_body=letter_body, resume_text=resume_text, profile=profile or "n/a"
        )
    )


def untraceable_claims(report: FaithfulnessReport) -> list[ClaimCheck]:
    """The "rejected" set (DESIGN §8, build-plan unit 24)."""
    return [c for c in report.claims if not c.traceable]


def is_faithful(report: FaithfulnessReport) -> bool:
    return not untraceable_claims(report)
