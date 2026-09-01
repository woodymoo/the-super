"""
The Super — payment verification branch (ADK 2.0 graph workflow)

Mock PayPal tooling plus the graph wiring.
When the real PayPal Transaction Search API goes live, only the single function
_lookup_transactions() needs replacing.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from google.adk import Agent, Event, Workflow
from google.adk.agents.context import Context

from .schemas import IncomingMessage
from .skills_registry import TENANT_SMS_SKILL
from .tools.gmail import draft_sms_reply, send_sms_now
from .tools.store import get_ledger, write_ledger
from .tenants import get_tenant, rent_due_date

FIXTURES = Path(__file__).parent / "fixtures" / "paypal_transactions.json"
USE_MOCK = True  # set to False when going live


# ------------------------------------------------------------ Data contracts
# These three schemas are the type contract for data passed between nodes.
# ADK 2.0 forwards each node's return value to the next one automatically;
# there is no session state to write.

class PaymentClaim(BaseModel):
    """A payment as claimed in the tenant's text."""
    room_id: str
    tenant_email: str
    claimed_amount: float
    claimed_method: str          # "paypal" / "zelle" / ...
    month: str                   # "2026-09"
    month_stated: bool           # did the tenant **explicitly say** which month


class RecentTxn(BaseModel):
    """A recently found deposit. Used to verify the specific claim "I just sent"."""
    txn_id: str
    amount: float
    date: str                             # ISO
    days_ago: int
    month_bucket: str                     # which ledger month this payment falls in


class MonthStatus(BaseModel):
    """The full picture for one month. Every field is a fact looked up by code, not an inference."""
    month: str
    expected: float
    # claimed (tenant asserts) / confirmed (landlord confirms) / disputed / missing / None (no record)
    # ⚠️ claimed != confirmed. When writing the text, never render claimed as "we received your rent".
    ledger_status: str | None = None
    paypal_found: float = 0.0             # total found in PayPal for that month
    settled: bool = False                 # the ledger total reaches the amount due -> no need to ask about this month


class PaymentContext(BaseModel):
    """The facts the model needs in order to ask "which month is this rent for".

    All of it is looked up by code. The model only organizes these facts into one
    precise question — it does not look anything up, does not judge, and may not
    invent facts that aren't here.
    """
    room_id: str
    tenant_name: str
    claimed_amount: float                 # how much the tenant says they sent
    expected_amount: float                # that tenant's monthly rent
    amount_matches_rent: bool             # whether the claimed amount equals exactly one month's rent
    guessed_month: str                    # the extraction node's best guess

    # —— Verify the tenant's **specific claim**: "I just sent X" implies a deposit
    # of X arrived recently. Skipping this check means accepting their account by
    # default, and that is exactly the gap a false payment claim exploits.
    recent_matches: list[RecentTxn] = []   # recent deposits with a matching amount
    has_recent_match: bool = False         # was one found
    recent_window_days: int = 5

    months: list[MonthStatus] = []        # the picture for recent months (ascending)
    unsettled_months: list[str] = []      # months not yet settled (ascending) — internal information

    # —— Which month this payment books to: computed by a deterministic rule, not
    # left to the model. Allocation affects days-overdue counts and whether the
    # statutory track applies, so it is a consequential judgment (CLAUDE.md constraint 2).
    suggested_month: str | None = None    # the month it should apply to
    suggested_reason: str = ""            # why — goes into the text so the tenant can check it
    suggestion_is_certain: bool = False   # only one possibility -> state it; several -> let the tenant choose
    # The most recent settled month; stepping one month past it is a common but **unreliable** guess
    last_settled_month: str | None = None


class PaymentVerification(BaseModel):
    """The conclusion after comparing against the ledger."""
    room_id: str
    month: str
    month_stated: bool
    claimed_amount: float
    expected_amount: float
    # ⚠️ These are exactly the fields that are None when nothing is found; without
    # defaults the not_found branch is guaranteed to crash
    found_amount: float | None = None
    found_date: str | None = None    # date of the first deposit (ISO), quoted in the receipt
    txn_id: str | None = None
    status: Literal["verified", "amount_mismatch", "not_found", "overpaid"]
    note: str


# ---------------------------------------------------------------- Mock layer
# The only fake thing lives in this function. The signature is deliberately
# shaped like the real API, so it can later become
# requests.get(PAYPAL_TRANSACTION_SEARCH_URL, ...).

