# SPDX-License-Identifier: AGPL-3.0-or-later
"""A pyproject console script reaches its function, and the placeholder says so (WI-lipal).

``[project.scripts] tool = "tool.cli:main"`` is emitted by toml_config as a
``defines_target`` edge to a dotted placeholder (``python:tool.cli:0-0:main``)
with the file path in meta; the build-target linker then adds a ``calls`` edge
to the real function. It looked only beside the manifest, so on a ``src/``
layout -- and for a meta-package naming another package's module -- the real
``main`` got no edge at all and the placeholder became a workspace-prefixed
``external_symbol``: the script read as running external code (INV-nuzas).

The linker now finds the module where Python imports it from, and it may
demote the placeholder it consumed (ADR-0057 §14.1, granted on
``docs/audits/0024``): the stub stays in the graph, stamped
``superseded_by`` with the resolved edge's id -- a positive claim, not a
deletion.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def _survey(root: Path) -> dict:
    out = root.parent / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


_WORKSPACE = {
    "pyproject.toml": '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
    "packages/tool/pyproject.toml": (
        '[project]\nname = "tool"\nversion = "0.1.0"\n\n'
        '[project.scripts]\ntool = "tool.cli:main"\n'
    ),
    "packages/tool/src/tool/__init__.py": "",
    "packages/tool/src/tool/cli.py": "def main():\n    return 0\n",
    "packages/meta/pyproject.toml": (
        '[project]\nname = "meta"\nversion = "0.1.0"\ndependencies = ["tool"]\n\n'
        '[project.scripts]\nmeta-tool = "tool.cli:main"\n'
    ),
    "packages/meta/src/meta/__init__.py": "",
}
_MAIN = "python:packages/tool/src/tool/cli.py:1-2:main:function"


def _from_manifest(data: dict, manifest: str, edge_type: str) -> list[dict]:
    return [
        e for e in data["edges"]
        if e["type"] == edge_type and e["src"].startswith(f"toml:{manifest}:")
    ]


class TestTheScriptReachesItsFunction:
    def test_a_src_layout_member_s_own_script(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _write(root, _WORKSPACE)
        data = _survey(root)
        calls = _from_manifest(data, "packages/tool/pyproject.toml", "calls")
        assert [e["dst"] for e in calls] == [_MAIN]

    def test_a_meta_package_naming_another_package_s_module(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _write(root, _WORKSPACE)
        data = _survey(root)
        calls = _from_manifest(data, "packages/meta/pyproject.toml", "calls")
        assert [e["dst"] for e in calls] == [_MAIN]


class TestThePlaceholderIsDemotedNotDeleted:
    def test_each_placeholder_names_the_edge_that_resolved_it(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _write(root, _WORKSPACE)
        data = _survey(root)
        for manifest in ("packages/tool/pyproject.toml", "packages/meta/pyproject.toml"):
            [stub] = _from_manifest(data, manifest, "defines_target")
            [resolved] = _from_manifest(data, manifest, "calls")
            assert stub["dst"].startswith("python:tool.cli:"), stub["dst"]
            assert (stub.get("meta") or {}).get("superseded_by") == resolved["id"], stub
            assert "build-target-linker" in stub["meta"]["superseded_by_origin"]

    def test_an_unresolved_script_target_is_left_alone(self, tmp_path: Path) -> None:
        """Control: a script naming a module the tree does not have (a
        dependency's) keeps its placeholder, undemoted."""
        root = tmp_path / "repo"
        _write(root, {
            "pyproject.toml": (
                '[project]\nname = "x"\nversion = "0.1.0"\n\n'
                '[project.scripts]\nx = "elsewhere.cli:main"\n'
            ),
            "x/__init__.py": "",
        })
        data = _survey(root)
        [stub] = _from_manifest(data, "pyproject.toml", "defines_target")
        assert _from_manifest(data, "pyproject.toml", "calls") == []
        assert "superseded_by" not in (stub.get("meta") or {})
