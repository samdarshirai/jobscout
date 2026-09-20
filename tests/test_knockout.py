from jobscout.criteria import KnockoutRule
from jobscout.knockout import AxisFact, KnockoutFacts, decide_knockout, extract_knockout_facts


def test_decide_knockout_returns_none_when_every_axis_passes():
    facts = KnockoutFacts(
        axes=[
            AxisFact(axis="seniority_band", passes=True, evidence="senior title"),
            AxisFact(axis="location", passes=True, evidence="remote, Germany"),
        ]
    )
    assert decide_knockout(facts) is None


def test_decide_knockout_returns_reason_naming_each_failing_axis():
    facts = KnockoutFacts(
        axes=[
            AxisFact(axis="seniority_band", passes=True, evidence="senior title"),
            AxisFact(axis="german_required", passes=False, evidence="fluent German required"),
        ]
    )
    reason = decide_knockout(facts)
    assert reason == "german_required: fluent German required"


def test_extract_knockout_facts_calls_structured_llm(monkeypatch):
    rules = [KnockoutRule(axis="seniority_band", rule="senior or mid only")]
    expected = KnockoutFacts(
        axes=[AxisFact(axis="seniority_band", passes=True, evidence="Senior Engineer")]
    )
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.knockout.get_llm", lambda: _FakeLLM())

    result = extract_knockout_facts("Senior Engineer role", rules)

    assert result == expected
    assert captured["schema"] is KnockoutFacts
    assert "seniority_band" in captured["prompt"]
    assert "Senior Engineer role" in captured["prompt"]


def test_extract_knockout_facts_includes_city_the_jd_text_is_silent_on(monkeypatch):
    """Confirmed live: a real JD's prose never mentioned its office city at
    all (location lived only in the job board's own metadata) — the
    location axis needs `city` passed in explicitly or it has nothing to
    check and defaults to passing."""
    rules = [KnockoutRule(axis="location", rule="Germany-based only")]
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return KnockoutFacts(axes=[AxisFact(axis="location", passes=False, evidence="Madrid")])

    class _FakeLLM:
        def with_structured_output(self, schema):
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.knockout.get_llm", lambda: _FakeLLM())

    extract_knockout_facts("A JD with no location mentioned anywhere.", rules, city="Madrid, Spain")

    assert "Madrid, Spain" in captured["prompt"]