def _lookup_transactions(payer_email: str, month: str) -> list[dict]:
    """Look up deposits from a given payer in a given month.

    The mock version reads fixtures/paypal_transactions.json.
    The real version calls the PayPal Transaction Search API v1.
    """
    if not USE_MOCK:
        raise NotImplementedError("implement this when wiring up the real PayPal API")

    with open(FIXTURES) as f:
        ledger = json.load(f)

    return [
        txn for txn in ledger.get(month, [])
        if txn["payer_email"].lower() == payer_email.lower()
    ]


def month_router(node_input: PaymentClaim):
    """Did the tenant say clearly which month this is for?

    If not, it cannot enter the verification flow — we don't know which month to
    book it to, and verifying "September rent" and "August rent" are two entirely
    different questions.
    """
    if node_input.month_stated:
        return Event(route="MONTH_CLEAR", output=node_input)
    return Event(route="MONTH_UNCLEAR", output=node_input)


def gather_payment_context(node_input: PaymentClaim) -> PaymentContext:
    """Look up the facts needed to ask about the month. Pure code; no model call.

    Rent is paid in advance, so money sent at the end of a month is usually for
    the next one. Guessing that money sent on 8/31 is for August throws the whole
    ledger out of alignment — and a ledger with the wrong month is a serious
    liability in a tenancy dispute.

    So rather than asking a bare "which month?", look at what the ledger already
    holds. After verification, "we see your $1,000 from August 30; August is
    already settled, so we'll apply this to September" lets the tenant confirm in
    one word.

    ⚠️ PayPal is checked here as well (the landlord's requirement: check the books
       at the same time as asking about the month). The result goes to the
       landlord, but it does **not** auto-generate a receipt.
    """
    tenant = get_tenant(node_input.room_id) or {}
    expected = float(tenant.get("rent_amount", EXPECTED_RENT))

    # The candidate window can't be arbitrary. Only two kinds of month can be
    # where this money belongs:
    #   · already due but unsettled (the tenant is clearing arrears)
    #   · the next month (rent is prepaid, so the tenant is paying early)
    # Months beyond that aren't due yet, and reaching further back than the
    # records go is meaningless — counting them as "unsettled" makes the agent
    # ask absurd questions.
    rows: list[MonthStatus] = []
    for m in _candidate_months(node_input):
        entry = get_ledger(m).get(node_input.room_id) or {}
        txns = _lookup_transactions(node_input.tenant_email, m)
        found = round(sum(t["amount"] for t in txns), 2)
        rows.append(MonthStatus(
            month=m,
            expected=expected,
            ledger_status=entry.get("status"),
            paypal_found=found,
            # A month counts as settled only when the books really cover it. A
            # ledger entry saying claimed with nothing found in PayPal is not
            # settled — that is precisely the "tenant says they paid but the
            # money never arrived" case.
            settled=found >= expected,
        ))

    recent = find_recent_payments(
        node_input.tenant_email, node_input.claimed_amount, node_input.month)

    # When computing allocation, **subtract this newly arrived payment** —
    # otherwise it marks its own month as "settled", the system suggests the
    # month after that, and everything shifts by one.
    recent_by_month: dict[str, float] = {}
    for r in recent:
        recent_by_month[r.month_bucket] = recent_by_month.get(r.month_bucket, 0) + r.amount

    today = datetime.now().date()
    tenant_rec = get_tenant(node_input.room_id) or {}
    due_unsettled, future_unsettled = [], []
    for r in rows:
        before_this = r.paypal_found - recent_by_month.get(r.month, 0)
        if before_this >= r.expected:
            continue                      # already settled before this payment; not a candidate
        if tenant_rec and rent_due_date(tenant_rec, r.month) <= today:
            due_unsettled.append(r.month)
        else:
            future_unsettled.append(r.month)

    # Standard accounting practice: clear the oldest arrears first; if nothing is
    # owed, it prepays the next month due.
    candidates = due_unsettled or future_unsettled[:1]
    suggested = candidates[0] if candidates else None
    if suggested and due_unsettled:
        reason = f"{suggested} is the oldest month still open"
    elif suggested:
        reason = f"{suggested} rent is the next one due"
    else:
        reason = ""

    unsettled = [r.month for r in rows if not r.settled]
    settled = [r.month for r in rows if r.settled]

    return PaymentContext(
        room_id=node_input.room_id,
        tenant_name=tenant.get("name", node_input.room_id),
        claimed_amount=node_input.claimed_amount,
        expected_amount=expected,
        amount_matches_rent=abs(node_input.claimed_amount - expected) < 0.01,
        guessed_month=node_input.month,
        recent_matches=recent,
        has_recent_match=bool(recent),
        suggested_month=suggested,
        suggested_reason=reason,
        suggestion_is_certain=len(due_unsettled) <= 1,
        months=rows,
        unsettled_months=unsettled,
        last_settled_month=settled[-1] if settled else None,
    )


