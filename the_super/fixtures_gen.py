"""按当前名册生成 mock PayPal 账本,保证 payer_email 对得上。

跑法:  python -m the_super.fixtures_gen 2026-09

场景固定为 5 户各演一种状态,方便录 demo 时讲解:
    第 1 户  按时全额        -> paid_full
    第 2 户  少付 400        -> underpaid
    第 3 户  查无记录        -> nothing_received
    第 4 户  逾期后补齐      -> paid_full(但 date 在应付日之后)
    第 5 户  查无记录但未到期 -> not_yet_due
"""

import json
import sys
from datetime import date, timedelta

from .tenants import load_tenants, rent_due_date

FIXTURE = __import__("pathlib").Path(__file__).parent / "fixtures" / "paypal_transactions.json"


def build(month: str, plan: list[str] | None = None) -> dict:
    """plan 决定每户的付款状态,按名册顺序。**不是随机的**,改这里就改剧本。

        full    按时全额        -> paid_full
        partial 少付 400        -> underpaid
        none    查无记录        -> nothing_received / not_yet_due
        late    逾期后补齐      -> paid_full(date 在应付日之后)
        recent  昨天刚全额转账  -> paid_full,且落在"最近几天"窗口内,
                                   用于演示"我刚转了 X"能被验证到
    """
    tenants = load_tenants()
    txns = []
    plan = plan or ["recent", "partial", "none", "late", "none"]
    for i, (t, mode) in enumerate(zip(tenants, plan)):
        if mode == "none":
            continue
        due = rent_due_date(t, month)
        amount = t["rent_amount"] if mode != "partial" else t["rent_amount"] - 400
        if mode == "late":
            paid_on = due + timedelta(days=3)
        elif mode == "recent":
            # 昨天 —— 让 find_recent_payments 能查到,演示"我刚转了"被验证
            paid_on = date.today() - timedelta(days=1)
        else:
            paid_on = due
        txns.append({
            "txn_id": f"MOCK-{month[-2:]}{chr(65 + i)}{10 + i}",
            "payer_email": t["email"],
            "amount": round(amount, 2),
            "date": f"{paid_on}T09:00:00-04:00",
            "note": {"full": "按时全额", "partial": "少付",
                     "late": "逾期后补齐", "recent": "昨天刚转"}[mode],
        })
    return {"_comment": f"由 fixtures_gen 按名册生成 ({month})。真实邮箱,勿提交。",
            month: txns}


if __name__ == "__main__":
    month = sys.argv[1] if len(sys.argv) > 1 else "2026-09"
    data = build(month)
    # 上个月已经过去了,应当全部收齐 —— 否则催缴逻辑会去追历史欠款,
    # 而 demo 想展示的是当月的收租周期。
    y, m = (int(x) for x in month.split("-"))
    prev = f"{y-1}-12" if m == 1 else f"{y}-{m-1:02d}"
    data[prev] = build(prev, ["full"] * 5)[prev]
    FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"✅ 已生成 {FIXTURE}  ({prev} 全额收齐 + {month} 剧本)")
