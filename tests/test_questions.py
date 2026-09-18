from jobscout.questions import STATIC_QUESTIONS, DynamicQuestions, generate_dynamic_questions


def test_static_questions_cover_every_design_topic():
    keys = {q["key"] for q in STATIC_QUESTIONS}
    assert keys == {
        "german_level",
        "include_german_required_roles",
        "salary_floor",
        "work_mode",
        "acceptable_cities",
        "company_size_stage",
        "hard_exclude_industries",
        "must_have_stack",
        "contract_type",
    }
    for q in STATIC_QUESTIONS:
        assert q["prompt"]


def test_generate_dynamic_questions_calls_structured_llm(monkeypatch):
    expected = DynamicQuestions(questions=["Q1?", "Q2?", "Q3?"])
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.questions.get_llm", lambda: _FakeLLM())

    result = generate_dynamic_questions("Jane Doe, React dev", {"work_mode": "remote"})

    assert result == ["Q1?", "Q2?", "Q3?"]
    assert captured["schema"] is DynamicQuestions
    assert "Jane Doe" in captured["prompt"]
    assert "remote" in captured["prompt"]
