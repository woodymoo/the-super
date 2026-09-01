# Recording the Demo

For the Devpost submission video. 2.5–3 minutes total, in five segments.

> Read [manual.md](manual.md) first — especially the "nothing is running in the
> background" section. The system is scheduled batch processing, not a resident
> service; if you send a text and don't run `main.py poll`, nothing happens.

---

## 0. The three ways to run it (be clear on this before recording)

| Command | Reads real Gmail | Sends texts | Role in the demo |
|---|---|---|---|
| `python main.py poll` | ✅ | ✅ | Segment 3: the real SMS round trip |
| `python demo.py <scenario>` | ❌ | ⚠️ DRY_RUN | Segment 4: the preset scenarios |
| `python demo.py rent <date>` | ❌ | ❌ | Segment 5: pure computation with a simulated date |

`demo.py rent` **does not need a second window running `main.py`** — it never
touches Gmail; it just computes the rent status and prints it. The three paths
are independent.

---

## 1. Before recording (15 minutes)

```bash
cd the-super
source .venv/bin/activate

python authorize.py                          # confirm the OAuth token hasn't expired
python -m the_super.fixtures_gen 2026-08     # align the mock ledger with the month
rm -f the_super/fixtures/tickets.json \
      the_super/fixtures/ledger.json \
      the_super/fixtures/history_cursor.json  # clear debugging leftovers
```

### Checklist

| Item | Requirement |
|---|---|
| `DRY_RUN` in `the_super/.env` | **Keep it `true`.** Saying "every outbound message has a safety switch" during the demo is a plus |
| OAuth token | Expires after 7 days while the app is in Testing, so re-run `authorize.py` on the day |
| Roster redaction | In `tenants.json` only 1F-A's **phone number** is real (needed to claim the incoming message); names and emails are already fake |
| Voice forwarding | `Forward messages to email` must be on in the Voice settings |

### Prepare one real incoming message

From your own phone, text the Voice number in **English** — segment 3 uses it:

> `Hi, I just sent $1000 for this month's rent via PayPal.`

### Must not appear on screen

- **The Gmail UI** — the subject line of a forwarded Voice email contains the real phone number
- `the_super/fixtures/tenants.json`
- `the_super/.env`, `token.json`, `credentials.json`

Terminal output from `python demo.py` and `main.py poll` is clean and contains no
phone numbers.

---

## 2. The five segments

### Segment 1 · The problem (20 s, voiceover only)

Five rooms. Tenants text about rent and repairs. The landlord rarely checks
email. Nothing connects SMS, PayPal, and maintenance tickets.

### Segment 2 · Architecture (40 s)

```bash
python -c "
from the_super.agent import root_agent
from the_super.payment import payment_workflow
from the_super.maintenance import maintenance_workflow
for wf in (root_agent, payment_workflow, maintenance_workflow):
    print(f'\n{wf.name}')
    for e in wf.graph.edges:
        f=getattr(e.from_node,'name',e.from_node); t=getattr(e.to_node,'name',e.to_node)
        r=f'  [{e.route}]' if getattr(e,'route',None) else ''
        print(f'  {f:26} -> {t}{r}')
"
```

Then scroll to `verify_payment()` in `the_super/payment.py` and hold for two seconds.

**The line to say: the amount comparison is `if/else`, not a prompt.**
This is the most important technical claim in the video — judgments with
financial consequences are not left to the model's good behavior.

### Segment 3 · Live run (50 s, the most convincing part)

```bash
python -c "
from dotenv import load_dotenv; load_dotenv('the_super/.env')
from the_super.tools.gmail import read_new_messages
for m in read_new_messages(): print(f'[{m.source}] {m.room_id}: {m.body}')"
```

**Suggested split screen:** your phone on the left (the text you just sent), the
terminal on the right (the parsed result). This segment proves a real Google
Voice SMS travelled through the whole pipeline — it is not mock data.

