# 录制 Demo

面向 Devpost 提交视频。总长 2.5–3 分钟,五段。

> 先读 [manual.md](manual.md) —— 尤其是「没有任何东西在后台运行」那节。
> 系统是定时批处理,不是常驻服务;发了短信不跑 `main.py poll` 就没有任何反应。

---

## 零、三种运行方式的区别(录之前务必分清)

| 命令 | 读真实 Gmail | 会发短信 | 在 demo 里的角色 |
|---|---|---|---|
| `python main.py poll` | ✅ | ✅ | 第 3 段:真实短信闭环 |
| `python demo.py <场景>` | ❌ | ⚠️ DRY_RUN | 第 4 段:七个预设场景 |
| `python demo.py rent <日期>` | ❌ | ❌ | 第 5 段:纯计算,模拟日期 |

`demo.py rent` **不需要另开窗口配合 `main.py`** —— 它完全不碰 Gmail,
只是把房租状态算出来打印。三条路互相独立。

---

## 一、开录前(15 分钟)

```bash
cd the-super
source .venv/bin/activate

python authorize.py                          # 确认 OAuth token 没过期
python -m the_super.fixtures_gen 2026-08     # mock 账本对齐当月
rm -f the_super/fixtures/tickets.json \
      the_super/fixtures/ledger.json \
      the_super/fixtures/history_cursor.json  # 清掉调试残留
```

### 检查清单

| 项 | 要求 |
|---|---|
| `the_super/.env` 里 `DRY_RUN` | **保持 `true`**。演示时讲「所有外发都有安全开关」是加分项 |
| OAuth token | Testing 状态下 7 天过期,当天必须重跑一次 `authorize.py` |
| 名册脱敏 | `tenants.json` 里只有 1F-A 的**手机号**是真的(认领来信必须留),姓名邮箱已是假数据 |
| Voice 转发 | Voice 设置里 `Forward messages to email` 必须开着 |

### 准备一条真实来信

用你的手机给 Voice 号发一条**英文**短信,第 3 段要用:

> `Hi, I just sent $1000 for this month's rent via PayPal.`

### 不要出现在画面里

- **Gmail 界面** —— Voice 转发邮件的标题里有真实手机号
- `the_super/fixtures/tenants.json`
- `the_super/.env`、`token.json`、`credentials.json`

`python demo.py` 和 `main.py poll` 的终端输出是干净的,不含号码。

---

## 二、五段结构

### 第 1 段 · 问题(20 秒,纯口播)

Five rooms. Tenants text about rent and repairs. The landlord rarely checks
email. Nothing connects SMS, PayPal, and maintenance tickets.

### 第 2 段 · 架构(40 秒)

```bash
python -c "
from the_super.agent import root_agent
from the_super.payment import payment_workflow
from the_super.maintenance import maintenance_workflow
for wf in (root_agent, payment_workflow, maintenance_workflow):
    print(f'\n{wf.name}')
    for e in wf.graph.edges:
        f=getattr(e.from_node,'name',e.from_node); t=getattr(e.to_node,'name',e.to_node)
        r=f'  [{e.route}]' if getattr(e,'route',None) else ''
        print(f'  {f:26} -> {t}{r}')
"
```

然后翻到 `the_super/payment.py` 的 `verify_payment()` 停两秒。

**要说的一句话:金额比对是 `if/else`,不是 prompt。**
这是全片最重要的技术论点 —— 有财务后果的判断不交给模型自觉遵守。

### 第 3 段 · 实盘(50 秒,最有说服力)

```bash
python -c "
from dotenv import load_dotenv; load_dotenv('the_super/.env')
from the_super.tools.gmail import read_new_messages
for m in read_new_messages(): print(f'[{m.source}] {m.room_id}: {m.body}')"
```

**建议分屏拍**:左边你的手机(刚发出的短信),右边终端(解析结果)。
这段证明的是真实 Google Voice 短信穿过了整条管道,不是 mock 数据。

### 第 4 段 · 三组对比(50 秒,最出彩)

每组都是"同一类消息、一个细节不同 → 完全不同的处理"。选两组录即可。

**组一 · 描述质量决定路径**

```bash
python demo.py fix-vague     # "The toilet is leaking."
python demo.py fix-clear     # 完整描述漏水位置和时间
```

前者索要照片,而且具体到拍哪里(base / behind the tank / supply line);
后者直接生成派单简报,还列出要带的材料。
**要说的:agent 知道自己什么时候信息不够。**

**组二 · 一句话决定要不要自动处理**

```bash
python demo.py pay-ok        # "$1000 for September rent"
python demo.py pay-nomonth   # "I just sent you the rent"
```

同一个租客、同样一笔 $1000、账本里都查得到 —— 但后者没说是哪个月,
系统**拒绝自动回执**,反而去查台账,然后问:
"We have your August rent recorded already, so I'm assuming this one is
for September — is that right?"

**要说的:房租是预付的,月末交的钱通常是下个月的。8/31 交的钱记成 8 月,
整本台账就错位了。所以金额对得上也不够,月份不定就不能自动处理。**

**组三 · 该闭嘴的时候闭嘴**

```bash
python demo.py legal         # 提到律师、押金 14 天
```

回复只有一句:"We've received your message and it's being reviewed.
We'll follow up with you directly."

押金该不该退、14 天对不对 —— 一个字都没碰。
**要说的:这条短信将来可能被读给法官听,它唯一该证明的是"消息收到了"。**

### 第 5 段 · 时间线 + 安全边界(40 秒)

```bash
python demo.py rent 2026-08-02
python demo.py rent 2026-08-07
```

每户按**自己合同的应付日**触发,不是全楼统一。

然后讲这条法律时间线:

> 逾期第 1 天发催缴,给 5 天补缴期。期满时正好逾期满 5 天 ——
> 这才够资格走 14 天通知程序。**而 agent 到此为止,不生成 14 天通知**,
> 因为短信不构成法定送达。

收尾三句:PayPal 是 mock · `DRY_RUN` 安全开关 · 无状态设计不烧常驻容器。

---

## 三、场景播放器

`demo.py` 是演示专用入口,不属于应用逻辑。
所有场景用**假号码假邮箱**构造,不碰真实名册。

```bash
python demo.py list                # 列出全部场景
python demo.py fix-vague           # 跑单个
python demo.py all                 # 依次跑完
python demo.py rent 2026-08-04     # 模拟某天的房租周期
```

### 消息场景(10 个)

| 场景 | 来信(英文) | 预期路径 | 对租客 |
|---|---|---|---|
| `pay-ok` | "$1000 for **September** rent" | 金额相符 → 起草回执 | 📝 草稿 |
| `pay-nomonth` | "I just sent you the rent"(没说月份) | **查台账 → 带已知信息问月份** | ✅ 自动发 |
| `pay-short` | "$600 for September rent" | 少付 → **不回执**,转人工 | 🚫 不回 |
| `pay-none` | "I sent September rent yesterday" | 账本查无记录 → 转人工 | 🚫 不回 |
| `pay-vague` | "can we talk about the money stuff" | 低置信度 → 安全阀 | ✅ 缓冲回复 |
| `legal` | 提到律师、押金 14 天 | 涉及法律 → 不表态 | ✅ 缓冲回复 |
| `lease` | 问提前搬走押金怎么算 | 涉及租约 → 交房东 | ✅ 缓冲回复 |
| `fix-vague` | "The toilet is leaking." | 描述不清 → 索要照片 | ✅ 自动发 |
| `fix-clear` | 完整描述漏水位置和时间 | 生成派单简报 | 🚫 待批准 |
| `fix-urgent` | "The kitchen pipe burst ..." | urgent 定级 + 自救指引 | ✅ 自动发 |

### 日期怎么模拟

`check_rent(month, today)` 的 `today` 是**参数**不是 `datetime.now()`,
所以直接传日期即可 —— 不用改系统时间,不用 freezegun。

```bash
python demo.py rent 2026-08-02   # 1F(应付 1 号)已逾期,2F/3F 未到期
python demo.py rent 2026-08-04   # 2F(应付 3 号)进入逾期
python demo.py rent 2026-08-07   # 5 户全部逾期
```

补缴截止日会跟着触发日往后推(8/2 触发 → 8/7 截止;8/4 触发 → 8/9 截止),
这个细节值得录进去。

---

## 三点五、Skills:措辞规范外置(值得单独讲 20 秒)

所有对租客说话的措辞规范不在代码里,在 `skills/tenant-sms/`:

```
skills/tenant-sms/
├── SKILL.md                  ← 铁律 + 语气 + 目录(常驻上下文)
└── references/
    ├── payment.md            ← 回执 / 月份不明的三种问法 / 少付 / 多付 / 查无记录
    ├── maintenance.md        ← 按设备类型的照片对照表 + 紧急自救指引
    ├── collections.md        ← 催缴各阶段 + 法律时间线
    └── holding.md            ← 缓冲回复的四个档位
```

用 ADK 2.0 的 Skills 机制(`SkillToolset`),**按需加载**:
模型先看 `SKILL.md` 的目录,判断这次要哪个 reference 再去读。

```
instruction:  186 字符   ← 只剩"去读技能"的指路
技能总内容:  6300+ 字符  ← 按需加载,不常驻
```

**demo 里可以这样证明它真的生效**:打开 `references/maintenance.md`,
指出里面写的 "shut off the valve (turn clockwise)",然后跑 `fix-urgent`,
输出里出现同一句话 —— 而 `clockwise` 这个词在整个代码库里只存在于那个 md 文件。

**要说的一句话:措辞归 Skill,判定不归。**
"金额相不相符""能不能自动发"仍然是确定性代码。改 markdown 能改变 agent 怎么说话,
但改不了它被允许做什么。

---

## 四、语言

- **对外(租客、维修师傅)一律英文** —— 租客只看英文
  - 催缴短信:`rent.py` 的 `build_collection_sms()`,确定性模板
  - 付款回执 / 索要照片 / 派单简报:instruction 里要求模型用英文
- **对内(给房东的日报、待办、汇总)保持中文**

回执措辞在英文下同样受约束:只能说
`"we have received your payment notice for ... rent"`,
绝不能说 `"payment confirmed"` / `"funds received"`。

---

## 五、Google Voice 要不要装 app

**系统不需要。** 整套走 Gmail API,Voice 只负责服务端的「短信 ↔ 邮件」转发。

录 demo 时**拍你自己的手机**比开 voice.google.com 更好 —— 你的号码就是名册里的
1F-A,发出去的短信会回到你手机上,一个完整往返闭环最有说服力。

---

## 六、提交说明必须写清楚

1. **PayPal 验证是 mock**,隔离在 `payment.py` 的 `_lookup_transactions()` 一个函数里
2. **Google Voice 邮件解析不是官方 API**,格式由 Google 自行决定,所以隔离在
   `gmail.py` 的解析常量区,改版时只改一处
3. **催缴短信是自动发送的** —— 这与 `CLAUDE.md` 架构约束 1(绝不自动发送对外消息)
   相矛盾,是房东明确要求的取舍,需要在提交说明里解释

## 七、别做

- 别拍 Gmail、`tenants.json`、`.env`
- 别在最后一刻把 `DRY_RUN` 改成 `false` 试真发 —— 发出去撤不回,而且画面上看不出区别
- 录制当天别改代码
