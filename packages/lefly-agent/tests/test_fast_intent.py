from __future__ import annotations

import unittest

from lefly_agent.fast_intent import FastIntentRouter


class FastIntentRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = FastIntentRouter()

    def test_required_safety_matrix(self):
        cases = {
            "点头": "play_motion",
            "请点一下头": "play_motion",
            "不要点头": None,
            "你会点头吗": None,
            "如果你点头会怎样": None,
            "点头然后查天气": None,
            "我刚才说的是点头": None,
        }

        for text, expected in cases.items():
            with self.subTest(text=text):
                decision = self.router.route(text)
                self.assertEqual(decision.intent, expected)
                self.assertEqual(decision.matched, expected is not None)

    def test_normalizes_whitespace_and_terminal_punctuation(self):
        decision = self.router.route("  请 点 一下 头！！！ ")

        self.assertTrue(decision.matched)
        self.assertEqual(decision.intent, "play_motion")
        self.assertEqual(dict(decision.arguments), {"name": "nod"})

    def test_rejects_multiple_or_uncertain_intents(self):
        for text in (
            "向左看，然后点头",
            "向左看，再向右看",
            "也许点头",
            "能不能点头",
            "点头还是摇头",
        ):
            with self.subTest(text=text):
                self.assertFalse(self.router.route(text).matched)

    def test_rejects_alias_shared_by_two_catalog_entries(self):
        with self.assertRaisesRegex(ValueError, "ambiguous alias"):
            FastIntentRouter(
                aliases={
                    "nod": ("动作",),
                    "shake_head": ("动作。",),
                }
            )

    def test_returns_only_catalog_owned_allowlisted_arguments(self):
        decision = self.router.route("把灯变成黄色")

        self.assertEqual(decision.intent, "set_head_light")
        self.assertEqual(dict(decision.arguments), {"color": "#F1A22E"})
        with self.assertRaises(TypeError):
            decision.arguments["color"] = "#000000"

    def test_typed_and_final_asr_use_identical_decision_logic(self):
        typed = self.router.route("向右看", source="typed")
        final_asr = self.router.route("向右看", source="final_asr")

        self.assertEqual(typed, final_asr)

    def test_custom_allowlisted_alias_is_anchored(self):
        router = FastIntentRouter(aliases={"nod": ("点个头",)})

        self.assertTrue(router.route("点个头").matched)
        self.assertFalse(router.route("我刚才让你点个头").matched)

    def test_rejects_empty_oversized_or_unknown_source(self):
        for value in ("", "   ", "字" * 501):
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                self.router.route(value)
        with self.assertRaises(ValueError):
            self.router.route("点头", source="partial_asr")


if __name__ == "__main__":
    unittest.main()
