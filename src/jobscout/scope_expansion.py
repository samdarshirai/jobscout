"""Scope Expansion proposal + trigger logic (DESIGN §10 "Scope-expansion
mechanics", build-plan unit 27).

CONTEXT.md: Scope Expansion — "An Agent proposal to widen the Criteria
after three consecutive thin Polls — exactly one concrete change plus
evidence, held at an Approval Gate."

Deliberately fully pure — no `conn`, no DB, no file I/O, unlike some
sibling modules in this codebase (e.g. `dedupe.py` owns its own cache
table). The parent session owns all persistence and trigger-data
fetching for this unit; every function here takes already-fetched
plain data so it stays testable with no DB setup at all.
"""

import datetime

from pydantic import BaseModel, Field

from jobscout.llm import get_llm

_SUPPRESSION_DAYS = 14


class ScopeExpansionProposal(BaseModel):
    """CONTEXT.md: Scope Expansion — exactly one concrete change plus evidence."""

    axis: str = Field(description="The single Knockout axis or Criteria field this targets")
    change: str = Field(description="The one concrete change, e.g. 'salary_floor: €70000 → €60000'")
    evidence: str = Field(
        description="Data-grounded justification counted from the given exclusions"
    )
    surfaced_titles: list[str] = Field(
        description="Concrete Posting titles the evidence names as newly-surfaced"
    )


_PROPOSAL_PROMPT = (
    "The Candidate's Queue has been thin for 3 consecutive Polls. Look for a "
    "PATTERN in the Excluded Postings below: which single Knockout axis "
    "appears most often, or is most fixably narrow? Propose changing ONLY "
    "that one axis, by the smallest change that would flip the most "
    "exclusions — never propose changing more than one axis, never propose "
    "a vague 'loosen everything'. Name the concrete Posting titles your "
    "evidence counts as newly-surfaced. If the exclusions show no clear "
    "single-axis pattern, still propose your best single-axis change but "
    "keep the evidence honest about the weaker signal — never fabricate a "
    "stronger pattern than the data actually shows.\n\n"
    "Criteria:\n{criteria}\n\nExcluded Postings:\n{recent_exclusions}"
)


def propose_scope_expansion(
    criteria: dict, recent_exclusions: list[dict]
) -> ScopeExpansionProposal:
    """One structured LLM call (DESIGN §4)."""
    structured_llm = get_llm().with_structured_output(ScopeExpansionProposal)
    return structured_llm.invoke(
        _PROPOSAL_PROMPT.format(criteria=criteria, recent_exclusions=recent_exclusions)
    )


def trigger_met(pass_counts: list[int]) -> bool:
    """3 consecutive Polls with fewer than 2 Postings passing Knockouts
    (§10). `pass_counts` is chronologically ordered oldest-first."""
    if len(pass_counts) < 3:
        return False
    return all(count < 2 for count in pass_counts[-3:])


def is_suppressed(
    axis: str, recent_rejections: list[dict], now: datetime.datetime | None = None
) -> bool:
    """A rejected proposal for the same axis stays suppressed for 2 weeks
    (§10). `now` is a parameter (not `utcnow()` internally) so callers can
    pin a reference time for deterministic tests."""
    # naive UTC, not aware — matches every `decided_at` string parsed below,
    # which comes from SQLite's naive `datetime('now')` (no tz suffix)
    now = now or datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    cutoff = now - datetime.timedelta(days=_SUPPRESSION_DAYS)
    for rejection in recent_rejections:
        if rejection["axis"] != axis:
            continue
        decided_at = datetime.datetime.strptime(rejection["decided_at"], "%Y-%m-%d %H:%M:%S")
        if decided_at >= cutoff:
            return True
    return False
