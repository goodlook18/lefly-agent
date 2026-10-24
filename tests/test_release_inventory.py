import hashlib
from pathlib import Path
import tempfile
import unittest

from tools.release_inventory import build_inventory


class ReleaseInventoryTests(unittest.TestCase):
    def test_builder_is_deterministic_and_excludes_inventory_and_git(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Public\n", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git/config").write_text("private\n", encoding="utf-8")
            (root / ".lefly-release-inventory.json").write_text("{}\n", encoding="utf-8")

            first = build_inventory(root)
            second = build_inventory(root)

            self.assertEqual(first, second)
            self.assertEqual(first, {"README.md": hashlib.sha256(b"# Public\n").hexdigest()})

    def test_read_only_builder_detects_local_configuration_without_changing_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / ".git/config").write_text("private\n", encoding="utf-8")
            (root / "README.md").write_text("# Public\n", encoding="utf-8")
            inventory = root / ".lefly-release-inventory.json"
            inventory.write_text("{}\n", encoding="utf-8")
            local_config = root / "packages/lefly-agent/agent.toml"
            local_config.parent.mkdir(parents=True)
            local_config.write_text("dummy = 'example'\n", encoding="utf-8")

            observed = build_inventory(root)

            self.assertEqual(set(observed), {"README.md", "packages/lefly-agent/agent.toml"})
            self.assertEqual(inventory.read_text(encoding="utf-8"), "{}\n")


if __name__ == "__main__":
    unittest.main()
