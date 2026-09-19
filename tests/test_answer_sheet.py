from jobscout.answer_sheet import _NOT_ON_FILE, AnswerSheet, _DraftedAnswers, generate_answer_sheet

_PROFILE = {
    "resume": {},
    "static_answers": {"salary_floor": "70000, hard floor"},
    "dynamic_answers": {},
}


class _FakeStructuredLLM:
    def __init__(self, response):
        self._response = response
        self.prompt = None

    def invoke(self, prompt):
        self.prompt = prompt
        return self._response


class _FakeLLM:
    def __init__(self, response):
        self._response = response
        self.schema = None
        self.structured_llm = None

    def with_structured_output(self, schema):
        self.schema = schema
        self.structured_llm = _FakeStructuredLLM(self._response)
        return self.structured_llm


def _fake(monkeypatch, response):
    fake_llm = _FakeLLM(response)
    monkeypatch.setattr("jobscout.answer_sheet.get_llm", lambda: fake_llm)
    return fake_llm


def test_visa_status_and_notice_period_are_always_the_fixed_placeholder(monkeypatch):
    # Even if a rogue fake LLM response somehow carried those fields, the
    # public AnswerSheet must never surface anything but the placeholder —
    # code controls this, not the model.
    response = _DraftedAnswers(
        salary_expectation="Around €70,000, matching their stated floor.",
        why_this_company="Their focus on distributed systems matches my background.",
    )
    _fake(monkeypatch, response)

    result = generate_answer_sheet("JD text", "Resume text", _PROFILE, "Acme")

    assert result.visa_status == _NOT_ON_FILE
    assert result.notice_period == _NOT_ON_FILE


def test_salary_expectation_and_why_this_company_come_from_the_llm(monkeypatch):
    response = _DraftedAnswers(
        salary_expectation="Around €70,000, matching their stated floor.",
        why_this_company="Their focus on distributed systems matches my background.",
    )
    _fake(monkeypatch, response)

    result = generate_answer_sheet("JD text", "Resume text", _PROFILE, "Acme")

    assert result.salary_expectation == "Around €70,000, matching their stated floor."
    assert result.why_this_company == "Their focus on distributed systems matches my background."
    assert isinstance(result, AnswerSheet)


def test_prompt_contains_jd_resume_company_and_salary_floor(monkeypatch):
    response = _DraftedAnswers(salary_expectation="x", why_this_company="y")
    fake_llm = _fake(monkeypatch, response)

    generate_answer_sheet("A very specific JD.", "A very specific resume.", _PROFILE, "Acme GmbH")

    prompt = fake_llm.structured_llm.prompt
    assert "A very specific JD." in prompt
    assert "A very specific resume." in prompt
    assert "Acme GmbH" in prompt
    assert "70000, hard floor" in prompt


def test_missing_salary_floor_does_not_crash_and_passes_not_specified(monkeypatch):
    response = _DraftedAnswers(salary_expectation="x", why_this_company="y")
    fake_llm = _fake(monkeypatch, response)
    profile = {"resume": {}, "static_answers": {}, "dynamic_answers": {}}

    result = generate_answer_sheet("JD", "Resume", profile, "Acme")

    assert isinstance(result, AnswerSheet)
    assert "not specified" in fake_llm.structured_llm.prompt


def test_empty_salary_floor_also_falls_back_to_not_specified(monkeypatch):
    response = _DraftedAnswers(salary_expectation="x", why_this_company="y")
    fake_llm = _fake(monkeypatch, response)
    profile = {"resume": {}, "static_answers": {"salary_floor": ""}, "dynamic_answers": {}}

    generate_answer_sheet("JD", "Resume", profile, "Acme")

    assert "not specified" in fake_llm.structured_llm.prompt


def test_schema_passed_to_llm_is_the_internal_two_field_model_not_the_public_one(monkeypatch):
    response = _DraftedAnswers(salary_expectation="x", why_this_company="y")
    fake_llm = _fake(monkeypatch, response)

    generate_answer_sheet("JD", "Resume", _PROFILE, "Acme")

    assert fake_llm.schema is _DraftedAnswers
    assert fake_llm.schema is not AnswerSheet
    assert set(_DraftedAnswers.model_fields.keys()) == {"salary_expectation", "why_this_company"}
    assert "visa_status" not in _DraftedAnswers.model_fields
    assert "notice_period" not in _DraftedAnswers.model_fields


def test_public_answer_sheet_has_all_four_fields(monkeypatch):
    response = _DraftedAnswers(salary_expectation="x", why_this_company="y")
    _fake(monkeypatch, response)

    result = generate_answer_sheet("JD", "Resume", _PROFILE, "Acme")

    assert set(AnswerSheet.model_fields.keys()) == {
        "visa_status",
        "notice_period",
        "salary_expectation",
        "why_this_company",
    }
    assert all(isinstance(getattr(result, f), str) for f in AnswerSheet.model_fields)
