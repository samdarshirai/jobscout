from jobscout.criteria import (
    Criteria,
    KnockoutRule,
    ScoredDimension,
    derive_criteria,
    read_criteria_file,
    write_criteria_file,
)


def _sample_criteria() -> Criteria:
    return Criteria(
        knockout=[KnockoutRule(axis="seniority_band", rule="senior or mid only")],
        scored=[
            ScoredDimension(dimension="stack fit", weight=0.4, rubric="5 = 3+ core tools"),
            ScoredDimension(dimension="domain/product interest", weight=0.2, rubric="soft axis"),
            ScoredDimension(dimension="scope & seniority signals", weight=0.2, rubric="ownership"),
            ScoredDimension(dimension="eng-culture signals", weight=0.2, rubric="testing/CI"),
        ],
        learn=["ambiguous stack preference"],
    )


def test_write_and_read_criteria_file_round_trips(tmp_path):
    path = tmp_path / "criteria.yaml"
    criteria = _sample_criteria()

    write_criteria_file(criteria, path)
    loaded = read_criteria_file(path)

    assert loaded == criteria
    assert "seniority_band" in path.read_text()


def test_derive_criteria_calls_structured_llm(monkeypatch):
    expected = _sample_criteria()
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.criteria.get_llm", lambda: _FakeLLM())

    result = derive_criteria(
        {"resume": {"stack": ["React"]}, "static_answers": {"work_mode": "remote"}}
    )

    assert result == expected
    assert captured["schema"] is Criteria
    assert "React" in captured["prompt"]
