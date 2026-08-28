#!/usr/bin/env python3
"""Regenerate the deterministic inventory for a public LeFly release tree."""

import argparse
from pathlib import Path
import sys
from typing import Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.public_release import write_inventory


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-version", required=True)
    args = parser.parse_args(list(argv) if argv else None)

    try:
        path = write_inventory(args.root, args.release_version)
    except (OSError, ValueError) as error:
        print(f"Release inventory update failed: {error}", file=sys.stderr)
        return 1
    print(f"Updated {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
