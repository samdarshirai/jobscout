"""Tracing setup (DESIGN §14, §16, build-plan unit 14).

Wires the Scrubber in as LangSmith's global payload redactor so a Run's
data is scrubbed before it uploads. Whether tracing is on at all, and
where it goes, stays LangSmith's own env-var behavior (`LANGCHAIN_TRACING_V2`
etc. — see .env.example); this only makes sure whatever leaves has been
scrubbed first. No self-hosting (§14) — LangSmith's free tier only.
"""

import os
from functools import partial

import langsmith as ls
from langsmith import Client

from jobscout.scrubber import scrub_payload


def configure_tracing() -> None:
    """Call once at startup (§16: tracing is on from the first Run) —
    cheap and idempotent, safe to call from every CoreService.__init__."""
    scrubber = partial(
        scrub_payload,
        name=os.environ.get("CANDIDATE_NAME"),
        employer=os.environ.get("CANDIDATE_EMPLOYER"),
    )
    ls.configure(client=Client(hide_inputs=scrubber, hide_outputs=scrubber))
