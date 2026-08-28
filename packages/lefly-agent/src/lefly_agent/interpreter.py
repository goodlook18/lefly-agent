"""Offline adapter over the strict deterministic fast-intent catalog."""

from typing import Protocol

from .fast_intent import FastIntentRouter
from .models import AgentAction, AgentPlan


class TextInterpreter(Protocol):
    async def interpret(self, text: str) -> AgentPlan:
        ...


_ACTION_TOOLS = {
    "play_motion": "motion.play",
    "set_head_light": "light.solid",
    "set_head_light_brightness": "light.brightness",
    "enter_rest_state": "device.rest",
}


class DeterministicChineseInterpreter:
    max_text_length = 500

    def __init__(self, router: FastIntentRouter | None = None) -> None:
        self.router = router or FastIntentRouter()

    async def interpret(self, text: str) -> AgentPlan:
        decision = self.router.route(text)
        if not decision.matched:
            return AgentPlan(
                (),
                decision.confirmation
                or "我还不理解这条指令，可以试试点头、向左看、变黄灯或休眠。",
            )
        tool = _ACTION_TOOLS[decision.intent]
        action = AgentAction(tool, decision.arguments, decision.confirmation or "")
        return AgentPlan((action,), decision.confirmation or "好的，正在执行。")
