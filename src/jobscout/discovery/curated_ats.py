"""Curated ATS Board discovery — orchestrates `companies.yaml` + the
ATS-type cache + the `trafilatura` fallback into normalized Postings
(DESIGN §3; CONTEXT.md: Posting, Curated ATS Board).

No LLM, plain Python (DESIGN §4's node table). Holds no SQLite
connection of its own — `conn` is a parameter, same shape as
`jobscout.criteria.derive_criteria` taking a plain `dict`.
"""

import hashlib
import sqlite3
from pathlib import Path

import httpx

from jobscout.discovery.ats import detect_and_fetch, fetch_from_known_ats
from jobscout.discovery.companies import DEFAULT_COMPANIES_PATH, TargetCompany, load_companies
from jobscout.discovery.fallback import fetch_careers_page_posting


def _dedupe_id(
    source: str, slug: str, external_id: str | None, title: str, city: str | None
) -> str:
    """§11 dedupe key, namespaced by Source so two companies on the same
    ATS vendor can't collide on a bare native id (schema.sql's literal
    "ATS job-id" wording undershoots this — see plan Global Constraints)."""
    if external_id:
        return f"{source}:{slug}:{external_id}"
    raw = f"{slug.lower()}|{title.lower()}|{(city or '').lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cached_ats_type(conn: sqlite3.Connection, slug: str) -> str | None:
    row = conn.execute(
        "SELECT ats_type FROM app_company_ats WHERE company_slug = ?", (slug,)
    ).fetchone()
    return row["ats_type"] if row else None


def _cache_ats_type(conn: sqlite3.Connection, slug: str, ats_type: str) -> None:
    conn.execute(
        "INSERT INTO app_company_ats (company_slug, ats_type) VALUES (?, ?)", (slug, ats_type)
    )
    conn.commit()


def _discover_company(
    conn: sqlite3.Connection, client: httpx.Client, company: TargetCompany
) -> list[dict]:
    try:
        ats_type = _cached_ats_type(conn, company.slug)
        if ats_type is None:
            detected = detect_and_fetch(client, company.slug)
            ats_type = detected[0] if detected else "none"
            _cache_ats_type(conn, company.slug, ats_type)
            raw_jobs = detected[1] if detected else []
        elif ats_type == "none":
            raw_jobs = []
        else:
            raw_jobs = fetch_from_known_ats(client, ats_type, company.slug)

        if ats_type == "none":
            if not company.careers_url:
                return []
            posting = fetch_careers_page_posting(client, company.careers_url)
            if posting is None:
                return []
            source = f"careers:{company.name}"
            return [
                {
                    "id": _dedupe_id(
                        source, company.slug, None, posting["title"], posting["city"]
                    ),
                    "source": source,
                    "company": company.name,
                    "title": posting["title"],
                    "city": posting["city"],
                    "url": posting["url"],
                    "jd_text": posting["jd_text"],
                }
            ]

        source = f"ats:{company.name}"
        return [
            {
                "id": _dedupe_id(source, company.slug, j["external_id"], j["title"], j["city"]),
                "source": source,
                "company": company.name,
                "title": j["title"],
                "city": j["city"],
                "url": j["url"],
                "jd_text": j["jd_text"],
            }
            for j in raw_jobs
        ]
    except httpx.HTTPError:
        return []  # §17: one unreachable company never blocks the Poll


def discover_curated_ats(
    conn: sqlite3.Connection,
    companies_path: Path = DEFAULT_COMPANIES_PATH,
    client: httpx.Client | None = None,
) -> list[dict]:
    """All Target Companies' Postings for this Poll."""
    companies = load_companies(companies_path)
    owns_client = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        postings = []
        for company in companies:
            postings.extend(_discover_company(conn, client, company))
        return postings
    finally:
        if owns_client:
            client.close()
