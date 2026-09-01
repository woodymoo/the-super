"""Gmail tooling — all SMS traffic and email attachments go through here.

With "Forward messages to email" enabled in Google Voice:
  - A tenant's text -> arrives as an email from txt.voice.google.com
  - Replying to that email -> Voice delivers it to the tenant as a text

⚠️ This is not an official API. The Voice email format is Google's own and can
   change at any time. Every format-dependent constant and rule is collected in
   the "Voice email parsing" section below, so a break requires one edit here.
   The parsing rules follow fixtures/sample_voice_email.txt (a real sample
   captured 2026-08-31).

⚠️ Infrastructure failures (network, expired credentials, Gmail 5xx) are always
   allowed to propagate — ADK 2.0 retries them. No except Exception here
   (see CLAUDE.md).
"""

import base64
import json
import os
import re
from email.mime.text import MIMEText
from pathlib import Path

from googleapiclient.discovery import build

from ..schemas import IncomingMessage
from .store import remember_thread
from ..tenants import identify_tenant, normalize_phone  # noqa: F401  (the channel layer keeps this name)

VOICE_SENDER_DOMAIN = "txt.voice.google.com"

# Processed message ids, to avoid handling one twice. Delete this file to replay
# (useful when recording the demo).
CURSOR_FILE = Path(os.environ.get(
    "GMAIL_HISTORY_FILE",
    Path(__file__).parent.parent / "fixtures" / "history_cursor.json"))

# With DRY_RUN=true every write only logs instead of executing. Keep it on for the
# first run against a real mailbox.
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")


def _service():
    """Gmail API client. Reuses the credentials set up by authorize.py."""
    from authorize import get_credentials
    return build("gmail", "v1", credentials=get_credentials())


# ------------------------------------------------------------ Voice email parsing
# These 4 constants are the "one place to edit when Google changes the format".

# The subject looks like: "New text message from (917) 555-0101"
VOICE_SUBJECT_RE = re.compile(r"new text message from\s+(.+?)\s*$", re.I)

# In the text/plain body, the tenant's message sits between these two boilerplate lines
VOICE_BODY_START = "<https://voice.google.com>"
VOICE_BODY_END = "To respond to this text message"


def parse_voice_email(raw_message: dict) -> tuple[str, str]:
    """Extract (sender number, message body) from a forwarded Voice email.

    The number is returned **normalized to 10 digits** (9175550101), so callers
    don't have to handle formatting. When parsing fails it returns an empty
    string and the caller decides whether to skip or escalate — it does not
    raise, because a format change is a business/semantic problem rather than an
    infrastructure failure.
    """
    headers = {h["name"].lower(): h["value"]
               for h in raw_message.get("payload", {}).get("headers", [])}

    sender = ""
    m = VOICE_SUBJECT_RE.search(headers.get("subject", ""))
    if m:
        sender = normalize_phone(m.group(1))
    if not sender:
        # Fallback: the From display name is also the number — "(917) 555-0101" <...@txt.voice.google.com>
        dm = re.match(r'"([^"]+)"', headers.get("from", ""))
        if dm:
            sender = normalize_phone(dm.group(1))

    return sender, _strip_voice_boilerplate(_extract_plain_text(raw_message))


def _strip_voice_boilerplate(plain: str) -> str:
    """Strip the footer Voice adds, leaving only what the tenant actually sent.

    The observed structure:
        1 (blank line)
        2 <https://voice.google.com>
        3 <- the tenant's message (may span several lines)
        4 To respond to this text message, reply to this email or ...
        5+ a pile of links and Google's address
    """
    lines = plain.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if start is None and s == VOICE_BODY_START:
            start = i + 1
        elif start is not None and s.startswith(VOICE_BODY_END):
            end = i
            break
    if start is None:
        return plain.strip()          # the format changed — fall back to the whole body, better than losing the message
    return "\n".join(lines[start:end]).strip()


def _extract_plain_text(raw_message: dict) -> str:
    """Pull the text/plain body out of a Gmail message payload."""
    def walk(part):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        for sub in part.get("parts", []) or []:
            if found := walk(sub):
                return found
        return None

    return (walk(raw_message.get("payload", {})) or "").strip()


# ---------------------------------------------------------------- Cursor

def _load_seen() -> set[str]:
    if not CURSOR_FILE.exists():
        return set()
    return set(json.loads(CURSOR_FILE.read_text()).get("seen", []))


def _save_seen(seen: set[str]) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CURSOR_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seen": sorted(seen)[-500:]}, indent=2))
    tmp.replace(CURSOR_FILE)          # atomic replace, so a crash mid-write can't corrupt the cursor


