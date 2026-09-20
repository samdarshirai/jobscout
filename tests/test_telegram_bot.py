from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobscout.telegram_bot import (
    QUIET_END_HOUR,
    QUIET_START_HOUR,
    format_gate_message,
    format_posting_message,
    format_summary_line,
    gate_keyboard,
    handle_gate_button,
    handle_posting_button,
    in_quiet_hours,
    is_authorized,
    next_send_time,
    posting_keyboard,
    select_burst,
    send_gate_prompt,
    send_poll_notification,
)


# ---- quiet hours -----------------------------------------------------


def test_in_quiet_hours_true_late_at_night():
    assert in_quiet_hours(datetime(2026, 1, 1, 23, 0)) is True


def test_in_quiet_hours_true_early_morning():
    assert in_quiet_hours(datetime(2026, 1, 1, 6, 0)) is True


def test_in_quiet_hours_false_during_the_day():
    assert in_quiet_hours(datetime(2026, 1, 1, 14, 0)) is False


def test_in_quiet_hours_boundaries_are_inclusive_exclusive():
    assert in_quiet_hours(datetime(2026, 1, 1, QUIET_START_HOUR, 0)) is True
    assert in_quiet_hours(datetime(2026, 1, 1, QUIET_END_HOUR, 0)) is False


def test_next_send_time_is_now_outside_quiet_hours():
    now = datetime(2026, 1, 1, 14, 0)
    assert next_send_time(now) == now


def test_next_send_time_holds_for_tonights_8am_when_already_past_midnight():
    now = datetime(2026, 1, 1, 3, 0)
    assert next_send_time(now) == datetime(2026, 1, 1, 8, 0)


def test_next_send_time_holds_for_tomorrows_8am_when_still_evening():
    now = datetime(2026, 1, 1, 23, 0)
    assert next_send_time(now) == datetime(2026, 1, 2, 8, 0)


# ---- burst cap ---------------------------------------------------------


def test_select_burst_returns_everything_at_or_under_the_cap():
    postings = [{"id": f"p{i}", "score": i} for i in range(5)]
    shown, overflow = select_burst(postings)
    assert shown == postings
    assert overflow == 0


def test_select_burst_caps_to_top_3_by_score_over_the_limit():
    postings = [{"id": f"p{i}", "score": i} for i in range(6)]
    shown, overflow = select_burst(postings)
    assert [p["id"] for p in shown] == ["p5", "p4", "p3"]
    assert overflow == 3


# ---- message formatting -------------------------------------------------


def test_format_summary_line_pluralizes_and_reports_gates_and_overflow():
    assert format_summary_line(1, 0, 0) == "1 new match."
    assert format_summary_line(2, 1, 0) == "2 new matches, 1 gate needs you."
    assert format_summary_line(2, 2, 3) == "2 new matches, 2 gates need you. 3 more -- see web."


def test_format_posting_message_flags_weak_fit_below_50():
    posting = {"company": "Acme", "title": "Eng", "city": "Munich", "score": 40, "rationale": "meh"}
    text = format_posting_message(posting)
    assert "Acme" in text and "Eng" in text and "Munich" in text
    assert "weak fit" in text


def test_format_posting_message_no_flag_at_or_above_50():
    posting = {"company": "Acme", "title": "Eng", "city": None, "score": 50, "rationale": "ok"}
    assert "weak fit" not in format_posting_message(posting)


def test_posting_keyboard_encodes_action_and_posting_id_with_colons():
    markup = posting_keyboard("ats:Acme:acme:1")
    data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert data == ["up:ats:Acme:acme:1", "down:ats:Acme:acme:1", "letter:ats:Acme:acme:1", "skip:ats:Acme:acme:1"]


def test_format_gate_message_search_plan():
    text = format_gate_message({"gate": "search_plan", "plan": {"queries": ["frontend engineer"]}})
    assert "Search Plan" in text and "frontend engineer" in text


