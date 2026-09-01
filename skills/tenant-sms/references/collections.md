# 房租催缴短信

## 法律时间线(决定了措辞的边界)

```
应付日          —— 当天不算逾期
应付日 + 1 天   —— 发催缴,给 5 天补缴期
补缴期满         —— 此时逾期满 5 天,房东才有资格启动 14 天通知程序
```

5 天这个数字不是随便定的:**房租逾期满 5 天以上,才可以发 14 天通知。**
所以补缴期设成 5 天,既给了租客机会,也让时间线自然衔接到下一步。

## agent 在这条线上的位置

**只写第一级催缴。** 14 天通知**不生成**,因为:
- 它有法定的形式和内容要求
- 短信通常不构成有效送达
- 形式不合规的通知可能**污染后续的正式程序**

到了该发 14 天通知的时候,agent 的正确动作是通知房东,不是自己写。

## 第一级催缴措辞

### 完全没收到

> Hi Sarah, your October rent of $1,000 was due on October 1 and we haven't
> received it yet. Could you take care of it by October 6? If you've already
> sent it, just reply with the date and method — PayPal eChecks can take up
> to 3 business days to clear and we'll re-check.

### 少付

> Hi Sarah, your October rent was due on October 1. We received $600, which
> leaves $400 outstanding. Could you send the remainder by October 6? If
> you've already sent it, let us know the date and method and we'll re-check.

## 措辞要点

**必须有的:**
- 应付日(具体日期,不是"上个月")
- 金额(没收到就写全额,少付就写三个数:收到/应付/差额)
- 补缴截止日(具体日期)
- 「如果你已经付了」的余地 + eCheck 说明

**绝对不能有的:**
- 任何法律措辞:legal action / eviction / attorney / court / notice
- 任何威胁性表述:"or else" / "final warning" / "we will have no choice"
- 滞纳金金额 —— 那取决于租约条款,agent 不读租约
- 对租客的评价:"again" / "as usual" / "you always"

**语气校准:** 这条短信将来可能被人读到(租客、律师、法官)。
写的时候假设它会被读。既不能软到没有留痕效果,也不能硬到显得是威胁。
**纯事实陈述 + 一个明确的动作 + 一个台阶**,就是正确的温度。

## 为什么要给台阶

租客可能真的付了。PayPal eCheck 最长 3 个工作日,而且租客可能用了
档案之外的邮箱付款。发一条不留余地的催缴给一个已经付了钱的人,
是这个系统能犯的最尴尬的错误。
