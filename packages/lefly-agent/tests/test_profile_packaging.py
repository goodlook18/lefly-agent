"""A built wheel must use the editable profile files as its defaults."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path


class ProfilePackagingTests(unittest.TestCase):
    def test_sdist_contains_editable_profile_sources(self):
        source_package = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "lefly-agent"
            shutil.copytree(
                source_package,
                project,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", "build", "dist", "agent.toml"),
            )
            dist = root / "dist"
            result = subprocess.run(
                [sys.executable, "setup.py", "sdist", "--dist-dir", str(dist)],
                cwd=project,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            archive = next(dist.glob("lefly_agent-*.tar.gz"))
            with tarfile.open(archive, "r:gz") as bundle:
                names = [
                    name for name in bundle.getnames()
                    if "/configs/agent." in name and name.endswith(".toml")
                ]
            self.assertEqual(len(names), 5)

    def test_wheel_profile_is_generated_from_source_config(self):
        source_package = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "lefly-agent"
            shutil.copytree(
                source_package,
                project,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", "build", "dist", "agent.toml"),
            )
            profile = project / "configs" / "agent.qwen.toml"
            profile.write_text(
                "[model]\nprovider='qwen'\nmodel='wheel-source-model'\n",
                encoding="utf-8",
            )
            wheels = root / "wheels"
            result = subprocess.run(
                [
                    sys.executable, "-m", "pip", "wheel", "--no-deps",
                    "--no-build-isolation", "--disable-pip-version-check",
                    "-w", str(wheels), str(project),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            wheel = next(wheels.glob("lefly_agent-*.whl"))
            with zipfile.ZipFile(wheel) as archive:
                names = sorted(
                    name for name in archive.namelist()
                    if name.startswith("lefly_agent/model_profiles/agent.")
                    and name.endswith(".toml")
                )
                self.assertEqual(len(names), 5)
                for name in names:
                    self.assertEqual(
                        archive.read(name),
                        (project / "configs" / Path(name).name).read_bytes(),
                    )
                qwen = tomllib.loads(
                    archive.read("lefly_agent/model_profiles/agent.qwen.toml").decode("utf-8")
                )
            self.assertEqual(qwen["model"]["model"], "wheel-source-model")

            target = root / "installed"
            install = subprocess.run(
                [
                    sys.executable, "-m", "pip", "install", "--no-deps",
                    "--disable-pip-version-check", "--target", str(target), str(wheel),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            outside = root / "outside"
            outside.mkdir()
            environment = os.environ.copy()
            environment.pop("LEFLY_PROFILE_SOURCE_ROOT", None)
            environment["PYTHONPATH"] = str(target)
            loaded = subprocess.run(
                [
                    sys.executable, "-c",
                    "from lefly_agent.config import load_agent_config; "
                    "print(load_agent_config(model_profile='qwen', environ={}).model.model)",
                ],
                cwd=outside,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(loaded.stdout.strip(), "wheel-source-model")

            profile.write_text(
                "[model]\nprovider='qwen'\nmodel='edited-after-install'\n",
                encoding="utf-8",
            )
            environment["LEFLY_PROFILE_SOURCE_ROOT"] = str(project)
            source_loaded = subprocess.run(
                [
                    sys.executable, "-c",
                    "from lefly_agent.config import load_agent_config; "
                    "print(load_agent_config(model_profile='qwen').model.model)",
                ],
                cwd=outside,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(source_loaded.returncode, 0, source_loaded.stderr)
            self.assertEqual(source_loaded.stdout.strip(), "edited-after-install")


if __name__ == "__main__":
    unittest.main()
