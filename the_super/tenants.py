"""租客名册 —— 租客数据和应付日的唯一真相源。

原先租客识别散在 tools/gmail.py 里(那是渠道层),但催缴逻辑也要用同一份
数据和同一套号码规范化规则。两处各读一遍迟早会漂移,集中到这里。

⚠️ fixtures/tenants.json 含真实 PII,已 gitignore。
   提交到仓库的是 tenants.json.template。
"""

import json
import re
from calendar import monthrange
from datetime import date
from pathlib import Path

TENANTS_FILE = Path(__file__).parent / "fixtures" / "tenants.json"


def load_tenants() -> list[dict]:
    """读名册。文件缺失直接抛 —— 基础设施问题不吞(见 CLAUDE.md)。"""
    with open(TENANTS_FILE) as f:
        return json.load(f)["tenants"]


def get_tenant(room_id: str) -> dict | None:
    return next((t for t in load_tenants() if t["room_id"] == room_id), None)


# ---------------------------------------------------------------- 身份识别

def normalize_phone(phone: str) -> str:
    """+1 (917) 555-0101 → 9175550101"""
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else digits


def identify_tenant(phone_or_email: str) -> dict | None:
    """靠号码或邮箱认到具体租客。

    这是跨渠道关联的钥匙:短信来自号码,照片来自邮箱,两者认成同一个人。

    能吃 Gmail From header 的两种形状:
        "Tenant A <tenant.a@example.com>"   → 抽出尖括号里的地址
        "tenant.a@example.com"
    """
    needle = phone_or_email.strip().lower()

    # Gmail 的 From header 长这样,不剥掉会全部识别失败
    bracketed = re.search(r"<([^>]+)>", needle)
    if bracketed:
        needle = bracketed.group(1).strip()

    needle_digits = normalize_phone(needle)
    for t in load_tenants():
        if t["email"].strip().lower() == needle:
            return t
        # 号码比对要求双方都是完整 10 位,否则空字符串会误匹配
        if needle_digits and len(needle_digits) == 10 \
                and normalize_phone(t["phone"]) == needle_digits:
            return t
    return None


# ---------------------------------------------------------------- 应付日

def rent_due_date(tenant: dict, month: str) -> date:
    """该租客在指定月份的应付日。month 格式 "2026-09"。

    每个租客的合同应付日不同(rent_due_day),不是全局常量。
    应付日超过当月天数时(如 2 月没有 30 号)按当月最后一天算。
    """
    year, mon = (int(x) for x in month.split("-"))
    day = int(tenant.get("rent_due_day", 1))
    return date(year, mon, min(day, monthrange(year, mon)[1]))


def days_overdue(tenant: dict, month: str, today: date) -> int:
    """逾期天数。应付日当天及之前为 0(含当天,不算逾期)。"""
    return max(0, (today - rent_due_date(tenant, month)).days)