# ---------------------------------------------------------------- Public API

def read_new_messages(max_results: int = 20) -> list[IncomingMessage]:
    """Fetch unprocessed new messages, separating Voice texts from tenant email.

    The cursor is a set of processed message ids rather than a historyId —
    historyId has no baseline on the first run, and once it skips it drops
    messages silently. A set of ids can be replayed (just delete the cursor file).
    """
    svc = _service()
    seen = _load_seen()
    out: list[IncomingMessage] = []

    listed = svc.users().messages().list(
        userId="me", maxResults=max_results,
        q=f"from:{VOICE_SENDER_DOMAIN} OR has:attachment").execute()

    for meta in listed.get("messages", []):
        mid = meta["id"]
        if mid in seen:
            continue
        msg = svc.users().messages().get(
            userId="me", id=mid, format="full").execute()
        headers = {h["name"].lower(): h["value"]
                   for h in msg.get("payload", {}).get("headers", [])}
        from_hdr = headers.get("from", "")

        if VOICE_SENDER_DOMAIN in from_hdr:
            sender, body = parse_voice_email(msg)
            source = "sms"
        else:
            sender, body = from_hdr, _extract_plain_text(msg)
            source = "email"

        tenant = identify_tenant(sender) if sender else None
        if tenant is None:
            seen.add(mid)             # record unrecognized senders so we don't retry them endlessly
            continue

        out.append(IncomingMessage(
            source=source,
            gmail_thread_id=msg["threadId"],
            gmail_message_id=mid,
            sender=sender,
            room_id=tenant["room_id"],
            tenant_email=tenant["email"],
            body=body,
            received_at=headers.get("date", ""),
            has_attachments=any(
                p.get("filename") for p in msg.get("payload", {}).get("parts", []) or []),
        ))
        # Remember the thread — collection texts and receipts can only go out through it
        remember_thread(tenant["room_id"], msg["threadId"])
        seen.add(mid)

    _save_seen(seen)
    return out


def _raw_reply(thread_id: str, text: str) -> dict:
    """Build a reply email addressed to the given thread."""
    svc = _service()
    thread = svc.users().threads().get(
        userId="me", id=thread_id, format="metadata").execute()
    headers = {h["name"].lower(): h["value"]
               for h in thread["messages"][0]["payload"]["headers"]}

    mime = MIMEText(text, "plain", "utf-8")
    mime["To"] = headers.get("reply-to") or headers.get("from", "")
    mime["Subject"] = "Re: " + headers.get("subject", "")
    if mid := headers.get("message-id"):
        mime["In-Reply-To"] = mid
        mime["References"] = mid
    return {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode(),
            "threadId": thread_id}


def draft_sms_reply(gmail_thread_id: str, text: str) -> str:
    """Create a draft. **Does not send.** The landlord hits send in Gmail and Voice delivers it as a text."""
    if DRY_RUN:
        print(f"[DRY_RUN] would create draft -> thread={gmail_thread_id}\n{text}\n")
        return "dry-run-draft"
    body = _raw_reply(gmail_thread_id, text)
    draft = _service().users().drafts().create(
        userId="me", body={"message": body}).execute()
    return draft["id"]


def send_sms_now(gmail_thread_id: str, text: str) -> str:
    """Send immediately. See CLAUDE.md constraint 1 — only for the cases where auto-send is allowed."""
    if DRY_RUN:
        print(f"[DRY_RUN] would send -> thread={gmail_thread_id}\n{text}\n")
        return "dry-run-sent"
    sent = _service().users().messages().send(
        userId="me", body=_raw_reply(gmail_thread_id, text)).execute()
    return sent["id"]


def fetch_attachments(gmail_message_id: str) -> list[dict]:
    """Fetch attachments, returning [{filename, mime_type, data(bytes)}, ...].

    Images go straight to Gemini for visual assessment, so this returns raw bytes
    rather than writing to disk.
    """
    svc = _service()
    msg = svc.users().messages().get(
        userId="me", id=gmail_message_id, format="full").execute()
    out = []
    for part in msg.get("payload", {}).get("parts", []) or []:
        if not part.get("filename"):
            continue
        aid = part["body"].get("attachmentId")
        if not aid:
            continue
        att = svc.users().messages().attachments().get(
            userId="me", messageId=gmail_message_id, id=aid).execute()
        out.append({"filename": part["filename"],
                    "mime_type": part.get("mimeType", ""),
                    "data": base64.urlsafe_b64decode(att["data"])})
    return out
