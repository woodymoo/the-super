# The Super — Agent 设计文档

5 个单间 · 每间 $1000/月 · **应付日按各自合同,逐户不同** · 短信为主渠道

---

## 一、渠道架构:一切都是 Gmail

这是整个设计里最重要的决定。Google Voice 开启 "Forward messages to email" 后:

| 方向 | 实际通道 | Gmail 里怎么识别 |
|---|---|---|
| 租客发短信给你 | Voice → 转成邮件 | `from:txt.voice.google.com` |
| 你回短信给租客 | 回复那封邮件 → Voice 发出短信 | 直接 reply |
| 租客发照片/视频 | 租客自己的邮箱 | `from:<租客邮箱>` + 有附件 |
| 你回邮件 | 普通邮件 | 直接 reply |

**结论:整个系统只需要 Gmail API 一套认证。** 不需要 Twilio,不需要换号码,租客毫无感知。

**风险点**:Google Voice 的邮件转发格式是它自己定的,不是正式 API,Google 改版就可能挂。设计上把解析 Voice 邮件的逻辑独立成一个函数,坏了只改一处。

---

## 二、数据模型

存 Firestore。5 个租客的数据量极小,免费额度绰绰有余。

### `tenants`(手工维护,5 条)

```
room_id          "2F-A"
name             租客姓名
phone            短信号码(用来匹配 Voice 邮件里的发件号)
email            发照片用的邮箱(用来关联附件)
rent_amount      1000
move_in_date
```

`phone` 和 `email` 是**跨渠道关联的钥匙**——短信来自号码,照片来自邮箱,靠这张表把它们认成同一个人。

### `ledger`(付款台账)

```
month            "2026-09"
room_id
expected         1000
claimed_amount   租客短信里说的金额
claimed_at       租客声称付款的时间
confirmed_at     你确认回执的时间
status           claimed | confirmed | disputed | missing
```

注意 `claimed` 和 `confirmed` 是两个状态。租客说付了不等于你确认了,这个区分是台账的核心。

### `tickets`(维修工单)

```
ticket_id
room_id
opened_at
severity         urgent | normal | low
description      租客原话
clarity          clear | needs_media
awaiting_media   bool + 过期时间
media[]          关联到的照片/视频
assessment       Gemini 看图后的判断
contractor       派给谁
status           open | awaiting_media | ready_to_dispatch | dispatched | closed
history[]        这个房间的历史工单(查重复问题用)
```

`awaiting_media` 带过期时间是关键:租客说"我发照片过去"但可能忘了,48 小时没收到就该提醒。

---

## 三、Agent 组成

### 1. `classifier`(路由器)

**输入**:一封新邮件(Voice 短信 or 租客照片邮件)
**输出**:`{tenant_id, intent, confidence}`

意图分三类:

- `PAYMENT` — 提到付款、PayPal、金额
- `MAINTENANCE` — 报修、损坏、故障、投诉
- `OTHER` — 其他(问问题、闲聊、搬家通知……)

**设计要点**:置信度低就归到 `OTHER`,不要硬猜。误判成付款会污染台账,代价比让你多看一眼大得多。

### 2. `payment_workflow`

```
extract_claim → month_router
  ├─ MONTH_CLEAR   → verify_payment → verification_router
  │                     ├─ AUTO_RECEIPT → draft_receipt → deliver_receipt(存草稿)
  │                     └─ ESCALATE     → 转人工
  └─ MONTH_UNCLEAR → gather_payment_context → ask_month_agent → send_month_question
```

**做什么**:
1. 从短信里抽出金额、付款方式,以及**租客有没有明说是哪个月**
2. 月份不明 → 查台账和 PayPal,带着已知信息去问,**不进验证流程**
3. 月份明确 → 和该租客的 `tenants.rent_amount` 对比
4. 只有金额相符才起草回执,存 Gmail 草稿
5. 写入 `ledger`,状态 `claimed`

**为什么月份不明要单独分流:** 房租是预付的,月末交的钱通常是下个月的。
8/31 交的钱记成 8 月,整本台账就错位 —— 台账错月在租务纠纷里是硬伤。
而且"验证 9 月房租"和"验证 8 月房租"是两个不同的问题,月份不定就没得验。

问月份时先看台账已有什么。"我们这边 8 月已经记上了,这笔是 9 月的吗?"
比泛泛的 "which month?" 好得多 —— 租客回一个字就能确认。

**三种情况分别处理**:

| 情况 | 回执措辞 |
|---|---|
| 金额 = 该租客的 `rent_amount` | 标准确认:已收到 X 月房租的**付款通知**,谢谢 |
| 金额 < $1000 | 确认收到 $X,并说明剩余 $Y 待付 —— **不要自作主张说"没关系"** |
| 金额 > $1000 或说不清 | 不起草,标记 `disputed`,交给你 |

**注意**:agent 并不能验证 PayPal 里真的到账了。它只是记录租客的声明。回执的措辞必须诚实——"已收到你的付款通知",而不是"已确认到账"。这个区别在租务纠纷里可能很重要。

### 3. `maintenance_triage`

**做什么**:
1. **定级** — 涉及水、电、燃气、暖气、安全 → `urgent`;其余按影响程度
2. **判断描述清不清楚** — 能不能凭这段话联系师傅?
3. 查 `tickets` 历史,这个房间以前报过同样的问题吗?

**"描述清楚"的判断标准**(写进 instruction):

- 清楚:说明了**哪个设备/位置** + **什么现象** + **什么时候开始**
- 不清楚:"厨房坏了"、"水有问题"、"空调不制冷"(缺少现象细节)

