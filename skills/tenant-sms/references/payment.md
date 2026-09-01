# Payment-related SMS

**Before you reply, the system has always already checked PayPal**
(`verify_payment`, which completes immediately). So every payment reply must
convey the check and its result — "we've checked our PayPal account and ..." —
rather than a vague acknowledgement. Precision beats brevity.

## Scenario A · Amount matches, month stated → receipt

The tenant named the month, the amount equals that tenant's `rent_amount`, and
the deposit **was found** in PayPal (status=verified).

**This is the only case that generates a receipt.** And the receipt is saved as a
draft — it goes out only when the landlord hits send.

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and see your $1,000 payment received on October 1. We've noted it toward
> October rent. If anything here doesn't look right, let us know.

Key points:
- **State the check** ("we've checked our PayPal account") — the evidentiary
  value of this receipt is that it records what was reconciled and when
- Amount from `found_amount`, date from `found_date`; invent no number. If the
  input has no date, write no date — don't guess
- Say "noted it toward October rent", **not** "your October rent is settled" /
  "your account is current" — you are asserting this transaction, not a
  settled-in-full state
- Spell the month out (October), not 2026-10 — that form is for the system
- Use a thousands separator in amounts

## Scenario B · Month not stated → verify first, then ask

### The first fork is not the month, it's «did we find the money»

The tenant says "I just sent $1,000" — that is a **specific claim that a deposit
landed in the last few days**. The system has already checked it for you:

| Field | Meaning |
|---|---|
| `has_recent_match` | **Was a matching deposit found in the last few days** ← look here first |
| `recent_matches[]` | If found, each one's `amount` / `date` / `days_ago` |
| `claimed_amount` | How much the tenant says they sent |
| `expected_amount` | That tenant's monthly rent |
| `amount_matches_rent` | Whether the claimed amount equals exactly one month's rent |
| `unsettled_months` | Months not yet settled — before verification say **not a word**; after verification mention only the one this payment applies to |

**Fork on `has_recent_match` first, and only then consider the month.**
Discussing "which month is this for" before the money is found amounts to
accepting the tenant's account by default.

---

### B-A · `has_recent_match = true` → state how it applies; don't ask an open question

The money is verified. **The fraud risk on this one is gone**; what remains is
where it books — and that has already been computed by deterministic code
(`suggested_month` / `suggested_reason`).

Your job is to **state the conclusion clearly**, not to ask the tenant again.

**B-A1 · `suggestion_is_certain = true` → state it + leave room**

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and see your $1,000 received on August 30. Since August rent is already
> settled, we'll apply this to September, which is due tomorrow. Let us
> know if you meant a different month.

Five required elements, none optional:
1. **The check** — "we've checked our PayPal account", which gives the reply
   factual weight
2. **That payment's amount and date** — so the tenant can confirm it's the same one
3. **The conclusion** — "we'll apply this to September"
4. **The reason** — from `suggested_reason`, so the tenant can check the logic
5. **Room to correct** — "let us know if..."

When `suggested_reason` points at an **unpaid earlier month**, say so just as
directly. After verification, the month status directly tied to this payment is
fair to state:

> Hi Sarah, thanks — we've checked PayPal and see your $1,000 received
> today. Our records show August rent is still open, so we'll apply this
> to August. Let us know if you meant a different month.

If the tenant used a relative phrase like "this month" / "next month",
**name the ambiguity directly** — that is precisely the problem:

> You mentioned "this month" — since rent is due in advance, we're reading
> that as September. Let us know if you meant August.

**B-A2 · `suggestion_is_certain = false` → list the specific candidates**

When several due months are unpaid, the choice affects each month's days-overdue
count and whether the statutory track later applies, so the tenant must decide:

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and see your $1,000 received on August 30. We show both July and August
> still open — which should this go toward?

Key point: **name the months**. Don't ask "which month" — the tenant shouldn't
have to reconstruct our books.

### B-B · `has_recent_match = false` → you cannot say "received"; ask for evidence

**This is the branch that stops false payment claims.** Not found is not found.

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and don't see a $1,000 payment yet. PayPal eChecks can take up to 3
> business days to clear — could you send the confirmation number, the date
> you sent it, and the email you paid from? We'll re-check.

Key points:
- **Never** say anything implying "we're all good on our side"
- **Never** reveal which months are settled and which aren't — that is exactly
  what someone making a false claim needs
- The word `yet` carries all the caution: we simply **haven't seen it**, we are
  not asserting they didn't pay
- Ask for three concrete things: confirmation number, date, paying email.
  Those actually resolve the case, and **an honest tenant can produce them while
  a false claim cannot**
- Don't ask "which month" — the money isn't confirmed to exist, so allocation is
  premature

---

