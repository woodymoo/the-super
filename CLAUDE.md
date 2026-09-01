# The Super — Project Guide

An autonomous property management agent. Devpost "All Things Agentic Hackathon",
Taskmaster track. **Deadline: 2026-08-31 17:00 PDT.**

Full design in `docs/design.md`; draft submission notes in `docs/submission.md`.
Read `docs/design.md` before touching anything.

---

## ⚠️ Use ADK 2.0 — do not write 1.x code

This is the easiest thing to get wrong. ADK Python 2.0 went GA on 2026-05-19 and
introduced the graph execution engine. The overwhelming majority of ADK examples
online and in training data are from the 1.x era — **do not copy them**.

**Forbidden (1.x style):**

- `SequentialAgent` / `ParallelAgent` / `LoopAgent` — replaced by graph workflows
- `output_key="foo"` plus `{foo}` templating in a downstream instruction — 2.0
  passes data automatically via node return values
- Passing data between nodes by hand through `session.state[...]`
- Appending events directly with `context.session.events.append(...)`
- Overriding `_run_async_impl()` or `generate_content()` — the graph engine
  silently ignores them

**Correct (2.0 style):**

```python
from google.adk import Agent, Event, Workflow
from pydantic import BaseModel

class MySchema(BaseModel):
    field: str

step_a = Agent(name="a", model="gemini-flash-latest",
               instruction="...", output_schema=MySchema)

def pure_code_node(node_input: MySchema) -> OtherSchema:
    """A pure-code node; it does not call the model."""
    return OtherSchema(...)

def router(node_input: OtherSchema):
    return Event(route="BRANCH_X" if cond else "BRANCH_Y")

root_agent = Workflow(
    name="root_agent",
    edges=[
        ("START", step_a, pure_code_node, router),
        (router, {"BRANCH_X": node_x, "BRANCH_Y": node_y}),
    ],
)
```

Key points:
- Nodes pass data via **return values**; pydantic schemas are the type contract.
  Nothing goes through session state
- A node can be an Agent, a plain function, a Tool, or another Workflow
- A routing function returns `Event(route=...)`, and the next line of `edges`
  dispatches with a dict

`payment.py` is the verified reference implementation; write new code in its style.

When unsure about the API, check https://adk.dev/graphs/ and
https://adk.dev/graphs/routes/ with WebFetch rather than writing from memory.

## ⚠️ No broad excepts in tools

ADK 2.0 has framework-level automatic retry. Leaving `except Exception:` in a
tool hides the failure from the framework and **permanently disables retry for
that step**. Never catch `BaseException` — that swallows `NodeInterruptedError`
and breaks human-in-the-loop pauses.

- Business/semantic errors (unknown tenant, malformed amount) → return a
  structured error result to the model
- Infrastructure failures (network, IMAP, Firestore timeout) → **let them
  propagate**; the framework will retry

---

## ⚠️ Optional fields must have defaults

This one has bitten twice. When ADK serializes a node's output it strips `None`,
and re-validation then fails with `Field required` — because in pydantic
`X | None` without a default is **still required**.

```python
repeat_of: str | None            # ❌ the model doesn't return this field -> the whole branch crashes
repeat_of: str | None = None     # ✅
```

The dangerous ones are the fields that are **None in the normal case**
(`found_amount` when nothing is found, `cure_deadline` when no collection is
due). They guarantee a crash on every branch except the happy path, and only
surface once that branch actually runs.

After adding a pydantic model, sweep for them:

```bash
python -c "
import importlib, inspect
from pydantic import BaseModel
for mod in ['the_super.schemas','the_super.payment','the_super.maintenance','the_super.rent']:
    m = importlib.import_module(mod)
    for _, o in vars(m).items():
        if inspect.isclass(o) and issubclass(o, BaseModel) and o is not BaseModel:
            for fn, f in o.model_fields.items():
                if f.is_required() and type(None) in getattr(f.annotation, '__args__', ()):
                    print(f'❌ {o.__name__}.{fn}')"
```

---

## Wording belongs to the Skill; decisions belong to the code

Every wording standard for talking to tenants lives outside the code in
`skills/tenant-sms/` (ADK 2.0 Skills: `SKILL.md` + `references/*.md`, loaded on
demand). Editing the markdown changes how the agent speaks.

**But only "how to say it" belongs to the Skill.** The following are always
deterministic code:

| Belongs to code | Where |
|---|---|
| Whether an amount matches | `payment.verify_payment` |
| Whether the month was stated clearly | `payment.month_router` |
| Whether a receipt may be sent automatically | `payment.verification_router` |
| How many days overdue triggers collection | `rent.check_one` |
| What may be sent to a tenant automatically | The `send_sms_now` / `draft_sms_reply` call sites in each node |

