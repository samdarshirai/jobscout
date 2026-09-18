import httpx

from jobscout.discovery.adzuna import discover_adzuna


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _result(**overrides) -> dict:
    result = {
        "id": 12345,
        "title": "Senior Backend Engineer",
        "company": {"display_name": "Acme GmbH"},
        "location": {"display_name": "Berlin, Germany"},
        "description": "<p>We build things.</p>",
        "redirect_url": "https://adzuna.de/land/ad/12345",
        "salary_min": 60000,
        "salary_max": 75000,
        "salary_is_predicted": False,
    }
    result.update(overrides)
    return result


def test_discover_adzuna_normalizes_a_result():
    def handler(request):
        assert request.url.path == "/v1/api/jobs/de/search/1"
        assert request.url.params["app_id"] == "id123"
        assert request.url.params["app_key"] == "key456"
        return httpx.Response(200, json={"results": [_result()], "count": 1})

    postings = discover_adzuna(_client(handler), "id123", "key456")

    assert postings == [
        {
            "id": "adzuna:12345",
            "source": "adzuna",
            "company": "Acme GmbH",
            "title": "Senior Backend Engineer",
            "city": "Berlin, Germany",
            "url": "https://adzuna.de/land/ad/12345",
            "jd_text": "Salary: 60000-75000\n\nWe build things.",
        }
    ]


def test_discover_adzuna_marks_predicted_salary():
    def handler(request):
        return httpx.Response(
            200, json={"results": [_result(salary_is_predicted=True)], "count": 1}
        )

    postings = discover_adzuna(_client(handler), "id123", "key456")

    assert postings[0]["jd_text"].startswith("Salary: 60000-75000 (predicted)\n\n")


def test_discover_adzuna_handles_one_sided_salary():
    def handler(request):
        return httpx.Response(
            200, json={"results": [_result(salary_min=None, salary_max=80000)], "count": 1}
        )

    postings = discover_adzuna(_client(handler), "id123", "key456")

    assert postings[0]["jd_text"].startswith("Salary: up to 80000\n\n")


def test_discover_adzuna_omits_salary_line_when_absent():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [_result(salary_min=None, salary_max=None)],
                "count": 1,
            },
        )

    postings = discover_adzuna(_client(handler), "id123", "key456")

    assert postings[0]["jd_text"] == "We build things."


def test_discover_adzuna_stops_paging_on_empty_page():
    calls = []

    def handler(request):
        page = int(request.url.path.rsplit("/", 1)[-1])
        calls.append(page)
        if page == 1:
            return httpx.Response(200, json={"results": [_result()], "count": 1})
        return httpx.Response(200, json={"results": [], "count": 1})

    postings = discover_adzuna(_client(handler), "id123", "key456", max_pages=5)

    assert calls == [1, 2]
    assert len(postings) == 1


def test_discover_adzuna_stops_on_http_error():
    def handler(request):
        page = int(request.url.path.rsplit("/", 1)[-1])
        if page == 1:
            return httpx.Response(200, json={"results": [_result()], "count": 1})
        return httpx.Response(500)

    postings = discover_adzuna(_client(handler), "id123", "key456", max_pages=3)

    assert len(postings) == 1


def test_discover_adzuna_missing_company_or_location_defaults_gracefully():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [_result(company={}, location={}, description="")],
                "count": 1,
            },
        )

    postings = discover_adzuna(_client(handler), "id123", "key456")

    assert postings[0]["company"] == "Unknown"
    assert postings[0]["city"] is None
