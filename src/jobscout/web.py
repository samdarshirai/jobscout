"""Web surface (DESIGN §12, build-plan units 41-42).

A thin caller over `CoreService` (DESIGN §12: "CLI, Telegram bot, and web
are all thin callers") — every route either reads through one of the
`get_*_view`/`get_*_detail` methods or calls the same action methods the
Telegram bot calls, so an action taken here is indistinguishable from one
taken there. FastAPI, localhost, optional bearer token for tunnelled
access (§12) via `JOBSCOUT_WEB_TOKEN`. Serves a single static page that
polls these JSON routes every 2s (§12: polling, not SSE).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jobscout import spend
from jobscout.service import CoreService, get_service

# override=True: .env is this app's single source of config truth (README/.env.example) --
# a stray shell export of the same name (e.g. left over from an ablation-sweep session that
# mutates OPENROUTER_MODEL/FEEDBACK_MECHANISM in-process) must not silently outrank it.
#
# Deliberately loaded HERE, not in `jobscout/__init__.py`: this module is only ever imported
# by the real entrypoints (`jobscout serve`/CLI via `serve.py`, or `uvicorn jobscout.web:app`
# directly), never by `jobscout.service`/`jobscout.graph.*`/etc. on their own -- loading it at
# the package level instead broke test isolation (confirmed live: it silently pulled real
# `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` into every test process, including ones mocking discovery,
# so a "stub 1 fake posting" test got 51 real ones back from a live network call).
load_dotenv(override=True)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="jobscout")
_service: CoreService | None = None


def _svc() -> CoreService:
    global _service
    if _service is None:
        _service = get_service()
    return _service


def require_token(request: Request) -> None:
    token = os.environ.get("JOBSCOUT_WEB_TOKEN")
    if not token:
        return  # DESIGN §12: bearer token is optional, for tunnelled access only
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


api = FastAPI(dependencies=[Depends(require_token)])


def _run_handle_json(handle) -> dict:
    return {"run_id": handle.run_id, "status": handle.status, "pending_gate": handle.pending_gate}


def _service_error(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except spend.SpendCapExceeded as e:
        raise HTTPException(status_code=402, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---- explain (unit 41) -------------------------------------------------


@api.get("/queue")
def get_queue(svc: CoreService = Depends(_svc)):
    return [vars(e) for e in svc.get_queue()]


@api.get("/runs/{run_id}")
def get_run(run_id: str, svc: CoreService = Depends(_svc)):
    return svc.get_run_detail(run_id)


@api.get("/postings/{posting_id}")
def get_posting(posting_id: str, svc: CoreService = Depends(_svc)):
    return _service_error(svc.get_posting_detail, posting_id)


@api.get("/preference")
def get_preference(svc: CoreService = Depends(_svc)):
    return svc.get_preference_view()


@api.get("/eval")
def get_eval(svc: CoreService = Depends(_svc)):
    return svc.get_eval_view()


@api.get("/spend")
def get_spend(svc: CoreService = Depends(_svc)):
    return {"spend_usd": svc.total_spend(), "cap_usd": spend.CAP_USD}


# ---- act (unit 42) ------------------------------------------------------


class Decision(BaseModel):
    decision: str  # "approve" | "reject"


class Verdict(BaseModel):
    verdict: str  # "up" | "down"
    reason: str | None = None


@api.post("/runs/trigger")
def trigger_run(svc: CoreService = Depends(_svc)):
    return _run_handle_json(_service_error(svc.trigger_run))


@api.post("/runs/{run_id}/resume")
def resume_run(run_id: str, body: Decision, svc: CoreService = Depends(_svc)):
    return _run_handle_json(_service_error(svc.resume_run, run_id, body.decision))


@api.post("/postings/{posting_id}/verdict")
def record_verdict(posting_id: str, body: Verdict, svc: CoreService = Depends(_svc)):
    _service_error(svc.record_verdict, posting_id, body.verdict, body.reason)
    return {"ok": True}


@api.post("/postings/{posting_id}/letter")
def draft_letter(posting_id: str, svc: CoreService = Depends(_svc)):
    return _run_handle_json(_service_error(svc.draft_letter_for_posting, posting_id))


@api.post("/letters/{run_id}/resume")
def resume_letter(run_id: str, body: Decision, svc: CoreService = Depends(_svc)):
    return _run_handle_json(_service_error(svc.resume_letter, run_id, body.decision))


@api.post("/scope-expansion/{run_id}/resume")
def resume_scope_expansion(run_id: str, body: Decision, svc: CoreService = Depends(_svc)):
    return _run_handle_json(_service_error(svc.resume_scope_expansion, run_id, body.decision))


@api.post("/postings/{posting_id}/skip")
def skip_posting(posting_id: str, svc: CoreService = Depends(_svc)):
    _service_error(svc.skip_posting, posting_id)
    return {"ok": True}


@api.post("/postings/{posting_id}/applied")
def mark_applied(posting_id: str, svc: CoreService = Depends(_svc)):
    _service_error(svc.mark_applied, posting_id)
    return {"ok": True}


app.mount("/api", api)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
