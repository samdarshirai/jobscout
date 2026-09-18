"""Fit Notes generation (DESIGN §8, build-plan unit 25).

CONTEXT.md: Fit Notes — "the honest account of where the Candidate
falls short of a Posting, delivered to her separately — never folded
into the Cover Letter." This module has zero coupling to `letter.py`:
it produces its own separate output from the same two grounding
sources (resume, JD) that DESIGN §6's scoring anti-inflation rule
already treats as the canonical evidence pair.
"""

from pydantic import BaseModel, Field

from jobscout.llm import get_llm


class FitGap(BaseModel):
    gap: str = Field(
        description="One concrete, honest way the candidate falls short of this JD"
    )
    jd_line: str | None = Field(
        default=None, description="Quoted JD line this gap is about, or null if none"
    )


class FitNotes(BaseModel):
    """CONTEXT.md: Fit Notes — gaps between resume and JD, reported separately."""

    gaps: list[FitGap]


_FIT_NOTES_PROMPT = (
    "List the concrete, honest ways this candidate falls short of the Job "
    "Description below, grounded specifically in what the JD asks for versus "
    "what the resume actually shows — not generic career advice or "
    "speculation. Quote the specific JD requirement line when there is one "
    "to point at; leave it null for a more holistic gap. Empty list if there "
    "is no real gap — don't manufacture one.\n\n"
    "Resume:\n{resume_text}\n\nJob Description:\n{jd_text}"
)


def generate_fit_notes(jd_text: str, resume_text: str) -> FitNotes:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes)."""
    structured_llm = get_llm().with_structured_output(FitNotes)
    return structured_llm.invoke(
        _FIT_NOTES_PROMPT.format(resume_text=resume_text, jd_text=jd_text)
    )
