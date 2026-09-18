import httpx
import pytest

from jobscout.discovery.ats import detect_and_fetch, fetch_from_known_ats


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_detect_and_fetch_greenhouse():
    def handler(request):
        assert request.url.path == "/v1/boards/acme/jobs"
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 1,
                        "title": "Senior Frontend Engineer",
                        "location": {"name": "Berlin"},
                        "content": "<p>Do things</p>",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    }
                ]
            },
        )

    ats_type, jobs = detect_and_fetch(_client(handler), "acme")

    assert ats_type == "greenhouse"
    assert jobs == [
        {
            "external_id": "1",
            "title": "Senior Frontend Engineer",
            "city": "Berlin",
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "jd_text": "<p>Do things</p>",
        }
    ]


def test_detect_and_fetch_lever_when_greenhouse_misses():
    def handler(request):
        if "greenhouse" in request.url.host:
            return httpx.Response(404)
        assert "lever" in request.url.host
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Backend Engineer",
                    "categories": {"location": "Remote"},
                    "descriptionPlain": "Ship things",
                    "hostedUrl": "https://jobs.lever.co/acme/abc",
                }
            ],
        )

    ats_type, jobs = detect_and_fetch(_client(handler), "acme")

    assert ats_type == "lever"
    assert jobs == [
        {
            "external_id": "abc",
            "title": "Backend Engineer",
            "city": "Remote",
            "url": "https://jobs.lever.co/acme/abc",
            "jd_text": "Ship things",
        }
    ]


def test_detect_and_fetch_ashby_when_greenhouse_and_lever_miss():
    def handler(request):
        if "ashbyhq" in request.url.host:
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "xyz",
                            "title": "Platform Engineer",
                            "location": "Berlin, Germany",
                            "descriptionPlain": "Own the platform",
                            "jobUrl": "https://jobs.ashbyhq.com/acme/xyz",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    ats_type, jobs = detect_and_fetch(_client(handler), "acme")

    assert ats_type == "ashby"
    assert jobs[0]["external_id"] == "xyz"
    assert jobs[0]["city"] == "Berlin, Germany"


def test_detect_and_fetch_personio_when_all_others_miss():
    xml = (
        "<workzag-jobs>"
        "<position>"
        "<id>42</id><name>Data Engineer</name><office>Munich</office>"
        "<careerSiteUrl>https://acme.jobs.personio.de/job/42</careerSiteUrl>"
        "<jobDescriptions>"
        "<jobDescription><name>Role</name><value>Build pipelines</value></jobDescription>"
        "</jobDescriptions>"
        "</position>"
        "</workzag-jobs>"
    )

    def handler(request):
        if "personio" in request.url.host:
            return httpx.Response(200, text=xml)
        return httpx.Response(404)

    ats_type, jobs = detect_and_fetch(_client(handler), "acme")

    assert ats_type == "personio"
    assert jobs == [
        {
            "external_id": "42",
            "title": "Data Engineer",
            "city": "Munich",
            "url": "https://acme.jobs.personio.de/job/42",
            "jd_text": "Build pipelines",
        }
    ]


def test_detect_and_fetch_returns_none_when_all_miss():
    result = detect_and_fetch(_client(lambda request: httpx.Response(404)), "ghost-co")
    assert result is None


def test_fetch_from_known_ats_skips_probing_other_types():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(
            200, json=[{"id": "1", "text": "T", "categories": {}, "descriptionPlain": "", "hostedUrl": ""}]
        )

    jobs = fetch_from_known_ats(_client(handler), "lever", "acme")

    assert len(calls) == 1
    assert "lever" in calls[0]
    assert jobs[0]["external_id"] == "1"


def test_fetch_from_known_ats_returns_empty_list_on_failure():
    jobs = fetch_from_known_ats(_client(lambda request: httpx.Response(500)), "greenhouse", "acme")
    assert jobs == []


def test_greenhouse_returns_none_on_malformed_json():
    def handler(request):
        if "greenhouse" in request.url.host:
            return httpx.Response(200, text="<html>maintenance</html>")
        return httpx.Response(404)

    result = detect_and_fetch(_client(handler), "acme")
    assert result is None


def test_lever_returns_none_on_malformed_json():
    def handler(request):
        if "lever" in request.url.host:
            return httpx.Response(200, text="<html>maintenance</html>")
        return httpx.Response(404)

    result = detect_and_fetch(_client(handler), "acme")
    assert result is None


def test_ashby_returns_none_on_malformed_json():
    def handler(request):
        if "ashbyhq" in request.url.host:
            return httpx.Response(200, text="<html>maintenance</html>")
        return httpx.Response(404)

    result = detect_and_fetch(_client(handler), "acme")
    assert result is None


def test_greenhouse_returns_none_on_missing_required_field():
    def handler(request):
        return httpx.Response(200, json={"jobs": [{"id": 1}]})

    result = detect_and_fetch(_client(handler), "acme")
    assert result is None


def test_lever_returns_none_on_missing_required_field():
    def handler(request):
        if "lever" in request.url.host:
            return httpx.Response(200, json=[{"id": "abc"}])
        return httpx.Response(404)

    result = detect_and_fetch(_client(handler), "acme")
    assert result is None


def test_ashby_returns_none_on_missing_required_field():
    def handler(request):
        if "ashbyhq" in request.url.host:
            return httpx.Response(200, json={"jobs": [{"id": "xyz"}]})
        return httpx.Response(404)

    result = detect_and_fetch(_client(handler), "acme")
    assert result is None
