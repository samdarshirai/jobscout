from pathlib import Path

from jobscout.resume import ExtractedProfile, extract_resume_text, parse_profile

FIXTURE = Path(__file__).parent.parent / "data" / "example" / "fake_resume.pdf"


def test_extract_resume_text_reads_real_pdf_content():
    text = extract_resume_text(FIXTURE)

    assert "Jane Doe" in text
    assert "React" in text
    assert "Led a team of 4 engineers" in text


def test_parse_profile_calls_structured_llm_with_resume_text(monkeypatch):
    expected = ExtractedProfile(
        roles=["Senior Frontend Engineer"],
        years_experience=6.0,
        stack=["React", "TypeScript", "GraphQL"],
        seniority_signals=["Led a team of 4 engineers"],
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

    monkeypatch.setattr("jobscout.resume.get_llm", lambda: _FakeLLM())

    result = parse_profile("Jane Doe\nSenior Frontend Engineer")

    assert result == expected
    assert captured["schema"] is ExtractedProfile
    assert "Jane Doe" in captured["prompt"]
