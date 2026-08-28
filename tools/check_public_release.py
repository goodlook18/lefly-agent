#!/usr/bin/env python3
"""Validate a complete LeFly public repository tree."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Iterable, Sequence
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.check_release_versions import validate_release_version
from tools.public_release import build_inventory


RELEASE_VERSION = "0.1.1"
REQUIRED_ROOT_FILES = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    ".lefly-release-inventory.json",
)
ALLOWED_ROOT_ENTRIES = {
    ".github",
    ".gitignore",
    ".lefly-release-inventory.json",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "NOTICE",
    "README.md",
    "README.zh-CN.md",
    "SECURITY.md",
    "contracts",
    "docs",
    "examples",
    "packages",
    "tests",
    "tools",
}
ALLOWED_DOC_FILES = {
    "docs/README.md",
    "docs/architecture.md",
    "docs/assets/console-overview-v0.1.0.png",
    "docs/assets/hardware-parts.jpg",
    "docs/assets/hardware-survey-qr.png",
    "docs/compatibility.md",
    "docs/hardware-preview.md",
    "docs/protocol.md",
    "docs/quickstart.md",
    "docs/roadmap.md",
    "docs/sdk-python.md",
    "docs/simulator.md",
    "docs/text-agent.md",
    "docs/third-party-notices.md",
    "docs/zh-CN/README.md",
    "docs/zh-CN/architecture.md",
    "docs/zh-CN/compatibility.md",
    "docs/zh-CN/hardware-preview.md",
    "docs/zh-CN/protocol.md",
    "docs/zh-CN/quickstart.md",
    "docs/zh-CN/roadmap.md",
    "docs/zh-CN/sdk-python.md",
    "docs/zh-CN/simulator.md",
    "docs/zh-CN/text-agent.md",
}
TEXT_EXTENSIONS = {
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_MARKDOWN_LINK = re.compile(r"(!?)\[[^\]]*\]\(([^\r\n)]*)\)")
_ENV_SECRET = re.compile(
    r"(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)"
    r"\s*=\s*['\"]?([^'\"\s]+)"
)
_TOKEN_PREFIX = re.compile(
    r"\b(?:sk|ghp|github_pat|xox[baprs])-[A-Za-z0-9_-]{16,}"
)
_STATIC_REFERENCE = re.compile(r"(?:src|href)=[\"']([^\"']+)[\"']")


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def _sorted(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda item: (item.code, item.path, item.detail))


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_text_files(paths: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for root in paths:
        candidates = [root] if root.is_file() else sorted(root.rglob("*")) if root.is_dir() else []
        for path in candidates:
            if (
                path in seen
                or ".git" in path.parts
                or not path.is_file()
                or path.suffix.lower() not in TEXT_EXTENSIONS
            ):
                continue
            seen.add(path)
            yield path


def _safe_example_secret(value: str) -> bool:
    normalized = value.strip("'\"").lower()
    return (
        normalized in {"", "...", "example", "placeholder"}
        or normalized.startswith(("your-", "example-", "<", "$", "${"))
    )


def _check_text(root: Path, files: Iterable[Path]) -> list[Finding]:
    findings = []
    for path in files:
        relative = _relative(root, path)
        if relative == "tools/check_public_release.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(
                Finding("invalid_utf8", _relative(root, path), "text file is not UTF-8")
            )
            continue
        if "/Users/" in text:
            findings.append(Finding("local_mac_path", relative, "contains /Users/"))
        if "/home/" in text:
            findings.append(Finding("local_linux_path", relative, "contains /home/"))
        if "![[" in text:
            findings.append(Finding("obsidian_image", relative, "contains Obsidian image syntax"))
        if "-----BEGIN PRIVATE KEY-----" in text:
            findings.append(Finding("private_key", relative, "contains a private-key header"))
        if _TOKEN_PREFIX.search(text):
            findings.append(Finding("token_prefix", relative, "contains a credential-like token"))
        for match in _ENV_SECRET.finditer(text):
            if not _safe_example_secret(match.group(1)):
                findings.append(
                    Finding("assigned_secret", relative, "contains a non-example secret assignment")
                )
                break
        if path.suffix.lower() == ".md":
            if re.search(r"\bT(?:ODO|BD)\b", text, flags=re.IGNORECASE):
                findings.append(
                    Finding("unfinished_english", relative, "contains an unfinished marker")
                )
            if "你来补" in text:
                findings.append(
                    Finding("unfinished_chinese", relative, "contains an unfinished marker")
                )
    return findings


def check_markdown_links(root: Path) -> list[Finding]:
    findings = []
    for path in sorted(root.rglob("*.md")):
        if ".git" in path.relative_to(root).parts:
            continue
        text = path.read_text(encoding="utf-8")
        fenced = False
        marker = ""
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith(("```", "~~~")):
                current = stripped[:3]
                if not fenced:
                    fenced = True
                    marker = current
                elif current == marker:
                    fenced = False
                    marker = ""
                continue
            if fenced:
                continue
            for match in _MARKDOWN_LINK.finditer(line):
                is_image = bool(match.group(1))
                raw = match.group(2).strip()
                if not raw or raw.startswith(("#", "<")):
                    continue
                parsed = urlsplit(raw)
                if parsed.scheme or parsed.netloc:
                    continue
                decoded = unquote(parsed.path)
                if not decoded:
                    continue
                target = (path.parent / decoded).resolve(strict=False)
                try:
                    target.relative_to(root.resolve())
                except ValueError:
                    findings.append(
                        Finding(
                            "image_path_escape" if is_image else "link_path_escape",
                            _relative(root, path),
                            f"line {line_number}: {raw}",
                        )
                    )
                    continue
                if not target.exists():
                    findings.append(
                        Finding(
                            "missing_link",
                            _relative(root, path),
                            f"line {line_number}: {raw}",
                        )
                    )
    return findings


def check_inventory(root: Path) -> list[Finding]:
    path = root / ".lefly-release-inventory.json"
    if not path.is_file():
        return [Finding("missing_inventory", path.name, "release inventory is absent")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [Finding("invalid_inventory", path.name, str(error))]
    if data.get("schema_version") != 1 or not isinstance(data.get("files"), dict):
        return [Finding("invalid_inventory", path.name, "invalid schema or files map")]
    if data.get("release_version") != RELEASE_VERSION:
        return [
            Finding(
                "inventory_version_mismatch",
                path.name,
                f"expected release version {RELEASE_VERSION}",
            )
        ]

    recorded = data["files"]
    current = build_inventory(root)
    findings = []
    for relative in sorted(set(recorded) - set(current)):
        findings.append(Finding("inventory_missing_file", relative, "recorded file is absent"))
    for relative in sorted(set(current) - set(recorded)):
        findings.append(Finding("inventory_unexpected_file", relative, "file is not recorded"))
    for relative in sorted(set(recorded) & set(current)):
        if recorded[relative] != current[relative]:
            findings.append(
                Finding("inventory_digest_mismatch", relative, "SHA-256 does not match")
            )
    return findings


def _check_static_assets(root: Path) -> list[Finding]:
    static = root / "packages/lefly-simulator/src/lefly_simulator/static"
    index = static / "index.html"
    if not index.is_file():
        return [Finding("missing_static_index", _relative(root, index), "index.html is absent")]
    findings = []
    text = index.read_text(encoding="utf-8")
    for raw in _STATIC_REFERENCE.findall(text):
        parsed = urlsplit(raw)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        relative = parsed.path.lstrip("/")
        target = (static / relative).resolve(strict=False)
        try:
            target.relative_to(static.resolve())
        except ValueError:
            findings.append(Finding("static_path_escape", _relative(root, index), raw))
            continue
        if not target.is_file():
            findings.append(Finding("missing_static_asset", _relative(root, index), raw))
    return findings


def _check_versions(root: Path) -> list[Finding]:
    try:
        failures = validate_release_version(root, RELEASE_VERSION)
    except (KeyError, OSError, ValueError) as error:
        return [Finding("invalid_version_metadata", "packages", str(error))]
    return [Finding("version_mismatch", "packages", failure) for failure in failures]


def _direct_console_dependencies(
    root: Path,
) -> tuple[dict[str, str], dict[str, str], list[Finding]]:
    path = root / "packages/lefly-console-web/package-lock.json"
    relative = _relative(root, path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        package = data["packages"][""]
        runtime = package.get("dependencies", {})
        development = package.get("devDependencies", {})
        if not isinstance(runtime, dict) or not isinstance(development, dict):
            raise ValueError("direct dependency maps must be objects")
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        return {}, {}, [Finding("invalid_dependency_metadata", relative, str(error))]
    return runtime, development, []


def check_provenance(root: Path) -> list[Finding]:
    """Check that bundled runtime dependencies appear in the public notice."""

    root = root.resolve()
    runtime, _, findings = _direct_console_dependencies(root)
    if findings:
        return _sorted(findings)

    notice_relative = "docs/third-party-notices.md"
    notice_path = root / notice_relative
    try:
        notice = notice_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        findings.append(Finding("missing_runtime_notice", notice_relative, str(error)))
        notice = ""
    for name in sorted(runtime):
        if f"`{name}`" not in notice:
            findings.append(
                Finding(
                    "missing_runtime_notice",
                    notice_relative,
                    f"direct bundled dependency `{name}` is not recorded",
                )
            )

    return _sorted(findings)


def _check_required_and_forbidden(root: Path) -> list[Finding]:
    findings = []
    for relative in REQUIRED_ROOT_FILES:
        if not (root / relative).is_file():
            findings.append(Finding("missing_required_file", relative, "required file is absent"))
    for path in sorted(root.iterdir()):
        if path.name == ".git":
            continue
        if path.name not in ALLOWED_ROOT_ENTRIES:
            findings.append(
                Finding(
                    "forbidden_path",
                    path.name,
                    "path is not part of the public repository layout",
                )
            )
    for path in sorted(root.rglob("*")):
        relative = _relative(root, path)
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if ".git" in Path(relative).parts:
            findings.append(
                Finding(
                    "forbidden_path",
                    relative,
                    "nested repository metadata is not public",
                )
            )
            continue
        if path.is_symlink():
            findings.append(Finding("forbidden_symlink", relative, "public tree contains symlink"))
        if path.name == ".DS_Store":
            findings.append(Finding("forbidden_path", relative, "local metadata is not public"))
        if path.is_file() and relative.startswith("docs/") and relative not in ALLOWED_DOC_FILES:
            findings.append(
                Finding(
                    "forbidden_path",
                    relative,
                    "file is not part of the public documentation set",
                )
            )
    return findings


def check_public_tree(root: Path, *, verify_inventory: bool = True) -> list[Finding]:
    root = root.resolve()
    findings = []
    findings.extend(_check_required_and_forbidden(root))
    findings.extend(_check_text(root, _iter_text_files([root])))
    findings.extend(check_markdown_links(root))
    if verify_inventory:
        findings.extend(check_inventory(root))
    findings.extend(_check_static_assets(root))
    findings.extend(_check_versions(root))
    findings.extend(check_provenance(root))
    return _sorted(findings)


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--skip-inventory",
        action="store_true",
        help="skip release inventory digests for pull request validation",
    )
    args = parser.parse_args(list(argv) if argv else None)

    try:
        findings = check_public_tree(
            args.root,
            verify_inventory=not args.skip_inventory,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Public release check failed: {error}", file=sys.stderr)
        return 1
    if findings:
        for finding in findings:
            print(f"{finding.code}: {finding.path}: {finding.detail}", file=sys.stderr)
        return 1
    print("Public release checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
