"""
The Super — 付款验证分支 (ADK 2.0 graph workflow)

mock PayPal 工具 + 图接线。
真实 PayPal Transaction Search API 上线时,只需替换 _lookup_transactions() 一个函数。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from google.adk import Agent, Event, Workflow
from google.adk.agents.context import Context

from .schemas import IncomingMessage
from .skills_registry import TENANT_SMS_SKILL
from .tools.gmail import draft_sms_reply, send_sms_now
from .tools.store import get_ledger, write_ledger
from .tenants import get_tenant, rent_due_date

FIXTURES = Path(__file__).parent / "fixtures" / "paypal_transactions.json"
USE_MOCK = True  # 上线时改 False


# ---------------------------------------------------------------- 数据契约
# 这三个 schema 就是节点之间传递数据的类型契约。
# ADK 2.0 会把每个节点的返回值自动传给下一个节点,不需要写 session state。

class PaymentClaim(BaseModel):
    """租客短信里声称的付款。"""
    room_id: str
    tenant_email: str
    claimed_amount: float
    claimed_method: str          # "paypal" / "zelle" / ...
    month: str                   # "2026-09"
    month_stated: bool           # 租客是否**明确说了**是哪个月的房租


class RecentTxn(BaseModel):
    """最近查到的一笔入账。用来验证租客"我刚转了"这个具体声称。"""
    txn_id: str
    amount: float
    date: str                             # ISO
    days_ago: int
    month_bucket: str                     # 这笔钱落在账本的哪个月


class MonthStatus(BaseModel):
    """某个月的完整实况。所有字段都是代码查出来的事实,不是推断。"""
    month: str
    expected: float
    # claimed(租客声称) / confirmed(房东确认) / disputed / missing / None(无记录)
    # ⚠️ claimed != confirmed。写短信时绝不能把 claimed 说成"我们收到了你的房租"。
    ledger_status: str | None = None
    paypal_found: float = 0.0             # PayPal 查到的该月合计
    settled: bool = False                 # 账本金额已达应付 -> 这个月不用再问


class PaymentContext(BaseModel):
    """问"这是哪个月的房租"时,模型需要知道的事实。

    全部由代码查出来。模型只负责把这些事实组织成一句精确的问话 ——
    不负责查、不负责判断、也不许编造这里没有的事实。
    """
    room_id: str
    tenant_name: str
    claimed_amount: float                 # 租客说他转了多少
    expected_amount: float                # 该租客的月租
    amount_matches_rent: bool             # 声称金额是否正好等于一个月租金
    guessed_month: str                    # 抽取节点的最佳猜测

    # —— 验证租客的**具体声称**:"我刚转了 X" 意味着最近有一笔 X 进账。
    # 不验证这一条,就等于接受了租客的说法。虚报付款正是从这里钻空子。
    recent_matches: list[RecentTxn] = []   # 近期金额吻合的入账
    has_recent_match: bool = False         # 找到了吗
    recent_window_days: int = 5

    months: list[MonthStatus] = []        # 最近几个月的实况(升序)
    unsettled_months: list[str] = []      # 尚未结清的月份(升序) —— 内部信息

    # —— 这笔钱该记到哪个月:确定性规则算出来的,不交给模型判断。
    # 款项归属会影响逾期天数和能不能走法定程序,是有后果的判断(CLAUDE.md 约束 2)。
    suggested_month: str | None = None    # 建议归属月份
    suggested_reason: str = ""            # 为什么 —— 写进短信让租客能核对
    suggestion_is_certain: bool = False   # 只有一个可能 -> 陈述;多个 -> 让租客选
    # 最近一个已结清的月份;据此往后推一个月是常见但**不可靠**的猜测
    last_settled_month: str | None = None


class PaymentVerification(BaseModel):
    """比对账本之后的结论。"""
    room_id: str
    month: str
    month_stated: bool
    claimed_amount: float
    expected_amount: float
    # ⚠️ 查无记录时这两个正是 None,不给默认值会让 not_found 分支必崩
    found_amount: float | None = None
    txn_id: str | None = None
    status: Literal["verified", "amount_mismatch", "not_found", "overpaid"]
    note: str


# ---------------------------------------------------------------- Mock 层
# 唯一的假货就在这个函数里。签名刻意做成和真实 API 一样,
# 将来换成 requests.get(PAYPAL_TRANSACTION_SEARCH_URL, ...) 即可。

def _lookup_transactions(payer_email: str, month: str) -> list[dict]:
    """查询指定付款人在指定月份的入账记录。

    Mock 版本读 fixtures/paypal_transactions.json。
    真实版本调用 PayPal Transaction Search API v1。
    """
    if not USE_MOCK:
        raise NotImplementedError("接真实 PayPal API 时实现这里")

    with open(FIXTURES) as f:
        ledger = json.load(f)

    return [
        txn for txn in ledger.get(month, [])
        if txn["payer_email"].lower() == payer_email.lower()
    ]


def month_router(node_input: PaymentClaim):
    """租客说清楚是哪个月了吗?

    没说清就不能进验证流程 —— 我们不知道这笔钱该记到哪个月,
    验证"9 月房租"和"8 月房租"是两个完全不同的问题。
    """
    if node_input.month_stated:
        return Event(route="MONTH_CLEAR", output=node_input)
    return Event(route="MONTH_UNCLEAR", output=node_input)


def gather_payment_context(node_input: PaymentClaim) -> PaymentContext:
    """把问月份需要的事实查出来。纯代码,不调模型。

    房租是预付的,月末交的钱通常是下个月的。8/31 交的钱猜成 8 月,
    整本台账就错位了 —— 台账错月在租务纠纷里是硬伤。

    所以与其泛泛地问 "which month?",不如先看台账里已经有什么:
    "我们这边 8 月已经记上了,这笔是 9 月的吗?" —— 租客回一个字就能确认。

    ⚠️ 这里同时查 PayPal(房东要求:问月份的同时也要查账),
       结果一并交给房东,但**不**据此自动生成回执。
    """
    tenant = get_tenant(node_input.room_id) or {}
    expected = float(tenant.get("rent_amount", EXPECTED_RENT))

    # 候选窗口不能瞎取。只有两类月份可能是这笔钱的归属:
    #   · 已经到期但没结清的(租客在补欠款)
    #   · 下一个月(房租预付,租客提前交)
    # 再往后的月份根本还没到期,往前超出有据可查的范围也没意义 ——
    # 把它们算成"未结清"会让 agent 问出荒唐的问题。
    rows: list[MonthStatus] = []
    for m in _candidate_months(node_input):
        entry = get_ledger(m).get(node_input.room_id) or {}
        txns = _lookup_transactions(node_input.tenant_email, m)
        found = round(sum(t["amount"] for t in txns), 2)
        rows.append(MonthStatus(
            month=m,
            expected=expected,
            ledger_status=entry.get("status"),
            paypal_found=found,
            # 只有账本里真的够数才算结清。台账写着 claimed 但 PayPal 查不到,
            # 不算结清 —— 那正是"租客说付了但钱没到"的情况。
            settled=found >= expected,
        ))

    recent = find_recent_payments(
        node_input.tenant_email, node_input.claimed_amount, node_input.month)

    # 算归属时要**扣掉这笔刚到的钱** —— 否则它会把自己所属的月份
    # 标成"已结清",于是系统建议记到再下一个月,整体错位一格。
    recent_by_month: dict[str, float] = {}
    for r in recent:
        recent_by_month[r.month_bucket] = recent_by_month.get(r.month_bucket, 0) + r.amount

    today = datetime.now().date()
    tenant_rec = get_tenant(node_input.room_id) or {}
    due_unsettled, future_unsettled = [], []
    for r in rows:
        before_this = r.paypal_found - recent_by_month.get(r.month, 0)
        if before_this >= r.expected:
            continue                      # 这笔钱之前就结清了,不是候选
        if tenant_rec and rent_due_date(tenant_rec, r.month) <= today:
            due_unsettled.append(r.month)
        else:
            future_unsettled.append(r.month)

    # 会计惯例:先冲最早的欠款;都没欠就是预付下一个到期月。
    candidates = due_unsettled or future_unsettled[:1]
    suggested = candidates[0] if candidates else None
    if suggested and due_unsettled:
        reason = f"{suggested} is the oldest month still open"
    elif suggested:
        reason = f"{suggested} rent is the next one due"
    else:
        reason = ""

    unsettled = [r.month for r in rows if not r.settled]
    settled = [r.month for r in rows if r.settled]

    return PaymentContext(
        room_id=node_input.room_id,
        tenant_name=tenant.get("name", node_input.room_id),
        claimed_amount=node_input.claimed_amount,
        expected_amount=expected,
        amount_matches_rent=abs(node_input.claimed_amount - expected) < 0.01,
        guessed_month=node_input.month,
        recent_matches=recent,
        has_recent_match=bool(recent),
        suggested_month=suggested,
        suggested_reason=reason,
        suggestion_is_certain=len(due_unsettled) <= 1,
        months=rows,
        unsettled_months=unsettled,
        last_settled_month=settled[-1] if settled else None,
    )


def _candidate_months(claim: PaymentClaim, lookback: int = 6) -> list[str]:
    """这笔钱可能归属的月份,升序。

    上界是猜测月份的**下一个月**(预付),再往后没到期。
    下界是"有据可查的最早月份" —— 台账里记过、或 PayPal 查到过入账的最早那个月。
    完全没有任何记录的远古月份不该出现在候选里,租客可能那时还没入住。
    """
    upper = _shift_month(claim.month, 1)
    window = [_shift_month(claim.month, d) for d in range(-lookback, 2)]

    earliest = None
    for m in window:
        has_ledger = claim.room_id in get_ledger(m)
        has_paypal = bool(_lookup_transactions(claim.tenant_email, m))
        if has_ledger or has_paypal:
            earliest = m
            break

    start = earliest or claim.month
    return [m for m in window if start <= m <= upper]


RECENT_WINDOW_DAYS = 5      # "我刚转了" 的合理时间窗。PayPal eCheck 最长 3 个工作日


def find_recent_payments(tenant_email: str, amount: float, around: str,
                         within_days: int = RECENT_WINDOW_DAYS) -> list[RecentTxn]:
    """找出近期金额吻合的入账 —— 验证"我刚转了 X"这个具体声称。

    这是**确定性验证**,不是给模型判断的材料。找不到就是找不到,
    此时绝不能对租客说任何暗示"我们收到了"的话。

    ⚠️ 仍然只通过 _lookup_transactions() 读数据,mock 不外溢(CLAUDE.md 约束 5)。
    """
    today = datetime.now().date()
    out: list[RecentTxn] = []
    for m in (_shift_month(around, -1), around, _shift_month(around, 1)):
        for txn in _lookup_transactions(tenant_email, m):
            if abs(txn["amount"] - amount) >= 0.01:
                continue
            paid = datetime.fromisoformat(txn["date"]).date()
            days = (today - paid).days
            if 0 <= days <= within_days:
                out.append(RecentTxn(txn_id=txn["txn_id"], amount=txn["amount"],
                                     date=txn["date"], days_ago=days, month_bucket=m))
    return sorted(out, key=lambda x: x.days_ago)


def _shift_month(month: str, delta: int) -> str:
    y, m = (int(x) for x in month.split("-"))
    mm = m + delta
    return f"{y + (mm - 1) // 12}-{(mm - 1) % 12 + 1:02d}"


def _recent_months(around: str, span: int = 3) -> list[str]:
    """以 around 为中心的前后几个月,升序。"""
    y, m = (int(x) for x in around.split("-"))
    out = []
    for delta in range(-span, span + 1):
        mm = m + delta
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        out.append(f"{yy}-{mm:02d}")
    return out


def send_month_question(node_input: str, ctx: Context):
    """把问月份的短信发出去 —— 零风险动作,可自动发。"""
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    send_sms_now(original.gmail_thread_id, node_input)
    return Event(
        message=(f"❓ {original.room_id} 声称付了房租但没说是哪个月\n"
                 f"已自动发短信确认。账本查询结果已记录,待租客回复后再验证。")
    )


# ---------------------------------------------------------------- 验证节点
# 这是一个纯代码节点 —— 不调用任何 AI 模型。
# 金额比对是确定性逻辑,交给模型做只会引入不必要的不确定性。

EXPECTED_RENT = 1000.00

def verify_payment(node_input: PaymentClaim) -> PaymentVerification:
    """比对租客声称的付款和账本记录,给出验证结论。"""
    txns = _lookup_transactions(node_input.tenant_email, node_input.month)
    total = sum(t["amount"] for t in txns)
    txn_id = txns[0]["txn_id"] if txns else None

    if not txns:
        status, note = "not_found", (
            f"账本中未找到 {node_input.tenant_email} 在 {node_input.month} 的入账记录。"
            "可能尚未到账,或付款邮箱与档案不符。"
        )
    elif abs(total - EXPECTED_RENT) < 0.01:
        status, note = "verified", f"已入账 ${total:.2f},与应付金额一致。"
    elif total < EXPECTED_RENT:
        status, note = "amount_mismatch", (
            f"已入账 ${total:.2f},应付 ${EXPECTED_RENT:.2f},"
            f"尚欠 ${EXPECTED_RENT - total:.2f}。"
        )
    else:
        status, note = "overpaid", f"已入账 ${total:.2f},超出应付金额。"

    return PaymentVerification(
        room_id=node_input.room_id,
        month=node_input.month,
        month_stated=node_input.month_stated,
        claimed_amount=node_input.claimed_amount,
        expected_amount=EXPECTED_RENT,
        found_amount=total if txns else None,
        txn_id=txn_id,
        status=status,
        note=note,
    )


# ---------------------------------------------------------------- 路由节点
# 纯 if/else,不调模型。这正是 graph workflow 相对 prompt 编排的核心优势:
# "验证通过才发回执" 是一条业务规则,不该交给模型自觉遵守。

def verification_router(node_input: PaymentVerification):
    # 兜底:月份不明本该在 month_router 就被分流,走到这里说明接线错了。
    if not node_input.month_stated:
        return Event(route="ESCALATE", output=node_input)
    if node_input.status == "verified":
        return Event(route="AUTO_RECEIPT", output=node_input)
    return Event(route="ESCALATE", output=node_input)


# ---------------------------------------------------------------- AI 节点

extract_claim = Agent(
    name="extract_claim",
    model="gemini-flash-latest",
    instruction="""从租客短信中抽取付款信息。

