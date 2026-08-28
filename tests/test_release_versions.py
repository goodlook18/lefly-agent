import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from tools.check_release_versions import read_versions, validate_release_version


ROOT = Path(__file__).resolve().parents[1]


class ReleaseVersionTests(unittest.TestCase):
    def test_agent_metadata_matches_the_python_312_runtime_gate(self):
        with (ROOT / "packages/lefly-agent/pyproject.toml").open("rb") as source:
            metadata = tomllib.load(source)

        self.assertEqual(metadata["project"]["requires-python"], ">=3.12,<3.13")

    def test_reads_every_public_package_version(self):
        self.assertEqual(
            read_versions(ROOT),
            {
                "packages/lefly-agent/pyproject.toml": "0.1.1",
                "packages/lefly-console-web/package.json": "0.1.1",
                "packages/lefly-protocol/pyproject.toml": "0.1.1",
                "packages/lefly-sdk-python/pyproject.toml": "0.1.1",
                "packages/lefly-simulator/pyproject.toml": "0.1.1",
            },
        )

    def test_accepts_the_frozen_source_alpha_version(self):
        self.assertEqual(validate_release_version(ROOT, "0.1.1"), [])

    def test_reports_the_path_and_observed_mismatched_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                "packages/lefly-agent/pyproject.toml",
                "packages/lefly-protocol/pyproject.toml",
                "packages/lefly-sdk-python/pyproject.toml",
                "packages/lefly-simulator/pyproject.toml",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    '[project]\nname = "fixture"\nversion = "0.1.0"\n',
                    encoding="utf-8",
                )

            console = root / "packages/lefly-console-web/package.json"
            console.parent.mkdir(parents=True, exist_ok=True)
            console.write_text(
                json.dumps({"name": "@lefly/console-web", "version": "0.1.1"}),
                encoding="utf-8",
            )

            self.assertEqual(
                validate_release_version(root, "0.1.0"),
                [
                    "packages/lefly-console-web/package.json has version 0.1.1; "
                    "expected 0.1.0"
                ],
            )


if __name__ == "__main__":
    unittest.main()
