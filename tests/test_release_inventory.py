import json
from pathlib import Path
import tempfile
import unittest

from tools.public_release import build_inventory, write_inventory
from tools.update_release_inventory import main as update_inventory


class ReleaseInventoryTests(unittest.TestCase):
    def test_write_inventory_is_deterministic_and_excludes_itself_and_git(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Public\n", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git/config").write_text("private\n", encoding="utf-8")

            first = write_inventory(root, "0.1.0")
            second = write_inventory(root, "0.1.0")
            data = json.loads(first.read_text(encoding="utf-8"))

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(data["release_version"], "0.1.0")
            self.assertEqual(data["files"], build_inventory(root))
            self.assertEqual(list(data["files"]), ["README.md"])

    def test_public_cli_regenerates_the_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Before\n", encoding="utf-8")

            self.assertEqual(
                update_inventory(
                    ["--root", str(root), "--release-version", "0.1.0"]
                ),
                0,
            )
            before = (root / ".lefly-release-inventory.json").read_bytes()
            (root / "README.md").write_text("# After\n", encoding="utf-8")

            self.assertEqual(
                update_inventory(
                    ["--root", str(root), "--release-version", "0.1.0"]
                ),
                0,
            )
            self.assertNotEqual(
                before,
                (root / ".lefly-release-inventory.json").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
