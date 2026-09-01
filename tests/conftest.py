"""Test fixtures.

Every test here is deterministic: no network, no model calls, no system clock.
`today` is a parameter of check_rent/check_one, and PayPal lookups go through
_lookup_transactions, so both are injectable by design — this suite is what makes
that design pay off.
"""

import json

import pytest

from the_super import payment, rent, tenants
from the_super.tools import store


ROSTER = [
    {"room_id": "1F-A", "name": "Tenant A", "phone": "+1 (917) 555-0101",
     "email": "a@example.com", "rent_amount": 1000.00, "rent_due_day": 1},
    {"room_id": "2F-A", "name": "Tenant C", "phone": "+1 (917) 555-0103",
     "email": "c@example.com", "rent_amount": 1000.00, "rent_due_day": 3},
    # Deliberately not $1000: guards the per-tenant amount rule.
    {"room_id": "3F-A", "name": "Tenant E", "phone": "+1 (917) 555-0105",
     "email": "e@example.com", "rent_amount": 1200.00, "rent_due_day": 5},
]


@pytest.fixture(autouse=True)
def roster(monkeypatch):
    """Use a fixed roster, never the real (gitignored) tenants.json."""
    monkeypatch.setattr(tenants, "load_tenants", lambda: ROSTER)
    return ROSTER


@pytest.fixture
def paypal(monkeypatch):
    """Injectable PayPal ledger: {month: [txn, ...]}.

    Patches the single mock seam (_lookup_transactions) that CLAUDE.md
    constraint 5 requires all PayPal access to go through.

    ⚠️ It must be patched in BOTH modules. rent.py does
    `from .payment import _lookup_transactions`, which binds its own name at
    import time, so patching payment alone leaves rent calling the real fixture
    file and every rent test silently sees "nothing received".
    """
    book: dict[str, list[dict]] = {}

    def fake_lookup(payer_email: str, month: str) -> list[dict]:
        return [t for t in book.get(month, [])
                if t["payer_email"].lower() == payer_email.lower()]

    monkeypatch.setattr(payment, "_lookup_transactions", fake_lookup)
    monkeypatch.setattr(rent, "_lookup_transactions", fake_lookup)
    return book


@pytest.fixture(autouse=True)
def ledger_file(tmp_path, monkeypatch):
    """Point the ledger at a temp file so tests never touch fixtures/ledger.json.

    ⚠️ autouse deliberately. This started as an opt-in fixture, and one test that
    called record_claim without requesting it wrote straight into the app's real
    fixtures/ledger.json — corrupting demo state and, worse, feeding a stale month
    back into gather_payment_context so an unrelated allocation test failed. Any
    test that reaches a write must be redirected, whether or not it asks to be.
    """
    path = tmp_path / "ledger.json"
    monkeypatch.setattr(store, "LEDGER_FILE", path)

    def read() -> dict:
        return json.loads(path.read_text()) if path.exists() else {}

    return read
