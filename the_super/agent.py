"""The Super —— 根 workflow。

START → 分类 → 路由
  · PAYMENT     → payment_workflow
  · MAINTENANCE → maintenance_workflow
  · OTHER       → 标记待人工处理(不猜)
"""

from google.adk import Agent, Event, Workflow
from google.adk.agents.context import Context

from .schemas import Classification, IncomingMessage, RoutedMessage
from .skills_registry import TENANT_SMS_SKILL
from .tools.gmail import send_sms_now
from .payment import payment_workflow
from .maintenance import maintenance_workflow


classifier = Agent(
    name="classifier",
    model="gemini-flash-latest",
    input_schema=IncomingMessage,
    instruction="""判断这条租客消息属于哪一类。

- PAYMENT —— 提到已付款、PayPal、Zelle、转账、房租金额
- MAINTENANCE —— 报修、损坏、故障、不工作、漏水、没热水、投诉居住问题
- OTHER —— 其他一切(询问、搬家通知、闲聊、无法判断)

**置信度规则:**
只有当消息明确无歧义时才填 high。任何含糊、同时涉及两类、
或你需要猜测的情况,一律填 low。

reason 用一句话说明判断依据。

宁可误判为 OTHER,也不要把不确定的消息塞进 PAYMENT ——
错误写入付款台账的代价,远大于让房东多看一眼。""",
    output_schema=Classification,
)


def intent_router(node_input: Classification, ctx: Context):
    """置信度低的一律转人工,不进自动流程。

    同时把原始消息取回来往下传 —— 分类结果里没有租客原话,
    而付款抽取和维修定级都需要原文。ctx.user_content 是工作流的初始输入。
    """
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    routed = RoutedMessage(message=original, classification=node_input)
    if node_input.confidence == "low":
        return Event(route="OTHER", output=routed)
    return Event(route=node_input.intent, output=routed)


holding_reply_agent = Agent(
    name="holding_reply",
    model="gemini-flash-latest",
    input_schema=RoutedMessage,
    tools=[TENANT_SMS_SKILL],
    instruction="""这条消息系统判断不该自己处理 —— 分类不确定,或涉及租约、
押金、法律等需要房东本人判断的事。

写一条**缓冲回复**:确认收到,说明会有人跟进,**不表任何态**。

**先加载 tenant-sms 技能,读 references/holding.md** ——
里面按情形分了几种写法(分类不确定 / 涉及租约押金 / 涉及法律投诉 /
疑似紧急安全),选对应的那种。

⚠️ 绝不回应实质内容,哪怕问题看起来很简单。
⚠️ 绝不承诺时限。
⚠️ 涉及法律时字越少越好。

只输出短信正文。""",
    output_schema=str,
)


def send_holding_reply(node_input: str, ctx: Context):
    """把缓冲回复发出去,同时通知房东。

    为什么这条可以自动发:它不承诺任何事,是系统能发的最安全的对外消息。
    而在有它之前,这类消息的结果是"通知房东、租客那边完全静默" ——
    沉默本身会激化矛盾,而且在纠纷里对房东不利。
    """
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    send_sms_now(original.gmail_thread_id, node_input)
    return Event(
        message=(
            f"📥 一条消息需要你看一下\n"
            f"来自:{original.room_id}\n"
            f"原文:「{original.body}」\n"
            f"已自动回复租客:已收到,稍后答复(不表态)。"
        )
    )


root_agent = Workflow(
    name="the_super",
    edges=[
        ("START", classifier, intent_router),
        (intent_router, {
            "PAYMENT": payment_workflow,
            "MAINTENANCE": maintenance_workflow,
            "OTHER": holding_reply_agent,
        }),
        (holding_reply_agent, send_holding_reply),
    ],
)
