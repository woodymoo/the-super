"""节点之间传递数据的类型契约。

ADK 2.0 的图引擎靠节点返回值传数据,这些 pydantic 模型就是接口定义。
"""

from typing import Literal

from pydantic import BaseModel


class IncomingMessage(BaseModel):
    """一条进来的消息,已经过渠道解析和租客识别。"""
    source: Literal["sms", "email"]      # sms = 经 Voice 转发,email = 租客直发
    gmail_thread_id: str
    gmail_message_id: str
    sender: str                          # 短信号码 或 邮箱地址
    # ⚠️ Optional 字段必须给默认值。ADK 序列化节点输出时会剥掉 None,
    # 再校验就报 'Field required' —— `X | None` 不带默认在 pydantic 里仍是必填。
    room_id: str | None = None           # 匹配不到租客时为 None
    tenant_email: str | None = None
    body: str
    received_at: str                     # ISO 8601
    has_attachments: bool = False


class Classification(BaseModel):
    """分类结果。"""
    intent: Literal["PAYMENT", "MAINTENANCE", "OTHER"]
    confidence: Literal["high", "low"]
    reason: str


class RoutedMessage(BaseModel):
    """分类结果 + 原始消息。

    分类节点的输出只有 intent/confidence/reason,不含租客原话 ——
    下游的付款抽取和维修定级都需要原文。ADK 2.0 靠返回值传数据,
    所以要把两者合起来往下传。

    ⚠️ 这个对象由 intent_router 用代码组装(原文从 ctx.user_content 取回),
       不是让模型复述一遍 —— 模型复述会悄悄改掉 room_id 这类关键字段。
    """
    message: IncomingMessage
    classification: Classification


class Tenant(BaseModel):
    room_id: str
    name: str
    phone: str
    email: str
    rent_amount: float
