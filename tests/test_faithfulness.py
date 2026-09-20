from jobscout.faithfulness import (
    ClaimCheck,
    FaithfulnessReport,
    check_faithfulness,
    is_faithful,
    untraceable_claims,
)


def test_is_faithful_true_when_every_claim_traceable():
    report = FaithfulnessReport(
        claims=[
            ClaimCheck(claim="Led a team of 5", traceable=True, resume_evidence="Led a team of 5 engineers"),
            ClaimCheck(claim="5 years Python", traceable=True, resume_evidence="5+ years Python"),
        ]
    )
    assert is_faithful(report) is True
    assert untraceable_claims(report) == []


def test_is_faithful_false_when_one_claim_untraceable():
    untraceable = ClaimCheck(claim="Shipped a rocket to Mars", traceable=False, resume_evidence=None)
    report = FaithfulnessReport(
        claims=[
            ClaimCheck(claim="5 years Python", traceable=True, resume_evidence="5+ years Python"),
            untraceable,
        ]
    )
    assert is_faithful(report) is False
    assert untraceable_claims(report) == [untraceable]


def test_untraceable_claims_returns_only_the_mixed_failures():
    good_a = ClaimCheck(claim="Built a CI pipeline", traceable=True, resume_evidence="Built CI pipeline")
    bad_a = ClaimCheck(claim="Ran a Fortune 500 company", traceable=False, resume_evidence=None)
    good_b = ClaimCheck(claim="Worked in Berlin", traceable=True, resume_evidence="Based in Berlin")
    bad_b = ClaimCheck(claim="Invented Python", traceable=False, resume_evidence=None)
    report = FaithfulnessReport(claims=[good_a, bad_a, good_b, bad_b])

    assert untraceable_claims(report) == [bad_a, bad_b]
    assert is_faithful(report) is False


def test_is_faithful_true_when_claims_list_is_empty():
    report = FaithfulnessReport(claims=[])
    assert is_faithful(report) is True
    assert untraceable_claims(report) == []


def test_check_faithfulness_calls_structured_llm(monkeypatch):
    expected = FaithfulnessReport(
        claims=[ClaimCheck(claim="5 years Python", traceable=True, resume_evidence="5+ years Python")]
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

    monkeypatch.setattr("jobscout.faithfulness.get_llm", lambda: _FakeLLM())

    result = check_faithfulness("Dear Hiring Manager, I have 5 years Python.", "5+ years Python experience.")

    assert result == expected
    assert captured["schema"] is FaithfulnessReport
    assert "Dear Hiring Manager, I have 5 years Python." in captured["prompt"]
    assert "5+ years Python experience." in captured["prompt"]


def test_check_faithfulness_includes_profile_in_the_prompt(monkeypatch):
    """Confirmed live (unit 37 ablation): a real letter's language-level
    and location-preference claims only the Profile supports (not the
    Resume) came back "untraceable" until the checker could see Profile
    too."""
    expected = FaithfulnessReport(claims=[])
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, schema):
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.faithfulness.get_llm", lambda: _FakeLLM())

    profile = {"static_answers": {"german_level": "b1"}}
    check_faithfulness("My German is B1.", "resume text", profile)

    assert "german_level" in captured["prompt"]


def test_check_faithfulness_profile_defaults_to_n_a(monkeypatch):
    expected = FaithfulnessReport(claims=[])
    captured = {}

    class _FakeStructuredLLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return expected

    class _FakeLLM:
        def with_structured_output(self, schema):
            return _FakeStructuredLLM()

    monkeypatch.setattr("jobscout.faithfulness.get_llm", lambda: _FakeLLM())

    check_faithfulness("letter", "resume")

    assert "n/a" in captured["prompt"]
