# 使用手册

---

## 一、最重要的一件事:没有任何东西在后台运行

这**不是**一个常驻服务。你发一条短信,系统不会有任何反应 ——
除非有人执行 `python main.py poll`。

```
你发短信  ──►  Google Voice 转成邮件进 Gmail  ──►  [ 停在这里,等着 ]
                                                         │
                        只有你跑 `main.py poll` 才会继续 ─┘
```

这是**故意的设计**(见 `CLAUDE.md` 架构约束 4)。agent 保持无状态,
等待靠"记一笔然后退出、下一轮再看"实现。让容器常驻空等会一直烧钱。

生产环境里由 Cloud Scheduler 每 5–15 分钟叫醒一次;开发和 demo 阶段就是你手动跑。

---

## 二、三种运行方式,别搞混

| 命令 | 读真实 Gmail? | 调 Gemini? | 会发短信? | 用途 |
|---|---|---|---|---|
| `python main.py poll` | ✅ 是 | ✅ 是 | ✅ 会 | 真实处理新消息 |
| `python demo.py <场景>` | ❌ 否 | ✅ 是 | ⚠️ 走 DRY_RUN | 演示,用假消息 |
| `python demo.py rent <日期>` | ❌ 否 | ❌ 否 | ❌ 不会 | 纯计算,模拟日期 |

`demo.py rent` **完全不碰 Gmail**,它只是把房租状态算出来打印。
你在另一个窗口跑它,不会对刚发的短信产生任何影响 —— 那是两条独立的路。

---

## 三、`DRY_RUN` 安全开关

`the_super/.env` 里:

```
DRY_RUN=true     # 所有发送/建草稿只打日志,不真的执行
DRY_RUN=false    # 真的发出去
```

**第一次对真实邮箱跑务必保持 `true`。** 短信发出去撤不回。

`true` 时你会看到:

```
[DRY_RUN] 本应发送 -> thread=1a05804f1204...
Hi, we have received your maintenance report regarding...
```

---

## 四、完整跑一遍(手机 → agent → 手机)

### 准备

```bash
cd the-super
source .venv/bin/activate
python authorize.py       # 确认 OAuth token 有效(Testing 状态 7 天过期)
```

确认 `.env` 里 `DRY_RUN=true`。

### 第 1 步:用手机发短信

给你的 Google Voice 号发(**英文**,租客只看英文):

> `The toilet is leaking.`

等 10–30 秒让 Voice 转发到 Gmail。

### 第 2 步:确认收到了

```bash
python -c "
from dotenv import load_dotenv; load_dotenv('the_super/.env')
from the_super.tools.gmail import read_new_messages
for m in read_new_messages(): print(f'[{m.source}] {m.room_id}: {m.body}')"
```

看到 `[sms] 1F-A: The toilet is leaking.` 就对了。

⚠️ 这条命令会**消费游标** —— 读过的消息记进 `history_cursor.json`,
下次不会再返回。要重放就先 `rm -f the_super/fixtures/history_cursor.json`。

### 第 3 步:跑完整管道

```bash
rm -f the_super/fixtures/history_cursor.json   # 允许重放刚才那条
python main.py poll
```

会看到分类 → 定级 → 起草 → 发送(DRY_RUN 下只打日志)。

### 第 4 步:真的发出去

改 `.env` 里 `DRY_RUN=false`,重跑第 3 步。
几秒后你手机会收到 agent 的英文回复,要你拍照片。

**这就是完整闭环。** 录 demo 时这段最有说服力。

---

## 四点五、改 agent 说话的方式:不用改代码

所有对租客的措辞规范在 `skills/tenant-sms/`,是普通的 markdown:

| 文件 | 管什么 |
|---|---|
| `SKILL.md` | 铁律(禁用表述)、语气、reference 目录 |
| `references/payment.md` | 回执、月份不明的三种问法、少付、多付、查无记录 |
| `references/maintenance.md` | 按设备类型该拍哪些部位、紧急自救指引 |
| `references/collections.md` | 催缴各阶段措辞 + 法律时间线 |
| `references/holding.md` | 缓冲回复的四个档位 |

**改这些文件就能改 agent 的说话方式,不用动 Python,不用重启。**
下次运行时模型会读到新内容。

用的是 ADK 2.0 的 Skills 机制,按需加载 —— 模型先看 `SKILL.md` 的目录,
判断这次要哪个 reference 再去读。所以规则写多细都不会撑爆上下文。

⚠️ **但只有"怎么说"归 Skill。**
"金额相不相符""月份说没说清""能不能自动发" 这类判定是确定性代码
(`payment.py` / `rent.py`)。在 SKILL.md 里写"金额差一点也可以放行"是没用的 ——
路由根本不读它。