Writing "a small amount difference is acceptable" in SKILL.md does nothing — the
router never reads it. That is deliberate: prompt-driven behavior drifts across
model versions, and you cannot write a test proving it is right every time.

New wording rule → put it in `skills/tenant-sms/references/`, not in an
instruction. Instructions should only point at which reference to read.

---

## Architectural constraints (do not violate)

1. **Outbound messages are drafts by default, not auto-sent.** Every message to
   a tenant or a contractor is saved as a Gmail draft and waits for a human to
   hit send. Daily digests and summaries addressed to the landlord are sent
   automatically.

   **Auto-send exceptions (these three only):**
   - Texts asking for photos (zero risk; worst case you ask again)
   - Daily digests and summaries to the landlord themselves
   - **Rent collection texts** — on 2026-08-31 the landlord explicitly asked for
     these to be auto-sent, overriding the original "never auto-send" rule here.
     Implemented in `rent_cycle` in `main.py`. The collection copy comes from
     deterministic templates in `rent.py` and never passes through the model.
     **No 14-day statutory notice is generated** — an SMS does not constitute
     valid service, so that step must happen offline.

   With `DRY_RUN=true` every send only logs. Keep it on for the first run
   against a real mailbox.

2. **Any judgment with financial or legal consequences must be deterministic
   code, not a prompt.** Amount comparison and rules like "only send a receipt
   after verification passes" are implemented with `if`, not written into an
   instruction. See `verify_payment` and `verification_router` in `payment.py`.

3. **Receipt wording follows the verification result** (landlord's requirement,
   2026-09-01, superseding the earlier "only ever say we received your payment
   notice" rule):
   - `verify_payment` found the deposit (status=verified / has_recent_match=true)
     → the receipt **explicitly states** that PayPal was checked and the payment
     was found. Amounts and dates come strictly from the code's verification
     result (`found_amount` / `found_date` / `recent_matches`); the model invents
     no numbers. Settled-in-full conclusions such as "account settled" remain
     forbidden
   - Not found → say only "we have received your payment notice / we don't see it
     yet", never implying the money arrived
   - In the ledger `claimed` and `confirmed` remain two states; it counts as
     confirmed only when the landlord hits send

4. **The agent stays stateless.** Waiting states such as "waiting for photos" are
   implemented with Firestore state plus the next Cloud Scheduler poll. Never
   loop and wait inside the agent — that keeps a Cloud Run container alive and
   burns money.

5. **PayPal is mocked.** The mock is isolated in the single function
   `_lookup_transactions()` in `payment.py`, which reads
   `fixtures/paypal_transactions.json`. Do not let mock logic spread elsewhere.

---

## Channel architecture

**Everything goes through the Gmail API, so only one OAuth setup is needed.**

| Direction | Channel | How to recognize it in Gmail |
|---|---|---|
| Tenant sends an SMS | Google Voice forwards to the mailbox | `from:txt.voice.google.com` |
| Reply by SMS to a tenant | Reply to that Voice email | reply |
| Tenant sends photos/video | The tenant's own mailbox | `from:<tenant email>` + attachment |

Google Voice has no public API. The forwarded-email format is its own convention,
not a documented interface — so **the parsing logic must live in its own
function**, so that a Google format change only requires one edit.

---

## Business facts

- 5 rooms, $1000/month each
- **The due day differs per unit**, recorded as `rent_due_day` in
  `fixtures/tenants.json` (1F-A/1F-B on the 1st, 2F-A/2F-B on the 3rd, 3F-A on
  the 5th). Amounts likewise come per-unit from `rent_amount` — never use a
  global constant.
- Tenants pay via PayPal and then text the amount; the landlord's reply text
  serves as the receipt
- Maintenance is reported by text; when the description is unclear, ask for
  photos/video (which arrive by email)
- The landlord rarely checks email, so **the daily digest must go out as an SMS**

---

## Development environment

```bash
source .venv/bin/activate
adk web              # run from the project root, do not cd into the_super/
```

`.env` lives in `the_super/` and contains `GOOGLE_GENAI_USE_ENTERPRISE=FALSE`
and `GOOGLE_API_KEY` (an AI Studio free key, not Vertex).

---

## Implementation order

Phase 1 (required): read Gmail → classify and route → payment branch → daily digest
Phase 2 (the core highlight): maintenance triage + requesting photos when unclear
Phase 3 (bonus): cross-channel attachment correlation + Gemini image understanding
Phase 4 (if time allows): dispatch briefs, rent-cycle collection

**If time is short, cut Phase 3/4.** Saturday must be left entirely free for
recording the demo video.

---

## Demo notes

Before recording, replace every tenant name, phone number, email address, and
real unit number with fake data.
The submission notes must state clearly that PayPal verification is mocked.