def test_format_gate_message_outbound_letter():
    text = format_gate_message({"gate": "outbound_letter", "draft": {"body": "Dear team,"}})
    assert "Cover Letter" in text and "Dear team," in text


def test_format_gate_message_scope_expansion():
    text = format_gate_message({
        "gate": "scope_expansion",
        "proposal": {"axis": "salary_floor", "change": "60000", "evidence": "3 postings excluded"},
    })
    assert "Scope Expansion" in text and "salary_floor" in text and "3 postings excluded" in text


def test_gate_keyboard_encodes_gate_and_run_id():
    markup = gate_keyboard("search_plan", "run-1")
    data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert data == ["gate:approve:search_plan:run-1", "gate:reject:search_plan:run-1"]


# ---- authorization -------------------------------------------------------


def test_is_authorized_true_for_allowed_chat_id():
    assert is_authorized(42, {42, 99}) is True


def test_is_authorized_false_for_unknown_chat_id():
    assert is_authorized(7, {42, 99}) is False


# ---- handlers (duck-typed Update/Context, no live bot) -------------------


def _query(chat_id, data):
    return SimpleNamespace(
        message=SimpleNamespace(chat_id=chat_id, chat=SimpleNamespace(send_message=AsyncMock())),
        data=data,
        answer=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )


def _context(allowed_chat_ids, service, bot=None, job_queue=None):
    return SimpleNamespace(
        bot_data={"allowed_chat_ids": allowed_chat_ids, "service": service},
        bot=bot, job_queue=job_queue,
    )


@pytest.mark.asyncio
async def test_handle_posting_button_rejects_an_unauthorized_chat_id():
    svc = SimpleNamespace(record_verdict=AsyncMock())
    query = _query(chat_id=7, data="up:p1")
    update = SimpleNamespace(callback_query=query)

    await handle_posting_button(update, _context({42}, svc))

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    svc.record_verdict.assert_not_called()


@pytest.mark.asyncio
async def test_handle_posting_button_up_records_verdict():
    calls = []
    svc = SimpleNamespace(record_verdict=lambda posting_id, verdict: calls.append((posting_id, verdict)))
    query = _query(chat_id=42, data="up:ats:Acme:acme:1")
    update = SimpleNamespace(callback_query=query)

    await handle_posting_button(update, _context({42}, svc))

    assert calls == [("ats:Acme:acme:1", "up")]
    query.edit_message_reply_markup.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_posting_button_skip_calls_skip_posting():
    calls = []
    svc = SimpleNamespace(skip_posting=lambda posting_id: calls.append(posting_id))
    query = _query(chat_id=42, data="skip:p1")
    update = SimpleNamespace(callback_query=query)

    await handle_posting_button(update, _context({42}, svc))

    assert calls == ["p1"]


@pytest.mark.asyncio
async def test_handle_posting_button_letter_sends_the_gate_it_pauses_at():
    handle = SimpleNamespace(
        status="paused", run_id="run-1",
        pending_gate={"gate": "outbound_letter", "draft": {"body": "Dear team,"}},
    )
    svc = SimpleNamespace(draft_letter_for_posting=lambda posting_id: handle)
    query = _query(chat_id=42, data="letter:p1")
    update = SimpleNamespace(callback_query=query)

    await handle_posting_button(update, _context({42}, svc))

    query.message.chat.send_message.assert_awaited_once()
    text = query.message.chat.send_message.await_args.args[0]
    assert "Cover Letter" in text


@pytest.mark.asyncio
async def test_handle_gate_button_approve_resumes_the_right_run_kind():
    calls = []
    svc = SimpleNamespace(resume_run=lambda run_id, decision: calls.append((run_id, decision)))
    query = _query(chat_id=42, data="gate:approve:search_plan:run-1")
    update = SimpleNamespace(callback_query=query)

    await handle_gate_button(update, _context({42}, svc))

    assert calls == [("run-1", "approve")]


