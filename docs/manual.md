# User Manual

---

## 1. The single most important thing: nothing runs in the background

This is **not** a resident service. You send a text and the system does nothing
at all — unless someone runs `python main.py poll`.

```
You text  ──►  Google Voice forwards it as email into Gmail  ──►  [ it stops here and waits ]
                                                                            │
                       it only continues when you run `main.py poll` ───────┘
```

This is **deliberate** (see architectural constraint 4 in `CLAUDE.md`). The agent
stays stateless, and waiting is implemented as "record it, exit, look again next
round". Keeping a container alive to idle costs money continuously.

In production Cloud Scheduler wakes it every 5–15 minutes; during development and
the demo, you run it by hand.

---

## 2. The three ways to run it — don't mix them up

| Command | Reads real Gmail? | Calls Gemini? | Sends texts? | Purpose |
|---|---|---|---|---|
| `python main.py poll` | ✅ yes | ✅ yes | ✅ yes | Really process new messages |
| `python demo.py <scenario>` | ❌ no | ✅ yes | ⚠️ under DRY_RUN | Demo, with fake messages |
| `python demo.py rent <date>` | ❌ no | ❌ no | ❌ no | Pure computation with a simulated date |

`demo.py rent` **never touches Gmail**; it just computes the rent status and
prints it. Running it in another window has no effect on the text you just sent —
they are two independent paths.

---

## 3. The `DRY_RUN` safety switch

In `the_super/.env`:

```
DRY_RUN=true     # every send/draft only logs; nothing actually happens
DRY_RUN=false    # really send
```

**Keep it `true` for the first run against a real mailbox.** A sent text cannot
be recalled.

With `true` you will see:

```
[DRY_RUN] would send -> thread=1a05804f1204...
Hi, we have received your maintenance report regarding...
```

---

## 4. A full round trip (phone → agent → phone)

### Setup

```bash
cd the-super
source .venv/bin/activate
python authorize.py       # confirm the OAuth token is valid (7-day expiry while in Testing)
```

Confirm `DRY_RUN=true` in `.env`.

### Step 1: text from your phone

Send to your Google Voice number (**in English** — tenants read English only):

> `The toilet is leaking.`

Wait 10–30 seconds for Voice to forward it to Gmail.

### Step 2: confirm it arrived

```bash
python -c "
from dotenv import load_dotenv; load_dotenv('the_super/.env')
from the_super.tools.gmail import read_new_messages
for m in read_new_messages(): print(f'[{m.source}] {m.room_id}: {m.body}')"
```

Seeing `[sms] 1F-A: The toilet is leaking.` means it worked.

⚠️ This command **consumes the cursor** — messages that have been read are
recorded in `history_cursor.json` and won't come back. To replay, first run
`rm -f the_super/fixtures/history_cursor.json`.

### Step 3: run the full pipeline

```bash
rm -f the_super/fixtures/history_cursor.json   # allow that message to replay
python main.py poll
```

You'll see classification → severity → drafting → sending (log-only under DRY_RUN).

### Step 4: actually send it

Set `DRY_RUN=false` in `.env` and re-run step 3.
A few seconds later your phone receives the agent's English reply asking for photos.

**That is the complete round trip.** It is the most convincing segment to record.

---

## 4.5. Changing how the agent speaks — without changing code

Every wording standard for tenants lives in `skills/tenant-sms/` as ordinary markdown:

| File | What it governs |
|---|---|
| `SKILL.md` | Hard rules (forbidden phrasing), tone, and the reference index |
| `references/payment.md` | Receipts, the branches for an unstated month, underpaid, overpaid, not found |
| `references/maintenance.md` | Which spots to photograph per fixture type, emergency self-help |
| `references/collections.md` | Wording per collection stage + the legal timeline |
| `references/holding.md` | The four grades of holding reply |

**Editing these files changes how the agent speaks — no Python changes, no
restart.** The model picks up the new content on the next run.

It uses the ADK 2.0 Skills mechanism with on-demand loading: the model reads the
index in `SKILL.md`, decides which reference this case needs, and reads only that
one. So the rules can be as detailed as you like without blowing up the context.

⚠️ **But only "how to say it" belongs to the Skill.**
Judgments like "does the amount match", "was the month stated clearly", and "may
this be auto-sent" are deterministic code (`payment.py` / `rent.py`). Writing
"a small amount difference is acceptable" in SKILL.md does nothing — the router
never reads it.

---

## 5. What is sent automatically, and what is only drafted

