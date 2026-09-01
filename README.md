# The Super

An autonomous property management agent for a five-room rental. Tenants text
about rent and repairs; the agent classifies each message, verifies payment
claims against the books, triages maintenance, and knows when to stop and hand
the matter to a human.

Built on [ADK 2.0](https://adk.dev/graphs/) graph workflows for the
"All Things Agentic" hackathon, Taskmaster track.

> **PayPal verification is mocked.** It is isolated in a single function,
> `_lookup_transactions()` in `the_super/payment.py`, which reads a fixture file.
> Everything else — Gmail, Google Voice SMS, Gemini — runs against live services.

---

## The idea

Most agent demos optimize for how much the agent does on its own. This one is
built around **where it should stop**.

The boundary is drawn by consequence, not by capability:

| Action | Consequence | Policy |
|---|---|---|
| Asking a tenant for photos | Very low — worst case you ask again | Sent automatically |
| Asking which month a payment is for | Low, and not asking misfiles the money | Sent automatically |
| "Received, someone will follow up" | Commits to nothing | Sent automatically |
| A payment receipt | It is a financial record | **Saved as a draft; a human sends it** |
| Dispatching a contractor | Spends money | **Requires approval** |
| Anything touching the lease, deposit, or legal matters | Severe | **The agent never engages the substance** |
| A 14-day statutory notice | Requires valid service | **Never generated at all** |

Approval needs no custom UI: the agent saves a Gmail draft, and the landlord
opens Gmail and hits send — from a phone if they like.

## Two design rules

**1. Judgments with financial or legal consequences are code, not prompts.**

Whether an amount matches, whether the month was stated clearly, whether a
receipt may be auto-sent, how many days overdue triggers collection — all of it
is `if`/`else` in `payment.py` and `rent.py`. None of it is an instruction the
model is trusted to follow.

The reason is testable behavior. Prompt-driven decisions drift across model
versions and you cannot write a test proving one is right every time. The
deterministic layer has 32 tests; the model-driven nodes have none, deliberately.

**2. Wording lives outside the code.**

Every standard for how the agent talks to tenants is markdown in
`skills/tenant-sms/`, loaded on demand via ADK Skills. Editing a markdown file
changes how the agent speaks, with no code change and no restart — but it cannot
change what the agent is allowed to do.

That split is the point. A rule saying "a small shortfall is fine" in the skill
would do nothing; the router never reads it.

## Everything is Gmail

Google Voice forwards texts to email, so one OAuth scope covers the whole system.

| Direction | Channel | Recognized by |
|---|---|---|
| Tenant texts in | Voice forwards to Gmail | `from:txt.voice.google.com` |
| Reply to a tenant | Reply to that email | Voice delivers it as a text |
| Tenant sends photos | Their own mailbox | sender address + attachment |

No Twilio, no second phone number, and nothing changes for the tenant. Google
Voice has no public API, so the forwarded-email parsing is deliberately confined
to one function — a format change means one edit.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp the_super/.env.example the_super/.env      # fill in the [FILL] items
cp the_super/fixtures/tenants.json.template the_super/fixtures/tenants.json
cp the_super/fixtures/paypal_transactions.json.template \
   the_super/fixtures/paypal_transactions.json

python authorize.py                            # one-time Gmail OAuth
```

Keep `DRY_RUN=true` in `.env` for the first run: every send only logs.

```bash
python demo.py list              # ten scenarios, no real mailbox needed
python demo.py fix-vague         # watch it decide it needs photos
python demo.py rent 2026-08-04   # rent timeline on a simulated date

python main.py poll              # process real Gmail
pytest                           # 32 tests, under a second
```

Nothing runs in the background. The agent is stateless by design — waiting is a
Firestore record plus the next scheduled poll, not a container sitting idle. In
production Cloud Scheduler drives `poll`, `rent`, and `digest`.

## Layout

```
the_super/
  agent.py        root workflow: classify -> route
  payment.py      verification, month allocation, receipts   <- reference implementation
  maintenance.py  severity triage, photo requests, dispatch briefs
  rent.py         rent cycle and collection copy (deterministic templates)
  tenants.py      roster, identification, per-lease due dates
  tools/          Gmail and persistence
skills/tenant-sms/  how the agent talks to tenants (markdown)
docs/design.md      full design
docs/manual.md      operating manual
tests/              the deterministic layer
```

`CLAUDE.md` holds the constraints that keep the above true.

## License

MIT — see [LICENSE](LICENSE).
