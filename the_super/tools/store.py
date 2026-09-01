"""持久化层 —— 台账和工单。

本地开发用 JSON 文件,上 Cloud Run 换 Firestore。
Cloud Run 容器随时会被回收,内存态一定丢,所以状态必须外置。

切换只改 USE_LOCAL 一个开关。
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

USE_LOCAL = True
DATA_DIR = Path(__file__).parent.parent / "fixtures"
LEDGER_FILE = DATA_DIR / "ledger.json"
TICKETS_FILE = DATA_DIR / "tickets.json"

MEDIA_WAIT_HOURS = 48   # 超过这个时间还没收到照片就提醒


def _load(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- 台账

def write_ledger(month: str, room_id: str, claimed_amount: float,
                 found_amount: float | None, status: str,
                 txn_id: str | None = None) -> None:
    """记一笔付款。

    status: claimed | confirmed | disputed | missing

    注意 claimed 和 confirmed 是两个状态。租客说付了不等于房东确认了 ——
    这个区分在租务纠纷里可能很重要,不要合并。
    """
    ledger = _load(LEDGER_FILE, {})
    ledger.setdefault(month, {})[room_id] = {
        "claimed_amount": claimed_amount,
        "found_amount": found_amount,
        "txn_id": txn_id,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(LEDGER_FILE, ledger)


def get_ledger(month: str) -> dict:
    return _load(LEDGER_FILE, {}).get(month, {})


def get_unpaid_rooms(month: str, all_rooms: list[str]) -> list[str]:
    """房租周期用:这个月还没有付款声明的房间。"""
    # 白名单而非黑名单:只有明确结清的状态才算"不用催"。
    # 原先写成 `status == "missing"` 才算未付,导致 disputed(纠纷中,
    # 最该跟进的那些)静默掉出催缴队列。
    settled = {"claimed", "confirmed"}
    paid = get_ledger(month)
    return [r for r in all_rooms
            if r not in paid or paid[r].get("status") not in settled]


# ---------------------------------------------------------------- 工单

def write_ticket(status: str, room_id: str | None = None,
                 draft: dict | None = None, draft_sms: str | None = None,
                 ticket_id: str | None = None, **fields) -> str:
    """新建或更新工单,返回 ticket_id。

    status: open | awaiting_media | ready_to_dispatch | dispatched | closed
    """
    tickets = _load(TICKETS_FILE, {})
    tid = ticket_id or f"T-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(timezone.utc)

    record = tickets.get(tid, {"ticket_id": tid, "opened_at": now.isoformat()})
    record.update({
        "status": status,
        "room_id": room_id or record.get("room_id"),
        "updated_at": now.isoformat(),
        **fields,
    })
    if draft:
        record["draft"] = draft
    if draft_sms:
        record["draft_sms"] = draft_sms
    if status == "awaiting_media":
        record["media_deadline"] = (
            now + timedelta(hours=MEDIA_WAIT_HOURS)
        ).isoformat()

    tickets[tid] = record
    _save(TICKETS_FILE, tickets)
    return tid


def get_ticket_history(room_id: str, limit: int = 10) -> list[dict]:
    """这个房间的历史工单 —— 用来识别重复问题。

    同一个部位反复出问题,是和第一次完全不同的情况,
    简报里必须让房东看到。
    """
    tickets = _load(TICKETS_FILE, {})
    matched = [t for t in tickets.values() if t.get("room_id") == room_id]
    matched.sort(key=lambda t: t.get("opened_at", ""), reverse=True)
    return matched[:limit]


def get_overdue_media_tickets() -> list[dict]:
    """超时仍未收到照片的工单 —— 日报里提醒房东。

    这就是"等待"的正确实现方式:状态存在这里,
    由 Cloud Scheduler 下一轮唤醒时检查,
    而不是让 agent 在内部循环等待(那会让容器常驻烧钱)。
    """
    now = datetime.now(timezone.utc).isoformat()
    tickets = _load(TICKETS_FILE, {})
    return [t for t in tickets.values()
            if t.get("status") == "awaiting_media"
            and t.get("media_deadline", "") < now]


# ---------------------------------------------------------------- 出站线程登记

THREADS_FILE = DATA_DIR / "threads.json"


def remember_thread(room_id: str, gmail_thread_id: str) -> None:
    """记住某租客最近一次的 Voice 邮件线程。

    Voice 只能"回复已有线程"来发短信,没有"主动给某号码发"的接口。
    催缴是定时触发的,手上没有来信可回 —— 所以收信时就要把线程存下来。
    """
    d = _load(THREADS_FILE, {})
    d[room_id] = {"thread_id": gmail_thread_id,
                  "updated_at": datetime.now(timezone.utc).isoformat()}
    _save(THREADS_FILE, d)


def get_thread(room_id: str) -> str | None:
    return (_load(THREADS_FILE, {}).get(room_id) or {}).get("thread_id")