def _candidate_months(claim: PaymentClaim, lookback: int = 6) -> list[str]:
    """The months this payment could belong to, ascending.

    The upper bound is the month **after** the guessed one (prepayment); anything
    beyond that isn't due yet. The lower bound is the earliest month on record —
    the earliest with a ledger entry or a PayPal deposit. Ancient months with no
    record at all shouldn't appear as candidates; the tenant may not even have
    moved in yet.
    """
    upper = _shift_month(claim.month, 1)
    window = [_shift_month(claim.month, d) for d in range(-lookback, 2)]

    earliest = None
    for m in window:
        has_ledger = claim.room_id in get_ledger(m)
        has_paypal = bool(_lookup_transactions(claim.tenant_email, m))
        if has_ledger or has_paypal:
            earliest = m
            break

    start = earliest or claim.month
    return [m for m in window if start <= m <= upper]


RECENT_WINDOW_DAYS = 5      # a reasonable window for "I just sent it". A PayPal eCheck takes up to 3 business days


def find_recent_payments(tenant_email: str, amount: float, around: str,
                         within_days: int = RECENT_WINDOW_DAYS) -> list[RecentTxn]:
    """Find recent deposits with a matching amount — verifying the specific claim "I just sent X".

    This is **deterministic verification**, not material for the model to weigh.
    Not found is not found, and in that case nothing may be said to the tenant
    that implies "we received it".

    ⚠️ Data still comes only through _lookup_transactions(); the mock does not
       leak outward (CLAUDE.md constraint 5).
    """
    today = datetime.now().date()
    out: list[RecentTxn] = []
    for m in (_shift_month(around, -1), around, _shift_month(around, 1)):
        for txn in _lookup_transactions(tenant_email, m):
            if abs(txn["amount"] - amount) >= 0.01:
                continue
            paid = datetime.fromisoformat(txn["date"]).date()
            days = (today - paid).days
            if 0 <= days <= within_days:
                out.append(RecentTxn(txn_id=txn["txn_id"], amount=txn["amount"],
                                     date=txn["date"], days_ago=days, month_bucket=m))
    return sorted(out, key=lambda x: x.days_ago)


def _shift_month(month: str, delta: int) -> str:
    y, m = (int(x) for x in month.split("-"))
    mm = m + delta
    return f"{y + (mm - 1) // 12}-{(mm - 1) % 12 + 1:02d}"


def _recent_months(around: str, span: int = 3) -> list[str]:
    """The months either side of `around`, ascending."""
    y, m = (int(x) for x in around.split("-"))
    out = []
    for delta in range(-span, span + 1):
        mm = m + delta
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        out.append(f"{yy}-{mm:02d}")
    return out


def send_month_question(node_input: str, ctx: Context):
    """Send the month question — a zero-risk action, so it may be sent automatically."""
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    send_sms_now(original.gmail_thread_id, node_input)
    return Event(
        message=(f"❓ {original.room_id} claimed a rent payment without saying which month\n"
                 f"A confirmation text was sent automatically. The ledger lookup is "
                 f"recorded; verification waits for the tenant's reply.")
    )


# ------------------------------------------------------------ Verification node
# This is a pure-code node — it calls no AI model.
# Comparing amounts is deterministic logic; handing it to a model would only
# introduce uncertainty we don't need.

EXPECTED_RENT = 1000.00

