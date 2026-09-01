"""Rent-cycle timing and collection copy."""

from datetime import date

import pytest

from the_super.rent import build_collection_sms, check_one
from the_super.tenants import days_overdue, rent_due_date


# ---------------------------------------------------------------- due dates

def test_due_day_is_per_tenant_not_global(roster):
    """1F-A is due on the 1st and 3F-A on the 5th; there is no building-wide date."""
    assert rent_due_date(roster[0], "2026-08") == date(2026, 8, 1)
    assert rent_due_date(roster[2], "2026-08") == date(2026, 8, 5)


def test_due_day_clamps_to_short_month(roster):
    """A lease due on the 30th has no such day in February."""
    feb = dict(roster[0], rent_due_day=30)
    assert rent_due_date(feb, "2026-02") == date(2026, 2, 28)


def test_due_date_itself_is_not_late(roster):
    assert days_overdue(roster[0], "2026-08", date(2026, 8, 1)) == 0
    assert days_overdue(roster[0], "2026-08", date(2026, 8, 2)) == 1


# ---------------------------------------------------------------- collection

def _txn(email, amount, day="2026-08-01"):
    return {"txn_id": "T1", "payer_email": email, "amount": amount,
            "date": f"{day}T09:00:00-04:00"}


def test_no_collection_on_the_due_date(roster, paypal):
    s = check_one(roster[0], "2026-08", date(2026, 8, 1))
    assert s.status == "not_yet_due"
    assert s.needs_collection is False
    assert s.cure_deadline is None      # Optional field, None on the happy path


def test_collection_triggers_the_day_after(roster, paypal):
    s = check_one(roster[0], "2026-08", date(2026, 8, 2))
    assert s.status == "nothing_received"
    assert s.needs_collection is True


def test_cure_period_is_five_days_from_the_trigger(roster, paypal):
    """The cure window follows the trigger date, not the due date — expiring
    exactly when the rent is 5 full days overdue, which is what the 14-day
    notice process requires."""
    s = check_one(roster[0], "2026-08", date(2026, 8, 2))
    assert s.cure_deadline == "2026-08-07"
    assert days_overdue(roster[0], "2026-08", date(2026, 8, 7)) == 6


def test_full_payment_stops_collection(roster, paypal):
    paypal["2026-08"] = [_txn("a@example.com", 1000.00)]
    s = check_one(roster[0], "2026-08", date(2026, 8, 9))
    assert s.status == "paid_full"
    assert s.needs_collection is False


def test_partial_payment_still_collects(roster, paypal):
    paypal["2026-08"] = [_txn("a@example.com", 600.00)]
    s = check_one(roster[0], "2026-08", date(2026, 8, 9))
    assert s.status == "underpaid"
    assert s.needs_collection is True


def test_amount_due_is_per_tenant(roster, paypal):
    """3F-A owes $1200. Paying $1000 is underpaid, not paid in full."""
    paypal["2026-08"] = [_txn("e@example.com", 1000.00)]
    s = check_one(roster[2], "2026-08", date(2026, 8, 9))
    assert s.expected_amount == 1200.00
    assert s.status == "underpaid"


# ---------------------------------------------------------------- copy

def test_collection_sms_has_no_legal_threats(roster, paypal):
    sms = build_collection_sms(check_one(roster[0], "2026-08", date(2026, 8, 2)))
    banned = ["legal", "evict", "attorney", "court", "notice to quit",
              "final warning", "or else", "lease violation"]
    assert not [w for w in banned if w in sms.lower()]


def test_collection_sms_states_all_three_numbers_when_short(roster, paypal):
    """Received / due / gap — the tenant should never have to do the arithmetic."""
    paypal["2026-08"] = [_txn("a@example.com", 600.00)]
    sms = build_collection_sms(check_one(roster[0], "2026-08", date(2026, 8, 9)))
    assert "$600.00" in sms and "$1000.00" in sms and "$400.00" in sms


def test_collection_sms_leaves_room_for_a_payment_in_flight(roster, paypal):
    sms = build_collection_sms(check_one(roster[0], "2026-08", date(2026, 8, 2)))
    assert "already paid" in sms.lower()
    assert "3 business days" in sms
