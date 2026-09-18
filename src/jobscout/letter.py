"""Cover Letter drafting (DESIGN §8, build-plan unit 23).

"LLM extracts the fact, code decides" — same split `knockout.py`
established for Knockouts: the LLM reports `jd_language` as a plain
FACT about the Job Description, and this module's own code decides
whether DESIGN §8's German-below-B2 override applies, rather than
trusting the model to remember a rule with real consequences (sending
a German letter to a company when her German isn't there yet). The
override only costs a second LLM call in the rare case it fires — the
common case (English JD, or German JD she's fine with) is one call.
"""

from typing import Literal

from pydantic import BaseModel, Field

from jobscout.llm import get_llm

_BELOW_B2 = {"none", "a1-a2"}
_OVERRIDE_FLAG = "JD implies German — decide before sending"


class CoverLetterDraft(BaseModel):
    """CONTEXT.md: Cover Letter — 3-paragraph, ~250 words, JD's language
    unless the German-below-B2 override applies."""

    body: str
    language: Literal["german", "english"]
    flag: str | None = None


class _DraftedLetter(BaseModel):
    jd_language: Literal["german", "english", "other"] = Field(
        description="The Job Description's own primary language"
    )
    body: str = Field(description="The letter body, written in the Job Description's language")


_LETTER_PROMPT = (
    "Write a Cover Letter for this candidate applying to the Job Description "
    "below: modern, three paragraphs, about 250 words — not a formal German "
    "Anschreiben. Write it in the Job Description's own language. Honest-but-"
    "positive tone: ground every claim strictly in the Resume and Profile "
    "given below, never assert a skill or experience they don't show. Also "
    "report jd_language: the Job Description's primary language (german, "
    "english, or other).\n\n"
    "{voice_notes}"
    "Resume:\n{resume_text}\n\nProfile:\n{profile}\n\nJob Description:\n{jd_text}"
)

_ENGLISH_OVERRIDE_PROMPT = (
    "Write the same Cover Letter as before, in English instead — same "
    "candidate, same Job Description, same grounding rules: modern, three "
    "paragraphs, about 250 words, honest-but-positive, every claim strictly "
    "traceable to the Resume and Profile below, never assert a skill or "
    "experience they don't show.\n\n"
    "{voice_notes}"
    "Resume:\n{resume_text}\n\nProfile:\n{profile}\n\nJob Description:\n{jd_text}"
)


def _voice_notes_block(voice_notes: str | None) -> str:
    if not voice_notes:
        return ""
    return f"Voice Notes (tone + signature to honor):\n{voice_notes}\n\n"


def _below_b2(profile: dict) -> bool:
    german_level = profile.get("static_answers", {}).get("german_level") or ""
    return german_level.strip().lower() in _BELOW_B2


def draft_letter(
    jd_text: str,
    resume_text: str,
    profile: dict,
    voice_notes: str | None = None,
) -> CoverLetterDraft:
    """One structured LLM call (DESIGN §4) — two only when the
    German-below-B2 override fires."""
    voice_block = _voice_notes_block(voice_notes)
    structured_llm = get_llm().with_structured_output(_DraftedLetter)
    drafted = structured_llm.invoke(
        _LETTER_PROMPT.format(
            voice_notes=voice_block, resume_text=resume_text, profile=profile, jd_text=jd_text
        )
    )

    if drafted.jd_language == "german" and _below_b2(profile):
        override_llm = get_llm().with_structured_output(_DraftedLetter)
        english = override_llm.invoke(
            _ENGLISH_OVERRIDE_PROMPT.format(
                voice_notes=voice_block,
                resume_text=resume_text,
                profile=profile,
                jd_text=jd_text,
            )
        )
        return CoverLetterDraft(body=english.body, language="english", flag=_OVERRIDE_FLAG)

    language = "german" if drafted.jd_language == "german" else "english"
    return CoverLetterDraft(body=drafted.body, language=language)