@pytest.mark.asyncio
async def test_handle_gate_button_reject_routes_scope_expansion_to_its_own_resume():
    calls = []
    svc = SimpleNamespace(resume_scope_expansion=lambda run_id, decision: calls.append((run_id, decision)))
    query = _query(chat_id=42, data="gate:reject:scope_expansion:run-2")
    update = SimpleNamespace(callback_query=query)

    await handle_gate_button(update, _context({42}, svc))

    assert calls == [("run-2", "reject")]


@pytest.mark.asyncio
async def test_handle_gate_button_search_plan_completed_sends_poll_notification():
    handle = SimpleNamespace(
        status="completed", run_id="run-1", pending_gate=None,
        state={"postings": [{"id": "p1", "status": "scored", "company": "Acme", "title": "Eng", "score": 80}]},
    )
    svc = SimpleNamespace(resume_run=lambda run_id, decision: handle)
    bot = SimpleNamespace(send_message=AsyncMock())
    query = _query(chat_id=42, data="gate:approve:search_plan:run-1")
    update = SimpleNamespace(callback_query=query)

    await handle_gate_button(update, _context({42}, svc, bot=bot))

    assert bot.send_message.await_count == 2  # summary + one posting


@pytest.mark.asyncio
async def test_handle_gate_button_search_plan_paused_again_sends_next_gate():
    handle = SimpleNamespace(
        status="paused", run_id="run-1",
        pending_gate={"gate": "outbound_letter", "draft": {"body": "hi"}},
        state={},
    )
    svc = SimpleNamespace(resume_run=lambda run_id, decision: handle)
    bot = SimpleNamespace(send_message=AsyncMock())
    query = _query(chat_id=42, data="gate:approve:search_plan:run-1")
    update = SimpleNamespace(callback_query=query)

    await handle_gate_button(update, _context({42}, svc, bot=bot))

    bot.send_message.assert_awaited_once()
    assert "Cover Letter" in bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_handle_gate_button_letter_gate_sends_no_follow_up():
    svc = SimpleNamespace(resume_letter=lambda run_id, decision: SimpleNamespace(status="completed"))
    bot = SimpleNamespace(send_message=AsyncMock())
    query = _query(chat_id=42, data="gate:approve:outbound_letter:run-1")
    update = SimpleNamespace(callback_query=query)

    await handle_gate_button(update, _context({42}, svc, bot=bot))

    bot.send_message.assert_not_awaited()


# ---- notification sending -------------------------------------------------


@pytest.mark.asyncio
async def test_send_gate_prompt_sends_immediately_to_every_chat_id():
    bot = SimpleNamespace(send_message=AsyncMock())

    await send_gate_prompt(bot, [1, 2], "run-1", {"gate": "search_plan", "plan": {"queries": ["x"]}})

    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_send_poll_notification_sends_now_outside_quiet_hours():
    bot = SimpleNamespace(send_message=AsyncMock())
    job_queue = SimpleNamespace(run_once=lambda *a, **k: pytest.fail("should not schedule"))
    postings = [{"id": "p1", "company": "Acme", "title": "Eng", "city": None, "score": 80,
                 "rationale": "why", "status": "scored"}]

    await send_poll_notification(bot, job_queue, [1], postings, now=datetime(2026, 1, 1, 14, 0))

    # 1 summary + 1 posting message
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_send_poll_notification_holds_during_quiet_hours():
    bot = SimpleNamespace(send_message=AsyncMock())
    scheduled = []
    job_queue = SimpleNamespace(run_once=lambda callback, when: scheduled.append((callback, when)))
    postings = [{"id": "p1", "company": "Acme", "title": "Eng", "city": None, "score": 80,
                 "rationale": "why", "status": "scored"}]

    await send_poll_notification(bot, job_queue, [1], postings, now=datetime(2026, 1, 1, 23, 0))

    bot.send_message.assert_not_called()
    assert len(scheduled) == 1
    assert scheduled[0][1] == datetime(2026, 1, 2, 8, 0)
