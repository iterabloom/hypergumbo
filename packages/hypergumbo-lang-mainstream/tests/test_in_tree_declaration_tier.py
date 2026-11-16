# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every node standing for one package carries one tier (INV-kutub).

ADR-0041 §1: ``supply_chain_tier`` is the distance, from the project, of the
code a node stands for. A package's own node (``project`` from pyproject,
``package`` from package.json / composer.json / Cargo.toml, ``module`` from
pom.xml) and every dependency declaration naming it stand for the same code,
so a sibling declaration must carry the sibling's tier. It used to be tier 2
for a Python sibling and tier 3 "external" for npm, Composer, Cargo and Maven
while the sibling's own node was tier 1.

Through the production pipeline (``run_behavior_map``), one fixture per
ecosystem with a sibling declaration and a third-party one. Nodes are grouped
by the package they stand for: the manifest kind plus the name under that
ecosystem's comparison (PEP 503 for Python, ``-``/``_`` for Cargo).
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/supply_chain.py
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map

_POM = (
    '<project xmlns="http://maven.apache.org/POM/4.0.0">'
    "<modelVersion>4.0.0</modelVersion>{}</project>\n"
)
_PARENT = "<parent><groupId>org.ex</groupId><artifactId>parent</artifactId></parent>"

WORKSPACES: dict[str, dict[str, str]] = {
    "python": {
        "packages/a/pyproject.toml":
            '[project]\nname = "pkg-a"\ndependencies = ["pkg_b", "requests"]\n',
        "packages/b/pyproject.toml": '[project]\nname = "pkg-b"\n',
        "packages/b/src/pkg_b/__init__.py": "def helper():\n    return 1\n",
    },
    "npm": {
        "package.json": '{"name": "root", "private": true, "workspaces": ["packages/*"]}',
        "packages/a/package.json":
            '{"name": "@ws/a", "dependencies": {"@ws/b": "*", "lodash": "^4"}}',
        "packages/b/package.json": '{"name": "@ws/b"}',
        "packages/b/index.js": "module.exports = { helper() { return 1; } };\n",
    },
    "composer": {
        "packages/a/composer.json":
            '{"name": "ws/a", "require": {"ws/b": "*", "monolog/monolog": "^3"}}',
        "packages/b/composer.json": '{"name": "ws/b"}',
    },
    "cargo": {
        "Cargo.toml": '[workspace]\nmembers = ["crate-a", "crate-b"]\n',
        "crate-a/Cargo.toml": (
            '[package]\nname = "crate-a"\n\n[dependencies]\n'
            'crate_b = { path = "../crate-b" }\nserde = "1"\n'
        ),
        "crate-b/Cargo.toml": '[package]\nname = "crate-b"\n',
        "crate-b/src/lib.rs": "pub fn helper() -> u32 { 1 }\n",
    },
    "maven": {
        "pom.xml": _POM.format(
            "<groupId>org.ex</groupId><artifactId>parent</artifactId>"
            "<modules><module>mod-a</module><module>mod-b</module></modules>"
        ),
        "mod-a/pom.xml": _POM.format(
            _PARENT + "<artifactId>mod-a</artifactId><dependencies>"
            "<dependency><groupId>${project.groupId}</groupId>"
            "<artifactId>mod-b</artifactId></dependency>"
            "<dependency><groupId>com.google.guava</groupId>"
            "<artifactId>guava</artifactId></dependency></dependencies>"
        ),
        "mod-b/pom.xml": _POM.format(_PARENT + "<artifactId>mod-b</artifactId>"),
    },
}

#: The in-tree sibling and the third-party package each fixture declares.
DECLARED = {
    "python": ("pkg-b", "requests"),
    "npm": ("@ws/b", "lodash"),
    "composer": ("ws/b", "monolog/monolog"),
    "cargo": ("crate-b", "serde"),
    "maven": ("mod-b", "guava"),
}

_PACKAGE_KINDS = {"project", "package", "module"}


def _identity(node: dict) -> tuple[str, str] | None:
    """(manifest basename, compared name) of the package a package node or a
    dependency declaration stands for; None for any other node."""
    if node["kind"] not in _PACKAGE_KINDS | {"dependency"}:
        return None
    manifest = (node.get("path") or "").rsplit("/", 1)[-1]
    name = node["name"]
    if manifest == "pyproject.toml":
        name = re.sub(r"[-_.]+", "-", name).lower()
    elif manifest == "Cargo.toml":
        name = name.replace("_", "-")
    return manifest, name


def _survey(tmp_path: Path, files: dict[str, str]) -> list[dict]:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())["nodes"]


@pytest.mark.parametrize("ecosystem", sorted(WORKSPACES))
def test_nodes_standing_for_one_package_carry_one_tier(
    tmp_path: Path, ecosystem: str,
) -> None:
    nodes = _survey(tmp_path, WORKSPACES[ecosystem])
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for node in nodes:
        identity = _identity(node)
        if identity is not None:
            groups[identity].append(node)

    sibling, third_party = DECLARED[ecosystem]
    sibling_group = [g for key, g in groups.items() if key[1] == sibling]
    # Reach first: the sibling has its own node AND a declaration naming it,
    # else the property below is vacuous.
    assert len(sibling_group) == 1
    kinds = sorted(n["kind"] for n in sibling_group[0])
    assert "dependency" in kinds and set(kinds) & _PACKAGE_KINDS, kinds

    for key, group in groups.items():
        tiers = {(n["kind"], n["supply_chain"]["tier"]) for n in group}
        assert len({tier for _kind, tier in tiers}) == 1, (key, tiers)

    (declaration,) = [
        n for n in sibling_group[0] if n["kind"] == "dependency"
    ]
    assert declaration["supply_chain"]["tier"] == 1
    assert declaration["supply_chain"]["reason"].startswith("declares in-tree package")

    (external,) = [
        n for n in nodes if n["kind"] == "dependency" and n["name"] == third_party
    ]
    assert external["supply_chain"] == {
        **external["supply_chain"],
        "tier": 3,
        "reason": "dependency declaration (external)",
    }
