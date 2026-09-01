# 付款相关短信

## 场景 A · 金额相符,月份明确 → 回执

租客说了是哪个月、金额和该租客的 rent_amount 一致、账本里查得到。

**这是唯一会生成回执的情况。** 而且回执只存草稿,房东点发送才真的发出去。

> Hi Sarah, we have received your payment notice for October rent of $1,000.
> Thanks for letting us know.

要点:
- "payment notice" 是必须的词。不是 "payment"、不是 "rent"
- 月份写全称(October),不写 2026-10 —— 那是给系统看的
- 金额带千位逗号
- 不加 "your account is now current" 之类的结论

## 场景 B · 没说是哪个月 → 先验证,再问

### 第一分叉不是月份,是「这笔钱查到了吗」

租客说 "I just sent $1,000" —— 这是一个**关于最近几天有一笔入账的具体声称**。
系统已经替你查过了:

| 字段 | 含义 |
|---|---|
| `has_recent_match` | **最近几天有没有查到金额吻合的入账** ← 先看这个 |
| `recent_matches[]` | 查到的话,每笔的 `amount` / `date` / `days_ago` |
| `claimed_amount` | 租客说他转了多少 |
| `expected_amount` | 该租客的月租 |
| `amount_matches_rent` | 声称金额是否正好等于一个月租金 |
| `unsettled_months` | 尚未结清的月份 —— **内部信息,不许说出去** |

**先按 `has_recent_match` 分叉,再考虑月份。**
钱都没查到就讨论"这是哪个月的",等于默认接受了租客的说法。

---

### B-A · `has_recent_match = true` → 陈述归属,不要开放式提问

钱验证到账了。**这笔的欺诈风险已经消除**,剩下的只是记账归属 ——
而归属已经由代码用确定性规则算好了(`suggested_month` / `suggested_reason`)。

你的任务是**把结论说清楚**,不是再问租客一遍。

**B-A1 · `suggestion_is_certain = true` → 陈述 + 留口子**

> Hi Sarah, thanks — we have your $1,000 from August 30. Since August rent
> is already settled, we'll apply this to September, which is due tomorrow.
> Let us know if you meant a different month.

四个要件缺一不可:
1. **那笔钱的金额和日期** —— 让租客确认是不是这一笔
2. **归属结论** —— "we'll apply this to September"
3. **理由** —— 用 `suggested_reason`,让租客能自己核对逻辑
4. **纠正的口子** —— "let us know if..."

如果租客用了 "this month" / "next month" 这类相对说法,**要直接点破歧义**,
因为那正是问题所在:

> You mentioned "this month" — since rent is due in advance, we're reading
> that as September. Let us know if you meant August.

**B-A2 · `suggestion_is_certain = false` → 列出具体候选让租客选**

有多个已到期的欠款月时,归属会影响各月的逾期天数和后续能不能走法定程序,
这个必须由租客决定:

> Hi Sarah, thanks — we have your $1,000 from August 30. We show both July
> and August still open. Which should this go toward?

要点:**列出具体月份**,不要问"哪个月"。租客不该替我们回忆账目。

### B-B · `has_recent_match = false` → 说不出"收到了",要凭据

**这是防虚报的关键分支。** 查不到就是查不到。

> Hi Sarah, thanks for letting us know. We don't see a $1,000 payment on
> our end yet. PayPal eChecks can take up to 3 business days to clear —
> could you send the confirmation number, the date you sent it, and the
> email you paid from? We'll re-check.

要点:
- **绝不**说任何暗示"我们这边没问题"的话
- **绝不**说出哪个月已结清、哪个月没结清 —— 那正是虚报者需要的信息
- `yet` 这个词承载全部谨慎:我们只是**还没看到**,不是断定他没付
- 要三样具体东西:confirmation number、日期、付款邮箱。
  这三样能真正解决问题,而且**诚实的租客给得出,虚报的给不出**
- 不问"是哪个月的" —— 钱还没确认存在,讨论归属为时过早

---

### B-C · 金额和月租对不上(`amount_matches_rent = false`)

先按 B-A / B-B 处理"查没查到",再把金额差异一起问:

> Hi Sarah, thanks for letting us know. Just so we log this correctly —
> you mentioned $600, and the monthly rent is $1,000. Which month is this
> toward, and is the rest coming separately?

要点:金额异常时不能只问月份。但保持中性,不写成质问。

---

### 通用要点

- **不要**在任何一条里说"我们收到了你的房租",除非 `has_recent_match=true`
  且你说的正是那一笔
- 推断必须软化:`should we apply it to` / `is that right`。
  **不能**写成 "this is for September" —— 那是替租客做决定
- 解释一句为什么问("so we log it to the right month"),否则像在为难对方
- **永远不要说出 `unsettled_months` 的内容。** 那是内部账目状态

## 场景 C · 金额少于应付

**不生成回执,转人工。** 但如果房东要发,措辞:

> Hi Sarah, we received your notice for $600 toward October rent.
> The full amount due is $1,000, so there's $400 remaining.
> Let us know if you've already sent the rest.

要点:
- 三个数字都要出现:收到的、应付的、差额。不要让租客自己算
- **不要**写 "no problem" / "that's fine" / "whenever you can" ——
  agent 没有权限减免或延期
- 最后一句留余地,可能第二笔在路上

## 场景 D · 金额多于应付

> Hi Sarah, we received your notice for $1,200 toward October rent,
> which is $200 more than the $1,000 due. Could you confirm whether the
> extra is intended for next month or something else?

要点:多付比少付更需要问清楚 —— 可能是提前付了下月、可能是还押金、
可能是转错了。不要擅自假设。

## 场景 E · 账本里查无记录

**永远不要说 "we didn't receive it" 或 "your payment failed"。**
我们只是查不到,可能在途、可能付到别的邮箱、可能我们的查询有问题。

> Hi Sarah, thanks for letting us know. We don't see the payment on our
> end yet. PayPal eChecks can take up to 3 business days to clear —
> could you send the confirmation number and the email you paid from?
> We'll re-check.

要点:
- "we don't see it **yet**" —— yet 这个词承载了全部的谨慎
- 主动给出两个可能原因(eCheck 在途、付款邮箱不同),不把责任推给租客
- 要具体信息(confirmation number、付款邮箱),这两样能真正解决问题
