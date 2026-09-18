"""Resume ingest — PDF to structured Profile (DESIGN §5 stage 1).

Touches neither the graph nor the store: `extract_resume_text` and
`parse_profile` are pure functions over their arguments plus one LLM call.
`jobscout.graph.onboard.ingest` calls both; `CoreService` (unit 3) is the
only thing that persists the result.
"""

from pathlib import Path

from pydantic import BaseModel, Field
from pypdf import PdfReader

from jobscout.llm import get_llm


class ExtractedProfile(BaseModel):
    """Structured resume extraction (build-plan unit 5)."""

    roles: list[str] = Field(description="Job titles/roles held, most recent first")
    years_experience: float = Field(description="Total years of professional experience")
    stack: list[str] = Field(description="Languages, frameworks, and tools used")
    seniority_signals: list[str] = Field(
        description="Phrases indicating seniority: team size led, scope of ownership, title"
    )


def extract_resume_text(pdf_path: Path) -> str:
    """Raw text of every page, joined with newlines."""
    reader = PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


_EXTRACTION_PROMPT = (
    "Extract a structured profile from this resume text. Identify roles "
    "held, total years of professional experience, the technical stack "
    "(languages/frameworks/tools), and any signals of seniority (team "
    "size led, scope of ownership, title).\n\nResume text:\n{resume_text}"
)


def parse_profile(resume_text: str) -> ExtractedProfile:
    """One structured LLM call (DESIGN §4: LLM calls only at named nodes)."""
    structured_llm = get_llm().with_structured_output(ExtractedProfile)
    return structured_llm.invoke(_EXTRACTION_PROMPT.format(resume_text=resume_text))