---

## 五、什么会自动发,什么只存草稿

| 类型 | 行为 | 为什么 |
|---|---|---|
| 索要照片的短信 | ✅ **自动发** | 零风险,说错了最多再问一次 |
| 问"这是哪个月的房租" | ✅ **自动发** | 同上,而且不问就会记错月份 |
| 缓冲回复(收到了,稍后答复) | ✅ **自动发** | 不承诺任何事,是最安全的对外消息 |
| 房租催缴短信 | ✅ **自动发** | 房东明确要求(覆盖了原约束 1) |
| 给房东的日报/汇总 | ✅ **自动发** | 收件人是房东本人 |
| 付款回执 | 📝 **存 Gmail 草稿** | 财务凭证,必须人工点发送 |
| 派单简报 | 📝 **落库待批准** | 要花钱 |
| 金额不符 / 查无记录 | 🚫 **不回复租客** | 直接转人工 |
| 14 天法定通知 | 🚫 **根本不生成** | 短信不构成法定送达,必须走线下 |

### 关于缓冲回复

分类不确定、涉及租约/押金/法律的消息,系统**不处理实质内容**,
但会自动回一条"已收到,会有人跟进",然后通知你。

在有这条之前,这类消息的结果是"通知房东、租客那边完全静默"。
租客发了一条关于押金的短信,两天没有任何回应 —— 这种沉默会激化矛盾,
而且在纠纷里对房东不利("我发过消息,从来没人理我")。

措辞分四个档位(见 `references/holding.md`),涉及法律时最短、不表任何态。

回执存成草稿后,你**打开 Gmail 点发送**就行,不需要任何自定义界面,手机上也能操作。

---

## 六、三个触发器

```bash
python main.py poll      # 拉新消息 → 分类 → 走对应分支
python main.py rent      # 房租周期:按各自应付日检查,逾期次日发催缴
python main.py digest    # 日报:发短信给房东,含超时未收到照片的工单
```

生产环境用 Cloud Scheduler 分别调用:
poll 每 5–15 分钟、rent 每天一次、digest 每天傍晚。

### 本地定时(可选)

macOS 用 launchd。注意 `main.py` 自己会加载 `.env`,
所以 plist 里不需要重复声明环境变量:

```xml
<key>ProgramArguments</key>
<array>
  <string>/绝对路径/the-super/.venv/bin/python</string>
  <string>/绝对路径/the-super/main.py</string>
  <string>poll</string>
</array>
<key>StartInterval</key><integer>600</integer>
```

---

## 七、常见问题

**发了短信没反应**
→ 没有后台进程。跑 `python main.py poll`。

**跑了 poll 但读不到消息**
→ 消息已被游标记为处理过。`rm -f the_super/fixtures/history_cursor.json` 重放。

**读到了但认不出租客**
→ 发信号码不在 `fixtures/tenants.json` 里。`identify_tenant` 认不出的消息会被跳过。

**没收到 agent 的回复**
→ 检查 `.env` 里 `DRY_RUN` 是不是还是 `true`。

**催缴短信发不出去**
→ Voice 没有"主动给某号码发短信"的接口,只能回复已有邮件线程。
   该租客必须先给你的 Voice 号发过短信,系统才记得下线程 id
   (存在 `fixtures/threads.json`)。汇总里会标 `⚠️ 无短信线程,未发出`。

**`KeyError: Context variable not found`**
→ 某个 Agent 的 instruction 里出现了 `{}`。ADK 会把花括号当 session state
   变量注入。instruction 里不能有花括号。

**OAuth 报 403 access_denied**
→ 这个 Gmail 账号不在 OAuth 同意屏幕的"测试用户"名单里。

**token 突然失效**
→ Testing 状态下 refresh token 7 天过期。重跑 `python authorize.py`。
   `gmail.modify` 是 restricted scope,要脱离 Testing 必须过完整审核,绕不开。

---

## 八、状态文件

全部在 `the_super/fixtures/`,全部已 gitignore:

| 文件 | 内容 | 能删吗 |
|---|---|---|
| `tenants.json` | 租客名册(含真实号码) | ❌ 删了系统不认人 |
| `paypal_transactions.json` | mock 账本 | 可用 `python -m the_super.fixtures_gen <月份>` 重新生成 |
| `history_cursor.json` | 处理过的 message id | ✅ 删掉可重放消息 |
| `ledger.json` | 付款台账 | ✅ 删掉重置 |
| `tickets.json` | 维修工单 | ✅ 删掉重置 |
| `threads.json` | 各租客的 Voice 邮件线程 | ⚠️ 删了就发不出催缴 |

录 demo 前重置:

```bash
rm -f the_super/fixtures/{tickets,ledger,history_cursor}.json
```