def verify_payment(node_input: PaymentClaim) -> PaymentVerification:
    """Compare the tenant's claimed payment against the ledger and conclude."""
    txns = _lookup_transactions(node_input.tenant_email, node_input.month)
    total = sum(t["amount"] for t in txns)
    txn_id = txns[0]["txn_id"] if txns else None

    if not txns:
        status, note = "not_found", (
            f"No deposit found for {node_input.tenant_email} in {node_input.month}. "
            "It may not have cleared yet, or the paying email may not match the file."
        )
    elif abs(total - EXPECTED_RENT) < 0.01:
        status, note = "verified", f"${total:.2f} found, matching the amount due."
    elif total < EXPECTED_RENT:
        status, note = "amount_mismatch", (
            f"${total:.2f} found, ${EXPECTED_RENT:.2f} due, "
            f"${EXPECTED_RENT - total:.2f} outstanding."
        )
    else:
        status, note = "overpaid", f"${total:.2f} found, more than the amount due."

    return PaymentVerification(
        room_id=node_input.room_id,
        month=node_input.month,
        month_stated=node_input.month_stated,
        claimed_amount=node_input.claimed_amount,
        expected_amount=EXPECTED_RENT,
        found_amount=total if txns else None,
        found_date=txns[0]["date"] if txns else None,
        txn_id=txn_id,
        status=status,
        note=note,
    )


# ---------------------------------------------------------------- Routing nodes
# Pure if/else, no model call. This is exactly where a graph workflow beats
# prompt orchestration: "only send a receipt after verification passes" is a
# business rule, and it should not depend on the model choosing to follow it.

def verification_router(node_input: PaymentVerification):
    # Backstop: an unstated month should already have been split off at
    # month_router, so reaching here means the wiring is wrong.
    if not node_input.month_stated:
        return Event(route="ESCALATE", output=node_input)
    if node_input.status == "verified":
        return Event(route="AUTO_RECEIPT", output=node_input)
    return Event(route="ESCALATE", output=node_input)


# ---------------------------------------------------------------- AI nodes

extract_claim = Agent(
    name="extract_claim",
    model="gemini-flash-latest",
    instruction="""Extract the payment details from a tenant's text.

The input contains message (the original text) and classification (the routing result).

Output a PaymentClaim:
- room_id, tenant_email — **copy them verbatim from message.room_id /
  message.tenant_email**. The system resolved these two fields from the phone
  number; do not infer or rewrite them.
- claimed_amount — extract the amount from message.body (a number, no currency symbol)
- claimed_method
- month (format YYYY-MM)
- month_stated — **whether the tenant named an unambiguous, specific month**.

  The only condition for true: a **month name or month number** appears.
      "rent for September" / "Sep rent" / "September's rent"

  false — all of the following are false; do not hesitate:
      - no month at all: "I sent the rent"
      - **relative phrasing**: "this month" / "next month" / "last month"
      - a date without a month: "the payment I sent on the 30th"

  ⚠️ **Relative phrasing must be false.** Rent is paid in advance: "this month's
  rent" said on August 31 may mean August (the current calendar month) or the
  September rent about to come due. That ambiguity is exactly what the system
  needs to resolve — guessing on its behalf books the money to a possibly wrong month.

⚠️ When month_stated=false, still fill month with your best guess (rent is
   prepaid, so usually the next month), but you **must** mark month_stated false.
   That field decides whether the system texts the tenant to confirm, and getting
   it wrong books the money to the wrong month.

If the text contains no explicit amount, set claimed_amount to 0. Do not guess.""",
    output_schema=PaymentClaim,
)

ask_month_agent = Agent(
    name="ask_month",
    model="gemini-flash-latest",
    input_schema=PaymentContext,
    tools=[TENANT_SMS_SKILL],
    instruction="""The tenant says they paid rent but didn't say which month. Write one text.

**First load the tenant-sms skill and read "Disclosure discipline" in SKILL.md
plus "Scenario B" in references/payment.md.**

**The first fork is has_recent_match, not the month:**
- has_recent_match=true  -> B-A: the money is verified, so **state the conclusion**
  rather than asking an open question. suggested_month / suggested_reason were
  computed by deterministic code; use them directly.
  suggestion_is_certain=true  -> B-A1: the check, the amount, the date, the
  allocation, the reason, and room to correct
  suggestion_is_certain=false -> B-A2: list the specific candidate months and let
  the tenant choose
  If the tenant used relative phrasing like "this month", name the ambiguity directly
- has_recent_match=false -> B-B: say plainly that we don't see it yet, and ask for
  the confirmation number, the date, and the paying email. **Never** say anything
  implying we received it
- amount_matches_rent=false -> layer in B-C and raise the amount discrepancy too

**Disclosure of ledger status follows verification:**
- When has_recent_match=false, **never** mention the settled status of any month.
  Saying "every other month is settled, so is this one for October" hands the
  answer to someone making a false payment claim — they only have to reply "yes"
  and the ledger gains a payment that never existed. At that point you may state
  only what the tenant already knows: the payment they say they sent.
- When has_recent_match=true, the month status directly tied to this payment
  **should be stated plainly** (suggested_reason already contains the reason),
  but do not enumerate the ledger month by month.

Output only the body of the text message.""",
    output_schema=str,
)


