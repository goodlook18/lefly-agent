import unittest

from lefly_agent import DeterministicChineseInterpreter


class DeterministicChineseInterpreterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.interpreter = DeterministicChineseInterpreter()

    async def test_interprets_one_motion(self):
        plan = await self.interpreter.interpret("请向左看")

        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].tool, "motion.play")
        self.assertEqual(dict(plan.actions[0].arguments), {"name": "look_left"})
        self.assertIn("向左看", plan.response)

    async def test_rejects_combined_instruction_for_llm_routing(self):
        plan = await self.interpreter.interpret("向左看，然后把灯变成黄色")

        self.assertEqual(plan.actions, ())
        self.assertIn("不执行", plan.response)

    async def test_supports_motion_light_brightness_and_rest_phrases(self):
        cases = {
            "点头": ("motion.play", {"name": "nod"}),
            "摇摇头": ("motion.play", {"name": "headshake"}),
            "跳个舞": ("motion.play", {"name": "dance_demo"}),
            "关灯": ("light.brightness", {"brightness": 0.0}),
            "灯调暗一点": ("light.brightness", {"brightness": 0.25}),
            "变白灯": ("light.solid", {"color": "#FFFFFF"}),
            "变黄灯": ("light.solid", {"color": "#F1A22E"}),
            "变蓝灯": ("light.solid", {"color": "#20A8B5"}),
            "变绿灯": ("light.solid", {"color": "#2F9D68"}),
            "变红灯": ("light.solid", {"color": "#F05D5E"}),
            "休眠": ("device.rest", {}),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                plan = await self.interpreter.interpret(text)
                self.assertEqual(len(plan.actions), 1)
                self.assertEqual(
                    (plan.actions[0].tool, dict(plan.actions[0].arguments)),
                    expected,
                )

    async def test_rejects_repeated_semantic_action_as_multi_intent(self):
        plan = await self.interpreter.interpret("向左看，再向左看")

        self.assertEqual(plan.actions, ())

    async def test_unknown_text_returns_explanation_without_actions(self):
        plan = await self.interpreter.interpret("今天天气怎么样")

        self.assertEqual(plan.actions, ())
        self.assertIn("还不理解", plan.response)

    async def test_refuses_negated_physical_commands(self):
        plan = await self.interpreter.interpret("不要向左看，也别关灯")

        self.assertEqual(plan.actions, ())
        self.assertIn("否定", plan.response)

    async def test_refuses_questions_hypotheticals_and_reported_commands(self):
        for text in ("你会点头吗", "如果你点头会怎样", "我刚才说的是点头"):
            with self.subTest(text=text):
                plan = await self.interpreter.interpret(text)
                self.assertEqual(plan.actions, ())

    async def test_accepts_documented_short_phrases(self):
        cases = {
            "抬头": "look_up",
            "低头": "look_down",
            "醒来": "wake_up",
            "休息": "device.rest",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                plan = await self.interpreter.interpret(text)
                actual = plan.actions[0].arguments.get("name", plan.actions[0].tool)
                self.assertEqual(actual, expected)

    async def test_reset_is_not_treated_as_device_rest(self):
        plan = await self.interpreter.interpret("复位")

        self.assertEqual(plan.actions, ())

    async def test_rejects_empty_or_oversized_text(self):
        for value in ("", "   ", "字" * 501):
            with self.subTest(value_length=len(value)):
                with self.assertRaises(ValueError):
                    await self.interpreter.interpret(value)


if __name__ == "__main__":
    unittest.main()
