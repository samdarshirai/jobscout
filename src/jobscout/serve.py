"""`jobscout serve` (DESIGN §16): Telegram bot + FastAPI web app + a
twice-daily Poll, one long-running process, kept alive by launchd on the
author's Mac. No cloud, no Docker -- SQLite (WAL) on disk means a hard
process kill and restart resumes cleanly from the last checkpoint, so this
deliberately doesn't chase graceful SIGTERM handling (§16's own stated
recovery story is "restart," not "drain").
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import time

import uvicorn
from telegram.ext import ContextTypes

from jobscout.service import get_service
from jobscout.telegram_bot import build_application, send_gate_prompt, send_poll_notification
from jobscout.web import app as web_app

logger = logging.getLogger(__name__)

POLL_TIMES = (time(8, 0), time(20, 0))  # DESIGN §16: 2x/day, local machine time


async def _run_scheduled_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = context.bot_data["service"]
    chat_ids = list(context.bot_data["allowed_chat_ids"])
    try:
        handle = svc.trigger_run()
    except Exception:
        logger.exception("scheduled Poll failed")
        return
    if handle.status == "paused" and handle.pending_gate:
        await send_gate_prompt(context.bot, chat_ids, handle.run_id, handle.pending_gate)
    else:
        await send_poll_notification(
            context.bot, context.job_queue, chat_ids, handle.state.get("postings", [])
        )


async def _serve() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_ids = [
        int(os.environ["TELEGRAM_CANDIDATE_CHAT_ID"]),
        int(os.environ["TELEGRAM_OPERATOR_CHAT_ID"]),
    ]
    service = get_service()
    application = build_application(token, chat_ids, service)
    for t in POLL_TIMES:
        application.job_queue.run_daily(_run_scheduled_poll, time=t)

    server = uvicorn.Server(uvicorn.Config(web_app, host="127.0.0.1", port=8000, log_level="info"))

    async with application:
        await application.start()
        await application.updater.start_polling()
        try:
            await server.serve()
        finally:
            await application.updater.stop()
            await application.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(_serve())
