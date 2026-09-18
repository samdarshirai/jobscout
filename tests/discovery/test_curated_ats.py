from pathlib import Path

import httpx
import pytest

from jobscout.discovery.curated_ats import discover_curated_ats
from jobscout.storage.db import get_connection, init_db


def _conn(tmp_path):
    conn = get_connection(tmp_path / "j.sqlite")
    init_db(conn)
    return conn


def _companies_file(tmp_path, yaml_text: str) -> Path:
    path = tmp_path / "companies.yaml"
    path.write_text(yaml_text)
    return path


def test_discover_curated_ats_probes_and_caches_a_new_company(tmp_path):
    conn = _conn(tmp_path)
    path = _companies_file(tmp_path, "companies:\n  - name: Acme GmbH\n    slug: acme\n")

    def handler(request):
        if "greenhouse" in request.url.host:
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 1,
                            "title": "Senior Frontend Engineer",
                            "location": {"name": "Berlin"},
                            "content": "JD text",
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    postings = discover_curated_ats(conn, path, client)

    assert postings == [
        {
            "id": "ats:Acme GmbH:acme:1",
            "source": "ats:Acme GmbH",
            "company": "Acme GmbH",
            "title": "Senior Frontend Engineer",
            "city": "Berlin",
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "jd_text": "JD text",
        }
    ]
    cached = conn.execute(
        "SELECT ats_type FROM app_company_ats WHERE company_slug = 'acme'"
    ).fetchone()
    assert cached["ats_type"] == "greenhouse"
    conn.close()


def test_discover_curated_ats_uses_the_cache_and_skips_other_probes(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO app_company_ats (company_slug, ats_type) VALUES ('acme', 'lever')"
    )
    conn.commit()
    path = _companies_file(tmp_path, "companies:\n  - name: Acme GmbH\n    slug: acme\n")

    calls = []

    def handler(request):
        calls.append(request.url.host)
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

    client = httpx.Client(transport=httpx.MockTransport(handler))
    postings = discover_curated_ats(conn, path, client)

    assert len(calls) == 1
    assert postings[0]["id"] == "ats:Acme GmbH:acme:abc"
    conn.close()


def test_discover_curated_ats_falls_back_to_trafilatura_when_no_ats_detected(
    tmp_path, monkeypatch
):
    conn = _conn(tmp_path)
    path = _companies_file(
        tmp_path,
        "companies:\n  - name: Acme GmbH\n    slug: acme\n"
        "    careers_url: https://acme.example/careers\n",
    )
    monkeypatch.setattr(
        "jobscout.discovery.curated_ats.fetch_careers_page_posting",
        lambda client, careers_url: {
            "external_id": None,
            "title": "Careers page",
            "city": None,
            "url": careers_url,
            "jd_text": "We hire engineers.",
        },
    )

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    postings = discover_curated_ats(conn, path, client)

    assert postings == [
        {
            "id": __import__("hashlib").sha256(b"acme|careers page|").hexdigest(),
            "source": "careers:Acme GmbH",
            "company": "Acme GmbH",
            "title": "Careers page",
            "city": None,
            "url": "https://acme.example/careers",
            "jd_text": "We hire engineers.",
        }
    ]
    cached = conn.execute(
        "SELECT ats_type FROM app_company_ats WHERE company_slug = 'acme'"
    ).fetchone()
    assert cached["ats_type"] == "none"
    conn.close()


def test_discover_curated_ats_skips_company_with_no_ats_and_no_careers_url(tmp_path):
    conn = _conn(tmp_path)
    path = _companies_file(tmp_path, "companies:\n  - name: Acme GmbH\n    slug: acme\n")

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    postings = discover_curated_ats(conn, path, client)

    assert postings == []
    conn.close()


def test_discover_curated_ats_with_no_companies_returns_empty(tmp_path):
    conn = _conn(tmp_path)
    path = _companies_file(tmp_path, "companies: []\n")
    postings = discover_curated_ats(conn, path)
    assert postings == []
    conn.close()
