from jobscout.judge import (
    ConsistencyVerdict,
    DivergenceVerdict,
    KnockoutVerdict,
    MatchedLineVerdict,
    judge_divergence,
    judge_knockout_correctness,
    judge_matched_line_relevance,
    judge_score_rationale_consistency,
)
from jobscout.llm import JUDGE_MODEL


def test_judge_score_rationale_consistency_calls_the_anchor_model(monkeypatch):
    expected = ConsistencyVerdict(consistent=True, reasoning="Low score matches weak rationale.")
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return _FakeStructuredLLM()

    def _fake_get_llm(model):
        captured["model"] = model
        return _FakeLLM()

    monkeypatch.setattr("jobscout.judge.get_llm", _fake_get_llm)

    result = judge_score_rationale_consistency(10, "Weak fit on stack.", "stack fit: 1/5")

    assert result == expected
    assert captured["model"] == JUDGE_MODEL
    assert captured["schema"] is ConsistencyVerdict
    assert "10" in captured["prompt"]
    assert "Weak fit on stack." in captured["prompt"]


def _fake_get_llm(schema, expected, captured):
    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, s):
            captured["schema"] = s
            return _FakeStructuredLLM()

    def _get_llm(model):
        captured["model"] = model
        return _FakeLLM()

    return _get_llm


def test_judge_matched_line_relevance_calls_the_anchor_model(monkeypatch):
    expected = MatchedLineVerdict(relevant=True, reasoning="Directly supports the dimension.")
    captured = {}
    monkeypatch.setattr("jobscout.judge.get_llm", _fake_get_llm(MatchedLineVerdict, expected, captured))

    result = judge_matched_line_relevance("stack fit", "We need Angular.", "5 years of Angular.")

    assert result == expected
    assert captured["model"] == JUDGE_MODEL
    assert captured["schema"] is MatchedLineVerdict
    assert "stack fit" in captured["prompt"]
    assert "We need Angular." in captured["prompt"]


def test_judge_knockout_correctness_calls_the_anchor_model(monkeypatch):
    expected = KnockoutVerdict(correct=False, reasoning="JD never mentions German.")
    captured = {}
    monkeypatch.setattr("jobscout.judge.get_llm", _fake_get_llm(KnockoutVerdict, expected, captured))

    result = judge_knockout_correctness(
        "Munich-based, English-speaking team.", "Munich", "- german_required: FAIL if German required",
        "excluded", "german_required: requires C1 German",
    )

    assert result == expected
    assert captured["model"] == JUDGE_MODEL
    assert "excluded" in captured["prompt"]
    assert "german_required" in captured["prompt"]


def test_judge_divergence_calls_the_anchor_model(monkeypatch):
    expected = DivergenceVerdict(category="weak_rationale", reasoning="Rationale cites nothing concrete.")
    captured = {}
    monkeypatch.setattr("jobscout.judge.get_llm", _fake_get_llm(DivergenceVerdict, expected, captured))

    result = judge_divergence(30, 90, "Some generic praise.", "Loved the domain fit.")

    assert result == expected
    assert captured["model"] == JUDGE_MODEL
    assert "30" in captured["prompt"]
    assert "90" in captured["prompt"]
    assert "Loved the domain fit." in captured["prompt"]
