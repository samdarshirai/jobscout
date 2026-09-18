"""Adzuna discovery adapter (DESIGN §3) — primary discovery, free
`app_id`+`app_key`, Germany coverage, structured salary + location.

No LLM, plain Python (DESIGN §4's node table). `description` in
Adzuna's search response is Adzuna's OWN TRUNCATED SUMMARY, not the
full JD (verified against Adzuna's docs) — this adapter's `jd_text` is
therefore only a best-effort fallback. `poll.py`'s `fetch_jd` node
follows each Posting's `url` (Adzuna's `redirect_url`) to try to
upgrade it to the full JD text, falling back to this summary if that
fetch or extraction fails. Known Adzuna trait (§3): noisier, includes
recruiter spam — no filter is specified, so none is built here.
"""

import httpx

from jobscout.discovery.htmltext import strip_html

_BASE_URL = "https://api.adzuna.com/v1/api/jobs"


def _salary_line(result: dict) -> str:
    lo, hi = result.get("salary_min"), result.get("salary_max")
    if not lo and not hi:
        return ""
    predicted = " (predicted)" if result.get("salary_is_predicted") else ""
    if lo and hi:
        return f"Salary: {lo:g}-{hi:g}{predicted}\n\n"
    if lo:
        return f"Salary: from {lo:g}{predicted}\n\n"
    return f"Salary: up to {hi:g}{predicted}\n\n"


def _normalize(result: dict) -> dict:
    description = strip_html(result.get("description") or "")
    return {
        "id": f"adzuna:{result['id']}",
        "source": "adzuna",
        "company": (result.get("company") or {}).get("display_name", "Unknown"),
        "title": result.get("title", ""),
        "city": (result.get("location") or {}).get("display_name"),
        "url": result.get("redirect_url"),
        "jd_text": _salary_line(result) + description,
    }


def discover_adzuna(
    client: httpx.Client,
    app_id: str,
    app_key: str,
    country: str = "de",
    what: str | None = None,
    where: str | None = None,
    max_pages: int = 1,
    results_per_page: int = 50,
) -> list[dict]:
    """Adzuna Postings for this Poll, up to `max_pages` search-result pages."""
    postings = []
    for page in range(1, max_pages + 1):
        params = {"app_id": app_id, "app_key": app_key, "results_per_page": results_per_page}
        if what:
            params["what"] = what
        if where:
            params["where"] = where
        try:
            resp = client.get(f"{_BASE_URL}/{country}/search/{page}", params=params)
            resp.raise_for_status()
        except httpx.HTTPError:
            break  # §17: one bad page never blocks the Poll
        results = resp.json().get("results") or []
        if not results:
            break
        postings.extend(_normalize(r) for r in results)
    return postings
