"""维修分支 (ADK 2.0 graph workflow)。

流程:定级 → 判断描述是否清楚 → 路由
  · 描述不清 → 起草索要照片的短信(可自动发,零风险)
  · 描述清楚 → 查历史 → 起草给房东的派单简报(必须人工批准)
"""

from typing import Literal

from pydantic import BaseModel
from google.adk import Agent, Event, Workflow
from google.adk.agents.context import Context

from .skills_registry import TENANT_SMS_SKILL

from .schemas import IncomingMessage, RoutedMessage
from .tools.gmail import send_sms_now
from .tools.store import get_ticket_history, write_ticket


class Triage(BaseModel):
    """维修请求的初步判断。"""
    room_id: str
    description: str                                  # 租客原话
    severity: Literal["urgent", "normal", "low"]
    clarity: Literal["clear", "needs_media"]
    missing_info: str                                 # 缺什么信息(clarity=clear 时为空)
    suggested_photos: str                             # 建议拍什么(clarity=clear 时为空)


class TicketDraft(BaseModel):
    ticket_id: str
    room_id: str
    severity: str
    summary: str
    # 必须给默认值 —— `str | None` 不带默认在 pydantic 里仍是必填,
    # 模型没返回这个字段就会 ValidationError,整条分支崩掉。
    repeat_of: str | None = None                      # 命中历史工单则填其 id
    contractor_brief: str


# ---------------------------------------------------------------- AI 节点

triage_agent = Agent(
    name="maintenance_triage",
    model="gemini-flash-latest",
    input_schema=RoutedMessage,
    instruction="""你负责给租客的报修请求定级,并判断描述是否足够清楚。

租客原话在 message.body,房间号在 message.room_id ——
room_id 直接原样复制,不要推断。

**定级规则:**
- urgent —— 涉及水(漏水/爆管)、电、燃气、暖气失效、任何安全隐患
- normal —— 影响正常使用但不紧急(电器故障、门窗损坏)
- low —— 不影响使用(异响、外观问题)

**描述是否清楚:**
描述清楚 = 同时说明了「哪个设备或位置」+「什么现象」+「大概什么时候开始」。
只要缺一项就是 needs_media。

例子:
- "厨房水槽下面的管子在滴水,昨晚开始的" → clear
- "厨房坏了" → needs_media,缺现象和位置
- "空调不制冷" → needs_media,缺开始时间和具体表现

**needs_media 时:**
- missing_info 写清楚缺什么
- suggested_photos 要具体说明拍什么,不要只写"请发照片"。
  例如"水槽下方管道接头的特写,以及打开水龙头时的短视频"

描述清楚时,missing_info 和 suggested_photos 留空字符串。""",
    output_schema=Triage,
)


request_media_agent = Agent(
    name="request_media",
    model="gemini-flash-latest",
    input_schema=Triage,
    tools=[TENANT_SMS_SKILL],
    instruction="""起草一条短信,向租客索要照片或视频。

**先加载 tenant-sms 技能,并读取 references/maintenance.md** ——
里面有按设备类型该问哪些部位的对照表,以及紧急情况的处理方式。
措辞规范以技能里的为准。

输入的 suggested_photos 是初步建议,技能里的对照表更具体,以对照表为准。

只输出短信正文,不要加任何前后说明。""",
    output_schema=str,
)


brief_agent = Agent(
    name="contractor_brief",
    model="gemini-flash-latest",
    instruction="""根据维修请求和该房间的历史工单,生成给维修师傅的工单简报。

包含:房间、问题描述、紧急程度、是否为重复问题(如果历史里有类似记录,
明确指出上次是什么时候、怎么处理的)。

**用英文写** —— 简报会转发给维修师傅。

简报是给房东看后转发给师傅的,不要写成给租客的口吻。""",
    output_schema=TicketDraft,
)


# ---------------------------------------------------------------- 纯代码节点

def clarity_router(node_input: Triage):
    """描述不清就先要照片,清楚就直接进入派单准备。"""
    if node_input.clarity == "needs_media":
        return Event(route="REQUEST_MEDIA", output=node_input)
    return Event(route="PREPARE_DISPATCH", output=node_input)


def load_history(node_input: Triage) -> dict:
    """查这个房间的历史工单,和当前请求一起交给 brief_agent。"""
    history = get_ticket_history(node_input.room_id)
    return {
        "current": node_input.model_dump(),
        "history": history,
    }


def open_awaiting_media_ticket(node_input: str, ctx: Context):
    """把索要照片的短信**真的发出去**,同时开一张 awaiting_media 工单。

    索要照片是 CLAUDE.md 约束 1 里唯一允许自动发给租客的类型 ——
    零风险,说错了最多再问一次。

    原始消息从 ctx.user_content 取回:这个节点的 node_input 只是短信文本,
    但发送需要 gmail_thread_id,开工单需要 room_id。
    """
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    sent_id = send_sms_now(original.gmail_thread_id, node_input)
    ticket_id = write_ticket(
        status="awaiting_media",
        room_id=original.room_id,          # 之前漏了,导致工单 room_id=None
        draft_sms=node_input,
        sent_message_id=sent_id,
    )
    return Event(
        message=(f"🔧 {original.room_id} 已开工单 {ticket_id}(等待照片)\n"
                 f"索要照片的短信已自动发出。")
    )


def stage_for_approval(node_input: TicketDraft):
    """派单简报准备好 —— 等房东批准,不自动发给师傅。"""
    write_ticket(status="ready_to_dispatch", draft=node_input.model_dump())
    return Event(
        message=(
            f"🔧 {node_input.room_id} 工单 {node_input.ticket_id} 待派单\n"
            f"级别:{node_input.severity}\n"
            f"{node_input.summary}\n"
            + (f"⚠️ 疑似重复问题(参见 {node_input.repeat_of})\n"
               if node_input.repeat_of else "")
            + "简报已备好,确认后转发给师傅。"
        )
    )


# ---------------------------------------------------------------- 图

maintenance_workflow = Workflow(
    name="maintenance_workflow",
    edges=[
        ("START", triage_agent, clarity_router),
        # ⚠️ 路由字典的值必须是**单个**节点。写成元组会被 ADK 当作并行 fan-out
        # (_graph.py: `{"route_x": (node_a, node_b)}  # fan-out: both triggered`),
        # 而不是顺序链 —— 两个节点会各自被触发、拿不到对方的输出。
        # 链要另起 edge item 写。
        (clarity_router, {
            "REQUEST_MEDIA": request_media_agent,
            "PREPARE_DISPATCH": load_history,
        }),
        (request_media_agent, open_awaiting_media_ticket),
        (load_history, brief_agent, stage_for_approval),
    ],
)
