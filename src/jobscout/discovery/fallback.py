"""`trafilatura` careers-page fallback (DESIGN §3) — Target Companies
with no detectable ATS. One Posting per company, the whole careers
page as JD text; no per-job splitting (plain Python, no LLM, DESIGN §4).
"""

import httpx
import trafilatura


def fetch_careers_page_posting(client: httpx.Client, careers_url: str) -> dict | None:
    resp = client.get(careers_url)
    if resp.status_code != 200:
        return None
    text = trafilatura.extract(resp.text)
    if not text:
        return None
    return {
        "external_id": None,
        "title": "Careers page",
        "city": None,
        "url": careers_url,
        "jd_text": text,
    }
