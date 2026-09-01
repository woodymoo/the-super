"""Scenario player for recording the demo — not part of the application, just a
demo entry point.

    python demo.py list                  list every scenario
    python demo.py pay-ok                run one message scenario
    python demo.py rent 2026-08-02       simulate the rent cycle on a given day
    python demo.py all                   run every message scenario in order

Message scenarios are always built with **fake numbers and fake emails**; they
never touch the real roster, so a screen recording leaks no PII.
Dates are injected as parameters (today is a parameter of check_rent), so there
is no need to change the system clock.
"""

import asyncio
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "the_super" / ".env")

from google.adk.runners import InMemoryRunner          # noqa: E402
from google.genai import types                          # noqa: E402

from the_super.agent import root_agent                  # noqa: E402
from the_super.rent import build_collection_sms, check_rent  # noqa: E402
from the_super.schemas import IncomingMessage           # noqa: E402

# room_id uses a real room from the roster, but sender/email are fake — the demo
# does not need real contact details
SCENARIOS = {
    "pay-ok": ("💰 Payment - month stated + amount matches -> draft the receipt",
               "1F-A", "Hi, I just sent $1000 for September rent via PayPal."),
    "pay-nomonth": ("❓ Payment - no month stated -> auto-send a confirming text, no receipt",
                    "1F-A", "Hi, I just sent you the rent via PayPal."),
    "pay-short": ("💰 Payment - underpaid -> no receipt, escalate to a human",
                  "1F-B", "Just PayPal'd you $600 for September rent. Money is "
                          "tight this month, I'll send the rest next week."),
    "pay-none": ("💰 Payment - nothing found in the ledger -> escalate to a human",
                 "2F-A", "I sent September rent yesterday, should be there by now."),
    "pay-vague": ("🤔 Vague - low confidence -> safety valve, escalate to a human",
                  "2F-B", "Hey, can we talk about the money stuff sometime?"),
    "legal": ("⚖️ Legal matter -> holding reply, no position taken",
              "2F-A", "My lawyer says the security deposit should have been "
                      "returned within 14 days. I want it back this week."),
    "lease": ("📄 Lease matter -> holding reply, hand to the landlord",
              "2F-B", "I'm thinking about moving out early. What happens to "
                      "my deposit if I break the lease?"),
    "fix-vague": ("🔧 Repair - description unclear -> ask for photos",
                  "3F-A", "The toilet is leaking."),
    "fix-clear": ("🔧 Repair - description clear -> produce the dispatch brief",
                  "3F-A", "Water is seeping from the seam where the toilet base "
                          "meets the bathroom floor. Started last night, there's "
                          "a small puddle on the floor now."),
    "fix-urgent": ("🔧 Repair - emergency -> urgent severity",
                   "1F-A", "The kitchen pipe burst, water is spraying everywhere. "
                           "I shut off the main valve."),
}


def make_message(room_id: str, body: str) -> IncomingMessage:
    """Build one incoming text. sender/email are entirely fake."""
    idx = "ABCDE"[["1F-A", "1F-B", "2F-A", "2F-B", "3F-A"].index(room_id)]
    return IncomingMessage(
        source="sms",
        gmail_thread_id=f"DEMO-THREAD-{room_id}",
        gmail_message_id=f"DEMO-MSG-{room_id}",
        sender=f"+1917555010{idx and '12345'[ord(idx)-65]}",
        room_id=room_id,
        tenant_email=f"tenant.{idx.lower()}@example.com",
        body=body,
        received_at=datetime.now(timezone.utc).isoformat(),
    )


async def run_message(key: str) -> None:
    title, room_id, body = SCENARIOS[key]
    print(f"\n{'='*66}\n{title}\n{'='*66}")
    print(f"📱 Text from {room_id}:\n   \u201c{body}\u201d\n")
    print("─" * 66)

    msg = make_message(room_id, body)
    runner = InMemoryRunner(agent=root_agent, app_name="demo")
    await runner.session_service.create_session(
        app_name="demo", user_id=room_id, session_id=key)

    async for ev in runner.run_async(
            user_id=room_id, session_id=key,
            new_message=types.Content(
                role="user", parts=[types.Part(text=msg.model_dump_json())])):
        if ev.content and ev.content.parts:
            for p in ev.content.parts:
                if p.text and p.text.strip():
                    print(f"🤖 {p.text.strip()}\n")


def run_rent(day: str) -> None:
    today = date.fromisoformat(day)
    month = f"{today:%Y-%m}"
    print(f"\n{'='*66}\n📅 If today were {today} (each unit on its own lease due day)\n{'='*66}")
    for s in check_rent(month, today):
        mark = "🔔 collection triggered" if s.needs_collection else ""
        print(f"  {s.room_id} {s.tenant_name:9} due {s.due_date}  "
              f"{s.days_overdue:>2} day(s) late  "
              f"received ${s.found_amount:>7.2f}/{s.expected_amount:.0f}  "
              f"{s.status:<17}{mark}")
    pending = [s for s in check_rent(month, today) if s.needs_collection]
    if pending:
        print(f"\n──── collection text for {pending[0].room_id} ────")
        print(build_collection_sms(pending[0]))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print("Message scenarios:")
        for k, (t, r, b) in SCENARIOS.items():
            print(f"  {k:12} {t}")
        print("\nRent timeline:  python demo.py rent 2026-08-02")
    elif cmd == "rent":
        run_rent(sys.argv[2] if len(sys.argv) > 2 else str(date.today()))
    elif cmd == "all":
        for k in SCENARIOS:
            asyncio.run(run_message(k))
    else:
        asyncio.run(run_message(cmd))
