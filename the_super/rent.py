"""Rent-cycle collection — entirely deterministic code, with no model call.

CLAUDE.md constraint 2: judgments with financial or legal consequences must be
written as deterministic code. For a collection text **both the trigger condition
and the copy are legally sensitive**, so even the wording is a template rather
than model output — a model rewords it every time, and this kind of text may
later serve as evidence.

Timeline (each tenant follows their own lease's due day, not one global date):
    On the due date        — not late
    Due date + 1 day       — still not paid in full -> draft the collection text with a 5-day cure period
    End of the cure period — the rent is now 5 full days overdue, and only then does the landlord qualify for the 14-day notice process

⚠️ This module only **drafts**. Sending to the tenant is handled by the caller,
   and no legal notice is generated here — a 14-day notice has statutory service
   requirements, an SMS generally does not constitute valid service, and that
   step must happen offline.
"""

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from .payment import _lookup_transactions
from .tenants import days_overdue, load_tenants, rent_due_date

# Collection starts the day after the due date
COLLECTION_TRIGGER_DAYS = 1

# The cure period the collection text grants. The 5 is not arbitrary: the
# landlord may start the 14-day notice process only after the rent is 5 full days
# overdue, so a 5-day cure period both gives the tenant a real chance and lines
# the timeline up with the next step.
CURE_PERIOD_DAYS = 5


class RentStatus(BaseModel):
    """One tenant's rent status for one month. Facts only, no actions."""
    room_id: str
    tenant_name: str
    tenant_email: str
    month: str
    due_date: str                       # ISO date
    days_overdue: int
    expected_amount: float
    found_amount: float
    status: Literal["not_yet_due", "paid_full", "underpaid", "nothing_received"]
    needs_collection: bool
    # ⚠️ This is exactly None when no collection is due, so it must have a default
    cure_deadline: str | None = None    # cure deadline


def check_one(tenant: dict, month: str, today: date) -> RentStatus:
    """One tenant's rent status. A pure function, so it is unit-testable."""
    due = rent_due_date(tenant, month)
    overdue = days_overdue(tenant, month, today)
    expected = float(tenant["rent_amount"])

    # The amount comes from that tenant's own rent_amount, not a global constant —
    # different leases may carry different amounts.
    txns = _lookup_transactions(tenant["email"], month)
    found = round(sum(t["amount"] for t in txns), 2)

    if overdue < COLLECTION_TRIGGER_DAYS:
        status = "paid_full" if found >= expected else "not_yet_due"
    elif found >= expected:
        status = "paid_full"
    elif found > 0:
        status = "underpaid"
    else:
        status = "nothing_received"

    needs = status in ("underpaid", "nothing_received")
    return RentStatus(
        room_id=tenant["room_id"],
        tenant_name=tenant["name"],
        tenant_email=tenant["email"],
        month=month,
        due_date=due.isoformat(),
        days_overdue=overdue,
        expected_amount=expected,
        found_amount=found,
        status=status,
        needs_collection=needs,
        cure_deadline=(today + timedelta(days=CURE_PERIOD_DAYS)).isoformat()
                      if needs else None,
    )


def check_rent(month: str, today: date) -> list[RentStatus]:
    """Rent status for every tenant, most overdue first."""
    rows = [check_one(t, month, today) for t in load_tenants()]
    return sorted(rows, key=lambda r: -r.days_overdue)


def build_collection_sms(s: RentStatus) -> str:
    """The body of the collection text — a deterministic template.

    Deliberate choices:
    - No legal threat language of any kind. This text exists to remind and to
      leave a record, not to serve notice.
    - Explicit room for "you may already have paid" — a PayPal eCheck takes up to
      3 business days to clear, so the tenant may genuinely have paid before we
      can see it.
    - For an underpayment, state the shortfall plainly.
    """
    if s.status == "underpaid":
        detail = (f"We received ${s.found_amount:.2f}, which is "
                  f"${s.expected_amount - s.found_amount:.2f} short of the "
                  f"${s.expected_amount:.2f} due.")
    else:
        detail = (f"We have not received your {s.month} rent payment of "
                  f"${s.expected_amount:.2f}.")

    return (
        f"Hi {s.tenant_name},\n\n"
        f"Your {s.month} rent was due on {s.due_date}. {detail}\n\n"
        f"Please complete payment by {s.cure_deadline}.\n\n"
        f"If you have already paid, please reply with the payment method and "
        f"date. PayPal eChecks can take up to 3 business days to clear, and "
        f"we will re-check."
    )
