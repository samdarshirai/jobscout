from jobscout.fit_notes import FitGap, FitNotes, generate_fit_notes


class _FakeStructuredLLM:
    def __init__(self, result):
        self._result = result
        self.prompt = None

    def invoke(self, prompt):
        self.prompt = prompt
        return self._result


class _FakeLLM:
    def __init__(self, result):
        self._structured = _FakeStructuredLLM(result)
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self._structured


def _stub(monkeypatch, result):
    fake_llm = _FakeLLM(result)
    monkeypatch.setattr("jobscout.fit_notes.get_llm", lambda: fake_llm)
    return fake_llm


def test_generate_fit_notes_returns_populated_gaps(monkeypatch):
    expected = FitNotes(
        gaps=[
            FitGap(gap="No Kubernetes experience", jd_line="3+ years running Kubernetes in prod"),
            FitGap(gap="JD implies more DevOps ownership than her background shows"),
        ]
    )
    fake_llm = _stub(monkeypatch, expected)

    result = generate_fit_notes("Job Description text", "Resume text")

    assert result == expected
    assert fake_llm.schema is FitNotes
    assert "Job Description text" in fake_llm._structured.prompt
    assert "Resume text" in fake_llm._structured.prompt


def test_generate_fit_notes_empty_gaps_is_valid(monkeypatch):
    expected = FitNotes(gaps=[])
    _stub(monkeypatch, expected)

    result = generate_fit_notes("Job Description text", "Resume text")

    assert result.gaps == []


def test_generate_fit_notes_prompt_contains_resume_and_jd(monkeypatch):
    fake_llm = _stub(monkeypatch, FitNotes(gaps=[]))

    generate_fit_notes("some JD content", "some resume content")

    prompt = fake_llm._structured.prompt
    assert "some JD content" in prompt
    assert "some resume content" in prompt


def test_generate_fit_notes_passes_fitnotes_schema(monkeypatch):
    fake_llm = _stub(monkeypatch, FitNotes(gaps=[]))

    generate_fit_notes("JD", "Resume")

    assert fake_llm.schema is FitNotes


def test_fit_gap_without_jd_line_round_trips(monkeypatch):
    expected = FitNotes(gaps=[FitGap(gap="Holistic gap, no single quotable line")])
    _stub(monkeypatch, expected)

    result = generate_fit_notes("JD", "Resume")

    assert result.gaps[0].jd_line is None
    assert result.gaps[0].gap == "Holistic gap, no single quotable line"
