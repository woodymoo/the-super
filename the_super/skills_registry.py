"""Skill registry — one shared instance for the whole project.

Every wording standard for talking to tenants lives outside the code in
skills/tenant-sms/.

Why not put it in an instruction: wording rules keep growing (which spots to ask
about per fixture, how to word each payment anomaly, when only a holding reply is
allowed), and stuffing that into an instruction means **carrying all of it on
every call**. A Skill loads on demand — the model reads the index in SKILL.md,
decides which reference this case needs, and reads only that one. The rules can
be as detailed as you like without blowing up the context.

⚠️ Wording belongs to the Skill; **decisions do not**. Consequential judgments
like "does the amount match" and "may this be auto-sent" remain deterministic
code. The Skill governs how to say it, not whether it may be said.
"""

import pathlib

from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"

TENANT_SMS_SKILL = SkillToolset(
    skills=[load_skill_from_dir(SKILLS_DIR / "tenant-sms")]
)
