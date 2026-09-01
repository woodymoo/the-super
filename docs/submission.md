# The Super — An Autonomous Building Superintendent

**Track:** The Taskmaster
**Built with:** Google Agent Development Kit (ADK) 2.0 graph workflows, Gemini, Gmail API

> ⚠️ Draft. The factual claims match the code as of 2026-08-31,
> but give the narrative voice your own pass. Items marked **[TBD]** need your decision.

---

## Inspiration

Every building has a "super" — the superintendent who handles the leaks, the
broken radiators, and the 11pm text messages. I own and manage a small rental
property, and for years I have *been* the super, in addition to my day job as a
cloud architect.

The friction is not the repairs. The friction is the **triage**.

My tenants do not file tickets. They text me. A message arrives that says
"the toilet is leaking" — three words, no location, no timeline, nothing a
contractor could act on. Another says "I sent the rent yesterday," and I have no
idea whether that is true until I log into PayPal. By the time I sit down to
deal with any of it, I am reconstructing the state of a five-unit building from
fragments.

This is a multi-step background workflow I have been executing manually, badly,
for years. It is exactly the kind of work that should be intercepted and
completed before I ever look at it.

## What it does

The Super runs on a schedule with no human trigger. Tenant SMS arrives through
Google Voice's email forwarding, and the agent handles the whole intake pipeline:

1. **Reads and parses** incoming tenant SMS from the Gmail inbox, identifying the
   tenant by phone number or email address.
2. **Classifies** intent — payment, maintenance, or neither — with an explicit
   low-confidence safety valve: anything ambiguous is routed to a human rather
   than guessed at.
3. **Verifies payments deterministically.** The claimed amount is checked against
   the ledger in plain `if/else` code, never by prompt. Only an exact match
   drafts a receipt; every mismatch escalates.
4. **Triages maintenance** by severity, and — the part I care most about —
   judges whether the description is even actionable. "The toilet is leaking"
   is not. The agent asks for specific photos: the base, behind the tank, the
   supply line.
5. **Drafts a contractor brief** when the description *is* complete, including
   the materials to bring.
6. **Runs a rent collection ladder** where each tenant has their own
   contractual due date. Overdue by one day triggers a collection SMS with a
   five-day cure period.

## The twist: autonomy bounded by consequence

The obvious move in an agent hackathon is to let the agent send everything. I
drew the line by consequence instead of by capability.

- **Fully automatic:** daily digests to me, photo requests to tenants
  (worst case, you ask twice), rent collection reminders.
- **Drafted, never sent:** payment receipts, maintenance replies, contractor
  dispatch. These are financial and legal documents.
- **Not generated at all:** the 14-day statutory notice. Rent must be five days
  overdue before that notice is even permissible, and an SMS does not constitute
  valid service. The agent walks the tenant to the edge of that boundary and
  stops.

That last one is the design decision I am proudest of. The collection ladder is
timed *backwards* from the legal requirement: collection goes out on day one
overdue with a five-day cure window, so that when the window closes the rent is
exactly five days late — the threshold at which the formal process becomes
available. The agent knows the timeline. It also knows which step is not its to
take.

## How I built it

**ADK 2.0 graph workflows.** The root workflow classifies, then routes to one of
two sub-workflows. Nodes pass data by return value through pydantic schemas —
no session state, no prompt-chaining. A routing function returns
`Event(route=..., output=...)` and the next edge dispatches on it.

**Deterministic code for consequential decisions.** This is the core
architectural rule. `verify_payment()` compares amounts. `verification_router()`
decides whether a receipt is even drafted. `build_collection_sms()` is a fixed
template. None of these are prompts, because "only send a receipt if the amount
matches" is a business rule, not something a model should be trusted to
remember.

**One OAuth scope for every channel.** Google Voice forwards tenant SMS to
Gmail; replying to that email sends an SMS back. Tenant photos arrive as
ordinary email. So the entire I/O surface is the Gmail API and a single
`gmail.modify` scope. Voice's forwarding format is not a public API, so all
format-dependent parsing is isolated in one place.

**Stateless by design.** Waiting for tenant photos is not a loop — it is a
Firestore/JSON ticket with a deadline, checked on the next scheduled wake-up.
A container that sits idle waiting is a container that bills.

## Challenges

**The graph engine is not the tool-calling paradigm.** Most ADK material online
predates 2.0. I lost real time to patterns that no longer apply: `{variable}`
templates in instructions (2.0 raises on those), and routing dictionaries whose
tuple values I assumed were sequential chains — they are parallel fan-out.
Reading the installed framework source settled both faster than the docs did.

**Data flow through routers.** A routing node that returns only a route drops
the payload; downstream nodes receive `None`. Worse, the classifier's output
schema does not carry the original message, so the branches lost the tenant's
actual words. The fix was to reassemble the original message with the
classification into one schema — in code, not by asking the model to repeat
itself back, which would have let it quietly rewrite the room ID.

**Knowing what not to automate.** The rent collection ladder was the hardest
thing to scope, and the answer came from the legal timeline rather than from
what was technically possible.

## Honest limitations

- **PayPal verification is mocked.** `_lookup_transactions()` reads a JSON
  fixture. The function signature matches the real Transaction Search API so
  that swapping it is a one-function change, but no live payment data is
  touched. The ledger deliberately separates `claimed` from `confirmed` for
  exactly this reason: a tenant saying they paid is not the same fact as the
  money arriving.
- **Google Voice email parsing is not an API.** The format is Google's to change.
- **Cross-channel photo correlation is not finished.** Attachment fetching
  exists; wiring it to the awaiting-photo tickets did not make the deadline.
- **Not deployed.** It runs locally on a schedule. Cloud Run Jobs +
  Cloud Scheduler is the intended target and the entry point is already
  shaped for it (`main.py poll|digest|rent`), but deployment was cut for time.
- **Tests cover the deterministic layer only.** 32 tests over the rent timeline, payment verification, routing, and ledger writes — the judgments with financial or legal consequences. The model-driven nodes (classification, wording) are not asserted on, which is deliberate: their output varies by model version, and the architecture exists precisely so that nothing consequential depends on it. Run with `pytest`.

## What's next

- **Wire the photo correlation** and pass the images to Gemini for visual
  assessment — the ticket should say what the picture shows.
- **Deploy to Cloud Run Jobs**, with the OAuth token in Firestore rather than a
  local file.
- **Seasonal anticipation.** The ticket history is a dataset. An agent that
  knows the boiler has been serviced every November for four years should raise
  it in October, not wait for a text message in January.

## What I learned

My instinct after twenty years of data pipelines was to write all the routing
logic myself. ADK 2.0's graph model pushed me the other way, but only partly —
and the partly is the interesting bit. Classification and triage genuinely
belong to the model. Amount comparison and "may we send this yet" genuinely do
not. The value was not in choosing one paradigm; it was in drawing the line
between them and making that line visible in the code, so that six months from
now nobody can accidentally move a financial rule into a prompt.
