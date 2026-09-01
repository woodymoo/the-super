"""技能注册表 —— 全项目共用一个实例。

对租客说话的措辞规范全部外置在 skills/tenant-sms/。

为什么不写进 instruction:措辞规则会不断变厚(每种设备该问哪些部位、
每种付款异常怎么措辞、什么情况只能给缓冲回复),塞进 instruction 会让
**每次调用都带上全部内容**。Skill 是按需加载的 —— 模型先看 SKILL.md 的
目录,判断这次要哪个 reference 再去读。写多细都不撑爆上下文。

⚠️ 措辞归 Skill,**判定不归**。"金额相不相符""能不能自动发"这类有后果的
判断仍然是确定性代码。Skill 管"怎么说",不管"该不该说"。
"""

import pathlib

from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"

TENANT_SMS_SKILL = SkillToolset(
    skills=[load_skill_from_dir(SKILLS_DIR / "tenant-sms")]
)
