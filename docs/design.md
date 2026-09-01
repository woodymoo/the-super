# The Super — Agent Design

5 rooms · $1000/month each · **due day differs per unit, per their own lease** · SMS is the primary channel

---

## 1. Channel architecture: everything is Gmail

This is the most important decision in the whole design. Once Google Voice has
"Forward messages to email" turned on:

| Direction | Actual channel | How to recognize it in Gmail |
|---|---|---|
| Tenant texts you | Voice → forwarded as email | `from:txt.voice.google.com` |
| You text a tenant back | Reply to that email → Voice sends the SMS | plain reply |
| Tenant sends photos/video | The tenant's own mailbox | `from:<tenant email>` + attachment |
| You reply by email | Ordinary email | plain reply |

**Conclusion: the whole system needs exactly one set of credentials — the Gmail
API.** No Twilio, no new phone number, and nothing changes for the tenant.

**Risk:** the Google Voice email-forwarding format is Voice's own convention, not
a documented API, so a Google change can break it. The design therefore isolates
the Voice-email parsing in a single function, so a break requires one edit.

---

## 2. Data model

Stored in Firestore. With 5 tenants the data volume is tiny — the free tier is
more than enough.

### `tenants` (maintained by hand, 5 records)

```
room_id          "2F-A"
name             tenant name
phone            SMS number (used to match the sender number in a Voice email)
email            mailbox they send photos from (used to correlate attachments)
rent_amount      1000
move_in_date
```

`phone` and `email` are **the keys for cross-channel correlation** — texts arrive
from a number, photos from a mailbox, and this table is what recognizes them as
the same person.

### `ledger` (payment ledger)

```
month            "2026-09"
room_id
expected         1000
claimed_amount   the amount stated in the tenant's text
claimed_at       when the tenant claimed to have paid
confirmed_at     when you confirmed the receipt
status           claimed | confirmed | disputed | missing
```

Note that `claimed` and `confirmed` are two distinct states. A tenant saying they
paid is not the same as you confirming it, and that distinction is the heart of
the ledger.

### `tickets` (maintenance tickets)

```
ticket_id
room_id
opened_at
severity         urgent | normal | low
description      the tenant's own words
clarity          clear | needs_media
awaiting_media   bool + expiry time
media[]          correlated photos/video
assessment       Gemini's judgment after looking at the images
contractor       who it was dispatched to
status           open | awaiting_media | ready_to_dispatch | dispatched | closed
history[]        prior tickets for this room (for spotting repeat problems)
```

Giving `awaiting_media` an expiry is essential: a tenant may say "I'll send
photos" and then forget, so 48 hours with nothing received should trigger a
reminder.

---

## 3. The agents

### 1. `classifier` (router)

**Input:** one new email (a Voice SMS or a tenant photo email)
**Output:** `{tenant_id, intent, confidence}`

Three intents:

- `PAYMENT` — mentions payment, PayPal, or an amount
- `MAINTENANCE` — a repair, damage, a fault, or a complaint
- `OTHER` — everything else (questions, small talk, move-out notice, …)

**Design point:** when confidence is low, classify as `OTHER` rather than
guessing. Misclassifying something as a payment pollutes the ledger, which costs
far more than making you glance at one extra message.

### 2. `payment_workflow`

```
extract_claim → month_router
  ├─ MONTH_CLEAR   → verify_payment → verification_router
  │                     ├─ AUTO_RECEIPT → draft_receipt → deliver_receipt (saves a draft)
  │                     └─ ESCALATE     → hand to a human
  └─ MONTH_UNCLEAR → gather_payment_context → ask_month_agent → send_month_question
```

**What it does:**
1. Extract the amount and payment method from the text, plus **whether the tenant
   actually stated which month it is for**
2. Month unclear → look at the ledger and PayPal, and ask using what we know
   **without entering the verification flow**
3. Month clear → compare against that tenant's `tenants.rent_amount`
4. Draft a receipt only if the amount matches, saved as a Gmail draft
5. Write to `ledger` with status `claimed`

**Why an unclear month gets its own branch:** rent is paid in advance, so money
sent at the end of a month is usually for the next one. Booking a payment made on
8/31 to August throws the entire ledger out of alignment — and a ledger with the
wrong month is a serious liability in a tenancy dispute. Beyond that,
"verify September rent" and "verify August rent" are two different questions;
with the month undetermined there is nothing to verify.

When asking about the month, look at what the ledger already holds first. After
verification, "we see your $1,000 from August 30; August is already settled, so
we'll apply this to September" is far better than a bare "which month?" — the
tenant only has to confirm. Before verification, ask for evidence instead and
disclose nothing about the ledger.

**The three cases:**

| Case | Receipt wording |
|---|---|
| Amount = that tenant's `rent_amount` | Standard confirmation: PayPal was checked, the payment was found, and it has been noted toward month X |
| Amount < $1000 | Confirm $X received and state the remaining $Y — **never decide on your own that "it's fine"** |
| Amount > $1000, or unclear | Draft nothing; mark `disputed` and hand it to you |

**Note:** for a mocked PayPal the agent verifies against fixture data, not the
live PayPal API. The receipt must stay honest about what was actually
established, and must never assert a settled-in-full state. That distinction can
matter a great deal in a tenancy dispute.

### 3. `maintenance_triage`

**What it does:**
1. **Triage severity** — anything involving water, electricity, gas, heat, or
   safety → `urgent`; everything else by impact
2. **Judge whether the description is clear** — is it enough to call a contractor?
3. Check `tickets` history: has this room reported the same problem before?

**The standard for "clear"** (written into the instruction):

