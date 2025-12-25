"""Supply chain classification for code analysis.

Classifies files into tiers based on their position in the project's
dependency graph. This enables focused analysis (first-party code
prioritized) and noise reduction (derived artifacts excluded).

Tiers
-----
- FIRST_PARTY (1): Project's own source code (highest priority)
- INTERNAL_DEP (2): Internal libraries, monorepo packages
- EXTERNAL_DEP (3): Third-party dependencies in readable form
- DERIVED (4): Build artifacts, transpiled/bundled output (skip analysis)

Classification Algorithm
------------------------
Classification happens at discovery time, before analysis. Signals are
checked in order; first match wins:

1. Derived artifact detection (tier 4) - path patterns + content heuristics
2. External dependency detection (tier 3) - node_modules/, vendor/, etc.
3. Internal dependency detection (tier 2) - workspace/monorepo packages
4. First-party detection (tier 1) - src/, lib/, app/ or default

See §8.6 of the hypergumbo spec for full details.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Optional


class Tier(IntEnum):
    """Supply chain position, lower = higher priority."""

    FIRST_PARTY = 1
    INTERNAL_DEP = 2
    EXTERNAL_DEP = 3
    DERIVED = 4


@dataclass
class FileClassification:
    """Classification result for a file."""

    tier: Tier
    reason: str
    package_name: Optional[str] = None


# Path patterns for tier inference (checked as prefixes on relative path)
DERIVED_PATH_PATTERNS = [
    r"^dist/",
    r"^build/",
    r"^out/",
    r"^target/",
    r"^\.next/",
    r"^\.nuxt/",
    r"^\.output/",
    r"^\.svelte-kit/",
    r"^__pycache__/",
    r"/__pycache__/",
]

DERIVED_FILENAME_PATTERNS = [
    r"\.min\.js$",
    r"\.min\.css$",
    r"\.bundle\.js$",
    r"\.compiled\.js$",
    r"\.pyc$",
    r"\.pyo$",
]

EXTERNAL_DEP_PATTERNS = [
    (r"^node_modules/", "node_modules/"),
    (r"^vendor/", "vendor/"),
    (r"^third_party/", "third_party/"),
    (r"^Pods/", "Pods/"),
    (r"^Carthage/", "Carthage/"),
    (r"^\.yarn/cache/", ".yarn/cache/"),
    (r"^_vendor/", "_vendor/"),
]

FIRST_PARTY_PATTERNS = [
    r"^src/",
    r"^lib/",
    r"^app/",
    r"^pkg/",
    r"^cmd/",
    r"^internal/",
    r"^crates/[^/]+/src/",
    r"^packages/[^/]+/src/",
]


def classify_file(
    path: Path,
    repo_root: Path,
    package_roots: Optional[set[Path]] = None,
) -> FileClassification:
    """Classify a file's supply chain tier.

    Args:
        path: Absolute path to the file
        repo_root: Root directory of the repository
        package_roots: Set of internal package root paths (from detect_package_roots)

    Returns:
        FileClassification with tier, reason, and optional package_name
    """
    # Get relative path for pattern matching
    try:
        rel = str(path.relative_to(repo_root))
    except ValueError:
        # Path not under repo_root, default to first-party
        return FileClassification(Tier.FIRST_PARTY, "default (outside repo)")

    # Normalize path separators for consistent matching
    rel = rel.replace("\\", "/")

    # 1. Check derived patterns first (these should be skipped)
    for pattern in DERIVED_PATH_PATTERNS:
        if re.match(pattern, rel):
            return FileClassification(Tier.DERIVED, f"path matches {pattern}")

    for pattern in DERIVED_FILENAME_PATTERNS:
        if re.search(pattern, rel):
            return FileClassification(Tier.DERIVED, f"filename matches {pattern}")

    # 2. Check content heuristics for minification (only for existing files)
    if path.exists() and is_likely_minified(path):
        return FileClassification(Tier.DERIVED, "detected as minified/generated")

    # 3. Check external dependencies
    for pattern, label in EXTERNAL_DEP_PATTERNS:
        if re.match(pattern, rel):
            pkg = _extract_package_name(rel, label)
            return FileClassification(Tier.EXTERNAL_DEP, f"in {label}", pkg)

    # 4. Check internal packages (monorepo workspaces)
    if package_roots:
        for pkg_root in package_roots:
            try:
                if path.is_relative_to(pkg_root):
                    return FileClassification(
                        Tier.INTERNAL_DEP, f"in workspace {pkg_root.name}"
                    )
            except (ValueError, TypeError):
                continue

    # 5. Check first-party patterns
    for pattern in FIRST_PARTY_PATTERNS:
        if re.match(pattern, rel):
            return FileClassification(Tier.FIRST_PARTY, f"path matches {pattern}")

    # 6. Default: assume first-party if no other signals
    return FileClassification(Tier.FIRST_PARTY, "default (no matching pattern)")


def is_likely_minified(path: Path) -> bool:
    """Detect likely minified/bundled/generated files via content heuristics.

    Checks:
    1. Average line length > 150 chars (minified code)
    2. Source map reference in last 3 lines (transpiled)
    3. "Generated by" or "@generated" in first 5 lines

    Args:
        path: Path to the file to check

    Returns:
        True if file appears to be minified/generated
    """
    try:
        content = path.read_text(errors="ignore")
    except (OSError, IOError):
        return False

    lines = content.splitlines()
    if not lines:
        return False

    # Heuristic 1: Average line length > 150 chars
    avg_line_len = len(content) / len(lines)
    if avg_line_len > 150:
        return True

    # Heuristic 2: Source map reference in last 3 lines
    tail = "\n".join(lines[-3:])
    if re.search(r"//[#@]\s*sourceMappingURL=", tail):
        return True

    # Heuristic 3: Generator header in first 5 lines
    head = "\n".join(lines[:5])
    if re.search(r"(Generated by|@generated|DO NOT EDIT)", head, re.IGNORECASE):
        return True

    return False


def detect_package_roots(repo_root: Path) -> set[Path]:
    """Detect internal package roots from workspace/monorepo configs.

    Scans for:
    - npm/yarn/pnpm workspaces in package.json
    - Cargo workspace members in Cargo.toml

    Args:
        repo_root: Root directory of the repository

    Returns:
        Set of Path objects pointing to internal package directories
    """
    roots: set[Path] = set()

    # npm/yarn/pnpm workspaces
    pkg_json = repo_root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            workspaces = data.get("workspaces", [])

            # Handle object format: {"packages": [...]}
            if isinstance(workspaces, dict):
                workspaces = workspaces.get("packages", [])

            for pattern in workspaces:
                # Expand globs like "packages/*"
                for match in repo_root.glob(pattern):
                    if match.is_dir():
                        roots.add(match)
        except (json.JSONDecodeError, OSError):
            pass

    # Cargo workspaces
    cargo_toml = repo_root / "Cargo.toml"
    if cargo_toml.exists():
        try:
            content = cargo_toml.read_text()
            # Simple TOML parsing for workspace members
            if "[workspace]" in content:
                for match in re.finditer(
                    r"members\s*=\s*\[(.*?)\]", content, re.DOTALL
                ):
                    for member in re.findall(r'"([^"]+)"', match.group(1)):
                        for path in repo_root.glob(member):
                            if path.is_dir():
                                roots.add(path)
        except OSError:
            pass

    return roots


def _extract_package_name(rel_path: str, pattern_label: str) -> Optional[str]:
    """Extract package name from dependency path.

    For node_modules/, extracts the npm package name (handling scoped packages).

    Args:
        rel_path: Relative path within the repo
        pattern_label: The matched pattern label (e.g., "node_modules/")

    Returns:
        Package name if extractable, None otherwise
    """
    if pattern_label == "node_modules/":
        parts = rel_path.split("node_modules/")[-1].split("/")
        if not parts[0]:
            # Empty path after node_modules/ (edge case)
            return None
        if parts[0].startswith("@"):
            # Scoped package: @scope/package
            if len(parts) >= 2:
                return "/".join(parts[:2])
            return parts[0]
        return parts[0]

    return None