| Type | Behavior | Why |
|---|---|---|
| Text asking for photos | ✅ **auto-sent** | Zero risk; worst case you ask again |
| Asking "which month is this rent for" | ✅ **auto-sent** | Same, and not asking means booking the wrong month |
| Holding reply (received, we'll follow up) | ✅ **auto-sent** | Commits to nothing; the safest outbound message |
| Rent collection text | ✅ **auto-sent** | Explicitly requested by the landlord (overrides the original constraint 1) |
| Daily digest / summary to the landlord | ✅ **auto-sent** | The recipient is the landlord themselves |
| Payment receipt | 📝 **saved as a Gmail draft** | It is a financial record; a human must hit send |
| Dispatch brief | 📝 **stored pending approval** | It spends money |
| Amount mismatch / nothing found | 🚫 **no reply to the tenant** | Escalated to a human directly |
| 14-day statutory notice | 🚫 **never generated** | An SMS is not valid service; this must happen offline |

### About holding replies

For messages that are unclear or that touch the lease, the deposit, or legal
matters, the system **does not engage with the substance** — but it does
automatically reply "received, someone will follow up" and then notify you.

Before this existed, the outcome for such messages was "notify the landlord, and
the tenant hears nothing at all". A tenant sends a text about their deposit and
gets no response for two days — that silence escalates the conflict, and in a
dispute it works against the landlord ("I sent a message and nobody ever
replied").

The wording comes in four grades (see `references/holding.md`); the legal one is
the shortest and takes no position at all.

Once a receipt is saved as a draft, you just **open Gmail and hit send** — no
custom interface needed, and it works from a phone.

---

## 5.5. Running the tests

```bash
source .venv/bin/activate
pytest                 # 32 tests, well under a second
```

They cover the deterministic layer: the rent timeline (per-tenant due days, the
5-day cure period), payment verification (per-tenant amounts, verified /
underpaid / overpaid / not found), the routing rules, and the ledger writes.
No network, no model calls, and no dependence on the system clock — `today` is a
parameter of `check_rent`, and PayPal access goes through the single
`_lookup_transactions` seam, so both are injected in `tests/conftest.py`.

The model-driven nodes are not asserted on. That is deliberate rather than a
gap: their output shifts with the model version, and the whole architecture
exists so that nothing with financial or legal consequences depends on it.

⚠️ Every test redirects the ledger to a temp file (an autouse fixture). If you
add a test that writes state, do not opt out of it — an earlier version was
opt-in and one test wrote straight into `fixtures/ledger.json`, corrupting demo
state and making an unrelated test fail through leftover data.

## 6. The three triggers

```bash
python main.py poll      # fetch new messages → classify → take the matching branch
python main.py rent      # rent cycle: check each unit's own due day; collection the day after it is late
python main.py digest    # daily digest: SMS to the landlord, including tickets overdue on photos
```

In production Cloud Scheduler calls them separately: poll every 5–15 minutes,
rent once a day, digest each evening.

### Local scheduling (optional)

Use launchd on macOS. Note that `main.py` loads `.env` itself, so the plist does
not need to redeclare environment variables:

```xml
<key>ProgramArguments</key>
<array>
  <string>/absolute/path/the-super/.venv/bin/python</string>
  <string>/absolute/path/the-super/main.py</string>
  <string>poll</string>
</array>
<key>StartInterval</key><integer>600</integer>
```

---

## 7. Troubleshooting

**I sent a text and nothing happened**
→ There is no background process. Run `python main.py poll`.

**poll ran but read no messages**
→ The message is already marked processed by the cursor. Run
   `rm -f the_super/fixtures/history_cursor.json` to replay.

**It read the message but can't identify the tenant**
→ The sending number isn't in `fixtures/tenants.json`. Messages that
   `identify_tenant` can't resolve are skipped.

**No reply from the agent**
→ Check whether `DRY_RUN` in `.env` is still `true`.

**Collection texts won't send**
→ Voice has no interface for texting a number out of the blue; it can only reply
   to an existing email thread. That tenant must have texted your Voice number at
   least once for the system to have recorded a thread id (stored in
   `fixtures/threads.json`). The summary marks these as
   `⚠️ no SMS thread, not sent`.

**`KeyError: Context variable not found`**
→ Some Agent's instruction contains `{}`. ADK treats curly braces as session
   state variables to inject. Instructions must not contain curly braces.

**OAuth returns 403 access_denied**
→ This Gmail account isn't in the "test users" list on the OAuth consent screen.

**The token suddenly stopped working**
→ While in Testing, the refresh token expires after 7 days. Re-run
   `python authorize.py`. `gmail.modify` is a restricted scope, so leaving
   Testing requires full verification — there is no way around it.

---

## 8. State files

All in `the_super/fixtures/`, all gitignored:

| File | Contents | Safe to delete? |
|---|---|---|
| `tenants.json` | Tenant roster (contains a real number) | ❌ without it the system can't identify anyone |
| `paypal_transactions.json` | Mock ledger | Regenerate with `python -m the_super.fixtures_gen <month>` |
| `history_cursor.json` | Processed message ids | ✅ delete to replay messages |
| `ledger.json` | Payment ledger | ✅ delete to reset |
| `tickets.json` | Maintenance tickets | ✅ delete to reset |
| `threads.json` | Each tenant's Voice email thread | ⚠️ delete it and collection texts can't be sent |

Reset before recording the demo:

```bash
rm -f the_super/fixtures/{tickets,ledger,history_cursor}.json
```
