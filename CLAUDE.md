# The Super — 项目说明

自主房产管理 agent。Devpost "All Things Agentic Hackathon",Taskmaster 赛道。
**截止:2026-08-31 17:00 PDT。**

完整设计见 `docs/design.md`,提交说明草稿见 `docs/submission.md`。
动手前先读 `docs/design.md`。

---

## ⚠️ 必须使用 ADK 2.0,不要写 1.x 代码

这是最容易出错的地方。ADK Python 2.0 于 2026-05-19 GA,引入了图执行引擎。
网上和训练数据里绝大多数 ADK 示例是 1.x 时代的,**不要照搬**。

**禁止使用(1.x 写法):**

- `SequentialAgent` / `ParallelAgent` / `LoopAgent` — 已被 graph workflow 取代
- `output_key="foo"` + 下游 instruction 里的 `{foo}` 模板 — 2.0 靠节点返回值自动传递
- 手动 `session.state[...]` 传递节点间数据
- `context.session.events.append(...)` 直接追加事件
- 覆写 `_run_async_impl()` 或 `generate_content()` — 图引擎会静默忽略

**应当使用(2.0 写法):**

```python
from google.adk import Agent, Event, Workflow
from pydantic import BaseModel

class MySchema(BaseModel):
    field: str

step_a = Agent(name="a", model="gemini-flash-latest",
               instruction="...", output_schema=MySchema)

def pure_code_node(node_input: MySchema) -> OtherSchema:
    """纯代码节点,不调模型。"""
    return OtherSchema(...)

def router(node_input: OtherSchema):
    return Event(route="BRANCH_X" if cond else "BRANCH_Y")

root_agent = Workflow(
    name="root_agent",
    edges=[
        ("START", step_a, pure_code_node, router),
        (router, {"BRANCH_X": node_x, "BRANCH_Y": node_y}),
    ],
)
```

要点:
- 节点之间靠**返回值**传数据,pydantic schema 是类型契约,不写 session state
- 节点可以是 Agent、普通函数、Tool,或另一个 Workflow
- 路由函数返回 `Event(route=...)`,下一行 edges 用 dict 分派

`payment.py` 是已验证可用的参考实现,新代码照它的风格写。

不确定 API 时用 WebFetch 查 https://adk.dev/graphs/ 和 https://adk.dev/graphs/routes/,
不要凭记忆写。

## ⚠️ 工具里不要写宽泛的 except

ADK 2.0 有框架级自动重试。工具里留 `except Exception:` 会把失败对框架屏蔽,
**永久禁用该步骤的重试**。绝不 catch `BaseException`(会吞掉 `NodeInterruptedError`,
破坏人工介入暂停)。

- 业务语义错误(未知租客、金额格式不对)→ 返回结构化 error 结果给模型
- 基础设施故障(网络、IMAP、Firestore 超时)→ **让它抛出去**,框架会重试

---

## ⚠️ Optional 字段必须给默认值

这个坑咬过两次。ADK 序列化节点输出时会剥掉 `None`,再校验就报
`Field required` —— 因为 `X | None` 不带默认值在 pydantic 里**仍然是必填**。

```python
repeat_of: str | None            # ❌ 模型不返回这个字段 -> 整条分支崩
repeat_of: str | None = None     # ✅
```

尤其危险的是那些"正常情况下就是 None"的字段
(`found_amount` 在查无记录时、`cure_deadline` 在不催缴时)——
它们会让 happy path 之外的分支必崩,而且只在跑到那条分支时才暴露。

新增 pydantic 模型后扫一遍:

```bash
python -c "
import importlib, inspect
from pydantic import BaseModel
for mod in ['the_super.schemas','the_super.payment','the_super.maintenance','the_super.rent']:
    m = importlib.import_module(mod)
    for _, o in vars(m).items():
        if inspect.isclass(o) and issubclass(o, BaseModel) and o is not BaseModel:
            for fn, f in o.model_fields.items():
                if f.is_required() and type(None) in getattr(f.annotation, '__args__', ()):
                    print(f'❌ {o.__name__}.{fn}')"
```

---

## 措辞归 Skill,判定归代码

对租客说话的措辞规范全部外置在 `skills/tenant-sms/`(ADK 2.0 Skills,
`SKILL.md` + `references/*.md`,按需加载)。改 markdown 就能改 agent 怎么说话。

**但只有"怎么说"归 Skill。** 下面这些永远是确定性代码:

| 归代码 | 在哪 |
|---|---|
| 金额相不相符 | `payment.verify_payment` |
| 月份说没说清 | `payment.month_router` |
| 能不能自动发回执 | `payment.verification_router` |
| 逾期几天该催缴 | `rent.check_one` |
| 什么可以自动发给租客 | 各节点的 `send_sms_now` / `draft_sms_reply` 调用点 |

在 SKILL.md 里写"金额差一点也可以放行"是没用的 —— 路由根本不读它。
这是有意的:prompt 的行为会随模型版本漂移,而且无法写测试证明它每次都对。

新增措辞规则 → 写进 `skills/tenant-sms/references/`,不要塞进 instruction。
instruction 里只留"去读哪个 reference"的指路。

---

## 架构约束(不要违反)

1. **对外消息默认存草稿,不自动发。** 所有对租客/维修师傅的消息一律存为 Gmail
   草稿,等人工点发送。给房东本人的日报和汇总自动发。

   **自动发的例外(仅此三类):**
   - 索要照片的短信(零风险,说错了最多再问一次)
   - 给房东本人的日报、汇总
   - **房租催缴短信** —— 房东 2026-08-31 明确要求改为自动发送,
     覆盖了本条原先的"绝不自动发送"。实现在 `main.py` 的 `rent_cycle`。
     催缴文案是 `rent.py` 里的确定性模板,不经过模型。
     **不生成 14 天法定通知** —— 短信不构成法定送达,那一步必须走线下。

   `DRY_RUN=true` 时所有发送只打日志。第一次对真实邮箱跑务必开着。

2. **有财务或法律后果的判断必须写成确定性代码,不能交给 prompt。**
   金额比对、"验证通过才发回执" 这类规则用 `if` 实现,不写进 instruction。
   参考 `payment.py` 里的 `verify_payment` 和 `verification_router`。

3. **付款回执措辞:**"已收到你的付款通知",不是"已确认到账"。
   agent 无法验证 PayPal 真实到账,台账里 `claimed` 和 `confirmed` 是两个状态。

4. **agent 保持无状态。** 等照片这类等待通过 Firestore 状态 + Cloud Scheduler
   下一轮轮询实现,不要在 agent 内部循环等待(会让 Cloud Run 容器常驻烧钱)。

5. **PayPal 是 mock。** 隔离在 `payment.py` 的 `_lookup_transactions()` 单个函数里,
   读 `fixtures/paypal_transactions.json`。不要把 mock 逻辑扩散到别处。

---

## 渠道架构

**一切都走 Gmail API,只需一套 OAuth。**

| 方向 | 通道 | Gmail 里怎么识别 |
|---|---|---|
| 租客发短信 | Google Voice 转发到邮箱 | `from:txt.voice.google.com` |
| 回短信给租客 | 回复那封 Voice 邮件 | reply |
| 租客发照片/视频 | 租客自己的邮箱 | `from:<租客邮箱>` + 附件 |

Google Voice 没有公开 API。邮件转发格式是它自己定的,不是正式接口 ——
**解析逻辑必须独立成一个函数**,Google 改版时只改一处。

---

## 业务事实

- 5 个单间,每间 $1000/月
- **应付日逐户不同**,写在 `fixtures/tenants.json` 的 `rent_due_day`
  (1F-A/1F-B 是 1 号,2F-A/2F-B 是 3 号,3F-A 是 5 号)。
  金额同样逐户取 `rent_amount`,不要用全局常量。
- 租客通过 PayPal 付款后发短信告知金额,房东回短信确认作为回执
- 报修通过短信,描述不清时索要照片/视频(走邮件)
- 房东不常查邮件,**日报必须发短信**

---

## 开发环境

```bash
source .venv/bin/activate
adk web              # 在项目根目录跑,不要 cd 进 the_super/
```

`.env` 在 `the_super/` 里,包含 `GOOGLE_GENAI_USE_ENTERPRISE=FALSE`
和 `GOOGLE_API_KEY`(AI Studio 免费 key,非 Vertex)。

---

## 实现顺序

Phase 1(必须):Gmail 读取 → 分类路由 → 付款分支 → 日报
Phase 2(核心亮点):维修定级 + 描述不清时索要照片
Phase 3(加分):跨渠道关联附件 + Gemini 看图
Phase 4(有余力):派单简报、房租周期催缴

**时间紧就砍 Phase 3/4。** 周六必须留一整天录 demo 视频。

---

## 演示注意

录屏前把租客姓名、电话、邮箱、真实门牌号全部替换成假数据。
提交说明里必须明确标注 PayPal 验证是 mock。
