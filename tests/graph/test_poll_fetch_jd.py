import hashlib

from jobscout.graph.poll import fetch_jd

_ADZUNA_POSTING = {
    "id": "adzuna:1",
    "source": "adzuna",
    "company": "Acme",
    "title": "Engineer",
    "city": "Berlin",
    "url": "https://api.adzuna.com/redirect/1",
    "jd_text": "Truncated Adzuna summary...",
}


def test_fetch_jd_upgrades_an_adzuna_posting_via_its_redirect_url(monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.fetch_page_text",
        lambda client, url: "Full JD text from the real listing page.",
    )
    result = fetch_jd({"postings": [_ADZUNA_POSTING]})
    upgraded = result["postings"][0]
    assert upgraded["jd_text"] == "Full JD text from the real listing page."
    assert upgraded["content_hash"] == hashlib.sha256(
        b"Full JD text from the real listing page."
    ).hexdigest()


def test_fetch_jd_falls_back_to_the_adzuna_summary_when_the_upgrade_fails(monkeypatch):
    monkeypatch.setattr("jobscout.graph.poll.fetch_page_text", lambda client, url: None)
    result = fetch_jd({"postings": [_ADZUNA_POSTING]})
    assert result["postings"][0]["jd_text"] == "Truncated Adzuna summary..."


def test_fetch_jd_never_calls_fetch_page_text_for_a_non_adzuna_source(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "jobscout.graph.poll.fetch_page_text",
        lambda client, url: calls.append(url),
    )
    posting = {
        "id": "arbeitnow:x",
        "source": "arbeitnow",
        "company": "Acme",
        "title": "Engineer",
        "city": "Berlin",
        "url": "https://arbeitnow.com/jobs/x",
        "jd_text": "Full JD already, no upgrade needed.",
    }
    result = fetch_jd({"postings": [posting]})
    assert calls == []
    assert result["postings"][0]["jd_text"] == "Full JD already, no upgrade needed."


def test_fetch_jd_still_drops_a_posting_with_no_jd_text_and_no_url(monkeypatch):
    monkeypatch.setattr(
        "jobscout.graph.poll.fetch_page_text", lambda client, url: None
    )
    posting = {**_ADZUNA_POSTING, "jd_text": "", "url": None}
    result = fetch_jd({"postings": [posting]})
    assert result["postings"] == []
