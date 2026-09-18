import httpx

from jobscout.discovery.arbeitnow import discover_arbeitnow


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _job(**overrides) -> dict:
    job = {
        "slug": "acme-backend-engineer",
        "company_name": "Acme GmbH",
        "title": "Backend Engineer",
        "description": "<p>Ship things.</p><ul><li>Python</li><li>Docker</li></ul>",
        "remote": False,
        "url": "https://arbeitnow.com/jobs/acme-backend-engineer",
        "tags": ["python"],
        "job_types": ["Full-time"],
        "location": "Berlin",
        "created_at": 1700000000,
    }
    job.update(overrides)
    return job


def test_discover_arbeitnow_normalizes_a_posting():
    def handler(request):
        return httpx.Response(200, json={"data": [_job()], "links": {}, "meta": {}})

    postings = discover_arbeitnow(_client(handler))

    assert postings == [
        {
            "id": "arbeitnow:acme-backend-engineer",
            "source": "arbeitnow",
            "company": "Acme GmbH",
            "title": "Backend Engineer",
            "city": "Berlin",
            "url": "https://arbeitnow.com/jobs/acme-backend-engineer",
            "jd_text": "Ship things.\n- Python\n- Docker",
        }
    ]


def test_discover_arbeitnow_remote_with_no_location_falls_back_to_remote():
    def handler(request):
        job = _job(location="", remote=True)
        return httpx.Response(200, json={"data": [job], "links": {}, "meta": {}})

    postings = discover_arbeitnow(_client(handler))

    assert postings[0]["city"] == "Remote"


def test_discover_arbeitnow_follows_pagination_up_to_max_pages():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if str(request.url).endswith("page=2"):
            return httpx.Response(
                200, json={"data": [_job(slug="page-2-job")], "links": {}, "meta": {}}
            )
        return httpx.Response(
            200,
            json={
                "data": [_job(slug="page-1-job")],
                "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"},
                "meta": {},
            },
        )

    postings = discover_arbeitnow(_client(handler), max_pages=2)

    assert len(calls) == 2
    assert [p["id"] for p in postings] == ["arbeitnow:page-1-job", "arbeitnow:page-2-job"]


def test_discover_arbeitnow_stops_at_max_pages_even_if_more_exist():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [_job()],
                "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"},
                "meta": {},
            },
        )

    postings = discover_arbeitnow(_client(handler), max_pages=1)

    assert len(postings) == 1


def test_discover_arbeitnow_returns_empty_on_transport_error():
    def handler(request):
        raise httpx.ConnectError("DNS lookup failed", request=request)

    postings = discover_arbeitnow(_client(handler))

    assert postings == []


def test_discover_arbeitnow_defensive_empty_description_gives_none_jd_text():
    def handler(request):
        job = _job(description="")
        return httpx.Response(200, json={"data": [job], "links": {}, "meta": {}})

    postings = discover_arbeitnow(_client(handler))

    assert postings[0]["jd_text"] is None
