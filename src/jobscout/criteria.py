"""Criteria derivation and the hand-editable file (DESIGN §5, §10, §12).

`Criteria` is the editable object derived from the Profile, in three buckets
(CONTEXT.md: Criteria — knockout/scored/learn). Touches neither the graph
nor the store; CoreService (unit 3) persists the SQLite version and calls
write_criteria_file for the hand-editable copy.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from jobscout.llm import get_llm

DEFAULT_CRITERIA_PATH = Path("data/criteria.yaml")


class KnockoutRule(BaseModel):
    """A hard pass/fail axis (CONTEXT.md: Knockout). Any fail excludes the
    Posting outright, no Score."""

    axis: str = Field(
        description="Named axis, e.g. seniority_band, location, german_required"
    )
    rule: str = Field(description="Plain-language pass/fail rule for this axis")


class ScoredDimension(BaseModel):
    """One of the four fixed weighted 0-5 axes (CONTEXT.md: Scored Dimension)."""

    dimension: str = Field(
        description=(
            "One of: stack fit, domain/product interest, scope & seniority "
            "signals, eng-culture signals"
        )
    )
    weight: float = Field(
        description="Relative weight, 0-1, the four should sum to roughly 1.0"
    )
    rubric: str = Field(
        description=(
            "Concrete anchor for this candidate, e.g. 'stack 5 = JD names 3+ "
            "of React/TypeScript/GraphQL as core'"
        )
    )


class Criteria(BaseModel):
    """The editable object derived from the Profile (CONTEXT.md: Criteria)."""

    knockout: list[KnockoutRule]
    scored: list[ScoredDimension] = Field(min_length=4, max_length=4)
    learn: list[str] = Field(
        description="Soft signals with insufficient info yet, left to the Preference Feedback Loop"
    )


_CRITERIA_PROMPT = (
    "Derive job-search criteria from this candidate's Profile, in three buckets:\n\n"
    "knockout: hard pass/fail axes, each a rule a Posting either meets or fails "
    "outright. Always include seniority_band, location, work_auth_language, and "
    "german_required, derived from the static answers. Add hard_exclude_industries "
    "and must_have_stack if the candidate named any. Add salary_floor here only if "
    "the candidate said their salary floor is a hard knockout, not scored.\n\n"
    "scored: exactly these four dimensions, each with a 0-1 weight (roughly "
    "summing to 1.0) and a concrete rubric anchor tailored to this candidate's "
    "actual resume: 'stack fit', 'domain/product interest', 'scope & seniority "
    "signals', 'eng-culture signals'. If salary is scored (not knockout), fold it "
    "into whichever dimension's rubric text fits best rather than inventing a "
    "fifth dimension.\n\n"
    "learn: soft signals with insufficient information yet, left for the "
    "preference feedback loop to refine from future thumbs up/down (can be "
    "empty).\n\nProfile:\n{profile}"
)


def derive_criteria(profile: dict) -> Criteria:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes)."""
    structured_llm = get_llm().with_structured_output(Criteria)
    return structured_llm.invoke(_CRITERIA_PROMPT.format(profile=profile))


def write_criteria_file(criteria: Criteria, path: Path = DEFAULT_CRITERIA_PATH) -> None:
    """Human-editable copy (DESIGN §12: "criteria stays file-edited")."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(criteria.model_dump(), sort_keys=False, allow_unicode=True)
    )


def read_criteria_file(path: Path = DEFAULT_CRITERIA_PATH) -> Criteria:
    return Criteria(**yaml.safe_load(path.read_text()))
