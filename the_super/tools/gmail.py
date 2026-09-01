"""Gmail 工具 —— 短信收发和邮件附件全部走这里。

Google Voice 开启 "Forward messages to email" 后:
  · 租客短信 → 变成来自 txt.voice.google.com 的邮件
  · 回复那封邮件 → Voice 以短信形式发回给租客

⚠️ 这不是正式 API,Voice 的邮件格式由 Google 自行决定,随时可能变。
   所有格式相关的常量和逻辑集中在下面"Voice 邮件解析"一节,坏了只改这一处。
   解析规则依据 fixtures/sample_voice_email.txt(2026-08-31 实测样本)。

⚠️ 基础设施故障(网络、认证过期、Gmail 5xx)一律让它抛出去 —— ADK 2.0 会重试。
   这里不写 except Exception(见 CLAUDE.md)。
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
from ..tenants import identify_tenant, normalize_phone  # noqa: F401  (渠道层沿用此名)

VOICE_SENDER_DOMAIN = "txt.voice.google.com"

# 处理过的 message id,避免重复处理。删掉这个文件即可重放(录 demo 有用)。
CURSOR_FILE = Path(os.environ.get(
    "GMAIL_HISTORY_FILE",
    Path(__file__).parent.parent / "fixtures" / "history_cursor.json"))

# DRY_RUN=true 时一切写操作只打日志不真执行。第一次对真实邮箱跑务必开着。
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")


def _service():
    """Gmail API client。凭据复用 authorize.py 里那套。"""
    from authorize import get_credentials
    return build("gmail", "v1", credentials=get_credentials())


# ---------------------------------------------------------------- Voice 邮件解析
# 以下 4 个常量就是"Google 改版时只改这一处"的那一处。

# Subject 实测形如: "New text message from (917) 555-0101"
VOICE_SUBJECT_RE = re.compile(r"new text message from\s+(.+?)\s*$", re.I)

# text/plain 正文里,短信原文夹在这两行样板之间
VOICE_BODY_START = "<https://voice.google.com>"
VOICE_BODY_END = "To respond to this text message"


def parse_voice_email(raw_message: dict) -> tuple[str, str]:
    """从 Voice 转发邮件中抽出 (发件号码, 短信正文)。

    号码返回**规范化后的 10 位**(9175550101),调用方不必再处理格式。
    解析不出来返回空字符串,由调用方决定跳过还是转人工 —— 不抛异常,
    因为格式变化是业务语义问题不是基础设施故障。
    """
    headers = {h["name"].lower(): h["value"]
               for h in raw_message.get("payload", {}).get("headers", [])}

    sender = ""
    m = VOICE_SUBJECT_RE.search(headers.get("subject", ""))
    if m:
        sender = normalize_phone(m.group(1))
    if not sender:
        # 兜底:From 的显示名也是号码 —— "(917) 555-0101" <...@txt.voice.google.com>
        dm = re.match(r'"([^"]+)"', headers.get("from", ""))
        if dm:
            sender = normalize_phone(dm.group(1))

    return sender, _strip_voice_boilerplate(_extract_plain_text(raw_message))


def _strip_voice_boilerplate(plain: str) -> str:
    """剥掉 Voice 加的页脚,只留租客真正发的那几行。

    实测结构:
        1 (空行)
        2 <https://voice.google.com>
        3 ← 短信原文(可能多行)
        4 To respond to this text message, reply to this email or ...
        5+ 一堆链接和 Google 地址
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
        return plain.strip()          # 格式变了 —— 退化成整段,总比丢消息好
    return "\n".join(lines[start:end]).strip()


def _extract_plain_text(raw_message: dict) -> str:
    """从 Gmail message payload 里取 text/plain 正文。"""
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


# ---------------------------------------------------------------- 游标

def _load_seen() -> set[str]:
    if not CURSOR_FILE.exists():
        return set()
    return set(json.loads(CURSOR_FILE.read_text()).get("seen", []))


def _save_seen(seen: set[str]) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CURSOR_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seen": sorted(seen)[-500:]}, indent=2))
    tmp.replace(CURSOR_FILE)          # 原子替换,写一半崩掉不会毁游标


# ---------------------------------------------------------------- 对外接口

def read_new_messages(max_results: int = 20) -> list[IncomingMessage]:
    """拉未处理的新消息,区分 Voice 短信和租客直发邮件。

    游标用"处理过的 message id 集合"而不是 historyId —— historyId 首次运行
    没有基准,且一旦跳号就静默丢消息。id 集合可以重放(删 cursor 文件即可)。
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
            seen.add(mid)             # 认不出的直接记下,不反复重试
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
        # 存下线程 —— 催缴/回执要靠它才能发得出去
        remember_thread(tenant["room_id"], msg["threadId"])
        seen.add(mid)

    _save_seen(seen)
    return out


def _raw_reply(thread_id: str, text: str) -> dict:
    """构造一封回复到指定 thread 的邮件。"""
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
    """建草稿。**不发送。** 房东在 Gmail 里点发送,Voice 转成短信发给租客。"""
    if DRY_RUN:
        print(f"[DRY_RUN] 本应建草稿 -> thread={gmail_thread_id}\n{text}\n")
        return "dry-run-draft"
    body = _raw_reply(gmail_thread_id, text)
    draft = _service().users().drafts().create(
        userId="me", body={"message": body}).execute()
    return draft["id"]


def send_sms_now(gmail_thread_id: str, text: str) -> str:
    """立即发送。见 CLAUDE.md 架构约束 1 —— 只用于允许自动发的场景。"""
    if DRY_RUN:
        print(f"[DRY_RUN] 本应发送 -> thread={gmail_thread_id}\n{text}\n")
        return "dry-run-sent"
    sent = _service().users().messages().send(
        userId="me", body=_raw_reply(gmail_thread_id, text)).execute()
    return sent["id"]


def fetch_attachments(gmail_message_id: str) -> list[dict]:
    """取附件,返回 [{filename, mime_type, data(bytes)}, ...]。

    图片直接交给 Gemini 做视觉判断,所以返回原始字节而非落盘。
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