输入含 message(原始短信)和 classification(分类结果)。

输出 PaymentClaim:
- room_id、tenant_email —— **直接从 message.room_id / message.tenant_email
  原样复制**。这两个字段是系统按电话号码认出来的,不要自己推断或改写。
- claimed_amount —— 从 message.body 里抽金额(数字,不带货币符号)
- claimed_method
- month(格式 YYYY-MM)
- month_stated —— **租客是否说出了一个没有歧义的具体月份**。

  true 的唯一条件:出现了**月份名或月份数字**。
      "rent for September" / "Sep rent" / "September's rent" / "9月房租"

  false —— 以下全部算 false,不要犹豫:
      · 完全没提月份:"I sent the rent" / "转了房租"
      · **相对说法**:"this month" / "next month" / "last month" /
        "本月" / "这个月的"
      · 只说了日期没说月份:"the payment I sent on the 30th"

  ⚠️ **相对说法必须判 false。** 房租是预付的:8 月 31 日说的
  "this month's rent",可能指 8 月(当前日历月),也可能指
  即将到期的 9 月。这个歧义正是系统要问清楚的东西 ——
  你替它猜一个,就等于把一笔钱记到了可能错误的月份。

⚠️ month_stated=false 时 month 仍填最佳猜测(房租预付,通常是下个月),
   但**必须**把 month_stated 标成 false。这个字段决定系统要不要发短信
   找租客确认,填错会把钱记到错误的月份。

