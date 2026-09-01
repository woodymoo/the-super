# Rent collection SMS

## ⚠️ Jurisdiction

The timeline below (5 full days overdue → 14-day notice) is the rule for **the
state this property is in**. **Re-check it before operating in another state or
for another property** — cure periods and notice periods differ by state, and
carrying the wrong timeline over leaves the whole collection track legally
unsound.

`CURE_PERIOD_DAYS` in `rent.py` is where this rule lands in code; change it
together with the jurisdiction.

## The legal timeline (which is what bounds the wording)

```
Due date              — not late that day
Due date + 1 day      — send collection, with a 5-day cure period
End of cure period    — now 5 days overdue; only now may the landlord start the 14-day notice process
```

The number 5 is not arbitrary: **rent must be more than 5 days overdue before a
14-day notice may be sent.** Setting the cure period to 5 days therefore both
gives the tenant a real chance and lines the timeline up with the next step.

## Where the agent sits on this track

**It writes the first-stage collection only.** It does **not** generate the
14-day notice, because:
- That notice has statutory form and content requirements
- An SMS generally does not constitute valid service
- A defective notice can **contaminate the formal process that follows**

When it is time for a 14-day notice, the agent's correct action is to tell the
landlord, not to write it.

## First-stage collection wording

### Nothing received

> Hi Sarah, your October rent of $1,000 was due on October 1 and we haven't
> received it yet. Could you take care of it by October 6? If you've already
> sent it, just reply with the date and method — PayPal eChecks can take up
> to 3 business days to clear and we'll re-check.

### Partial payment

> Hi Sarah, your October rent was due on October 1. We received $600, which
> leaves $400 outstanding. Could you send the remainder by October 6? If
> you've already sent it, let us know the date and method and we'll re-check.

## Wording requirements

**Must be present:**
- The due date (an actual date, not "last month")
- The amount (the full amount if nothing came in; all three numbers —
  received / due / gap — if it was partial)
- The cure deadline (an actual date)
- Room for "if you've already paid" + the eCheck explanation

**Must never appear:**
- Any legal language: legal action / eviction / attorney / court / notice
- Any threat: "or else" / "final warning" / "we will have no choice"
- A late-fee amount — that depends on the lease terms, and the agent does not
  read the lease
- Any judgment of the tenant: "again" / "as usual" / "you always"

**Calibrating the tone:** this text may later be read by someone — the tenant, a
lawyer, a judge. Write it assuming it will be. It can be neither so soft that it
leaves no record nor so hard that it reads as a threat.
**Plain factual statement + one clear action + one way out** is the right
temperature.

## Why there must be a way out

The tenant may genuinely have paid. A PayPal eCheck takes up to 3 business days,
and the tenant may have paid from an email that isn't on file. Sending an
unconditional demand to someone who already paid is the most embarrassing
mistake this system can make.