draft_receipt = Agent(
    name="draft_receipt",
    model="gemini-flash-latest",
    input_schema=PaymentVerification,
    tools=[TENANT_SMS_SKILL],
    # ⚠️ Never put curly braces in an instruction. ADK runs inject_session_state
    # over instructions and treats {foo} as a session state variable to look up;
    # when it isn't found you get a KeyError. In 2.0 data arrives via node return
    # values, so the month and amount come in through input_schema.
    instruction="""Draft a payment receipt text for the tenant.

**First load the tenant-sms skill and read "Scenario A" in references/payment.md.**
The wording standard there governs.

The input is a PaymentVerification record containing the month, the amount found
in the ledger (found_amount), and the deposit date (found_date). Read those
values from the input; do not invent them.

**Write in English** — tenants read English only.

Wording requirements (these are hard constraints, not suggestions):
- State the check and its result: "We've checked our PayPal account and see
  your ... payment received on ..." — the amount comes from found_amount and the
  date from found_date (if it is None in the input, write no date). Invent no number
- For allocation say "we've noted it toward ... rent". Never say
  "your account is settled" / "you're all paid up" — you are asserting that this
  one transaction arrived, not a settled-in-full state (landlord's requirement,
  2026-09-01; see CLAUDE.md constraint 3)
- Precision beats brevity, but stay within 4 sentences
- Promise nothing beyond this payment

Output only the body of the text message, with no surrounding commentary.""",
    output_schema=str,
)


def deliver_receipt(node_input: str, ctx: Context):
    """Save the receipt **as a Gmail draft** and write the payment ledger.

    ⚠️ Draft, do not send. A receipt is a financial record, and CLAUDE.md
    constraint 1 requires a human to hit send.
    The ledger status is claimed, not confirmed — the tenant claims a payment and
    we found a record, but "the landlord confirmed" is a separate action and the
    two states must not be merged.
    """
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    draft_id = draft_sms_reply(original.gmail_thread_id, node_input)
    write_ledger(
        month=f"{__import__('datetime').datetime.now():%Y-%m}",
        room_id=original.room_id,
        claimed_amount=0.0,
        found_amount=None,
        status="claimed",
    )
    return Event(
        message=(f"💰 {original.room_id} receipt draft created (draft {draft_id})\n"
                 f"Ledger recorded as claimed. **It reaches the tenant only when "
                 f"you open Gmail and hit send.**")
    )


def escalate_to_landlord(node_input: PaymentVerification):
    """Amount mismatch or nothing found — no automatic reply; hand it to the landlord."""
    return Event(
        message=(
            f"⚠️ {node_input.room_id} payment needs a human\n"
            + ("❓ The tenant didn't say which month; a confirmation text was sent automatically\n"
               if not node_input.month_stated else "")
            + f"Status: {node_input.status}\n"
            f"Tenant claims: ${node_input.claimed_amount:.2f}\n"
            f"{node_input.note}"
        )
    )


# ---------------------------------------------------------------- The graph
# START -> extract -> verify (pure code) -> route (pure code) -> two branches

payment_workflow = Workflow(
    name="payment_workflow",
    edges=[
        ("START", extract_claim, month_router),
        # Month unstated -> check the ledger and PayPal, ask using what we know,
        # and stay out of the verification flow. "Verify September rent" and
        # "verify August rent" are two questions; with the month undetermined
        # there is nothing to verify.
        (month_router, {
            "MONTH_CLEAR": verify_payment,
            "MONTH_UNCLEAR": gather_payment_context,
        }),
        (gather_payment_context, ask_month_agent, send_month_question),
        (verify_payment, verification_router),
        (verification_router, {
            "AUTO_RECEIPT": draft_receipt,
            "ESCALATE": escalate_to_landlord,
        }),
        (draft_receipt, deliver_receipt),
    ],
)