如果短信里没有明确的金额,claimed_amount 填 0。不要猜测。""",
    output_schema=PaymentClaim,
)

ask_month_agent = Agent(
    name="ask_month",
    model="gemini-flash-latest",
    input_schema=PaymentContext,
    tools=[TENANT_SMS_SKILL],
    instruction="""租客说交了房租,但没说是哪个月的。写一条短信。

**先加载 tenant-sms 技能,读 SKILL.md 的「信息披露纪律」和
references/payment.md 的「场景 B」。**

**第一分叉是 has_recent_match,不是月份:**
- has_recent_match=true  -> B-A:钱验证到账了,**陈述归属结论**不要开放式提问。
  suggested_month / suggested_reason 是代码用确定性规则算好的,直接用。
  suggestion_is_certain=true  -> B-A1:说金额+日期+归属+理由+纠正口子
  suggestion_is_certain=false -> B-A2:列出具体候选月份让租客选
  租客用了 "this month" 这类相对说法时,要直接点破歧义
- has_recent_match=false -> B-B:明确说"还没看到这笔",要 confirmation number、
  日期、付款邮箱。**绝不**说任何暗示我们收到了的话
- amount_matches_rent=false -> 叠加 B-C,把金额差异一起问

**绝对不能违反的一条:不要把 unsettled_months 或任何台账状态告诉租客。**
说"其他月份都结清了,这笔是不是 10 月的"等于把答案喂给虚报付款的人 ——
他只要回一句"是",台账里就多一条不存在的付款声明。
只能说出租客本来就知道的事:他自己转的那一笔。

