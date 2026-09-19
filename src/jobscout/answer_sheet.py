"""Answer Sheet generation (DESIGN §9, build-plan unit 28).

CONTEXT.md: Answer Sheet — filled responses to common application
questions (visa status, notice period, salary expectation, why this
company), part of a Package. Same "LLM generates what it can honestly
ground, code fills in the rest" split `letter.py` established for the
German-below-B2 override.

`visa_status` and `notice_period` are hard-coded placeholders, not LLM
output — deliberately. Nothing in this codebase collects either fact
(`questions.py`'s onboarding catalog has no such question, and a resume
essentially never states them), so asking the model to fill them in
would mean fabricating a real candidate's legal/personal status on a
real job application. That's a harm, not a hypothetical — do not "fix"
this by wiring these fields to the LLM.
"""

from pydantic import BaseModel, Field

from jobscout.llm import get_llm

_NOT_ON_FILE = "Not on file — please fill in before sending."


class AnswerSheet(BaseModel):
    """CONTEXT.md: Answer Sheet — part of a Package."""

    visa_status: str
    notice_period: str
    salary_expectation: str
    why_this_company: str


class _DraftedAnswers(BaseModel):
    salary_expectation: str = Field(
        description="A natural sentence stating salary expectation, grounded in the "
        "candidate's stated salary floor — never a number not derived from it"
    )
    why_this_company: str = Field(
        description="A specific, honest reason this candidate wants THIS company, "
        "grounded in the Job Description and Resume/Profile — not generic boilerplate"
    )


_ANSWER_SHEET_PROMPT = (
    "Fill in two application-question answers for this candidate, honest and "
    "specific, grounded strictly in what's given below — no invented facts.\n\n"
    "salary_expectation: a natural sentence stating their salary expectation, "
    "grounded in their stated salary floor below — never a number beyond what "
    "that floor implies.\n\n"
    "why_this_company: a specific, honest reason THIS candidate wants THIS "
    "company, grounded in the Job Description and Resume/Profile — not generic "
    "boilerplate that could apply to any company.\n\n"
    "Company: {company}\n\nCandidate's stated salary floor: {salary_floor}\n\n"
    "Resume:\n{resume_text}\n\nProfile:\n{profile}\n\nJob Description:\n{jd_text}"
)


def generate_answer_sheet(jd_text: str, resume_text: str, profile: dict, company: str) -> AnswerSheet:
    """One structured LLM call (DESIGN §4) for the two groundable fields;
    `visa_status`/`notice_period` are fixed placeholders, never LLM output."""
    salary_floor = profile.get("static_answers", {}).get("salary_floor") or "not specified"
    structured_llm = get_llm().with_structured_output(_DraftedAnswers)
    drafted = structured_llm.invoke(
        _ANSWER_SHEET_PROMPT.format(
            company=company,
            salary_floor=salary_floor,
            resume_text=resume_text,
            profile=profile,
            jd_text=jd_text,
        )
    )
    return AnswerSheet(
        visa_status=_NOT_ON_FILE,
        notice_period=_NOT_ON_FILE,
        salary_expectation=drafted.salary_expectation,
        why_this_company=drafted.why_this_company,
    )
