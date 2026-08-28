#!/usr/bin/env python3
"""Reject hardware-specific imports from LeFly clean-core paths."""

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Sequence


FORBIDDEN_MODULES = (
    "lerobot",
    "feetech_servo_sdk",
    "rpi_ws281x",
    "serial",
    "smbus2",
)

SKIPPED_DIRECTORIES = {
    ".git",
    ".codegraph",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    module: str


def _is_forbidden(module: str, forbidden_modules: Sequence[str]) -> bool:
    return any(
        module == forbidden or module.startswith(forbidden + ".")
        for forbidden in forbidden_modules
    )


def _python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            yield path
            continue
        if not path.is_dir():
            continue
        for candidate in sorted(path.rglob("*.py")):
            if not any(part in SKIPPED_DIRECTORIES for part in candidate.parts):
                yield candidate


def find_violations(
    paths: Iterable[Path], *, forbidden_modules: Sequence[str] = FORBIDDEN_MODULES
) -> List[Violation]:
    violations: List[Violation] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                if _is_forbidden(module, forbidden_modules):
                    violations.append(Violation(path, node.lineno, module))
    return sorted(violations, key=lambda item: (str(item.path), item.line, item.module))


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(list(argv) if argv else None)

    violations = find_violations(args.paths)
    if violations:
        for item in violations:
            print(f"{item.path}:{item.line}: forbidden import {item.module}")
        return 1

    print("Open-source boundary audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
