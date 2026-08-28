#!/usr/bin/env python3
"""Verify that every public LeFly package uses one release version."""

import argparse
import json
from pathlib import Path
import sys
import tomllib
from typing import Sequence


PYTHON_METADATA = (
    "packages/lefly-agent/pyproject.toml",
    "packages/lefly-protocol/pyproject.toml",
    "packages/lefly-sdk-python/pyproject.toml",
    "packages/lefly-simulator/pyproject.toml",
)
CONSOLE_METADATA = "packages/lefly-console-web/package.json"


def read_versions(root: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for relative in PYTHON_METADATA:
        with (root / relative).open("rb") as metadata_file:
            metadata = tomllib.load(metadata_file)
        versions[relative] = str(metadata["project"]["version"])

    with (root / CONSOLE_METADATA).open(encoding="utf-8") as metadata_file:
        console_metadata = json.load(metadata_file)
    versions[CONSOLE_METADATA] = str(console_metadata["version"])
    return dict(sorted(versions.items()))


def validate_release_version(root: Path, expected: str) -> list[str]:
    failures = []
    for relative, observed in read_versions(root).items():
        if observed != expected:
            failures.append(
                f"{relative} has version {observed}; expected {expected}"
            )
    return failures


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--expected", required=True)
    args = parser.parse_args(list(argv) if argv else None)

    try:
        failures = validate_release_version(args.root.resolve(), args.expected)
    except (KeyError, OSError, tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
        print(f"Unable to read release metadata: {error}", file=sys.stderr)
        return 1

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(f"Release versions match {args.expected}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
