"""ATS auto-detection + per-type job fetch (DESIGN §3).

CONTEXT.md: Curated ATS Board — the ATS (Greenhouse / Lever / Ashby /
Personio) hosting a Target Company's careers page, auto-detected and
cached per company. Probes in a fixed order so the result is
deterministic and the cache (owned by `jobscout.discovery.curated_ats`)
is stable.
"""

import html
from xml.etree import ElementTree

import httpx

ATS_TYPES = ("greenhouse", "lever", "ashby", "personio")


def _fetch_greenhouse(client: httpx.Client, slug: str) -> list[dict] | None:
    resp = client.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        params={"content": "true"},
    )
    if resp.status_code != 200:
        return None
    try:
        return [
            {
                "external_id": str(j["id"]),
                "title": j["title"],
                "city": (j.get("location") or {}).get("name"),
                "url": j.get("absolute_url"),
                "jd_text": html.unescape(j.get("content", "")),
            }
            for j in resp.json().get("jobs", [])
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _fetch_lever(client: httpx.Client, slug: str) -> list[dict] | None:
    resp = client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    if resp.status_code != 200:
        return None
    try:
        jobs = resp.json()
        if not isinstance(jobs, list):
            return None
        return [
            {
                "external_id": str(j["id"]),
                "title": j["text"],
                "city": (j.get("categories") or {}).get("location"),
                "url": j.get("hostedUrl"),
                "jd_text": j.get("descriptionPlain", ""),
            }
            for j in jobs
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _fetch_ashby(client: httpx.Client, slug: str) -> list[dict] | None:
    resp = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if resp.status_code != 200:
        return None
    try:
        return [
            {
                "external_id": str(j["id"]),
                "title": j["title"],
                "city": j.get("location"),
                "url": j.get("jobUrl"),
                "jd_text": j.get("descriptionPlain", ""),
            }
            for j in resp.json().get("jobs", [])
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _fetch_personio(client: httpx.Client, slug: str) -> list[dict] | None:
    resp = client.get(f"https://{slug}.jobs.personio.de/xml")
    if resp.status_code != 200:
        return None
    try:
        root = ElementTree.fromstring(resp.text)
    except ElementTree.ParseError:
        return None
    jobs = []
    for position in root.findall("position"):
        external_id = position.findtext("id")
        title = position.findtext("name")
        if external_id is None or title is None:
            continue
        description = " ".join(
            (d.findtext("value") or "").strip()
            for d in position.findall("./jobDescriptions/jobDescription")
        )
        jobs.append(
            {
                "external_id": external_id,
                "title": title,
                "city": position.findtext("office"),
                "url": position.findtext("careerSiteUrl"),
                "jd_text": description,
            }
        )
    return jobs


_FETCHERS = {
    "greenhouse": _fetch_greenhouse,
    "lever": _fetch_lever,
    "ashby": _fetch_ashby,
    "personio": _fetch_personio,
}


def detect_and_fetch(client: httpx.Client, slug: str) -> tuple[str, list[dict]] | None:
    """Probe each ATS type in `ATS_TYPES` order; first hit wins."""
    for ats_type in ATS_TYPES:
        jobs = _FETCHERS[ats_type](client, slug)
        if jobs is not None:
            return ats_type, jobs
    return None


def fetch_from_known_ats(client: httpx.Client, ats_type: str, slug: str) -> list[dict]:
    """Fetch from an already-cached ATS type. Never raises on a bad
    response — a transient failure just yields no Postings this Poll."""
    return _FETCHERS[ats_type](client, slug) or []
