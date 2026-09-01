"""录 demo 用的场景播放器 —— 不属于应用,只是个演示入口。

    python demo.py list                  列出所有场景
    python demo.py pay-ok                跑单个消息场景
    python demo.py rent 2026-08-02       模拟某天跑房租周期
    python demo.py all                   依次跑完所有消息场景

消息场景一律用**假号码假邮箱**构造,不碰真实名册,录屏不会泄露 PII。
日期靠参数注入(check_rent 的 today 是参数),不用改系统时间。
"""

import asyncio
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "the_super" / ".env")

from google.adk.runners import InMemoryRunner          # noqa: E402
from google.genai import types                          # noqa: E402

from the_super.agent import root_agent                  # noqa: E402
from the_super.rent import build_collection_sms, check_rent  # noqa: E402
from the_super.schemas import IncomingMessage           # noqa: E402

# room_id 用名册里的房间,但 sender/email 用假值 —— 演示不需要真号码
SCENARIOS = {
    "pay-ok": ("💰 付款 · 说明了月份 + 金额相符 → 起草回执",
               "1F-A", "Hi, I just sent $1000 for September rent via PayPal."),
    "pay-nomonth": ("❓ 付款 · 没说是哪个月 → 自动发短信确认,不出回执",
                    "1F-A", "Hi, I just sent you the rent via PayPal."),
    "pay-short": ("💰 付款 · 少付 → 不回执,转人工",
                  "1F-B", "Just PayPal'd you $600 for September rent. Money is "
                          "tight this month, I'll send the rest next week."),
    "pay-none": ("💰 付款 · 账本查无记录 → 转人工",
                 "2F-A", "I sent September rent yesterday, should be there by now."),
    "pay-vague": ("🤔 含糊 · 低置信度 → 安全阀转人工",
                  "2F-B", "Hey, can we talk about the money stuff sometime?"),
    "legal": ("⚖️ 涉及法律 → 缓冲回复,不表态",
              "2F-A", "My lawyer says the security deposit should have been "
                      "returned within 14 days. I want it back this week."),
    "lease": ("📄 涉及租约 → 缓冲回复,交给房东",
              "2F-B", "I'm thinking about moving out early. What happens to "
                      "my deposit if I break the lease?"),
    "fix-vague": ("🔧 报修 · 描述不清 → 索要照片",
                  "3F-A", "The toilet is leaking."),
    "fix-clear": ("🔧 报修 · 描述清楚 → 生成派单简报",
                  "3F-A", "Water is seeping from the seam where the toilet base "
                          "meets the bathroom floor. Started last night, there's "
                          "a small puddle on the floor now."),
    "fix-urgent": ("🔧 报修 · 紧急 → urgent 定级",
                   "1F-A", "The kitchen pipe burst, water is spraying everywhere. "
                           "I shut off the main valve."),
}


def make_message(room_id: str, body: str) -> IncomingMessage:
    """构造一条进来的短信。sender/email 全是假的。"""
    idx = "ABCDE"[["1F-A", "1F-B", "2F-A", "2F-B", "3F-A"].index(room_id)]
    return IncomingMessage(
        source="sms",
        gmail_thread_id=f"DEMO-THREAD-{room_id}",
        gmail_message_id=f"DEMO-MSG-{room_id}",
        sender=f"+1917555010{idx and '12345'[ord(idx)-65]}",
        room_id=room_id,
        tenant_email=f"tenant.{idx.lower()}@example.com",
        body=body,
        received_at=datetime.now(timezone.utc).isoformat(),
    )


async def run_message(key: str) -> None:
    title, room_id, body = SCENARIOS[key]
    print(f"\n{'='*66}\n{title}\n{'='*66}")
    print(f"📱 {room_id} 发来短信:\n   「{body}」\n")
    print("─" * 66)

    msg = make_message(room_id, body)
    runner = InMemoryRunner(agent=root_agent, app_name="demo")
    await runner.session_service.create_session(
        app_name="demo", user_id=room_id, session_id=key)

    async for ev in runner.run_async(
            user_id=room_id, session_id=key,
            new_message=types.Content(
                role="user", parts=[types.Part(text=msg.model_dump_json())])):
        if ev.content and ev.content.parts:
            for p in ev.content.parts:
                if p.text and p.text.strip():
                    print(f"🤖 {p.text.strip()}\n")


def run_rent(day: str) -> None:
    today = date.fromisoformat(day)
    month = f"{today:%Y-%m}"
    print(f"\n{'='*66}\n📅 如果今天是 {today}(每户按自己合同的应付日)\n{'='*66}")
    for s in check_rent(month, today):
        mark = "🔔 触发催缴" if s.needs_collection else ""
        print(f"  {s.room_id} {s.tenant_name:9} 应付 {s.due_date}  "
              f"逾期 {s.days_overdue:>2} 天  "
              f"收到 ${s.found_amount:>7.2f}/{s.expected_amount:.0f}  "
              f"{s.status:<17}{mark}")
    pending = [s for s in check_rent(month, today) if s.needs_collection]
    if pending:
        print(f"\n──── 发给 {pending[0].room_id} 的催缴短信 ────")
        print(build_collection_sms(pending[0]))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print("消息场景:")
        for k, (t, r, b) in SCENARIOS.items():
            print(f"  {k:12} {t}")
        print("\n房租时间线:  python demo.py rent 2026-08-02")
    elif cmd == "rent":
        run_rent(sys.argv[2] if len(sys.argv) > 2 else str(date.today()))
    elif cmd == "all":
        for k in SCENARIOS:
            asyncio.run(run_message(k))
    else:
        asyncio.run(run_message(cmd))
