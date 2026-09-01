"""Persistence layer — the ledger and the tickets.

Local development uses JSON files; on Cloud Run this becomes Firestore.
A Cloud Run container can be reclaimed at any time and in-memory state is always
lost, so state has to live outside the process.

Switching between them is the single USE_LOCAL flag.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

USE_LOCAL = True
DATA_DIR = Path(__file__).parent.parent / "fixtures"
LEDGER_FILE = DATA_DIR / "ledger.json"
TICKETS_FILE = DATA_DIR / "tickets.json"

MEDIA_WAIT_HOURS = 48   # remind if photos still haven't arrived after this long


def _load(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- Ledger

def write_ledger(month: str, room_id: str, claimed_amount: float,
                 found_amount: float | None, status: str,
                 txn_id: str | None = None) -> None:
    """Record a payment.

    status: claimed | confirmed | disputed | missing

    Note that claimed and confirmed are two distinct states. A tenant saying they
    paid is not the landlord confirming it — that distinction can matter a great
    deal in a tenancy dispute, so do not merge them.
    """
    ledger = _load(LEDGER_FILE, {})
    ledger.setdefault(month, {})[room_id] = {
        "claimed_amount": claimed_amount,
        "found_amount": found_amount,
        "txn_id": txn_id,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(LEDGER_FILE, ledger)


def get_ledger(month: str) -> dict:
    return _load(LEDGER_FILE, {}).get(month, {})


def get_unpaid_rooms(month: str, all_rooms: list[str]) -> list[str]:
    """For the rent cycle: rooms with no payment claim yet this month."""
    # An allowlist rather than a denylist: only an explicitly settled status
    # counts as "no collection needed". This used to treat only
    # `status == "missing"` as unpaid, which silently dropped disputed rooms —
    # exactly the ones most in need of follow-up — out of the collection queue.
    settled = {"claimed", "confirmed"}
    paid = get_ledger(month)
    return [r for r in all_rooms
            if r not in paid or paid[r].get("status") not in settled]


# ---------------------------------------------------------------- Tickets

def write_ticket(status: str, room_id: str | None = None,
                 draft: dict | None = None, draft_sms: str | None = None,
                 ticket_id: str | None = None, **fields) -> str:
    """Create or update a ticket, returning the ticket_id.

    status: open | awaiting_media | ready_to_dispatch | dispatched | closed
    """
    tickets = _load(TICKETS_FILE, {})
    tid = ticket_id or f"T-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(timezone.utc)

    record = tickets.get(tid, {"ticket_id": tid, "opened_at": now.isoformat()})
    record.update({
        "status": status,
        "room_id": room_id or record.get("room_id"),
        "updated_at": now.isoformat(),
        **fields,
    })
    if draft:
        record["draft"] = draft
    if draft_sms:
        record["draft_sms"] = draft_sms
    if status == "awaiting_media":
        record["media_deadline"] = (
            now + timedelta(hours=MEDIA_WAIT_HOURS)
        ).isoformat()

    tickets[tid] = record
    _save(TICKETS_FILE, tickets)
    return tid


def get_ticket_history(room_id: str, limit: int = 10) -> list[dict]:
    """This room's prior tickets — used to spot repeat problems.

    The same spot failing repeatedly is a completely different situation from the
    first occurrence, and the brief has to put that in front of the landlord.
    """
    tickets = _load(TICKETS_FILE, {})
    matched = [t for t in tickets.values() if t.get("room_id") == room_id]
    matched.sort(key=lambda t: t.get("opened_at", ""), reverse=True)
    return matched[:limit]


def get_overdue_media_tickets() -> list[dict]:
    """Tickets still waiting on photos past the deadline — surfaced in the daily digest.

    This is the correct way to implement "waiting": the state lives here and is
    checked when Cloud Scheduler wakes the next round, rather than having the
    agent loop and wait internally (which keeps a container alive and burns money).
    """
    now = datetime.now(timezone.utc).isoformat()
    tickets = _load(TICKETS_FILE, {})
    return [t for t in tickets.values()
            if t.get("status") == "awaiting_media"
            and t.get("media_deadline", "") < now]


# --------------------------------------------------------- Outbound thread registry

THREADS_FILE = DATA_DIR / "threads.json"


def remember_thread(room_id: str, gmail_thread_id: str) -> None:
    """Remember a tenant's most recent Voice email thread.

    Voice can only send a text by replying to an existing thread; it has no
    interface for texting a number out of the blue. Collection runs on a
    schedule with no incoming message to reply to, so the thread has to be
    recorded when mail arrives.
    """
    d = _load(THREADS_FILE, {})
    d[room_id] = {"thread_id": gmail_thread_id,
                  "updated_at": datetime.now(timezone.utc).isoformat()}
    _save(THREADS_FILE, d)


def get_thread(room_id: str) -> str | None:
    return (_load(THREADS_FILE, {}).get(room_id) or {}).get("thread_id")
