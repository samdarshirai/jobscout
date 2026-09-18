"""arbeitnow discovery adapter (DESIGN §3) — secondary discovery,
no key, EU tech postings.

No LLM, plain Python (DESIGN §4's node table). Verified against the
live API: `description` is already the FULL job-ad content (an HTML
fragment, not a truncated summary), so unlike Adzuna (unit 21) there's
no redirect-and-refetch step — a plain tag-strip is enough.
"""

import httpx

from jobscout.discovery.htmltext import strip_html

ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"


def _normalize(job: dict) -> dict:
    city = job.get("location") or None
    if not city and job.get("remote"):
        city = "Remote"
    jd_text = strip_html(job.get("description") or "") or None
    return {
        "id": f"arbeitnow:{job['slug']}",
        "source": "arbeitnow",
        "company": job.get("company_name", "Unknown"),
        "title": job.get("title", ""),
        "city": city,
        "url": job.get("url"),
        "jd_text": jd_text,
    }


def discover_arbeitnow(client: httpx.Client, max_pages: int = 1) -> list[dict]:
    """All arbeitnow Postings for this Poll, up to `max_pages`."""
    postings = []
    url = ARBEITNOW_URL
    page = 0
    while url and page < max_pages:
        try:
            resp = client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            break  # §17: a bad page never blocks the Poll
        body = resp.json()
        postings.extend(_normalize(j) for j in body.get("data", []))
        url = (body.get("links") or {}).get("next")
        page += 1
    return postings