- Clear: names **which fixture/location** + **what is happening** + **when it started**
- Unclear: "the kitchen is broken", "there's a problem with the water",
  "the AC isn't cooling" (missing the specifics of the symptom)

Unclear → draft a text asking for photos, and **be specific**: not "please send
photos" but "could you photograph the pipe joint under the sink, and take a short
video of the water flow?"

### 4. `media_analyst`

**Triggers:** a ticket in `awaiting_media`, or an incoming tenant email with an
attachment

**What it does:**
1. Correlate the attachment back to the right ticket via `tenants.email`
2. Hand the photos straight to Gemini for visual assessment
3. Update the ticket's `assessment` and `severity` (which may escalate after
   seeing the images)

**This is the technically most interesting part of the system:** a text at 9:04pm
and an email at 9:31pm arrive on different channels, under different identifiers,
with no shared conversation ID — and the `tenants` table plus a time window plus
the awaiting-media state stitch them into one ticket.

### 5. `dispatcher`

**What it does:** produce the job brief for the contractor.

It contains the address, the room, the problem description, the conclusion from
the images, the urgency, the tenant's contact details, and workable times for a
visit.

**Not sent automatically.** You have contractors you already use; choosing one is
your judgment. The agent only prepares the material.

### 6. `secretary` (daily digest)

Sends you an **SMS** summary at a fixed time each day (not an email — you don't
check email often):

```
Today: 2 new messages
· 2F-A reported a kitchen sink leak (urgent, images reviewed: corroded joint) → awaiting your dispatch
· 3F-B says they paid $1000 via PayPal → receipt awaiting approval
Open: 1 ticket has been waiting on photos for over 48 hours (1F-C)
```

SMS has a length limit; when it overflows, split it or send only the headlines
plus "details in email".

---

## 4. Human approval: tiered release

This is the point most worth putting in the submission notes. **Don't make it
all-or-nothing.**

| Action | Consequence | Recommended policy |
|---|---|---|
| Daily digest to you | None | **Fully automatic** |
| Text asking for photos | Very low | **Can be automatic** (worst case, you ask again) |
| Payment receipt text | Medium (it is a financial record) | **Requires your approval** |
| Substantive reply about a repair | High (commits repair timing, allocates responsibility) | **Must have your approval** |
| Dispatching a contractor | High (spends money) | **Must have your approval** |
| Anything touching the lease, deposit, or legal matters | Extreme | **The agent doesn't touch the substance** — it sends only a non-committal holding reply ("received, someone will follow up") and hands it to you |

**The rationale: the boundary of autonomy should be set by consequences, not by
technical capability.** That sentence is itself a good hackathon twist — most
entrants demonstrate "my agent is fully autonomous", while you demonstrate
"I know where it should stop".

How approval is implemented: the agent saves the draft as a **Gmail draft**. You
open Gmail, glance at it, and hit send — no custom UI required, and it works from
a phone. ADK 2.0's graph workflow has a built-in human-input node, which could
make this more formal if there is time.

---

## 5. The three triggers

| Trigger | Frequency | What it does |
|---|---|---|
| **Message poll** | Every 5–15 min | Fetch new email → classify → take the matching path |
| **Rent cycle** | Daily | Evaluate each unit against its own `tenants.rent_due_day`; the day after it is late, send collection with a 5-day cure period |
| **Daily digest** | Each evening | Summarize + check for photos that never arrived |

All of it runs on Cloud Scheduler. **The agent itself stays stateless** — if the
photos haven't arrived this round, it records that in Firestore and exits, then
looks again next round. Never let the agent loop and wait internally; that keeps
a Cloud Run container alive and burns money.

---

## 6. Tool inventory

```
read_new_messages(since)        → fetch new Gmail messages, separating Voice SMS from tenant email
identify_tenant(phone_or_email) → look up the tenants table
get_ledger(month, room_id)      → read the ledger
write_ledger(...)               → write the ledger
get_tickets(room_id, status)    → read tickets (including history)
write_ticket(...)               → write a ticket
fetch_attachments(email_id)     → fetch attachments
draft_sms_reply(thread_id, text)→ create a Gmail draft (replying to a Voice email = sending an SMS)
draft_email(to, subject, body)  → create an ordinary email draft
send_digest(text)               → send the digest SMS to yourself
```

Ten functions, and not one of them needs a new third-party API.

---

## 7. Phased implementation (in order of importance)

**Phase 1 — the skeleton (must be finished)**
- `read_new_messages` + `identify_tenant` + `classifier`
- The payment path only: check the amount → write the ledger → draft the receipt
- Daily digest

The payment path is the simplest, the most regular, and the easiest to
demonstrate — and it produces five real data points every month.

**Phase 2 — the maintenance path (the core highlight)**
- `maintenance_triage` severity + judging description clarity
- Draft a photo request when it is unclear

**Phase 3 — cross-channel correlation (the technical highlight)**
- `awaiting_media` state + email attachment correlation + Gemini image understanding

**Phase 4 — only with time to spare**
- Dispatch brief
- Rent-cycle collection
- Repeat-problem detection from ticket history

**If time is short, Phase 1 + 2 is enough for a complete demonstration of the
multi-step, unattended, interception-style workflow that the Taskmaster track
asks for.** Phase 3 is a bonus, not the pass mark.

---

## 8. Notes for the demo

- **Redact:** replace tenant names, phone numbers, and emails with fake data
  before recording. Change the real unit numbers too.
- **Prepare three test texts:** one standard payment, one vaguely described
  repair, one urgent leak. Those three cover the whole system's judgment.
- **Feature the moment it decides it needs photos** — that is the instant that
  best shows autonomous judgment, and it is more persuasive than any
  architecture diagram.
- **Say clearly that human approval is a design choice, not a capability gap.**
  Make sure that line gets said.
