"""Telegram bot surface (DESIGN §12, build-plan unit 40).

A thin caller over `CoreService` (DESIGN §12: "CLI, Telegram bot, and web
are all thin callers"). Message formatting, the burst cap, and quiet hours
are plain functions so they're testable without a live bot; `build_application`
and the handlers below are the untested glue that wires them to a real
`telegram.ext.Application` (long-polling, no public webhook, per §12).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from jobscout.service import CoreService

logger = logging.getLogger(__name__)

BURST_CAP = 5  # DESIGN §12: >5 queued Postings in a Poll -> top 3 + "see web"
TOP_N = 3
QUIET_START_HOUR = 22  # DESIGN §12: 22:00-08:00, local machine time (§16: runs on her Mac)
QUIET_END_HOUR = 8


def in_quiet_hours(now: datetime) -> bool:
    return now.hour >= QUIET_START_HOUR or now.hour < QUIET_END_HOUR


def next_send_time(now: datetime) -> datetime:
    """When a Poll's queue notification actually goes out (DESIGN §12):
    immediately outside quiet hours, else held for the next 08:00 -- today's
    if `now` is still in the evening half of the window, tomorrow's if
    already past midnight into it."""
    if not in_quiet_hours(now):
        return now
    send_day = now if now.hour < QUIET_END_HOUR else now + timedelta(days=1)
    return send_day.replace(hour=QUIET_END_HOUR, minute=0, second=0, microsecond=0)


def select_burst(postings: list[dict]) -> tuple[list[dict], int]:
    """Burst cap (DESIGN §12): more than 5 queued this Poll -> only the top
    3 by Score get a full message, the rest are just counted ("see web")."""
    if len(postings) <= BURST_CAP:
        return postings, 0
    ranked = sorted(postings, key=lambda p: p["score"], reverse=True)
    return ranked[:TOP_N], len(ranked) - TOP_N


def format_summary_line(n_matches: int, n_gates: int, overflow: int) -> str:
    parts = [f"{n_matches} new match{'es' if n_matches != 1 else ''}"]
    if n_gates:
        parts.append(f"{n_gates} gate{'s' if n_gates != 1 else ''} need{'s' if n_gates == 1 else ''} you")
    line = ", ".join(parts) + "."
    if overflow:
        line += f" {overflow} more -- see web."
    return line


def format_posting_message(posting: dict) -> str:
    flag = " ⚠️ weak fit" if posting["score"] < 50 else ""
    city = f" ({posting['city']})" if posting.get("city") else ""
    return (
        f"*{posting['company']}* — {posting['title']}{city}\n"
        f"Score: {posting['score']}/100{flag}\n"
        f"{posting.get('rationale', '')}"
    )


def posting_keyboard(posting_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("\U0001F44D", callback_data=f"up:{posting_id}"),
        InlineKeyboardButton("\U0001F44E", callback_data=f"down:{posting_id}"),
        InlineKeyboardButton("draft letter", callback_data=f"letter:{posting_id}"),
        InlineKeyboardButton("skip", callback_data=f"skip:{posting_id}"),
    ]])


_GATE_LABELS = {
    "search_plan": "Search Plan",
    "outbound_letter": "Cover Letter",
    "scope_expansion": "Scope Expansion",
}


def format_gate_message(pending_gate: dict) -> str:
    gate = pending_gate["gate"]
    label = _GATE_LABELS.get(gate, gate)
    if gate == "search_plan":
        body = "\n".join(f"- {q}" for q in pending_gate["plan"].get("queries", []))
    elif gate == "outbound_letter":
        body = pending_gate["draft"].get("body", "")
    else:  # scope_expansion
        proposal = pending_gate["proposal"]
        body = f"{proposal.get('axis')}: {proposal.get('change')}\n{proposal.get('evidence', '')}"
    return f"*{label} needs your approval*\n\n{body}"


def gate_keyboard(gate: str, run_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Approve", callback_data=f"gate:approve:{gate}:{run_id}"),
        InlineKeyboardButton("Reject", callback_data=f"gate:reject:{gate}:{run_id}"),
    ]])


def is_authorized(chat_id: int, allowed_chat_ids: set[int]) -> bool:
    """Hard lock to the Candidate's + Operator's chat ids (DESIGN §12) --
    every other sender is ignored."""
    return chat_id in allowed_chat_ids


async def handle_posting_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_authorized(query.message.chat_id, context.bot_data["allowed_chat_ids"]):
        await query.answer("Not authorized.", show_alert=True)
        return
    action, posting_id = query.data.split(":", 1)
    svc: CoreService = context.bot_data["service"]
    if action == "up":
        svc.record_verdict(posting_id, "up")
        await query.answer("\U0001F44D recorded")
    elif action == "down":
        svc.record_verdict(posting_id, "down")
        await query.answer("\U0001F44E recorded")
    elif action == "skip":
        svc.skip_posting(posting_id)
        await query.answer("Skipped")
    elif action == "letter":
        await query.answer("Drafting letter...")
        handle = svc.draft_letter_for_posting(posting_id)
        if handle.status == "paused" and handle.pending_gate:
            await query.message.chat.send_message(
                format_gate_message(handle.pending_gate),
                reply_markup=gate_keyboard(handle.pending_gate["gate"], handle.run_id),
                parse_mode="Markdown",
            )
    await query.edit_message_reply_markup(reply_markup=None)


_RESUME_METHOD_BY_GATE = {
    "search_plan": "resume_run",
    "outbound_letter": "resume_letter",
    "scope_expansion": "resume_scope_expansion",
}


async def handle_gate_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_authorized(query.message.chat_id, context.bot_data["allowed_chat_ids"]):
        await query.answer("Not authorized.", show_alert=True)
        return
    _, action, gate, run_id = query.data.split(":", 3)
    decision = "approve" if action == "approve" else "reject"
    svc: CoreService = context.bot_data["service"]
    resume = getattr(svc, _RESUME_METHOD_BY_GATE[gate])
    resume(run_id, decision)
    await query.answer(decision.capitalize())
    await query.edit_message_reply_markup(reply_markup=None)


async def send_gate_prompt(bot, chat_ids: list[int], run_id: str, pending_gate: dict) -> None:
    """Gate prompts go out immediately (DESIGN §12) -- quiet hours only
    hold the routine per-Poll queue notification, not an Approval Gate."""
    text = format_gate_message(pending_gate)
    keyboard = gate_keyboard(pending_gate["gate"], run_id)
    for chat_id in chat_ids:
        await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="Markdown")


async def send_poll_notification(
    bot, job_queue, chat_ids: list[int], postings: list[dict], n_gates: int = 0,
    now: datetime | None = None,
) -> None:
    """After a Poll (DESIGN §12): a summary line, then one message per
    queued Posting -- burst-capped, and held for 08:00 in quiet hours.

    ponytail: the hold is an in-process `JobQueue.run_once`, not a
    persisted row -- a process restart during the quiet window drops the
    pending send. Add a `pending_notification` table if that's ever
    observed live (the app already restarts clean off SQLite for
    everything else, per DESIGN §16)."""
    now = now or datetime.now()
    scored = [p for p in postings if p.get("status") == "scored"]
    shown, overflow = select_burst(scored)
    summary = format_summary_line(len(scored), n_gates, overflow)

    async def _send(context: ContextTypes.DEFAULT_TYPE | None = None) -> None:
        for chat_id in chat_ids:
            await bot.send_message(chat_id, summary)
            for posting in shown:
                await bot.send_message(
                    chat_id, format_posting_message(posting),
                    reply_markup=posting_keyboard(posting["id"]), parse_mode="Markdown",
                )

    send_at = next_send_time(now)
    if send_at <= now:
        await _send()
    else:
        job_queue.run_once(_send, when=send_at)


def build_application(token: str, allowed_chat_ids: list[int], service: CoreService) -> Application:
    application = Application.builder().token(token).build()
    application.bot_data["allowed_chat_ids"] = set(allowed_chat_ids)
    application.bot_data["service"] = service
    application.add_handler(CallbackQueryHandler(handle_gate_button, pattern=r"^gate:"))
    application.add_handler(CallbackQueryHandler(handle_posting_button, pattern=r"^(up|down|skip|letter):"))
    return application
