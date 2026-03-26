"""Built-in skill registry."""
from dataclasses import dataclass


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]


class SkillRegistry:
    """Simple built-in workflow registry."""

    def __init__(self):
        # Task E6 完成: 引入可列出的内建 skill 集合。
        self._skills = [
            Skill("fix-ci", "诊断并修复 CI 失败", ["ci", "check", "workflow"]),
            Skill("review-pr", "生成 PR 审查摘要", ["pr", "review"]),
            Skill("release", "准备发布清单与验证项", ["release", "deploy"]),
            Skill("security-scan", "执行安全检查建议", ["security", "vuln", "secret"]),
        ]

    def list_all(self) -> list[Skill]:
        return list(self._skills)

    def suggest(self, text: str) -> list[Skill]:
        lowered = text.lower()
        return [
            skill for skill in self._skills
            if any(trigger in lowered for trigger in skill.triggers)
        ]
