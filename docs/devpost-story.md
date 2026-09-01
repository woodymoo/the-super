## Inspiration

Every building has a "super" — the one who handles the leaks, the broken radiators, and the 11pm texts. I own a small five-unit rental and for years I have *been* the super, on top of a day job.

The friction was never the repairs. It was the **triage**. Tenants don't file tickets, they text. *"The toilet is leaking"* — three words, no location, nothing a contractor could act on. *"I sent the rent yesterday"* — and on the 31st that doesn't even tell me which month it's for. Book it wrong and the ledger is off by one, which matters in a dispute.

That's a multi-step background workflow I've been running manually, badly, for years.

## What it does

The Super wakes on a schedule with no human trigger. Tenant SMS reaches it through Google Voice's email forwarding, and it owns the whole intake:

- **Classifies** each message — payment, maintenance, or neither — and routes anything ambiguous to me instead of guessing.
- **Resolves which month a payment is for.** If the tenant didn't say, it doesn't guess: it reads the ledger and PayPal, then asks a question answerable in one word — *"We have August recorded, is this one for September?"*
- **Verifies the amount** against that tenant's own contractual rent. Exact match drafts a receipt; anything else escalates.
- **Triages maintenance** by severity, and judges whether the description is even *actionable*. "The toilet is leaking" isn't — so it asks for specific photos: the base, behind the tank, the supply line.
- **Runs a rent collection ladder** on each tenant's own due date, and texts me a daily digest, because I don't read email.

## How we built it

**ADK 2.0 graph workflows.** The root workflow classifies, then routes into sub-workflows. Nodes pass data by return value through pydantic schemas — no session state, no prompt-chaining. Some nodes are Gemini agents, some are plain Python that touches no model at all.

**Consequential decisions are code, not prompts.** `verify_payment()` compares amounts. `verification_router()` decides whether a receipt is even drafted. "Only send a receipt if the amount matches" is a business rule, not something a model should be trusted to remember across a version bump.

**Wording is a Skill; judgment is code.** Every phrasing rule for tenant-facing text lives in an ADK 2.0 Skill, loaded on demand. Editing markdown changes how the agent talks — but the Skill never decides *whether* a message may go out. The router doesn't read it.

**One OAuth scope for everything.** Voice forwards SMS into Gmail; replying to that email sends an SMS back. Photos arrive as ordinary email. No Twilio, no new number, nothing for tenants to install.

## Challenges we ran into

Most ADK material online predates 2.0, so I burned time on patterns that no longer apply — instruction templating, and routing dicts whose tuple values I read as sequential chains when they're parallel fan-out.

A router that returns only a route silently drops its payload downstream. And `X | None` without a default is still *required* in pydantic — so fields that are legitimately null (`found_amount` when PayPal has no record) crashed only on the paths you test last.

## Accomplishments that we're proud of

**Autonomy bounded by consequence, not by capability.** Digests, photo requests, and collection reminders send themselves. Receipts, maintenance replies, and contractor dispatch are drafted into Gmail and wait for my thumb — they're financial and legal documents. The 14-day statutory notice isn't generated at all: an SMS doesn't constitute valid service.

The collection ladder is timed *backwards* from that legal deadline — day-one collection, five-day cure window, so the window closes exactly when the formal process becomes available. **The agent knows the timeline, and knows which step isn't its to take.**

Same instinct in one word of copy: it says *"we've received your payment notice"*, never *"payment confirmed."* It cannot see money arrive.

## What we learned

Classification, triage, and phrasing belong to the model. Amount comparison and "may we send this yet" do not. The value wasn't picking a paradigm — it was drawing that line *visibly in the code*, so nobody can later move a financial rule into a prompt where no test can catch it drifting.

Giving the model less authority made it more useful, because I stopped double-checking it.

## What's next for The Super - Rental and tenants management

- **Cross-channel photo correlation.** A 9:04pm SMS and a 9:31pm email share no conversation ID; the tenant record plus a time window stitches them into one ticket — then Gemini reads the photo.
- **Deploy to Cloud Run Jobs + Cloud Scheduler.** The entry point is already shaped for it.
- **Seasonal anticipation.** If the boiler has been serviced every November for four years, raise it in October — don't wait for the January text.

---

**Honest limitations:** PayPal verification is **mocked** — `_lookup_transactions()` reads a JSON fixture, matching the real API's signature so swapping it is a one-function change. The ledger keeps `claimed` and `confirmed` separate for that reason. Google Voice's email format is not a public API. Not yet deployed. All names, numbers, and units in the demo are fabricated.
