"""Search Plan derivation — the first real LLM call (DESIGN §4, §10).

`plan_search` emits concrete queries + Sources + companies from Criteria,
held at the first Approval Gate before any network fetch.
"""

from typing import Literal

from pydantic import BaseModel, Field

from jobscout.llm import get_llm

Source = Literal["adzuna", "arbeitnow", "curated_ats", "trafilatura"]


class SearchPlan(BaseModel):
    """CONTEXT.md: Search Plan — queries + Sources + companies."""

    queries: list[str] = Field(
        description="Free-text search queries for the API Sources (Adzuna, arbeitnow)"
    )
    sources: list[Source] = Field(description="Which Sources to hit this Poll")
    companies: list[str] = Field(
        description=(
            "Target companies to check via Curated ATS Boards or the "
            "trafilatura fallback"
        )
    )


_SEARCH_PLAN_PROMPT = (
    "Derive a concrete Search Plan for this Poll from the candidate's Criteria.\n\n"
    "queries: free-text search strings for the API Sources (Adzuna, arbeitnow) — "
    "job titles / stack terms that match the scored dimensions and respect the "
    "knockout rules (e.g. don't query broad titles the seniority_band knockout "
    "would exclude).\n\n"
    "sources: pick from adzuna, arbeitnow, curated_ats, trafilatura — include "
    "curated_ats whenever the Criteria implies specific target companies or a "
    "company size/stage preference; skip a Source if nothing in the Criteria "
    "fits it.\n\n"
    "companies: concrete company names worth checking via Curated ATS Boards or "
    "the trafilatura fallback, inferred from the Criteria (industry, stack, "
    "stage). Empty if the Criteria gives no basis to name any.\n\n"
    "Criteria:\n{criteria}"
)


def derive_search_plan(criteria: dict) -> SearchPlan:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes)."""
    structured_llm = get_llm().with_structured_output(SearchPlan)
    return structured_llm.invoke(_SEARCH_PLAN_PROMPT.format(criteria=criteria))
