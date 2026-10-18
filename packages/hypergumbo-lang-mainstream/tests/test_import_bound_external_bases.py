# SPDX-License-Identifier: AGPL-3.0-or-later
"""A base named through an import of an external package stays external (WI-pivap).

The Java and Kotlin analyzers resolved a base class by simple name and
returned the ONLY in-repo candidate outright; the file's imports broke ties
between two or more candidates only. So ``import junit.framework.TestCase;
class T extends TestCase`` got a resolved ``extends`` edge to an unrelated
in-repo ``TestCase``, and the core inheritance linker then skipped the pair.
The Python analyzer had the same defect (WI-ratid). Both analyzers record each
class's package-qualified name, so the import's fully qualified name decides:
no in-repo candidate with that qualified name means the base is external.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/import_bound_bases.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.java import analyze_java
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin


def _inheritance(edges, src_name):
    return [e for e in edges
            if e.edge_type in ("extends", "implements") and f":{src_name}:" in e.src]


def test_java_import_of_an_external_class_is_not_the_in_repo_namesake(tmp_path: Path) -> None:
    (tmp_path / "TestCase.java").write_text("package org.acme;\npublic class TestCase {}\n")
    (tmp_path / "Serializable.java").write_text(
        "package org.acme;\npublic interface Serializable {}\n")
    (tmp_path / "T.java").write_text(
        "package org.acme;\nimport junit.framework.TestCase;\nimport java.io.Serializable;\n"
        "class T extends TestCase implements Serializable {}\n")
    edges = _inheritance(analyze_java(tmp_path).edges, "T")
    assert sorted((e.edge_type, e.dst, e.is_resolved) for e in edges) == [
        ("extends", "java:junit.framework:0-0:TestCase:unresolved", False),
        ("implements", "java:java.io:0-0:Serializable:unresolved", False),
    ]
    refs = {e.edge_type: e.dst_ref for e in edges}
    assert (refs["extends"].module_path, refs["extends"].name) == ("junit.framework", "TestCase")


def test_java_import_of_an_in_repo_class_still_resolves(tmp_path: Path) -> None:
    (tmp_path / "Base.java").write_text("package org.acme.core;\npublic class Base {}\n")
    (tmp_path / "Same.java").write_text("package org.acme;\npublic class Same {}\n")
    (tmp_path / "K.java").write_text(
        "package org.acme;\nimport org.acme.core.Base;\nclass K extends Base {}\n"
        "class L extends Same {}\n")
    result = analyze_java(tmp_path)
    by_src = {e.src.split(":")[-2]: e for e in result.edges if e.edge_type == "extends"}
    assert by_src["K"].is_resolved and by_src["K"].dst.endswith(":Base:class")
    assert by_src["L"].is_resolved, "a same-package base needs no import"


def test_kotlin_import_of_an_external_class_is_not_the_in_repo_namesake(tmp_path: Path) -> None:
    (tmp_path / "misc").mkdir()
    (tmp_path / "misc" / "Vm.kt").write_text("package com.acme.ui\nopen class ViewModel\n")
    (tmp_path / "M.kt").write_text(
        "package com.acme.app\nimport androidx.lifecycle.ViewModel\nclass M : ViewModel()\n")
    (tmp_path / "N.kt").write_text(
        "package com.acme.app\nimport com.acme.ui.ViewModel\nclass N : ViewModel()\n")
    edges = analyze_kotlin(tmp_path).edges
    m = _inheritance(edges, "M")
    assert [(e.dst, e.is_resolved) for e in m] == [
        ("kotlin:androidx.lifecycle:0-0:ViewModel:unresolved", False)]
    n = _inheritance(edges, "N")
    assert len(n) == 1 and n[0].is_resolved, "an in-repo import still resolves"


def test_the_core_linker_does_not_rebind_it(tmp_path: Path) -> None:
    """End to end: the inheritance linker skips a base the analyzer classified
    as external through the file's imports (WI-ratid's guard)."""
    (tmp_path / "TestCase.java").write_text("package org.acme;\npublic class TestCase {}\n")
    (tmp_path / "T.java").write_text(
        "package org.acme;\nimport junit.framework.TestCase;\nclass T extends TestCase {}\n")
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    names = {n["id"]: n["name"] for n in data["nodes"]}
    targets = [e["dst"] for e in data["edges"]
               if e["type"] == "extends" and names.get(e["src"]) == "T"]
    # The pipeline canonicalizes the unresolved target to its boundary node;
    # what matters is that it is junit's TestCase, not the in-repo one.
    assert len(targets) == 1 and targets[0].startswith(
        "java:junit.framework:0-0:TestCase:"), targets


# --- JS / TS -----------------------------------------------------------------
#
# js_ts symbols carry no package-qualified name, so the import SPECIFIER
# decides, on positive evidence only: a Node builtin, or a package the
# importing package.json declares (and no in-repo package.json is named).


def _js_extends(tmp_path: Path, src_name: str) -> list:
    from hypergumbo_lang_mainstream.js_ts import analyze_javascript

    return [e for e in analyze_javascript(tmp_path).edges
            if e.edge_type in ("extends", "implements") and f":{src_name}:" in e.src]


def test_js_npm_import_is_not_the_in_repo_namesake(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18.0.0"}}')
    (tmp_path / "Component.ts").write_text("export class Component {}\n")
    (tmp_path / "EventEmitter.ts").write_text("export class EventEmitter {}\n")
    (tmp_path / "a.ts").write_text(
        "import { Component } from 'react';\nexport class A extends Component {}\n")
    (tmp_path / "b.ts").write_text(
        "import React from 'react';\nexport class B extends React.Component {}\n")
    (tmp_path / "c.ts").write_text(
        "import { EventEmitter } from 'events';\nexport class C extends EventEmitter {}\n")
    for name, module in (("A", "react"), ("B", "react"), ("C", "events")):
        edges = _js_extends(tmp_path, name)
        assert [(e.is_resolved, e.dst_ref.module_path if e.dst_ref else None) for e in edges] \
            == [(False, module)], (name, [e.dst for e in edges])


def test_js_in_repo_imports_still_resolve(tmp_path: Path) -> None:
    """A workspace package and an undeclared alias are in-repo: no positive
    evidence of an external package, so the name resolution stands."""
    (tmp_path / "package.json").write_text(
        '{"workspaces": ["packages/*"], "dependencies": {"@acme/shared": "workspace:*"}}')
    shared = tmp_path / "packages" / "shared"
    shared.mkdir(parents=True)
    (shared / "package.json").write_text('{"name": "@acme/shared"}')
    (shared / "Base.ts").write_text("export class Base {}\n")
    (tmp_path / "Model.ts").write_text("export class Model {}\n")
    (tmp_path / "w.ts").write_text(
        "import { Base } from '@acme/shared';\nexport class W extends Base {}\n")
    (tmp_path / "x.ts").write_text(
        "import { Model } from '@/models';\nexport class X extends Model {}\n")
    for name in ("W", "X"):
        edges = _js_extends(tmp_path, name)
        assert [e.is_resolved for e in edges] == [True], (name, [e.dst for e in edges])


def test_js_specifier_rules_on_unreadable_manifests(tmp_path: Path) -> None:
    """The defensive edges: an unparseable or non-object package.json declares
    nothing, so only a Node core module is shown external."""
    from hypergumbo_lang_mainstream.js_ts import (
        _bare_specifier_is_external, _JsPackageIndex,
    )

    broken, listy = tmp_path / "broken", tmp_path / "listy"
    for root, text in ((broken, "{not json"), (listy, '["react"]')):
        root.mkdir()
        (root / "package.json").write_text(text)
        (root / "a.ts").write_text("")
    for root in (broken, listy):
        index = _JsPackageIndex([root / "a.ts"])
        assert not _bare_specifier_is_external("react", root / "a.ts", index)
        assert not _bare_specifier_is_external("./x", root / "a.ts", index)
        assert _bare_specifier_is_external("node:fs", root / "a.ts", index)
        assert _bare_specifier_is_external("fs/promises", root / "a.ts", index)