### Segment 4 · Three contrasts (50 s, the standout)

Each pair is "the same kind of message, one detail different → a completely
different outcome." Recording two of the pairs is enough.

**Pair 1 · Description quality decides the path**

```bash
python demo.py fix-vague     # "The toilet is leaking."
python demo.py fix-clear     # full description of where and when it leaks
```

The first asks for photos, and specifies exactly where (base / behind the tank /
supply line); the second produces the dispatch brief directly, including the
parts to bring.
**The line to say: the agent knows when it doesn't have enough information.**

**Pair 2 · One sentence decides whether it may act automatically**

```bash
python demo.py pay-ok        # "$1000 for September rent"
python demo.py pay-nomonth   # "I just sent you the rent"
```

Same tenant, same $1,000, found in the ledger either way — but the second never
said which month, so the system **refuses to auto-send a receipt**. It checks
PayPal, states what it found, and applies the deterministic allocation rule:
"We've checked our PayPal account and see your $1,000 received on August 30.
Since August rent is already settled, we'll apply this to September — let us know
if you meant a different month."

**The line to say: rent is paid in advance, so money sent at the end of a month
is usually for the next one. Booking an 8/31 payment to August throws the whole
ledger out of alignment. So a matching amount isn't enough — with the month
undetermined, it can't be handled automatically.**

**Pair 3 · Knowing when to say nothing**

```bash
python demo.py legal         # mentions a lawyer and the 14-day deposit rule
```

The reply is one sentence: "We've received your message and it's being reviewed.
We'll follow up with you directly."

Whether the deposit should be returned, whether 14 days is correct — it touches
none of it.
**The line to say: this text may one day be read aloud to a judge, and the only
thing it should prove is that the message was received.**

### Segment 5 · Timeline + safety boundaries (40 s)

```bash
python demo.py rent 2026-08-02
python demo.py rent 2026-08-07
```

Each unit triggers on **its own lease's due day**, not one date for the building.

Then explain the legal timeline:

> Collection goes out on day 1 of being late, with a 5-day cure period. When it
> expires, the rent is exactly 5 days overdue — only then does it qualify for the
> 14-day notice process. **And the agent stops there; it does not generate the
> 14-day notice**, because an SMS does not constitute valid service.

Close with three lines: PayPal is mocked · the `DRY_RUN` safety switch · the
stateless design doesn't burn a resident container.

---

## 3. The scenario player

`demo.py` is a demo-only entry point and not part of the application logic.
Every scenario is constructed with **fake numbers and fake emails**; it never
touches the real roster.

```bash
python demo.py list                # list every scenario
python demo.py fix-vague           # run one
python demo.py all                 # run them all in order
python demo.py rent 2026-08-04     # simulate the rent cycle on a given day
```

### Message scenarios (10)

| Scenario | Incoming text | Expected path | To the tenant |
|---|---|---|---|
| `pay-ok` | "$1000 for **September** rent" | Amount matches → draft the receipt | 📝 draft |
| `pay-nomonth` | "I just sent you the rent" (no month) | **Check PayPal → state what was found and how it applies** | ✅ auto-sent |
| `pay-short` | "$600 for September rent" | Underpaid → **no receipt**, escalate | 🚫 no reply |
| `pay-none` | "I sent September rent yesterday" | Nothing found in the ledger → escalate | 🚫 no reply |
| `pay-vague` | "can we talk about the money stuff" | Low confidence → safety valve | ✅ holding reply |
| `legal` | mentions a lawyer, the 14-day deposit rule | Legal matter → take no position | ✅ holding reply |
| `lease` | asks how the deposit works if they leave early | Lease matter → hand to the landlord | ✅ holding reply |
| `fix-vague` | "The toilet is leaking." | Description unclear → ask for photos | ✅ auto-sent |
| `fix-clear` | full description of where and when it leaks | Produce the dispatch brief | 🚫 awaiting approval |
| `fix-urgent` | "The kitchen pipe burst ..." | Urgent severity + self-help guidance | ✅ auto-sent |