### B-C · Amount doesn't match the rent (`amount_matches_rent = false`)

Handle "found or not" per B-A / B-B first, then fold in the amount discrepancy:

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and see $600 received on August 30. The monthly rent is $1,000 — which
> month is this toward, and is the rest coming separately?

When `has_recent_match=false`, don't say what was found — only that the amount
doesn't match, and ask for evidence:

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and don't see a $600 payment yet — and the monthly rent is $1,000.
> Could you send the confirmation number and the date you sent it?

Key point: when the amount is off, don't ask about the month alone. Stay neutral;
don't make it read as an interrogation.

---

### General points

- **Never** write "we received your rent" in any of these unless
  `has_recent_match=true` and you are describing that exact payment
- Soften inferences: `should we apply it to` / `is that right`.
  **Don't** write "this is for September" — that decides for the tenant
- Give one line of reason for asking ("so we log it to the right month"),
  otherwise it reads as making things difficult
- **Disclosure of `unsettled_months` follows verification**: before verification,
  not one month is mentioned; after verification, mention only the month covered
  by `suggested_reason` that this payment applies to. Never enumerate the ledger

## Scenario C · Amount less than due

**No receipt is generated; escalate to a human.** If the landlord does want to
send something, the wording is:

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and see $600 received toward October rent. The full amount due is $1,000,
> so there's $400 remaining. Let us know if you've already sent the rest.

Key points:
- All three numbers must appear: received, due, and the gap. Don't make the
  tenant do the arithmetic
- **Don't** write "no problem" / "that's fine" / "whenever you can" — the agent
  has no authority to waive or defer
- **Don't** write anything implying the month is «taken care of»:
  "that takes care of October" / "you're good for now" / "we'll call it even".
  In most states **accepting a partial payment can waive the right to evict for
  that month** unless the right to collect the balance is expressly reserved.
  A single "that's fine" in a text can become that evidence. The wording must
  stop at the fact that $400 remains
- Close with room for the possibility that a second payment is already on its way

## Scenario D · Amount more than due

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and see $1,200 received toward October rent — $200 more than the $1,000
> due. Could you confirm whether the extra is intended for next month or
> something else?

Key point: an overpayment needs clarifying more than an underpayment does — it
could be a prepayment of next month, a deposit repayment, or a mistake. Never
assume on the tenant's behalf.

## Scenario E · No record found in the ledger

**Never say "we didn't receive it" or "your payment failed".**
We merely can't find it: it may be in transit, paid from a different email, or
our lookup may be at fault.

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and don't see the payment yet. PayPal eChecks can take up to 3 business
> days to clear — could you send the confirmation number and the email you
> paid from? We'll re-check.

Key points:
- "we don't see it **yet**" — the word *yet* carries all the caution
- Offer two possible reasons yourself (eCheck in transit, a different paying
  email) rather than putting the fault on the tenant
- Ask for specifics (confirmation number, paying email) — those two actually
  resolve it

## Scenario F · The tenant **named a month**, but an earlier month is still unpaid

`month_stated=true` (the tenant explicitly said "for October rent") while
`unsettled_months` still contains an earlier month. **This is the easiest one to
get wrong.**

### Rule: the tenant's designation wins; the system may not reallocate on its own

The common-law hierarchy for applying payments is **debtor designates >
creditor chooses > oldest-first default**. When the tenant specified the
allocation as they paid, that designation binds us — we **may not** quietly
apply the money to the August arrears, however much more "sensible" that is as
accounting practice.

The system's oldest-arrears-first rule behind `suggested_month` applies
**only when the tenant did not designate**.

### Wording: send the receipt, handle arrears separately — **not in the same text**

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and see your $1,000 payment received on October 1. We've noted it toward
> October rent, as you indicated. If anything here doesn't look right,
> let us know.

Key points:
- **"as you indicated"** — this records that the allocation was **the tenant's
  own designation**. If the books are ever disputed, that phrase shows we did
  not reallocate it ourselves
- This text does **not** mention the August arrears, for two reasons:
  1. Combining them reads as bargaining with a receipt
  2. Collections has its own wording standard and timeline
     (`references/collections.md`), driven by the deterministic templates in
     `rent.py`. The receipt node should not drag it in
- Earlier arrears go out as a **separate collection text**, when it is due to be sent

### The one exception: the month the tenant named is **already settled**

Here the move isn't "respect the designation" but ask — the money is real, but
the allocation doesn't add up:

> Hi Sarah, thanks for letting us know. We've checked our PayPal account
> and see your $1,000 received on October 1. Our records show October rent
> is already covered — should we apply this to a different month, or is
> this a prepayment for November?

Key point: the money is verified, so October's status may be stated (it bears
directly on where this payment lands). But **don't** spill the arrears status of
other months along with it.
