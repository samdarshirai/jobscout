"""Onboarding questions — static catalog and dynamic LLM follow-ups (DESIGN §5 stages 2-3).

Touches neither the graph nor the store, same boundary as jobscout.resume.
"""

from pydantic import BaseModel, Field

from jobscout.llm import get_llm

STATIC_QUESTIONS = [
    {
        "key": "german_level",
        "prompt": "What is your German language level? (none, A1-A2, B1-B2, C1-C2, native)",
    },
    {
        "key": "include_german_required_roles",
        "prompt": "Include roles that require German, even above your stated level? (yes/no)",
    },
    {
        "key": "salary_floor",
        "prompt": "What is your minimum acceptable salary (annual, EUR)? Is this a "
        "hard floor (knockout) or just preferred (scored)?",
    },
    {"key": "work_mode", "prompt": "Preferred work mode: remote, hybrid, or onsite?"},
    {
        "key": "acceptable_cities",
        "prompt": "Which cities are acceptable for hybrid/onsite roles?",
    },
    {
        "key": "company_size_stage",
        "prompt": "Preferred company size/stage (startup, scale-up, mid-size, enterprise)?",
    },
    {"key": "hard_exclude_industries", "prompt": "Any industries to hard-exclude?"},
    {"key": "must_have_stack", "prompt": "Any must-have technologies/stack?"},
    {
        "key": "contract_type",
        "prompt": "Preferred contract type (permanent, contract, freelance)?",
    },
]


class DynamicQuestions(BaseModel):
    """3-5 targeted follow-ups from the resume + static answers (DESIGN §5 stage 3)."""

    questions: list[str] = Field(
        min_length=3,
        max_length=5,
        description=(
            "Targeted follow-up questions, e.g. resolving stack ambiguity "
            "('6y React but also Vue - are Vue-only roles ok?') or seniority "
            "preference ('you've led a team - IC only or open to lead?')"
        ),
    )


_DYNAMIC_PROMPT = (
    "Based on this resume and these static answers, ask 3-5 targeted follow-up "
    "questions that would help score job postings for this candidate. Good "
    "examples: resolving ambiguity in their stack, or seniority/leadership "
    "preference.\n\nResume text:\n{resume_text}\n\nStatic answers:\n{static_answers}"
)


def generate_dynamic_questions(resume_text: str, static_answers: dict) -> list[str]:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes)."""
    structured_llm = get_llm().with_structured_output(DynamicQuestions)
    result = structured_llm.invoke(
        _DYNAMIC_PROMPT.format(resume_text=resume_text, static_answers=static_answers)
    )
    return result.questions
