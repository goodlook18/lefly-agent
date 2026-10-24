"""Read-only file inventory for validating a public LeFly release tree."""

import hashlib
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_inventory(root: Path) -> dict[str, str]:
    inventory = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            raise ValueError(f"release tree contains symlink: {relative}")
        if not path.is_file() or path.name == ".lefly-release-inventory.json":
            continue
        inventory[relative.as_posix()] = _sha256(path)
    return inventory
