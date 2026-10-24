"""Build wheel profile data from the editable source configuration files."""

from __future__ import annotations

import tomllib
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


PROFILE_PROVIDERS = {
    "openai": "openai",
    "qwen": "qwen",
    "deepseek": "deepseek",
    "huawei-maas": "huawei_maas",
    "openai-compatible": "openai_compatible",
}


class BuildWithModelProfiles(build_py):
    def run(self):
        super().run()
        source = Path(__file__).resolve().parent / "configs"
        destination = Path(self.build_lib) / "lefly_agent" / "model_profiles"
        destination.mkdir(parents=True, exist_ok=True)
        for name, provider in PROFILE_PROVIDERS.items():
            filename = "agent.%s.toml" % name
            raw = (source / filename).read_bytes()
            profile = tomllib.loads(raw.decode("utf-8"))
            model = profile.get("model")
            if (
                set(profile) != {"model"}
                or not isinstance(model, dict)
                or set(model) - {"provider", "model", "base_url"}
                or model.get("provider") != provider
                or not isinstance(model.get("model"), str)
                or not model["model"].strip()
            ):
                raise ValueError("invalid model profile: %s" % filename)
            (destination / filename).write_bytes(raw)


setup(cmdclass={"build_py": BuildWithModelProfiles})
