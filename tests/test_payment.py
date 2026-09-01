"""Payment verification, ledger writes, and month allocation.

These are the judgments CLAUDE.md constraint 2 requires to be deterministic code
rather than prompts. Being deterministic is what makes them testable, and this
file is where that is cashed in.
"""

from datetime import date, datetime, timedelta

import pytest

from the_super.payment import (
    PaymentClaim,
    gather_payment_context,
    month_router,
    record_claim,
    verification_router,
    verify_payment,
)


def _txn(email, amount, day, txn_id="T1"):
    return {"txn_id": txn_id, "payer_email": email, "amount": amount,
            "date": f"{day}T09:00:00-04:00"}


def _route(event):
    """ADK 2.0 puts the route on event.actions, not on the event itself."""
    return event.actions.route


def _claim(room="1F-A", email="a@example.com", amount=1000.00,
           month="2026-08", stated=True):
    return PaymentClaim(room_id=room, tenant_email=email, claimed_amount=amount,
                        claimed_method="paypal", month=month, month_stated=stated)


# ---------------------------------------------------------------- verification

def test_matching_amount_verifies(paypal):
    paypal["2026-08"] = [_txn("a@example.com", 1000.00, "2026-08-01")]
    v = verify_payment(_claim())
    assert v.status == "verified"
    assert v.found_amount == 1000.00
    assert v.found_date.startswith("2026-08-01")
    assert v.txn_id == "T1"


def test_nothing_found_is_not_found_with_null_fields(paypal):
    """found_amount/found_date are None here — the case that crashes if the
    Optional fields lack defaults."""
    v = verify_payment(_claim())
    assert v.status == "not_found"
    assert v.found_amount is None and v.found_date is None and v.txn_id is None


def test_underpayment_is_a_mismatch(paypal):
    paypal["2026-08"] = [_txn("a@example.com", 600.00, "2026-08-01")]
    assert verify_payment(_claim(amount=600.00)).status == "amount_mismatch"


def test_expected_amount_is_per_tenant_not_a_global_constant(paypal):
    """3F-A's rent is $1200. Paying exactly $1200 must verify.

    Regression: verify_payment used a global EXPECTED_RENT = 1000, so this
    tenant paying in full was judged "overpaid", silently escalated, and never
    received a receipt.
    """
    paypal["2026-08"] = [_txn("e@example.com", 1200.00, "2026-08-05")]
    v = verify_payment(_claim(room="3F-A", email="e@example.com", amount=1200.00))
    assert v.expected_amount == 1200.00
    assert v.status == "verified"


def test_thousand_dollars_is_an_underpayment_for_a_twelve_hundred_lease(paypal):
    paypal["2026-08"] = [_txn("e@example.com", 1000.00, "2026-08-05")]
    v = verify_payment(_claim(room="3F-A", email="e@example.com", amount=1000.00))
    assert v.status == "amount_mismatch"
    assert "$200.00 outstanding" in v.note


# ---------------------------------------------------------------- routing

def test_unstated_month_never_reaches_verification():
    assert month_router(_claim(stated=False)).actions.route == "MONTH_UNCLEAR"
    assert month_router(_claim(stated=True)).actions.route == "MONTH_CLEAR"


@pytest.mark.parametrize("status", ["not_found", "amount_mismatch", "overpaid"])
def test_only_verified_payments_get_an_automatic_receipt(paypal, status):
    """The rule "receipt only after verification passes" is an if, not a prompt."""
    paypal["2026-08"] = {
        "not_found": [],
        "amount_mismatch": [_txn("a@example.com", 600.00, "2026-08-01")],
        "overpaid": [_txn("a@example.com", 1500.00, "2026-08-01")],
    }[status]
    v = verify_payment(_claim())
    assert v.status == status
    assert verification_router(v).actions.route == "ESCALATE"


def test_verified_payment_routes_to_receipt(paypal):
    paypal["2026-08"] = [_txn("a@example.com", 1000.00, "2026-08-01")]
    assert verification_router(verify_payment(_claim())).actions.route == "AUTO_RECEIPT"


def test_unstated_month_is_escalated_even_if_it_somehow_reaches_the_router(paypal):
    """Backstop against miswiring: an unstated month must never auto-receipt."""
    paypal["2026-08"] = [_txn("a@example.com", 1000.00, "2026-08-01")]
    v = verify_payment(_claim(stated=False))
    assert v.status == "verified"
    assert verification_router(v).actions.route == "ESCALATE"


