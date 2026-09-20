from jobscout.graph.poll import _is_relevant, _relevance_keywords, build_poll_graph
from jobscout.search_plan import SearchPlan
from jobscout.storage.db import get_checkpointer, get_connection, init_db

POLL_INIT = {"criteria": {}, "search_plan": {}, "decision": "", "postings": []}


def test_relevance_keywords_strips_generic_job_title_words():
    plan = {"queries": ["Senior Frontend Engineer Angular", "Lead Angular Developer Munich"]}
    keywords = _relevance_keywords(plan)
    assert keywords == {"angular"}


def test_relevance_keywords_keeps_multiple_distinct_stack_terms():
    plan = {"queries": ["Senior Frontend Engineer Angular RxJS NgRx"]}
    keywords = _relevance_keywords(plan)
    assert keywords == {"angular", "rxjs", "ngrx"}


def test_relevance_keywords_empty_when_query_is_entirely_generic():
    plan = {"queries": ["Senior Frontend Engineer Remote Germany"]}
    assert _relevance_keywords(plan) == set()


def test_relevance_keywords_empty_with_no_queries_at_all():
    assert _relevance_keywords({}) == set()
    assert _relevance_keywords({"queries": []}) == set()


def test_is_relevant_matches_a_keyword_in_the_title():
    posting = {"title": "Senior Angular Engineer", "jd_text": "Some generic description."}
    assert _is_relevant(posting, {"angular"}) is True


def test_is_relevant_matches_a_keyword_in_the_jd_text_only():
    posting = {"title": "Software Engineer", "jd_text": "You will build with Angular and TypeScript."}
    assert _is_relevant(posting, {"angular"}) is True


def test_is_relevant_rejects_a_posting_with_no_keyword_match():
    posting = {"title": "Steuerberater (m/w/d)", "jd_text": "Tax consulting role, no tech stack."}
    assert _is_relevant(posting, {"angular", "rxjs"}) is False


def test_is_relevant_is_a_noop_with_no_keywords_at_all():
    posting = {"title": "Anything", "jd_text": "Whatever"}
    assert _is_relevant(posting, set()) is True


def test_is_relevant_handles_a_missing_jd_text():
    posting = {"title": "Angular Engineer", "jd_text": None}
    assert _is_relevant(posting, {"angular"}) is True
    posting_no_match = {"title": "Steuerberater", "jd_text": None}
    assert _is_relevant(posting_no_match, {"angular"}) is False


def test_is_relevant_does_not_match_a_keyword_as_a_substring_of_another_word():
    """Confirmed live: "micro" (from a "Micro Frontends" query) matched
    "microservices" in unrelated backend JDs via substring search — a
    keyword must match the whole word, not any word containing it."""
    posting = {"title": "Backend Engineer", "jd_text": "We build microservices in Go."}
    assert _is_relevant(posting, {"micro"}) is False


def _compile(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    checkpointer_conn = get_connection(tmp_path / "j.sqlite")
    graph = build_poll_graph(conn).compile(checkpointer=get_checkpointer(checkpointer_conn))
    return graph, conn


def test_discover_node_filters_both_curated_and_broad_sources(tmp_path, monkeypatch):
    plan = SearchPlan(queries=["Senior Frontend Engineer Angular"], sources=["arbeitnow"], companies=[])
    monkeypatch.setattr("jobscout.graph.poll.derive_search_plan", lambda criteria: plan)
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_curated_ats",
        lambda conn, path, client: [
            {"id": "ats:Acme:1", "source": "ats:Acme", "company": "Acme", "title": "Steuerberater",
             "city": None, "url": "https://example.com/1", "jd_text": "Tax role, no stack match."},
            {"id": "ats:Acme:2", "source": "ats:Acme", "company": "Acme", "title": "Angular Engineer",
             "city": None, "url": "https://example.com/4", "jd_text": "Build with Angular."},
        ],
    )
    monkeypatch.setattr(
        "jobscout.graph.poll.discover_arbeitnow",
        lambda client: [
            {"id": "arbeitnow:1", "source": "arbeitnow", "company": "TaxCo", "title": "Steuerberater",
             "city": None, "url": "https://example.com/2", "jd_text": "Tax consulting, no tech stack."},
            {"id": "arbeitnow:2", "source": "arbeitnow", "company": "TechCo", "title": "Angular Engineer",
             "city": None, "url": "https://example.com/3", "jd_text": "Build with Angular and TypeScript."},
        ],
    )
    from langgraph.types import Command

    graph, conn = _compile(tmp_path)
    cfg = {"configurable": {"thread_id": "r1"}}
    graph.invoke(POLL_INIT, cfg)
    graph.invoke(Command(resume="approve"), cfg)
    state = graph.get_state(cfg)

    ids = {p["id"] for p in state.values["postings"]}
    # off-stack Postings dropped from BOTH curated_ats and arbeitnow; the
    # on-stack Posting from each survives — a hand-picked company still
    # posts non-engineering roles, so relevance filtering applies there too.
    assert ids == {"ats:Acme:2", "arbeitnow:2"}
    conn.close()
