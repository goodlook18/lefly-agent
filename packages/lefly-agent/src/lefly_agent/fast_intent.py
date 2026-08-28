"""Strict, non-executable routing for unambiguous robot text commands."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class FastIntentDecision:
    matched: bool
    intent: str | None = None
    arguments: Mapping[str, object] = field(default_factory=dict)
    confirmation: str | None = None

    def __post_init__(self) -> None:
        if self.matched != (self.intent is not None):
            raise ValueError("matched decisions must contain exactly one intent")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


@dataclass(frozen=True)
class _IntentSpec:
    intent: str
    arguments: Mapping[str, object]
    label: str
    aliases: tuple[str, ...]


def _spec(
    name: str,
    intent: str,
    arguments: Mapping[str, object],
    label: str,
    *aliases: str,
) -> tuple[str, _IntentSpec]:
    return name, _IntentSpec(intent, MappingProxyType(dict(arguments)), label, aliases)


INTENT_CATALOG: Mapping[str, _IntentSpec] = MappingProxyType(
    dict(
        (
            _spec("look_left", "play_motion", {"name": "look_left"}, "向左看", "向左看", "请向左看", "看左边", "看向左边", "左转"),
            _spec("look_right", "play_motion", {"name": "look_right"}, "向右看", "向右看", "请向右看", "看右边", "看向右边", "右转"),
            _spec("look_up", "play_motion", {"name": "look_up"}, "向上看", "向上看", "抬头看", "抬头"),
            _spec("look_down", "play_motion", {"name": "look_down"}, "向下看", "向下看", "低头看", "低头"),
            _spec("nod", "play_motion", {"name": "nod"}, "点头", "点头", "点点头", "请点一下头"),
            _spec("shake_head", "play_motion", {"name": "headshake"}, "摇头", "摇头", "摇摇头"),
            _spec("happy_wiggle", "play_motion", {"name": "happy_wiggle"}, "开心摇摆", "开心摇摆", "开心一点"),
            _spec("dance", "play_motion", {"name": "dance_demo"}, "舞蹈", "跳个舞", "跳舞", "舞蹈"),
            _spec("wake", "play_motion", {"name": "wake_up"}, "唤醒", "醒一醒", "醒来", "唤醒"),
            _spec("light_white", "set_head_light", {"color": "#FFFFFF"}, "头灯设为白色", "变白灯", "白色", "白灯", "把灯变成白色"),
            _spec("light_yellow", "set_head_light", {"color": "#F1A22E"}, "头灯设为黄色", "变黄灯", "黄色", "黄灯", "把灯变成黄色"),
            _spec("light_blue", "set_head_light", {"color": "#20A8B5"}, "头灯设为蓝色", "变蓝灯", "蓝色", "蓝灯", "把灯变成蓝色"),
            _spec("light_green", "set_head_light", {"color": "#2F9D68"}, "头灯设为绿色", "变绿灯", "绿色", "绿灯", "把灯变成绿色"),
            _spec("light_red", "set_head_light", {"color": "#F05D5E"}, "头灯设为红色", "变红灯", "红色", "红灯", "把灯变成红色"),
            _spec("light_off", "set_head_light_brightness", {"brightness": 0.0}, "关闭头灯", "关灯", "关闭灯光", "把灯关掉"),
            _spec("light_bright", "set_head_light_brightness", {"brightness": 1.0}, "调亮头灯", "调亮", "亮一点", "灯调亮一点", "最亮"),
            _spec("light_dim", "set_head_light_brightness", {"brightness": 0.25}, "调暗头灯", "调暗", "暗一点", "灯调暗一点"),
            _spec("rest", "enter_rest_state", {}, "进入休息状态", "休眠", "休息", "睡觉"),
        )
    )
)

INTENT_NAMES = tuple(INTENT_CATALOG)
DEFAULT_FAST_INTENT_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {name: spec.aliases for name, spec in INTENT_CATALOG.items()}
)

_NEGATIONS = ("不要", "别", "不用", "不需要", "停止", "取消")
_QUESTIONS = ("你会", "能不能", "可不可以", "是否")
_HYPOTHETICALS = ("如果", "假如", "要是")
_EXPLANATORY = ("我刚才说的是", "我说的是", "刚才提到", "这句话")
_UNCERTAIN = ("可能", "也许", "大概", "不知道")
_MULTI_INTENT = ("然后", "并且", "同时", "接着", "以及", "还要", "还是", "，", ",", ";", "；")
_SOURCES = {"typed", "final_asr"}


def normalize_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("text must be a string")
    stripped = value.strip().casefold()
    if not stripped:
        raise ValueError("text must be non-empty")
    if len(stripped) > 500:
        raise ValueError("text must not exceed 500 characters")
    compact = "".join(stripped.split())
    return re.sub(r"[。！？!?]+$", "", compact)


def _rejection_reason(original: str, normalized: str) -> str | None:
    compact_original = "".join(original.strip().casefold().split())
    if any(marker in normalized for marker in _NEGATIONS):
        return "检测到否定或取消表达，为避免误动作，本次不执行。"
    if (
        any(marker in normalized for marker in _QUESTIONS)
        or compact_original.endswith(("?", "？", "吗", "呢", "么"))
    ):
        return "检测到问句，为避免误动作，本次不执行。"
    if any(marker in normalized for marker in _HYPOTHETICALS):
        return "检测到假设表达，为避免误动作，本次不执行。"
    if any(marker in normalized for marker in _EXPLANATORY):
        return "检测到转述或说明表达，为避免误动作，本次不执行。"
    if any(marker in normalized for marker in _UNCERTAIN):
        return "检测到不确定表达，为避免误动作，本次不执行。"
    if "再" in normalized or any(marker in normalized for marker in _MULTI_INTENT):
        return "检测到复合指令，为避免误动作，本次不执行。"
    return None


class FastIntentRouter:
    """Match one full allowlisted phrase and return data without side effects."""

    def __init__(
        self,
        aliases: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        configured = aliases or {}
        unknown = sorted(set(configured) - set(INTENT_CATALOG))
        if unknown:
            raise ValueError("unknown fast intent: %s" % ", ".join(unknown))

        lookup: dict[str, _IntentSpec] = {}
        for name, spec in INTENT_CATALOG.items():
            values = spec.aliases + tuple(configured.get(name, ()))
            for alias in values:
                normalized = normalize_text(alias)
                owner = lookup.get(normalized)
                if owner is not None and owner is not spec:
                    raise ValueError("ambiguous alias: %r" % alias)
                lookup[normalized] = spec
        self._lookup = MappingProxyType(lookup)

    def route(self, text: str, *, source: str = "typed") -> FastIntentDecision:
        if source not in _SOURCES:
            raise ValueError("source must be typed or final_asr")
        normalized = normalize_text(text)
        rejection = _rejection_reason(text, normalized)
        if rejection is not None:
            return FastIntentDecision(False, confirmation=rejection)

        spec = self._lookup.get(normalized)
        if spec is None:
            return FastIntentDecision(False)
        return FastIntentDecision(
            True,
            intent=spec.intent,
            arguments=spec.arguments,
            confirmation="好的，正在执行：%s。" % spec.label,
        )