只输出短信正文。""",
    output_schema=str,
)


draft_receipt = Agent(
    name="draft_receipt",
    model="gemini-flash-latest",
    input_schema=PaymentVerification,
    tools=[TENANT_SMS_SKILL],
    # ⚠️ instruction 里绝对不要出现花括号。ADK 会对 instruction 跑
    # inject_session_state,把 {foo} 当 session state 变量查找,查不到直接
    # KeyError。2.0 靠节点返回值传数据,月份和金额从 input_schema 进来即可。
    instruction="""起草一条给租客的付款回执短信草稿。

**先加载 tenant-sms 技能,读 references/payment.md 的「场景 A」。**
措辞规范以技能里的为准。

输入是一条 PaymentVerification 记录,里面有月份 month 和账本查到的
金额 found_amount。这两个值从输入里读,不要编造。

**用英文写** —— 租客只看英文。

措辞要求(这是硬约束,不是建议):
- 只能说 "we have received your payment notice for ... rent"
- 绝对不能说 "payment confirmed" / "payment received" / "funds received" /
  "your rent has been received" —— 我们只是收到了租客的付款通知,
  无法验证 PayPal 真实到账。这两件事在租务纠纷里是不同的法律事实。
- 简短、友好、不超过两句话
- 不要承诺任何本次付款之外的事情

