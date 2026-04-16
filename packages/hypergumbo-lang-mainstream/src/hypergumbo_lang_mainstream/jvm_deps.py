# SPDX-License-Identifier: AGPL-3.0-or-later
"""JVM dependency manifest parsing for Gradle and Maven projects.

Extracts declared dependencies from build.gradle, build.gradle.kts, and
pom.xml files so that ``create_boundary_nodes`` can classify unresolved
Java/Kotlin imports as tier 2 (direct dependency the project declares) vs
tier 3 (unknown external).

The key insight: Maven/Gradle dependency coordinates use ``groupId`` as the
organizational namespace, which *usually* matches the Java/Kotlin package
prefix. For example, ``org.apache.kafka:kafka-clients`` → imports under
``org.apache.kafka.*``. This heuristic works well for most libraries; the
few where groupId diverges from the package namespace (e.g., Guava's
``com.google.guava`` groupId vs ``com.google.common.*`` packages) fall
through to the default EXTERNAL_DEP tier, which is correct — they're
still external.

WI-duhom: Gradle/Maven projects had near-zero external_dep boundary nodes
because the file-path-based classifier can never see dependencies that
aren't physically on disk. This module closes that gap.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # nosec B405 - parsing local build files
from pathlib import Path

from hypergumbo_core.supply_chain import DependencyManifest

_GRADLE_CONFIGS = (
    "implementation",
    "api",
    "compile",
    "compileOnly",
    "runtimeOnly",
    "testImplementation",
    "testCompileOnly",
    "testRuntimeOnly",
    "annotationProcessor",
    "kapt",
    "ksp",
    "androidTestImplementation",
    "debugImplementation",
    "releaseImplementation",
)

_GRADLE_DEP_RE = re.compile(
    r"(?:"
    + "|".join(re.escape(c) for c in _GRADLE_CONFIGS)
    + r")"
    r"""\s*[\(]?\s*['"]([^'"]+)['"]\s*\)?""",
)


def _extract_group_id(coordinate: str) -> str | None:
    """Extract groupId from a Gradle/Maven coordinate string.

    Handles ``group:artifact:version`` and ``group:artifact`` (no version,
    common with BOM/platform management).
    """
    parts = coordinate.split(":")
    if len(parts) >= 2:
        return parts[0].strip()
    return None


def _parse_gradle_file(path: Path, entries: dict[str, dict]) -> None:
    """Parse a single build.gradle or build.gradle.kts file."""
    try:
        content = path.read_text(errors="replace")
    except OSError:
        return

    for m in _GRADLE_DEP_RE.finditer(content):
        coordinate = m.group(1)
        group_id = _extract_group_id(coordinate)
        if group_id and group_id not in entries:
            entries[group_id] = {"direct": True}


def parse_gradle_dependencies(repo_root: Path) -> DependencyManifest:
    """Parse Gradle build files to extract dependency metadata.

    Scans the root and immediate subdirectories for build.gradle and
    build.gradle.kts files. Extracts ``group:artifact:version`` coordinates
    from dependency configuration lines (implementation, api, compile, etc.).

    Returns a DependencyManifest mapping groupId strings to ``{direct: True}``.
    All declared dependencies are treated as direct — Gradle doesn't
    distinguish direct/indirect in the build file itself (that's a
    resolution-time concept in the lockfile).
    """
    from hypergumbo_core.paths import is_test_file

    entries: dict[str, dict] = {}

    for name in ("build.gradle", "build.gradle.kts"):
        root_file = repo_root / name
        if root_file.exists():
            _parse_gradle_file(root_file, entries)

    for child in repo_root.iterdir():
        if not child.is_dir():
            continue
        # WI-bukof: skip test-fixture directories (testFixtures/, testdata/,
        # fixtures/, tests/, etc.) — their build.gradle declarations are
        # scaffolding for test fixtures, not real project dependencies.
        if is_test_file(child.name):
            continue
        for name in ("build.gradle", "build.gradle.kts"):
            sub_file = child / name
            if sub_file.exists():
                _parse_gradle_file(sub_file, entries)

    return DependencyManifest(entries=entries)


def _parse_pom_file(path: Path, entries: dict[str, dict]) -> None:
    """Parse a single pom.xml file for dependency groupIds."""
    try:
        tree = ET.parse(path)  # noqa: S314  # nosec B314
    except (OSError, ET.ParseError):
        return

    root_el = tree.getroot()
    ns = ""
    if root_el.tag.startswith("{"):
        ns = root_el.tag.split("}")[0] + "}"

    for deps_el in root_el.iter(f"{ns}dependencies"):
        for dep_el in deps_el.findall(f"{ns}dependency"):
            group_el = dep_el.find(f"{ns}groupId")
            if group_el is not None and group_el.text:
                group_id = group_el.text.strip()
                if group_id and group_id not in entries:
                    entries[group_id] = {"direct": True}


def parse_maven_dependencies(repo_root: Path) -> DependencyManifest:
    """Parse Maven pom.xml files to extract dependency metadata.

    Scans the root and immediate subdirectories for pom.xml files.
    Extracts groupId from ``<dependency>`` elements in both
    ``<dependencies>`` and ``<dependencyManagement><dependencies>``
    sections.

    Returns a DependencyManifest mapping groupId strings to ``{direct: True}``.
    """
    from hypergumbo_core.paths import is_test_file

    entries: dict[str, dict] = {}

    root_pom = repo_root / "pom.xml"
    if root_pom.exists():
        _parse_pom_file(root_pom, entries)

    for child in repo_root.iterdir():
        if not child.is_dir():
            continue
        # WI-bukof: skip test-fixture subdirectories.
        if is_test_file(child.name):
            continue
        sub_pom = child / "pom.xml"
        if sub_pom.exists():
            _parse_pom_file(sub_pom, entries)

    return DependencyManifest(entries=entries)


def parse_jvm_dependencies(repo_root: Path) -> DependencyManifest:
    """Parse both Gradle and Maven dependency declarations.

    Tries both build systems and merges results. In projects that have both
    (e.g., a Gradle build with a parent pom), all declared dependencies are
    included.
    """
    gradle = parse_gradle_dependencies(repo_root)
    maven = parse_maven_dependencies(repo_root)

    if gradle.entries and maven.entries:
        return DependencyManifest.merge([gradle, maven])
    if gradle.entries:
        return gradle
    if maven.entries:
        return maven
    return DependencyManifest()
