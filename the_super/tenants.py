"""Tenant roster — the single source of truth for tenant data and due dates.

Tenant identification used to live in tools/gmail.py (the channel layer), but the
collection logic needs the same data and the same phone-normalization rules.
Two separate readers would drift apart eventually, so it is centralized here.

⚠️ fixtures/tenants.json contains real PII and is gitignored.
   What is committed to the repo is tenants.json.template.
"""

import json
import re
from calendar import monthrange
from datetime import date
from pathlib import Path

TENANTS_FILE = Path(__file__).parent / "fixtures" / "tenants.json"


def load_tenants() -> list[dict]:
    """Read the roster. A missing file raises — infrastructure failures are not swallowed (see CLAUDE.md)."""
    with open(TENANTS_FILE) as f:
        return json.load(f)["tenants"]


def get_tenant(room_id: str) -> dict | None:
    return next((t for t in load_tenants() if t["room_id"] == room_id), None)


# ------------------------------------------------------------ Identification

def normalize_phone(phone: str) -> str:
    """+1 (917) 555-0101 → 9175550101"""
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else digits


def identify_tenant(phone_or_email: str) -> dict | None:
    """Resolve a phone number or email address to a specific tenant.

    This is the key to cross-channel correlation: texts arrive from a number,
    photos from a mailbox, and both resolve to the same person.

    Handles both shapes of a Gmail From header:
        "Tenant A <tenant.a@example.com>"   -> extract the address in the brackets
        "tenant.a@example.com"
    """
    needle = phone_or_email.strip().lower()

    # A Gmail From header looks like this; without stripping it, every lookup fails
    bracketed = re.search(r"<([^>]+)>", needle)
    if bracketed:
        needle = bracketed.group(1).strip()

    needle_digits = normalize_phone(needle)
    for t in load_tenants():
        if t["email"].strip().lower() == needle:
            return t
        # Phone comparison requires a full 10 digits on both sides, otherwise an empty string false-matches
        if needle_digits and len(needle_digits) == 10 \
                and normalize_phone(t["phone"]) == needle_digits:
            return t
    return None


# ---------------------------------------------------------------- Due dates

def rent_due_date(tenant: dict, month: str) -> date:
    """That tenant's due date in a given month. month is formatted "2026-09".

    Each tenant's lease has its own due day (rent_due_day); it is not a global
    constant. When the due day exceeds the length of the month (February has no
    30th), it falls back to the last day of that month.
    """
    year, mon = (int(x) for x in month.split("-"))
    day = int(tenant.get("rent_due_day", 1))
    return date(year, mon, min(day, monthrange(year, mon)[1]))


def days_overdue(tenant: dict, month: str, today: date) -> int:
    """Days overdue. Zero on and before the due date (the due date itself is not late)."""
    return max(0, (today - rent_due_date(tenant, month)).days)