不清楚 → 起草索要照片的短信,并且**要具体**:不是"请发照片",而是"麻烦拍一张水槽下面管道接头的照片,再拍一段水流的短视频"。

### 4. `media_analyst`

**触发时机**:工单处于 `awaiting_media`,或收到带附件的租客邮件

**做什么**:
1. 按 `tenants.email` 把附件关联回对应工单
2. 把照片直接交给 Gemini 做视觉判断
3. 更新工单的 `assessment` 和 `severity`(看图后可能升级)

**这是整个系统技术上最有意思的一环**:一条 9:04pm 的短信和一封 9:31pm 的邮件,来自不同渠道、不同标识、没有共同的会话 ID,靠 `tenants` 表 + 时间窗口 + 待图状态拼成同一个工单。

### 5. `dispatcher`

**做什么**:生成给维修师傅的工单简报

包含:地址、房间、问题描述、看图结论、紧急程度、租客联系方式、方便上门的时间。

**不自动发送。** 你自己有常用的师傅,选谁是你的判断,agent 只把材料准备好。

### 6. `secretary`(日报)

每天固定时间给你发一条**短信**摘要(不是邮件——你不常看邮件):

```
今日:2 条新消息
· 2F-A 报修厨房水槽漏水(紧急,已看图:接头锈蚀)→ 待你派单
· 3F-B 说已 PayPal 付 $1000 → 回执待批准
待办:1 条工单等照片已超 48 小时(1F-C)
```

短信有长度限制,超了就分条或者只发要点 + "详情见邮件"。

---

## 四、人工审批:分级放行

这是设计上最值得写进提交说明的一点。**不要一刀切。**

| 动作 | 后果 | 建议策略 |
|---|---|---|
| 给你发日报 | 无 | **全自动** |
| 索要照片的短信 | 极低 | **可以自动**(说错了最多再问一次) |
| 付款回执短信 | 中(是财务凭证) | **要你批准** |
| 维修问题的实质回复 | 高(承诺修理时间、责任归属) | **必须你批准** |
| 派单给师傅 | 高(花钱) | **必须你批准** |
| 任何涉及租约、押金、法律的回复 | 极高 | **agent 不碰实质内容**,只发一条不表态的缓冲回复("已收到,会有人跟进"),然后转给你 |

**理由:自主性的边界应该由后果决定,而不是由技术能力决定。** 这句话本身就是一个好的 hackathon twist——大部分参赛者会展示"我的 agent 全自动",你展示的是"我知道哪里该停"。

审批的实现方式:agent 把草稿存成 **Gmail 草稿**。你打开 Gmail 看一眼,点发送就行——不需要任何自定义 UI,而且手机上就能操作。ADK 2.0 的 graph workflow 有内置的 human input 节点,如果时间够可以用它做得更正式。

---

## 五、三个触发器

| 触发器 | 频率 | 干什么 |
|---|---|---|
| **消息轮询** | 每 5–15 分钟 | 抓新邮件 → 分类 → 走对应路径 |
| **房租周期** | 每天 | 每户按 `tenants.rent_due_day` 各自的应付日判断;逾期次日发催缴,给 5 天补缴期 |
| **日报** | 每天傍晚 | 汇总 + 检查超时未收到的照片 |

全部用 Cloud Scheduler。**agent 本身保持无状态**——这一轮没等到照片就在 Firestore 记一笔然后退出,下一轮再看。不要让 agent 内部循环等待,那会让 Cloud Run 容器一直活着烧钱。

---

## 六、工具清单

```
read_new_messages(since)        → 拉 Gmail 新邮件,区分 Voice 短信 / 租客邮件
identify_tenant(phone_or_email) → 查 tenants 表
get_ledger(month, room_id)      → 读台账
write_ledger(...)               → 写台账
get_tickets(room_id, status)    → 读工单(含历史)
write_ticket(...)               → 写工单
fetch_attachments(email_id)     → 取附件
draft_sms_reply(thread_id, text)→ 建 Gmail 草稿(回 Voice 邮件 = 发短信)
draft_email(to, subject, body)  → 建普通邮件草稿
send_digest(text)               → 发日报短信给你自己
```

十个函数,没有一个需要新的第三方 API。

---

## 七、分阶段实现(按重要性排序)

**Phase 1 — 骨架(必须完成)**
- `read_new_messages` + `identify_tenant` + `classifier`
- 只做付款路径:核对金额 → 写台账 → 起草回执
- 日报

付款路径最简单、最规整、最容易演示,而且每月有 5 次真实数据。

**Phase 2 — 维修路径(核心亮点)**
- `maintenance_triage` 定级 + 判断描述清晰度
- 不清楚就起草索要照片

**Phase 3 — 跨渠道关联(技术亮点)**
- `awaiting_media` 状态 + 邮件附件关联 + Gemini 看图

**Phase 4 — 有余力才做**
- 派单简报
- 房租周期催缴
- 历史工单的重复问题识别

**如果时间紧,Phase 1 + 2 就足以完整演示 Taskmaster 要的"多步骤、无人干预、拦截式工作流"。** Phase 3 是加分项,不是及格线。

---

## 八、演示时的注意事项

- **打码**:租客姓名、电话、邮箱在录屏前全部替换成假数据。真实门牌号也换掉。
- **准备三条测试短信**:一条标准付款、一条描述模糊的报修、一条紧急漏水。三条打完,整个系统的判断力就展示完了。
- **重点展示"它决定要照片"那一刻** —— 这是最能体现自主判断的瞬间,比任何架构图都有说服力。
- **说清人工审批是设计选择,不是能力不足。** 这句话一定要讲。
