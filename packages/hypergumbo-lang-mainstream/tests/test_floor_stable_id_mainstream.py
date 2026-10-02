# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-motiz: the stable_id floor fills mainstream kinds whose name is exact.

json/toml ``package`` (the manifest's own package name) and csharp
``property`` (named ``Class.Prop``) have no entry in the backstop's factory
table and their producers compute no ``stable_id``; they shipped ``null``
(the self-map's ``packages/htrac-frontend/package.json`` among them). Their
names are unique in their file, so the floor key
``make_declaration_stable_id(kind, language, path, name)`` identifies them and
the backstop (core ``populate_kind_stable_ids``, ADR-0035 §1) now fills it.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.analyze.base import make_declaration_stable_id
from hypergumbo_core.cli import run_behavior_map

_FILES = {
    "package.json": (
        '{"name": "demo-pkg", "version": "1.0.0",'
        ' "dependencies": {"left-pad": "1.0.0"}}\n'
    ),
    "Cargo.toml": (
        "[package]\n"
        "name = \"demo_crate\"\n"
        "version = \"0.1.0\"\n"
    ),
    "A.cs": (
        "namespace Demo {\n"
        "  public class A {\n"
        "    public int Id { get; set; }\n"
        "    public string Name { get; set; }\n"
        "  }\n"
        "  public class B {\n"
        "    public int Id { get; set; }\n"
        "  }\n"
        "}\n"
    ),
}

_EXPECTED = {
    ("json", "package", "demo-pkg", "package.json"),
    ("toml", "package", "demo_crate", "Cargo.toml"),
    ("csharp", "property", "A.Id", "A.cs"),
    ("csharp", "property", "A.Name", "A.cs"),
    ("csharp", "property", "B.Id", "A.cs"),
}


def test_package_and_property_take_the_exact_floor(tmp_path: Path) -> None:
    for name, text in _FILES.items():
        (tmp_path / name).write_text(text)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    nodes = json.loads(out.read_text())["nodes"]
    picked = {
        (n["language"], n["kind"], n["name"], n["path"]): n["stable_id"]
        for n in nodes if n["kind"] in ("package", "property")
    }
    assert set(picked) == _EXPECTED  # reach: every expected node is emitted
    for (language, kind, name, path), sid in picked.items():
        assert sid == make_declaration_stable_id(kind, language, path, name), (
            kind, name, sid)
