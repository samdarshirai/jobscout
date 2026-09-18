from jobscout.letter import CoverLetterDraft, _DraftedLetter, draft_letter

_PROFILE_FINE_GERMAN = {"static_answers": {"german_level": "C1-C2"}}
_PROFILE_BELOW_B2 = {"static_answers": {"german_level": "A1-A2"}}
_PROFILE_NO_GERMAN = {"static_answers": {"german_level": "none"}}


class _FakeStructuredLLM:
    def __init__(self, fake_llm):
        self._fake_llm = fake_llm

    def invoke(self, prompt):
        self._fake_llm.prompts.append(prompt)
        return self._fake_llm.responses.pop(0)


class _FakeLLM:
    """One shared response queue + prompt log across every `get_llm()` call
    — `draft_letter` calls `get_llm()` fresh per LLM call, so the fake must
    behave like one continuous mock, not reset state each time."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.schemas = []
        self.structured = None

    def with_structured_output(self, schema):
        self.schemas.append(schema)
        self.structured = _FakeStructuredLLM(self)
        return self.structured


def _patch_llm(monkeypatch, responses):
    fake = _FakeLLM(responses)
    monkeypatch.setattr("jobscout.letter.get_llm", lambda: fake)
    return fake


def test_english_jd_returns_the_llm_own_language_in_one_call(monkeypatch):
    drafted = _DraftedLetter(jd_language="english", body="Dear hiring team...")
    fake = _patch_llm(monkeypatch, [drafted])

    result = draft_letter("We need a backend engineer.", "resume text", _PROFILE_NO_GERMAN)

    assert result == CoverLetterDraft(body="Dear hiring team...", language="english", flag=None)
    assert len(fake.prompts) == 1
    assert fake.schemas == [_DraftedLetter]


def test_german_jd_with_sufficient_german_returns_german_in_one_call(monkeypatch):
    drafted = _DraftedLetter(jd_language="german", body="Sehr geehrte...")
    fake = _patch_llm(monkeypatch, [drafted])

    result = draft_letter("Wir suchen einen Ingenieur.", "resume text", _PROFILE_FINE_GERMAN)

    assert result == CoverLetterDraft(body="Sehr geehrte...", language="german", flag=None)
    assert len(fake.prompts) == 1


def test_german_jd_below_b2_triggers_english_override_with_flag(monkeypatch):
    german_draft = _DraftedLetter(jd_language="german", body="Sehr geehrte...")
    english_override = _DraftedLetter(jd_language="english", body="Dear hiring team...")
    fake = _patch_llm(monkeypatch, [german_draft, english_override])

    result = draft_letter("Wir suchen einen Ingenieur.", "resume text", _PROFILE_BELOW_B2)

    assert result == CoverLetterDraft(
        body="Dear hiring team...",
        language="english",
        flag="JD implies German — decide before sending",
    )
    assert len(fake.prompts) == 2


def test_german_jd_with_no_german_at_all_also_triggers_override(monkeypatch):
    german_draft = _DraftedLetter(jd_language="german", body="Sehr geehrte...")
    english_override = _DraftedLetter(jd_language="english", body="Dear hiring team...")
    fake = _patch_llm(monkeypatch, [german_draft, english_override])

    result = draft_letter("Wir suchen einen Ingenieur.", "resume text", _PROFILE_NO_GERMAN)

    assert result.flag == "JD implies German — decide before sending"
    assert result.language == "english"


def test_voice_notes_are_folded_into_the_prompt_when_present(monkeypatch):
    drafted = _DraftedLetter(jd_language="english", body="Dear hiring team...")
    fake = _patch_llm(monkeypatch, [drafted])

    draft_letter(
        "We need a backend engineer.",
        "resume text",
        _PROFILE_NO_GERMAN,
        voice_notes="Sign off with 'Best, Ronali'",
    )

    prompt = fake.prompts[0]
    assert "Voice Notes" in prompt
    assert "Sign off with 'Best, Ronali'" in prompt


def test_no_voice_notes_section_when_none(monkeypatch):
    drafted = _DraftedLetter(jd_language="english", body="Dear hiring team...")
    fake = _patch_llm(monkeypatch, [drafted])

    draft_letter("We need a backend engineer.", "resume text", _PROFILE_NO_GERMAN)

    assert "Voice Notes" not in fake.prompts[0]


def test_static_answers_german_level_is_read_for_the_override_decision(monkeypatch):
    german_draft = _DraftedLetter(jd_language="german", body="Sehr geehrte...")
    english_override = _DraftedLetter(jd_language="english", body="Dear hiring team...")
    fake = _patch_llm(monkeypatch, [german_draft, english_override])

    profile = {"resume": {}, "static_answers": {"german_level": "A1-A2"}, "dynamic_answers": {}}
    result = draft_letter("Wir suchen einen Ingenieur.", "resume text", profile)

    assert result.language == "english"
