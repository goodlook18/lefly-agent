"""Deterministic allowlist export primitives for the public source release."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tomllib
from typing import Literal
from urllib.parse import urlsplit, urlunsplit


_MARKDOWN_LINK = re.compile(r"(!?\[[^\]]*\]\()([^\r\n)]*)(\))")
_GENERATED_TREE_PARTS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "playwright-report",
    "test-results",
}
_LOCAL_TREE_FILES = {".DS_Store", ".coverage", "agent.toml"}


def _safe_relative_path(value: object, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{field} must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("./"):
        raise ValueError(f"{field} must stay inside its root: {value}")
    return path


def _is_same_or_parent(parent: PurePosixPath, child: PurePosixPath) -> bool:
    return parent == child or parent in child.parents


@dataclass(frozen=True)
class Mapping:
    source: PurePosixPath
    target: PurePosixPath
    kind: Literal["file", "tree"]
    rewrite_markdown_links: bool = False


@dataclass(frozen=True)
class Manifest:
    version: str
    mappings: tuple[Mapping, ...]

    def map_target(self, source_path: str | PurePosixPath) -> str | None:
        source = PurePosixPath(source_path)
        for mapping in self.mappings:
            if mapping.kind == "file" and source == mapping.source:
                return mapping.target.as_posix()
            if mapping.kind == "tree" and _is_same_or_parent(mapping.source, source):
                suffix = source.relative_to(mapping.source)
                return (mapping.target / suffix).as_posix()
        return None


def load_manifest(path: Path) -> Manifest:
    with path.open("rb") as manifest_file:
        data = tomllib.load(manifest_file)

    if data.get("schema_version") != 1:
        raise ValueError("public release manifest schema_version must be 1")
    version = data.get("release_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("release_version must be a non-empty string")

    raw_mappings = data.get("mapping")
    if not isinstance(raw_mappings, list) or not raw_mappings:
        raise ValueError("public release manifest requires at least one mapping")

    mappings = []
    for index, item in enumerate(raw_mappings):
        if not isinstance(item, dict):
            raise ValueError(f"mapping {index} must be a table")
        kind = item.get("kind")
        if kind not in ("file", "tree"):
            raise ValueError(f"mapping {index} kind must be file or tree")
        rewrite = item.get("rewrite_markdown_links", False)
        if not isinstance(rewrite, bool):
            raise ValueError(
                f"mapping {index} rewrite_markdown_links must be boolean"
            )
        mappings.append(
            Mapping(
                source=_safe_relative_path(item.get("source"), f"mapping {index} source"),
                target=_safe_relative_path(item.get("target"), f"mapping {index} target"),
                kind=kind,
                rewrite_markdown_links=rewrite,
            )
        )

    for index, mapping in enumerate(mappings):
        for earlier in mappings[:index]:
            if _is_same_or_parent(earlier.source, mapping.source) or _is_same_or_parent(
                mapping.source, earlier.source
            ):
                raise ValueError(
                    f"overlapping source mappings: {earlier.source} and {mapping.source}"
                )
            if _is_same_or_parent(earlier.target, mapping.target) or _is_same_or_parent(
                mapping.target, earlier.target
            ):
                raise ValueError(
                    f"overlapping target mappings: {earlier.target} and {mapping.target}"
                )

    return Manifest(version=version, mappings=tuple(mappings))


def rewrite_markdown_targets(text: str, manifest: Manifest) -> str:
    rewritten = []
    fenced = False
    fence_marker = ""

    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not fenced:
                fenced = True
                fence_marker = marker
            elif marker == fence_marker:
                fenced = False
                fence_marker = ""
            rewritten.append(line)
            continue

        if fenced:
            rewritten.append(line)
            continue

        def replace(match: re.Match[str]) -> str:
            raw_target = match.group(2)
            if not raw_target or raw_target.startswith(("#", "/", "<")):
                return match.group(0)
            parsed = urlsplit(raw_target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                return match.group(0)
            mapped = manifest.map_target(parsed.path)
            if mapped is None:
                return match.group(0)
            target = urlunsplit(("", "", mapped, parsed.query, parsed.fragment))
            return f"{match.group(1)}{target}{match.group(3)}"

        rewritten.append(_MARKDOWN_LINK.sub(replace, line))

    return "".join(rewritten)


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


def write_inventory(root: Path, release_version: str) -> Path:
    if not release_version.strip():
        raise ValueError("release_version must be a non-empty string")
    root = root.resolve()
    inventory_path = root / ".lefly-release-inventory.json"
    inventory_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_version": release_version,
                "files": build_inventory(root),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(inventory_path, 0o644)
    return inventory_path


def _copy_file(
    source: Path,
    target: Path,
    mapping: Mapping,
    manifest: Manifest,
) -> None:
    if source.is_symlink():
        raise ValueError(f"public release source contains symlink: {source}")
    if not source.is_file():
        raise ValueError(f"public release source is not a file: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if mapping.rewrite_markdown_links:
        if source.suffix.lower() != ".md":
            if mapping.kind == "file":
                raise ValueError(
                    f"rewrite_markdown_links requires a Markdown file: {mapping.source}"
                )
        else:
            rewritten = rewrite_markdown_targets(source.read_text(encoding="utf-8"), manifest)
            target.write_text(rewritten, encoding="utf-8")
            shutil.copystat(source, target, follow_symlinks=False)
            return
    shutil.copy2(source, target, follow_symlinks=False)


def _reject_symlink_components(root: Path, relative: PurePosixPath) -> None:
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError(f"public release source contains symlink: {relative}")


def _excluded_tree_path(relative: Path) -> bool:
    return (
        any(part in _GENERATED_TREE_PARTS or part.endswith(".egg-info") for part in relative.parts)
        or relative.name in _LOCAL_TREE_FILES
        or relative.suffix in {".pyc", ".pyo"}
        or relative.name == ".env"
        or relative.name.startswith(".env.") and relative.name != ".env.example"
    )


def export_tree(
    source: Path,
    destination: Path,
    manifest: Manifest,
) -> dict[str, str]:
    source = source.resolve()
    unresolved_destination = destination.absolute()
    resolved_destination = destination.resolve(strict=False)
    if source == resolved_destination or source in resolved_destination.parents:
        raise ValueError("destination must be outside the source tree")
    if unresolved_destination.is_symlink():
        raise ValueError(f"public release destination contains symlink: {destination}")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"destination must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()

    for mapping in manifest.mappings:
        _reject_symlink_components(source, mapping.source)
        source_path = source.joinpath(*mapping.source.parts)
        target_path = destination.joinpath(*mapping.target.parts)
        if source_path.is_symlink():
            raise ValueError(f"public release source contains symlink: {mapping.source}")
        if mapping.kind == "file":
            _copy_file(source_path, target_path, mapping, manifest)
            continue
        if not source_path.is_dir():
            raise ValueError(f"public release source is not a directory: {mapping.source}")
        for candidate in sorted(source_path.rglob("*")):
            relative = candidate.relative_to(source_path)
            if _excluded_tree_path(relative):
                continue
            if candidate.is_symlink():
                raise ValueError(
                    "public release source contains symlink: "
                    f"{candidate.relative_to(source)}"
                )
            exported = target_path / relative
            if candidate.is_dir():
                exported.mkdir(parents=True, exist_ok=True)
            elif candidate.is_file():
                _copy_file(candidate, exported, mapping, manifest)
            else:
                raise ValueError(
                    f"unsupported public release source type: {candidate.relative_to(source)}"
                )

    inventory_path = write_inventory(destination, manifest.version)
    return json.loads(inventory_path.read_text(encoding="utf-8"))["files"]
