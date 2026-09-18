"""Payload Scrubber (DESIGN §14, build-plan unit 14).

Redacts a trace payload before it leaves the machine: the Candidate's name
and current employer (exact match, case-insensitive), any email/phone, and
her exact comp figure (bucketed to the nearest ten-thousand, e.g. "€85k" ->
"€80-90k"). Graph structure — dict keys, list shape, non-string values —
and reasoning/rationale text survive untouched; only string leaves are
rewritten. The full, unredacted state stays in local SQLite only (§14);
this only touches what's about to be uploaded.
"""

import re

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# ponytail: loose international-phone heuristic — an 8+ digit run with
# common phone punctuation. Can false-positive on other long digit runs
# (ids, date ranges); tighten if real trace data shows it over-matching.
_PHONE_RE = re.compile(r"\+?\d[\d\-().\s]{6,}\d")
# ponytail: only the "€85k" shorthand is bucketed — a full number like
# "€85,000" isn't recognized. Extend the pattern if that format shows up
# in real payloads (comp is currently free-text from onboarding).
_COMP_RE = re.compile(r"([€$£])\s?(\d{1,3}(?:\.\d+)?)\s?[kK]\b")

_CONTACT = "[CONTACT]"


def _bucket_comp(match: re.Match) -> str:
    currency, amount = match.group(1), float(match.group(2))
    lower = int(amount // 10) * 10
    return f"{currency}{lower}–{lower + 10}k"


def _scrub_text(text: str, name: str | None, employer: str | None) -> str:
    text = _EMAIL_RE.sub(_CONTACT, text)
    text = _PHONE_RE.sub(_CONTACT, text)
    text = _COMP_RE.sub(_bucket_comp, text)
    if name:
        text = re.sub(re.escape(name), "[CANDIDATE]", text, flags=re.IGNORECASE)
    if employer:
        text = re.sub(re.escape(employer), "[EMPLOYER]", text, flags=re.IGNORECASE)
    return text


def scrub_payload(payload, name: str | None = None, employer: str | None = None):
    """Recursively redact every string leaf of `payload` — a dict, list,
    string, or any other (non-string) value passes through unchanged
    except for its string leaves."""
    if isinstance(payload, dict):
        return {k: scrub_payload(v, name, employer) for k, v in payload.items()}
    if isinstance(payload, list):
        return [scrub_payload(v, name, employer) for v in payload]
    if isinstance(payload, str):
        return _scrub_text(payload, name, employer)
    return payload