### How dates are simulated

`today` in `check_rent(month, today)` is a **parameter**, not `datetime.now()`,
so you just pass the date — no changing the system clock, no freezegun.

```bash
python demo.py rent 2026-08-02   # 1F (due on the 1st) is late; 2F/3F not yet due
python demo.py rent 2026-08-04   # 2F (due on the 3rd) becomes late
python demo.py rent 2026-08-07   # all 5 units are late
```

The cure deadline moves with the trigger date (triggered 8/2 → due 8/7;
triggered 8/4 → due 8/9), and that detail is worth capturing on video.

---

## 3.5. Skills: wording standards kept outside the code (worth 20 s of its own)

None of the wording standards for talking to tenants live in the code; they live
in `skills/tenant-sms/`:

```
skills/tenant-sms/
├── SKILL.md                  ← hard rules + tone + the index (always in context)
└── references/
    ├── payment.md            ← receipts / the branches for an unstated month / underpaid / overpaid / not found
    ├── maintenance.md        ← the photo checklist by fixture type + emergency self-help
    ├── collections.md        ← the collection stages + the legal timeline
    └── holding.md            ← the four grades of holding reply
```

It uses the ADK 2.0 Skills mechanism (`SkillToolset`) and loads **on demand**: the
model reads the index in `SKILL.md`, decides which reference this case needs, and
then reads that one.

```
instruction:      186 characters   ← only the pointer to "go read the skill"
total skill text: 6300+ characters ← loaded on demand, not resident
```

**How to prove on camera that it is really in effect:** open
`references/maintenance.md`, point at the line "shut off the valve (turn
clockwise)", then run `fix-urgent` — the same phrase appears in the output, and
the word `clockwise` exists nowhere else in the codebase but that markdown file.

**The line to say: wording belongs to the Skill, decisions do not.**
"Does the amount match" and "may this be auto-sent" remain deterministic code.
Editing markdown changes how the agent speaks; it cannot change what it is
allowed to do.

---

## 4. Language

- **Everything outbound (tenants, contractors) is English** — tenants read English only
  - Collection texts: `build_collection_sms()` in `rent.py`, a deterministic template
  - Payment receipts / photo requests / dispatch briefs: the instruction requires
    the model to write English
- Landlord-facing output (daily digest, to-dos, summaries) — see the note in the
  submission checklist below

Receipt wording stays constrained in English too. After verification it must
state what was checked and found ("we've checked our PayPal account and see your
$1,000 received on August 30"); before verification it may say only
`"we have received your payment notice"`, never `"payment confirmed"` /
`"funds received"`, and a settled-in-full conclusion is forbidden either way.

---

## 5. Do you need the Google Voice app?

**The system doesn't.** Everything runs through the Gmail API; Voice only handles
the server-side SMS ↔ email forwarding.

For the recording, **filming your own phone** beats opening voice.google.com —
your number is 1F-A in the roster, so the text comes back to your phone and a
complete round trip is the most convincing thing you can show.

---

## 6. What the submission notes must state

1. **PayPal verification is mocked**, isolated in the single function
   `_lookup_transactions()` in `payment.py`
2. **The Google Voice email parsing is not an official API** — the format is
   Google's own, so it is isolated in the parsing constants in `gmail.py` and a
   format change requires one edit
3. **Collection texts are sent automatically** — this contradicts architectural
   constraint 1 in `CLAUDE.md` (never auto-send outbound messages). It is a
   trade-off the landlord explicitly asked for and needs explaining in the
   submission notes

## 7. Don't

- Don't film Gmail, `tenants.json`, or `.env`
- Don't flip `DRY_RUN` to `false` at the last minute to try a real send — it
  can't be recalled, and it looks identical on screen
- Don't change code on recording day
