"""Knockout extraction + decision (DESIGN §6, build-plan unit 11).

"An LLM extracts the fact, a rule decides": one structured LLM call reads
the JD against each Knockout axis and reports pass/fail + evidence per
axis; a plain function then decides — any failing axis excludes the
Posting outright (DESIGN §6: auto-drop happens only on a hard Knockout).
The LLM never makes the exclude decision itself, only reports what the JD
says.
"""

from pydantic import BaseModel, Field

from jobscout.criteria import KnockoutRule
from jobscout.llm import get_llm


class AxisFact(BaseModel):
    axis: str = Field(description="Must match one of the given Knockout axes")
    passes: bool = Field(description="Does the JD satisfy this axis's rule?")
    evidence: str = Field(description="Quoted or paraphrased JD text the fact rests on")


class KnockoutFacts(BaseModel):
    axes: list[AxisFact]


_KNOCKOUT_PROMPT = (
    "For each Knockout axis below, read the job board's location field and the "
    "Job Description, and decide whether the JD satisfies that axis's rule. A JD "
    "silent on an axis passes it — only mark passes=false when the JD or the "
    "location field actively conflicts with the rule.\n\n"
    "For an axis about the role's SCOPE or SENIORITY (e.g. \"frontend engineering "
    "scope\"), judge it from what the JD actually describes the role as being about "
    "— its title, responsibilities, and day-to-day work — not from an isolated "
    "keyword that happens to appear elsewhere, like a stack term buried in an "
    "unrelated tools/systems list on an otherwise non-engineering role.\n\n"
    "Knockout axes:\n{axes}\n\nJob board location field: {city}\n\n"
    "Job Description:\n{jd_text}"
)


def extract_knockout_facts(
    jd_text: str, rules: list[KnockoutRule], city: str | None = None
) -> KnockoutFacts:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes).

    `city` is the job board's own structured location field, not part of
    jd_text — confirmed live: a JD's prose can be entirely silent on
    location (relying on the board's metadata instead), so the location
    axis needs this passed in explicitly or it has nothing to check
    against and defaults to passing."""
    axes_text = "\n".join(f"- {r.axis}: {r.rule}" for r in rules)
    structured_llm = get_llm().with_structured_output(KnockoutFacts)
    return structured_llm.invoke(
        _KNOCKOUT_PROMPT.format(axes=axes_text, city=city or "not given", jd_text=jd_text)
    )


def decide_knockout(facts: KnockoutFacts) -> str | None:
    """Any failing axis excludes the Posting outright (DESIGN §6). Returns
    the exclusion reason, or None if every axis passes."""
    failed = [axis for axis in facts.axes if not axis.passes]
    if not failed:
        return None
    return "; ".join(f"{axis.axis}: {axis.evidence}" for axis in failed)