只输出短信正文,不要加任何前后说明。""",
    output_schema=str,
)


def deliver_receipt(node_input: str, ctx: Context):
    """把回执**存成 Gmail 草稿**,并写付款台账。

    ⚠️ 存草稿不发送。回执是财务凭证,按 CLAUDE.md 约束 1 必须人工点发送。
    台账状态写 claimed 而不是 confirmed —— 租客声称付款、我们查到了记录,
    但"房东确认"是另一个动作,两个状态不能合并。
    """
    original = IncomingMessage.model_validate_json(ctx.user_content.parts[0].text)
    draft_id = draft_sms_reply(original.gmail_thread_id, node_input)
    write_ledger(
        month=f"{__import__('datetime').datetime.now():%Y-%m}",
        room_id=original.room_id,
        claimed_amount=0.0,
        found_amount=None,
        status="claimed",
    )
    return Event(
        message=(f"💰 {original.room_id} 回执草稿已生成(草稿 {draft_id})\n"
                 f"台账已记 claimed。**打开 Gmail 点发送才会发给租客。**")
    )


def escalate_to_landlord(node_input: PaymentVerification):
    """金额不符或查无记录 —— 不自动回复,转给房东。"""
    return Event(
        message=(
            f"⚠️ {node_input.room_id} 付款需人工处理\n"
            + ("❓ 租客没说是哪个月的房租,已自动发短信确认\n"
               if not node_input.month_stated else "")
            + f"状态:{node_input.status}\n"
            f"租客声称:${node_input.claimed_amount:.2f}\n"
            f"{node_input.note}"
        )
    )


# ---------------------------------------------------------------- 图
# START → 抽取 → 验证(纯代码) → 路由(纯代码) → 两条分支

payment_workflow = Workflow(
    name="payment_workflow",
    edges=[
        ("START", extract_claim, month_router),
        # 月份没说清 -> 查台账和 PayPal,带着已知信息去问,不进验证流程。
        # "验证 9 月房租" 和 "验证 8 月房租" 是两个问题,月份不定就没得验。
        (month_router, {
            "MONTH_CLEAR": verify_payment,
            "MONTH_UNCLEAR": gather_payment_context,
        }),
        (gather_payment_context, ask_month_agent, send_month_question),
        (verify_payment, verification_router),
        (verification_router, {
            "AUTO_RECEIPT": draft_receipt,
            "ESCALATE": escalate_to_landlord,
        }),
        (draft_receipt, deliver_receipt),
    ],
)
