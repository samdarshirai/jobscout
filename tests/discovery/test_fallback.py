import httpx

from jobscout.discovery.fallback import fetch_careers_page_posting


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_careers_page_posting_extracts_text(monkeypatch):
    html = "<html><body><main><p>We are hiring engineers in Berlin.</p></main></body></html>"
    monkeypatch.setattr(
        "jobscout.discovery.fallback.trafilatura.extract",
        lambda downloaded: "We are hiring engineers in Berlin.",
    )

    result = fetch_careers_page_posting(
        _client(lambda request: httpx.Response(200, text=html)),
        "https://acme.example/careers",
    )

    assert result == {
        "external_id": None,
        "title": "Careers page",
        "city": None,
        "url": "https://acme.example/careers",
        "jd_text": "We are hiring engineers in Berlin.",
    }


def test_fetch_careers_page_posting_returns_none_when_nothing_extractable(monkeypatch):
    monkeypatch.setattr(
        "jobscout.discovery.fallback.trafilatura.extract", lambda downloaded: None
    )

    result = fetch_careers_page_posting(
        _client(lambda request: httpx.Response(200, text="<html></html>")),
        "https://acme.example/careers",
    )

    assert result is None


def test_fetch_careers_page_posting_returns_none_on_404():
    result = fetch_careers_page_posting(
        _client(lambda request: httpx.Response(404)), "https://acme.example/careers"
    )
    assert result is None
