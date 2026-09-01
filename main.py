"""后台入口 —— 由 Cloud Scheduler 触发,不是给人聊天用的。

adk web / adk run 会自己建 Runner 和 SessionService,那是调试用的。
生产环境没有"正在打字的人",触发者是定时任务,所以要自己建。

三个触发器:
  · /poll    每 5–15 分钟  —— 拉新消息,分类,走对应分支
  · /rent    每月 1/2/5 号 —— 房租周期检查
  · /digest  每天傍晚      —— 日报发短信给房东
"""

import asyncio
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# ⚠️ 必须在导入 google.adk 和 the_super.* 之前加载。
# ADK 只在 CLI 路径(adk web / adk run)里自己加载 .env,
# `python main.py poll` 走不到那里 —— 不显式加载的话 GOOGLE_API_KEY
# 和 LANDLORD_SMS_THREAD 全是空的,Cloud Scheduler 的每次触发都是裸奔。
load_dotenv(Path(__file__).parent / "the_super" / ".env")

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402

from the_super.agent import root_agent  # noqa: E402
from the_super.tools.gmail import read_new_messages, send_sms_now  # noqa: E402
from the_super.rent import build_collection_sms, check_rent  # noqa: E402
from the_super.tools.store import (  # noqa: E402
    get_overdue_media_tickets,
    get_thread,
    write_ledger,
    write_ticket,
)

APP_NAME = "the_super"
LANDLORD_THREAD = os.environ.get("LANDLORD_SMS_THREAD", "")

# ⚠️ InMemorySessionService 仅供本地开发。
# Cloud Run 容器随时被回收,上云前必须换成 Firestore 或数据库后端,
# 否则每次重启 agent 都会失忆。
session_service = InMemorySessionService()

runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
)


async def process_one(message) -> None:
    """处理一条消息。

    session 三层结构:app / user / session。
    room_id 当 user_id(区分房间),**message_id** 当 session_id。

    ⚠️ 不能用 gmail_thread_id 当 session_id ——
    Google Voice 把与同一个号码的整段会话归到**一个** Gmail thread,
    所以租客发第二条短信时 thread_id 是重复的,create_session 会抛
    AlreadyExistsError。message_id 才是每条消息唯一的。
    """
    user_id = message.room_id or "unknown"
    session_id = message.gmail_message_id

    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )

    content = types.Content(
        role="user",
        parts=[types.Part(text=message.model_dump_json())],
    )

    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        if event.is_final_response():
            _print_event(message, event)


def _print_event(message, event) -> None:
    """只打印文本,不打印 thought_signature 那堆二进制。"""
    parts = getattr(event.content, "parts", None) or []
    for part in parts:
        text = (getattr(part, "text", None) or "").strip()
        if text:
            print(f"  [{message.room_id}] {text}")


async def poll() -> None:
    """拉新消息并逐条处理。

    ⚠️ 这里的 try/except 是**有意为之**,而且是全项目唯一一处。

    CLAUDE.md 说工具里不要写宽泛 except(会屏蔽 ADK 的框架级重试)。
    但这里不是工具内部,是驱动循环的**逐条业务边界**:一条消息处理失败
    不该让同一批的其他消息全部丢掉。ADK 的节点级重试在 process_one
    内部已经跑完了,走到这里说明那条消息确实处理不了。

    绝不 catch BaseException —— 那会吞掉 NodeInterruptedError,破坏人工介入暂停。
    """
    messages = read_new_messages()
    print(f"{len(messages)} 条新消息\n")

    ok = failed = 0
    for m in messages:
        try:
            await process_one(m)
            ok += 1
        except Exception:
            failed += 1
            print(f"  ❌ [{m.room_id}] 处理失败,跳过这条继续:")
            traceback.print_exc()
        print()

    print(f"完成:成功 {ok} 条,失败 {failed} 条")


async def digest() -> None:
    """日报 —— 发短信给房东本人,因为他不常查邮件。"""
    overdue = get_overdue_media_tickets()
    lines = [f"📋 {datetime.now(timezone.utc):%m-%d} 日报"]

    if overdue:
        lines.append(f"\n⏳ {len(overdue)} 张工单等照片已超时:")
        lines += [f"· {t['room_id']} ({t['ticket_id']})" for t in overdue]
    else:
        lines.append("\n无超时待办。")

    send_sms_now(LANDLORD_THREAD, "\n".join(lines))


async def rent_cycle() -> None:
    """房租周期 —— 每个租客按自己合同的应付日,逾期次日起草催缴。

    ⚠️ 只起草,不自动发给租客(架构约束 1)。给房东本人的汇总可以直接发。
    判定和文案都在 the_super/rent.py 里,是确定性代码,不经过模型。
    """
    today = datetime.now(timezone.utc).date()
    month = f"{today:%Y-%m}"
    rows = check_rent(month, today)

    for s in rows:
        if not s.needs_collection:
            continue
        write_ledger(
            month=s.month,
            room_id=s.room_id,
            claimed_amount=0.0,
            found_amount=s.found_amount,
            status="missing",
        )
        sms = build_collection_sms(s)
        thread = get_thread(s.room_id)
        if thread:
            # ⚠️ 自动发给租客 —— 这是房东明确要求的,覆盖了 CLAUDE.md 约束 1。
            # DRY_RUN=true 时只打日志。改这行之前先想清楚:发出去撤不回。
            sent_id = send_sms_now(thread, sms)
            status = "rent_collection_sent"
        else:
            # 从没收到过这个租客的短信 -> 没有可回复的线程,只能转人工
            sent_id, status = None, "rent_collection_no_thread"
        write_ticket(
            status=status,
            room_id=s.room_id,
            draft_sms=sms,
            month=s.month,
            days_overdue=s.days_overdue,
            sent_message_id=sent_id,
        )

    pending = [s for s in rows if s.needs_collection]
    if not pending:
        send_sms_now(LANDLORD_THREAD, f"✅ {month} 房租已全部收齐。")
        return

    lines = [f"💰 {month} 房租 —— {len(pending)} 户需处理", ""]
    for s in pending:
        gap = s.expected_amount - s.found_amount
        # 区分"已发出"和"发不出去" —— 汇总必须如实反映,
        # 否则房东以为都通知到了,实际有人从没收到。
        mark = "已发催缴" if get_thread(s.room_id) else "⚠️ 无短信线程,未发出"
        lines.append(
            f"· {s.room_id} {s.tenant_name}:应付 {s.due_date},"
            f"逾期 {s.days_overdue} 天,差 ${gap:.2f} —— {mark}"
        )
    blocked = [s for s in pending if not get_thread(s.room_id)]
    lines.append("")
    lines.append(f"已自动发出 {len(pending) - len(blocked)} 条。")
    if blocked:
        lines.append(
            f"{len(blocked)} 户无法发送:Voice 只能回复已有线程,"
            f"这些租客从没给你的 Voice 号发过短信,需要你手动联系。"
        )
    send_sms_now(LANDLORD_THREAD, "\n".join(lines))


if __name__ == "__main__":
    import sys
    task = sys.argv[1] if len(sys.argv) > 1 else "poll"
    asyncio.run({"poll": poll, "digest": digest, "rent": rent_cycle}[task]())
