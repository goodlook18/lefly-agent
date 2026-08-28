import tempfile
import unittest
from pathlib import Path

from tools.audit_open_source_boundary import find_violations


class OpenSourceBoundaryTest(unittest.TestCase):
    def test_clean_python_tree_has_no_violations(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clean.py"
            source.write_text("import json\nfrom dataclasses import dataclass\n")

            self.assertEqual(find_violations([Path(directory)]), [])

    def test_forbidden_imports_report_file_line_and_module(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "hardware.py"
            source.write_text(
                "import lerobot\n"
                "from rpi_ws281x import PixelStrip\n"
            )

            violations = find_violations([Path(directory)])

            self.assertEqual(
                [(item.line, item.module) for item in violations],
                [
                    (1, "lerobot"),
                    (2, "rpi_ws281x"),
                ],
            )
            self.assertTrue(all(item.path == source for item in violations))

    def test_all_forbidden_driver_roots_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "drivers.py"
            source.write_text(
                "import feetech_servo_sdk\n"
                "import serial.tools.list_ports\n"
                "from smbus2 import SMBus\n"
            )

            violations = find_violations([source])

            self.assertEqual(
                [item.module for item in violations],
                [
                    "feetech_servo_sdk",
                    "serial.tools.list_ports",
                    "smbus2",
                ],
            )


if __name__ == "__main__":
    unittest.main()
