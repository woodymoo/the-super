"""Background entry point — triggered by Cloud Scheduler, not for chatting with.

adk web / adk run build their own Runner and SessionService, but those are for
debugging. In production there is no "person typing"; the trigger is a scheduled
job, so we build them ourselves.

Three triggers:
  - /poll    every 5-15 min  — fetch new messages, classify, take the right branch
  - /rent    on the 1st/2nd/5th — the rent-cycle check
  - /digest  each evening     — text the daily digest to the landlord
"""

import asyncio
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# ⚠️ This must run before importing google.adk and the_super.*.
# ADK loads .env itself only on the CLI path (adk web / adk run), and
# `python main.py poll` never gets there — without loading it explicitly,
# GOOGLE_API_KEY and LANDLORD_SMS_THREAD are both empty and every Cloud Scheduler
# trigger runs blind.
load_dotenv(Path(__file__).parent / "the_super" / ".env")

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402

from the_super.agent import root_agent  # noqa: E402
from the_super.tools.gmail import read_new_messages, send_sms_now  # noqa: E402
from the_super.rent import build_collection_sms, check_rent  # noqa: E402
from the_super.tools.store import (  # noqa: E402
    get_overdue_media_tickets,
    get_thread,
    write_ledger,
    write_ticket,
)

APP_NAME = "the_super"
LANDLORD_THREAD = os.environ.get("LANDLORD_SMS_THREAD", "")

# ⚠️ InMemorySessionService is for local development only.
# A Cloud Run container can be reclaimed at any time, so this must become a
# Firestore or database backend before deploying — otherwise the agent loses its
# memory on every restart.
session_service = InMemorySessionService()

runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
)


async def process_one(message) -> None:
    """Process one message.

    Sessions have three levels: app / user / session.
    room_id serves as user_id (separating rooms) and **message_id** as session_id.

    ⚠️ gmail_thread_id cannot be the session_id — Google Voice groups the entire
    conversation with one number into a **single** Gmail thread, so the second
    text from a tenant carries a duplicate thread_id and create_session raises
    AlreadyExistsError. message_id is what is unique per message.
    """
    user_id = message.room_id or "unknown"
    session_id = message.gmail_message_id

    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )

    content = types.Content(
        role="user",
        parts=[types.Part(text=message.model_dump_json())],
    )

    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.is_final_response():
            _print_event(message, event)


def _print_event(message, event) -> None:
    """Print text only, not the thought_signature binary blob."""
    parts = getattr(event.content, "parts", None) or []
    for part in parts:
        text = (getattr(part, "text", None) or "").strip()
        if text:
            print(f"  [{message.room_id}] {text}")


async def poll() -> None:
    """Fetch new messages and process them one at a time.

    ⚠️ The try/except here is **deliberate**, and it is the only one in the project.

    CLAUDE.md says not to write broad excepts in tools (they hide ADK's
    framework-level retry). But this is not inside a tool; it is the driving
    loop's **per-message business boundary**: one message failing should not throw
    away the rest of the batch. ADK's node-level retries have already run inside
    process_one, so reaching here means that message genuinely cannot be handled.

    Never catch BaseException — that swallows NodeInterruptedError and breaks
    human-in-the-loop pauses.
    """
    messages = read_new_messages()
    print(f"{len(messages)} new message(s)\n")

    ok = failed = 0
    for m in messages:
        try:
            await process_one(m)
            ok += 1
        except Exception:
            failed += 1
            print(f"  ❌ [{m.room_id}] failed; skipping this one and continuing:")
            traceback.print_exc()
        print()

    print(f"Done: {ok} succeeded, {failed} failed")


async def digest() -> None:
    """Daily digest — texted to the landlord themselves, because they rarely check email."""
    overdue = get_overdue_media_tickets()
    lines = [f"📋 {datetime.now(timezone.utc):%m-%d} digest"]

    if overdue:
        lines.append(f"\n⏳ {len(overdue)} ticket(s) overdue waiting on photos:")
        lines += [f"· {t['room_id']} ({t['ticket_id']})" for t in overdue]
    else:
        lines.append("\nNothing overdue.")

    send_sms_now(LANDLORD_THREAD, "\n".join(lines))


async def rent_cycle() -> None:
    """Rent cycle — each tenant on their own lease due day; collection the day after.

    ⚠️ Collection texts are **sent to tenants automatically** — the landlord
    explicitly required this on 2026-08-31, overriding architectural constraint 1.
    Summaries to the landlord themselves are likewise sent directly. Both the
    decision and the copy live in the_super/rent.py as deterministic code that
    never passes through the model.
    """
    today = datetime.now(timezone.utc).date()
    month = f"{today:%Y-%m}"
    rows = check_rent(month, today)

    for s in rows:
        if not s.needs_collection:
            continue
        write_ledger(
            month=s.month,
            room_id=s.room_id,
            claimed_amount=0.0,
            found_amount=s.found_amount,
            status="missing",
        )
        sms = build_collection_sms(s)
        thread = get_thread(s.room_id)
        if thread:
            # ⚠️ Sent to the tenant automatically — explicitly required by the
            # landlord, overriding CLAUDE.md constraint 1. With DRY_RUN=true it
            # only logs. Think before changing this line: a sent text can't be recalled.
            #
            # TODO(quiet hours): commercial SMS practice is not to send late at
            # night or early in the morning. That is a decision, so it belongs to
            # code rather than the skill. Right now it relies on the Cloud
            # Scheduler cron time, and running `python main.py rent` by hand
            # bypasses it. For a hard guarantee, check the local time in the
            # property's timezone here and fall back to a draft outside the window.
            sent_id = send_sms_now(thread, sms)
            status = "rent_collection_sent"
        else:
            # Never received a text from this tenant -> no thread to reply to, so escalate
            sent_id, status = None, "rent_collection_no_thread"
        write_ticket(
            status=status,
            room_id=s.room_id,
            draft_sms=sms,
            month=s.month,
            days_overdue=s.days_overdue,
            sent_message_id=sent_id,
        )

    pending = [s for s in rows if s.needs_collection]
    if not pending:
        send_sms_now(LANDLORD_THREAD, f"✅ {month} rent is fully collected.")
        return

    lines = [f"💰 {month} rent — {len(pending)} unit(s) need attention", ""]
    for s in pending:
        gap = s.expected_amount - s.found_amount
        # Distinguish "sent" from "couldn't send" — the summary has to reflect
        # reality, or the landlord assumes everyone was notified when someone
        # never received anything.
        mark = "collection sent" if get_thread(s.room_id) else "⚠️ no SMS thread, not sent"
        lines.append(
            f"- {s.room_id} {s.tenant_name}: due {s.due_date}, "
            f"{s.days_overdue} day(s) overdue, ${gap:.2f} short — {mark}"
        )
    blocked = [s for s in pending if not get_thread(s.room_id)]
    lines.append("")
    lines.append(f"{len(pending) - len(blocked)} sent automatically.")
    if blocked:
        lines.append(
            f"{len(blocked)} could not be sent: Voice can only reply to an existing "
            f"thread, and these tenants have never texted your Voice number. "
            f"You'll need to contact them manually."
        )
    send_sms_now(LANDLORD_THREAD, "\n".join(lines))


if __name__ == "__main__":
    import sys
    task = sys.argv[1] if len(sys.argv) > 1 else "poll"
    asyncio.run({"poll": poll, "digest": digest, "rent": rent_cycle}[task]())
