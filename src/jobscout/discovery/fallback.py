"""`trafilatura` careers-page fallback (DESIGN §3) — Target Companies
with no detectable ATS. One Posting per company, the whole careers
page as JD text; no per-job splitting (plain Python, no LLM, DESIGN §4).
"""

import httpx
import trafilatura


def fetch_page_text(client: httpx.Client, url: str) -> str | None:
    """Whole-page fetch + boilerplate-stripped extract. Shared by the
    careers-page fallback below and unit 21's Adzuna `redirect_url`
    follow (poll.py's `fetch_jd`) — same shape of problem, a real
    external page of unknown structure."""
    resp = client.get(url, follow_redirects=True)
    if resp.status_code != 200:
        return None
    return trafilatura.extract(resp.text)


def fetch_careers_page_posting(client: httpx.Client, careers_url: str) -> dict | None:
    text = fetch_page_text(client, careers_url)
    if not text:
        return None
    return {
        "external_id": None,
        "title": "Careers page",
        "city": None,
        "url": careers_url,
        "jd_text": text,
    }
