"""房租周期催缴 —— 全部确定性代码,不调模型。

CLAUDE.md 约束 2:有财务或法律后果的判断必须写成确定性代码。
催缴短信的**触发条件和文案都是法律敏感的**,所以连措辞都是模板,
不交给模型生成 —— 模型每次换词,而这类文本将来可能要作为证据。

时间线(每个租客按自己合同的应付日,不是全局统一):
    应付日当天        —— 不算逾期
    应付日 + 1 天     —— 仍未收到全额 → 起草催缴短信,给 5 天补缴期
    补缴期满(逾期 6 天)—— 此时逾期已满 5 天,房东才有资格走 14 天通知程序

⚠️ 这里只**起草**。不自动发给租客(架构约束 1),也不生成任何法律通知 ——
   14 天通知涉及法定送达形式,短信通常不构成有效送达,必须走线下。
"""

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from .payment import _lookup_transactions
from .tenants import days_overdue, load_tenants, rent_due_date

# 应付日次日开始催缴
COLLECTION_TRIGGER_DAYS = 1

# 催缴短信给的补缴期。5 天是有来由的:逾期满 5 天之后房东才可以
# 启动 14 天通知程序,所以补缴期设成 5 天既给了租客机会,
# 也让时间线自然衔接到下一步。
CURE_PERIOD_DAYS = 5


class RentStatus(BaseModel):
    """某租客某月的房租状态。纯事实,不含动作。"""
    room_id: str
    tenant_name: str
    tenant_email: str
    month: str
    due_date: str                       # ISO 日期
    days_overdue: int
    expected_amount: float
    found_amount: float
    status: Literal["not_yet_due", "paid_full", "underpaid", "nothing_received"]
    needs_collection: bool
    # ⚠️ 不催缴时正是 None,必须给默认值
    cure_deadline: str | None = None    # 补缴截止日


def check_one(tenant: dict, month: str, today: date) -> RentStatus:
    """单个租客的房租状态。纯函数,可单测。"""
    due = rent_due_date(tenant, month)
    overdue = days_overdue(tenant, month, today)
    expected = float(tenant["rent_amount"])

    # 金额来源是租客自己的 rent_amount,不是全局常量 ——
    # 不同租客合同金额可以不同。
    txns = _lookup_transactions(tenant["email"], month)
    found = round(sum(t["amount"] for t in txns), 2)

    if overdue < COLLECTION_TRIGGER_DAYS:
        status = "paid_full" if found >= expected else "not_yet_due"
    elif found >= expected:
        status = "paid_full"
    elif found > 0:
        status = "underpaid"
    else:
        status = "nothing_received"

    needs = status in ("underpaid", "nothing_received")
    return RentStatus(
        room_id=tenant["room_id"],
        tenant_name=tenant["name"],
        tenant_email=tenant["email"],
        month=month,
        due_date=due.isoformat(),
        days_overdue=overdue,
        expected_amount=expected,
        found_amount=found,
        status=status,
        needs_collection=needs,
        cure_deadline=(today + timedelta(days=CURE_PERIOD_DAYS)).isoformat()
                      if needs else None,
    )


def check_rent(month: str, today: date) -> list[RentStatus]:
    """全部租客的房租状态,按逾期天数倒序。"""
    rows = [check_one(t, month, today) for t in load_tenants()]
    return sorted(rows, key=lambda r: -r.days_overdue)


def build_collection_sms(s: RentStatus) -> str:
    """催缴短信正文 —— 确定性模板。

    刻意做到的几点:
    · 不含任何法律威胁措辞。这条短信的作用是提醒和留痕,不是通知。
    · 明确留出"你可能已经付了"的余地 —— PayPal eCheck 最长 3 个工作日
      才到账,租客确实可能已付而我们还没看到。
    · 少付的情况报出差额,不含糊。
    """
    if s.status == "underpaid":
        detail = (f"We received ${s.found_amount:.2f}, which is "
                  f"${s.expected_amount - s.found_amount:.2f} short of the "
                  f"${s.expected_amount:.2f} due.")
    else:
        detail = (f"We have not received your {s.month} rent payment of "
                  f"${s.expected_amount:.2f}.")

    return (
        f"Hi {s.tenant_name},\n\n"
        f"Your {s.month} rent was due on {s.due_date}. {detail}\n\n"
        f"Please complete payment by {s.cure_deadline}.\n\n"
        f"If you have already paid, please reply with the payment method and "
        f"date. PayPal eChecks can take up to 3 business days to clear, and "
        f"we will re-check."
    )
