# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every import / build-target endpoint these analyzers write is a node id (WI-tatop).

PowerShell wrote an ``Import-Module`` / ``using module`` dst as
``powershell:?:?:<module>:module`` (``?`` where the span goes); the XML
analyzer wrote a Maven ``<mainClass>`` dst as the bare path
``com/example/Main.java``, and drew it, in a pom with no ``artifactId``, from
``xml:<pom>:1-1:project:module``, a symbol it never emitted. Each case runs
the analyzer's production entry point over a fixture and asserts the strict
gate (``edge_target.non_id_edge_endpoints``) and what the endpoints are.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_module_import_dst
from hypergumbo_core.analyze.edge_target import external_target, non_id_edge_endpoints
from hypergumbo_lang_mainstream.powershell import analyze_powershell
from hypergumbo_lang_mainstream.xml_config import analyze_xml_files


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_powershell_module_imports_end_on_module_stand_ins(tmp_path: Path) -> None:
    result = analyze_powershell(_write(tmp_path, {
        "run.ps1": (
            "Import-Module ActiveDirectory\n"
            "using module PSReadLine\n"
            "function Get-Thing { param($x) Write-Output $x }\n"
        ),
    }))
    imports = [e for e in result.edges if e.edge_type == "imports"]
    assert non_id_edge_endpoints(result.symbols, imports) == []
    assert sorted(e.dst for e in imports) == sorted([
        make_module_import_dst("powershell", "ActiveDirectory"),
        make_module_import_dst("powershell", "PSReadLine"),
    ])


_POM = (
    "<project>\n  <groupId>com.example</groupId>\n  <artifactId>demo</artifactId>\n"
    "  <build><plugins><plugin><configuration>"
    "<mainClass>com.example.Main</mainClass>"
    "</configuration></plugin></plugins></build>\n</project>\n"
)


def _targets(root: Path) -> list:
    result = analyze_xml_files(root)
    edges = [e for e in result.edges if e.edge_type == "defines_target"]
    assert non_id_edge_endpoints(result.symbols, edges) == []
    return edges


def test_maven_main_class_in_the_tree_ends_on_its_file(tmp_path: Path) -> None:
    [edge] = _targets(_write(tmp_path, {
        "app/pom.xml": _POM,
        "app/src/main/java/com/example/Main.java": "package com.example;\nclass Main {}\n",
    }))
    assert edge.dst == make_file_id("java", "app/src/main/java/com/example/Main.java")
    assert edge.meta["target_path"] == "app/src/main/java/com/example/Main.java"
    assert edge.is_resolved


def test_maven_main_class_not_in_the_tree_is_a_class_stand_in(tmp_path: Path) -> None:
    [edge] = _targets(_write(tmp_path, {"pom.xml": _POM}))
    assert edge.dst == external_target("java", "com.example.Main")[0]
    assert edge.meta["target_path"] == "com/example/Main.java"
    assert not edge.is_resolved


def test_a_pom_without_artifact_id_draws_from_its_file(tmp_path: Path) -> None:
    [edge] = _targets(_write(tmp_path, {
        "pom.xml": _POM.replace("<artifactId>demo</artifactId>", ""),
    }))
    assert edge.src == make_file_id("xml", "pom.xml")