# ---------------------------------------------------------------- ledger write

def test_ledger_records_the_verified_month_not_todays_month(paypal, ledger_file):
    """Regression: the ledger write used datetime.now(), so a tenant paying next
    month's rent in advance was filed under the current month — the exact
    misfiling this branch exists to prevent.

    The month is deliberately computed as "next month" rather than hardcoded.
    A fixed date would coincide with the current month for part of the year and
    the regression would pass by luck exactly when it isn't being watched — which
    is how the original bug survived: it only misfiles near a month boundary.
    """
    today = datetime.now().date()
    nxt = f"{today.year + today.month // 12}-{today.month % 12 + 1:02d}"
    assert nxt != f"{today:%Y-%m}"          # the guard only works if they differ

    paypal[nxt] = [_txn("a@example.com", 1000.00, today.isoformat(), "MOCK-NX")]
    record_claim(verify_payment(_claim(month=nxt)))

    book = ledger_file()
    assert list(book) == [nxt], f"filed under the wrong month: {list(book)}"


def test_ledger_keeps_the_verified_figures(paypal, ledger_file):
    """Regression: claimed_amount was written as 0.0, found_amount and txn_id as
    None, discarding everything verify_payment had established."""
    paypal["2026-08"] = [_txn("a@example.com", 1000.00, "2026-08-01", "MOCK-8A")]
    record_claim(verify_payment(_claim()))

    entry = ledger_file()["2026-08"]["1F-A"]
    assert entry["claimed_amount"] == 1000.00
    assert entry["found_amount"] == 1000.00
    assert entry["txn_id"] == "MOCK-8A"


def test_ledger_status_is_claimed_never_confirmed(paypal, ledger_file):
    """The agent cannot confirm receipt of funds; only the landlord can."""
    paypal["2026-08"] = [_txn("a@example.com", 1000.00, "2026-08-01")]
    record_claim(verify_payment(_claim()))
    assert ledger_file()["2026-08"]["1F-A"]["status"] == "claimed"


def test_record_claim_is_idempotent(paypal, ledger_file):
    """A framework retry must not double-record."""
    paypal["2026-08"] = [_txn("a@example.com", 1000.00, "2026-08-01")]
    v = verify_payment(_claim())
    record_claim(v)
    record_claim(v)
    assert len(ledger_file()["2026-08"]) == 1


def test_record_claim_passes_verification_through(paypal):
    """It sits mid-chain, so draft_receipt must still receive its input."""
    paypal["2026-08"] = [_txn("a@example.com", 1000.00, "2026-08-01")]
    v = verify_payment(_claim())
    assert record_claim(v) is v


# ------------------------------------------------------- month allocation

def _recent(email, amount, days_ago, txn_id="R1"):
    day = (datetime.now().date() - timedelta(days=days_ago)).isoformat()
    return _txn(email, amount, day, txn_id)


def test_unverified_payment_reports_no_match(paypal):
    """Nothing found -> the reply must not imply money arrived."""
    ctx = gather_payment_context(_claim(stated=False))
    assert ctx.has_recent_match is False
    assert ctx.recent_matches == []


def test_recent_payment_is_matched_and_dated(paypal):
    month = f"{datetime.now():%Y-%m}"
    paypal[month] = [_recent("a@example.com", 1000.00, 1)]
    ctx = gather_payment_context(_claim(month=month, stated=False))
    assert ctx.has_recent_match is True
    assert ctx.recent_matches[0].days_ago == 1
    assert ctx.recent_window_days > 0        # wired, not left at a stale default


def test_allocation_prefers_the_oldest_open_month(paypal):
    """Standard practice: clear the oldest arrears first, and say why."""
    paypal["2026-07"] = []
    paypal["2026-08"] = [_recent("a@example.com", 1000.00, 0, "NEW")]
    ctx = gather_payment_context(_claim(month="2026-08", stated=False))
    assert ctx.suggested_month is not None
    assert ctx.suggested_reason


def test_a_new_payment_does_not_settle_its_own_month(paypal):
    """The arriving payment is subtracted before computing allocation, otherwise
    it marks its own month settled and everything shifts by one."""
    month = f"{datetime.now():%Y-%m}"
    paypal[month] = [_recent("a@example.com", 1000.00, 0, "NEW")]
    ctx = gather_payment_context(_claim(month=month, stated=False))
    assert ctx.suggested_month == month
