import datetime

from jobscout.scope_expansion import (
    ScopeExpansionProposal,
    is_suppressed,
    propose_scope_expansion,
    trigger_met,
)


def test_propose_scope_expansion_calls_structured_llm(monkeypatch):
    expected = ScopeExpansionProposal(
        axis="salary_floor",
        change="salary_floor: €70000 → €60000",
        evidence="11 of 14 exclusions failed only on salary_floor; dropping to €60000 surfaces 4.",
        surfaced_titles=["Backend Engineer", "Platform Engineer"],
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

    monkeypatch.setattr("jobscout.scope_expansion.get_llm", lambda: _FakeLLM())

    criteria = {"knockout": [{"axis": "salary_floor", "rule": "min €70000"}]}
    recent_exclusions = [
        {"title": "Backend Engineer", "company": "Acme", "status_reason": "salary_floor: €55k"}
    ]

    result = propose_scope_expansion(criteria, recent_exclusions)

    assert result == expected
    assert captured["schema"] is ScopeExpansionProposal
    assert "salary_floor" in captured["prompt"]
    assert "Backend Engineer" in captured["prompt"]


def test_trigger_met_true_for_exactly_three_thin_polls():
    assert trigger_met([1, 0, 1]) is True


def test_trigger_met_true_when_more_than_three_entries_but_last_three_thin():
    assert trigger_met([5, 5, 1, 0, 1]) is True


def test_trigger_met_false_when_last_entry_not_thin():
    assert trigger_met([1, 1, 3]) is False


def test_trigger_met_false_with_fewer_than_three_entries():
    assert trigger_met([1, 1]) is False


def test_trigger_met_false_for_empty_list():
    assert trigger_met([]) is False


def test_is_suppressed_true_within_fourteen_days():
    now = datetime.datetime(2026, 9, 19, 12, 0, 0)
    rejections = [{"axis": "salary_floor", "decided_at": "2026-09-10 09:00:00"}]
    assert is_suppressed("salary_floor", rejections, now=now) is True


def test_is_suppressed_false_beyond_fourteen_days():
    now = datetime.datetime(2026, 9, 19, 12, 0, 0)
    rejections = [{"axis": "salary_floor", "decided_at": "2026-08-01 09:00:00"}]
    assert is_suppressed("salary_floor", rejections, now=now) is False


def test_is_suppressed_false_for_a_different_axis():
    now = datetime.datetime(2026, 9, 19, 12, 0, 0)
    rejections = [{"axis": "seniority_band", "decided_at": "2026-09-10 09:00:00"}]
    assert is_suppressed("salary_floor", rejections, now=now) is False


def test_is_suppressed_false_for_empty_rejections():
    assert is_suppressed("salary_floor", [], now=datetime.datetime(2026, 9, 19)) is False


def test_is_suppressed_boundary_exactly_fourteen_days_still_suppressed():
    now = datetime.datetime(2026, 9, 19, 12, 0, 0)
    rejections = [{"axis": "salary_floor", "decided_at": "2026-09-05 12:00:00"}]
    assert is_suppressed("salary_floor", rejections, now=now) is True


def test_is_suppressed_just_over_fourteen_days_not_suppressed():
    now = datetime.datetime(2026, 9, 19, 12, 0, 0)
    rejections = [{"axis": "salary_floor", "decided_at": "2026-09-05 11:59:59"}]
    assert is_suppressed("salary_floor", rejections, now=now) is False
