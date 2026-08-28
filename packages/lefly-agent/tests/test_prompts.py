from __future__ import annotations

import unittest

from lefly_agent.prompts import PUBLISHED_TOOL_NAMES, build_agent_instructions


class PromptTests(unittest.TestCase):
    def test_prompt_names_every_published_tool_and_injects_runtime_facts(self):
        prompt = build_agent_instructions(
            current_datetime="2026年8月21日 星期五 09:07（Asia/Shanghai）",
            motion_presets=("nod", "look_left"),
        )

        for tool_name in PUBLISHED_TOOL_NAMES:
            self.assertIn(tool_name, prompt)
        self.assertIn("2026年8月21日", prompt)
        self.assertIn("nod", prompt)
        self.assertIn("look_left", prompt)

    def test_prompt_requires_sources_and_protects_robot_owned_status(self):
        prompt = build_agent_instructions(
            current_datetime="2026年8月21日 星期五 09:07（Asia/Shanghai）",
            motion_presets=(),
        )

        for fact in ("日期", "天气", "搜索"):
            self.assertIn(fact, prompt)
        self.assertIn("不得编造", prompt)
        self.assertIn("状态灯", prompt)
        self.assertIn("不可", prompt)
        self.assertIn("当前没有已确认的预设动作", prompt)
        self.assertNotIn("status.set", PUBLISHED_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
