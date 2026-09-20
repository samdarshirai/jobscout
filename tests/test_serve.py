from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobscout.serve import _run_scheduled_poll


def _context(bot_data, bot=None, job_queue=None):
    return SimpleNamespace(bot_data=bot_data, bot=bot, job_queue=job_queue)


@pytest.mark.asyncio
async def test_scheduled_poll_sends_gate_prompt_when_paused():
    handle = SimpleNamespace(
        status="paused", run_id="run-1",
        pending_gate={"gate": "search_plan", "plan": {"queries": ["x"]}},
        state={},
    )
    svc = SimpleNamespace(trigger_run=lambda: handle)
    bot = SimpleNamespace(send_message=AsyncMock())

    await _run_scheduled_poll(_context({"service": svc, "allowed_chat_ids": {42}}, bot=bot))

    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduled_poll_sends_queue_notification_when_completed():
    handle = SimpleNamespace(
        status="completed", run_id="run-1", pending_gate=None,
        state={"postings": [{"id": "p1", "status": "scored", "company": "Acme", "title": "Eng", "score": 80}]},
    )
    svc = SimpleNamespace(trigger_run=lambda: handle)
    bot = SimpleNamespace(send_message=AsyncMock())

    await _run_scheduled_poll(_context({"service": svc, "allowed_chat_ids": {42}}, bot=bot))

    assert bot.send_message.await_count == 2  # summary + one posting


@pytest.mark.asyncio
async def test_scheduled_poll_swallows_a_failed_run():
    def _boom():
        raise RuntimeError("spend cap")

    svc = SimpleNamespace(trigger_run=_boom)
    bot = SimpleNamespace(send_message=AsyncMock())

    await _run_scheduled_poll(_context({"service": svc, "allowed_chat_ids": {42}}, bot=bot))

    bot.send_message.assert_not_awaited()
