from jobscout.search_plan import SearchPlan, derive_search_plan


def test_derive_search_plan_calls_structured_llm(monkeypatch):
    expected = SearchPlan(
        queries=["senior frontend engineer berlin"],
        sources=["adzuna", "curated_ats"],
        companies=["ACME GmbH"],
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

    monkeypatch.setattr("jobscout.search_plan.get_llm", lambda: _FakeLLM())

    result = derive_search_plan(
        {"knockout": [{"axis": "seniority_band", "rule": "senior only"}]}
    )

    assert result == expected
    assert captured["schema"] is SearchPlan
    assert "seniority_band" in captured["prompt"]
