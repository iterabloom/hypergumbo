# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for the mainstream package's non-callable kinds (WI-tusad).

These analyzers computed no ``stable_id`` for the kinds below, which rode the
ADR-0035 backstop floor key ``(kind, language, path, name)``: the same ``ARG``
in two Dockerfile stages, the same target in both branches of a Makefile or
CMake conditional, the same selector under two ``@media`` rules, the same
Ansible task name in two plays, each shared the key and was left null. Each now
mints ``overload_arity.scoped_stable_id`` with the scope its grammar nests the
symbol in; ``manifest_targets`` (a format pass, no language's producer) mints
through ``pass_minted_stable_id`` (INV-fufij).

Pinned per analyzer, on its own output: every symbol of a migrated kind holds a
non-null id that is not the floor key (matched by kind and position, never
through a dict keyed on name), the exact value for a scoped symbol, and the
delete-an-earlier-sibling property (the deleted lines are kept as blank lines,
so each survivor matches itself by position).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from hypergumbo_core.analyze.base import AnalysisResult, floor_stable_id_key
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    declared_arity_stable_id, pass_minted_stable_id, scoped_stable_id,
)
from hypergumbo_lang_mainstream.bash import analyze_bash
from hypergumbo_lang_mainstream.cmake import analyze_cmake_files
from hypergumbo_lang_mainstream.css import analyze_css_files
from hypergumbo_lang_mainstream.dockerfile import analyze_dockerfiles
from hypergumbo_lang_mainstream.json_config import analyze_json_files
from hypergumbo_lang_mainstream.make import analyze_make_files
from hypergumbo_lang_mainstream.manifest_targets import analyze_manifest_targets
from hypergumbo_lang_mainstream.toml_config import analyze_toml_files
from hypergumbo_lang_mainstream.xml_config import analyze_xml_files
from hypergumbo_lang_mainstream.yaml_ansible import analyze_ansible

Analyze = Callable[[Path], AnalysisResult]
Key = tuple[str, str, int, int]


def _symbols(analyze: Analyze, root: Path, name: str, text: str) -> list[Symbol]:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return list(analyze(root).symbols)


def _at(symbols: list[Symbol], kinds: set[str]) -> dict[Key, Symbol]:
    picked = [s for s in symbols if s.kind in kinds]
    by_pos = {(s.kind, s.name, s.span.start_line, s.span.start_col): s for s in picked}
    assert len(by_pos) == len(picked), "two symbols of one kind and name at one position"
    return by_pos


def _assert_producer_ids(by_pos: dict[Key, Symbol], lang: str, path: str) -> None:
    """Every symbol holds its own non-null id, none of them the floor key."""
    assert by_pos
    for key, sym in by_pos.items():
        assert sym.stable_id is not None, key
        assert sym.stable_id != floor_stable_id_key(sym.kind, lang, path, sym.name), key


def _assert_deletion_moves_nothing(
    analyze: Analyze, tmp_path: Path, name: str, text: str, deleted: str,
    kinds: set[str], lang: str,
) -> dict[Key, Symbol]:
    """Delete ``deleted`` (replaced by as many blank lines) and compare."""
    assert text.count(deleted) == 1
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before = _at(_symbols(analyze, tmp_path / "r1", name, text), kinds)
    after = _at(_symbols(analyze, tmp_path / "r2", name, edited), kinds)
    assert 0 < len(after) < len(before)  # reach
    _assert_producer_ids(before, lang, name)
    for key, sym in after.items():
        assert sym.stable_id == before[key].stable_id, key
    return before


def test_dockerfile_build_args_are_scoped_by_stage(tmp_path: Path) -> None:
    text = (
        "ARG BASE=alpine\n"
        "FROM alpine AS build\nARG VERSION\nENV MODE=build\n"
        "FROM alpine AS run\nARG VERSION\nENV MODE=run\nEXPOSE 8080\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_dockerfiles, tmp_path, "Dockerfile", text,
        "FROM alpine AS build\nARG VERSION\nENV MODE=build\n",
        {"stage", "build_arg", "env_var", "exposed_port"}, "dockerfile",
    )
    assert before[("build_arg", "VERSION", 6, 0)].stable_id == scoped_stable_id(
        kind="build_arg", lang="dockerfile", path="Dockerfile", name="VERSION",
        scope=("run",),
    )
    assert before[("build_arg", "BASE", 1, 0)].stable_id == scoped_stable_id(
        kind="build_arg", lang="dockerfile", path="Dockerfile", name="BASE",
    )


def test_makefile_targets_are_scoped_by_conditional_branch(tmp_path: Path) -> None:
    text = (
        "CC = gcc\n"
        "ifeq ($(OS),Windows_NT)\nall:\n\techo win\nendif\n"
        "ifneq ($(OS),Windows_NT)\nall:\n\techo unix\nelse\nall: x\n\techo y\nendif\n"
        "%.o: %.c\n\t$(CC) -c $<\n.PHONY: all\ndefine greet\necho hi\nendef\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_make_files, tmp_path, "Makefile", text,
        "ifeq ($(OS),Windows_NT)\nall:\n\techo win\nendif\n",
        {"target", "pattern_rule", "special_target", "function"}, "makefile",
    )
    assert before[("target", "all", 10, 0)].stable_id == scoped_stable_id(
        kind="target", lang="makefile", path="Makefile", name="all",
        scope=("ifneq ($(OS),Windows_NT)", "else"),
    )


def test_cmake_targets_are_scoped_by_enclosing_block(tmp_path: Path) -> None:
    text = (
        "if(LINUX)\n  add_library(foo l.c)\nendif()\n"
        "if(WIN32)\n  add_library(foo a.c)\nelseif(APPLE)\n  add_library(foo b.c)\n"
        "else()\n  add_library(foo c.c)\nendif()\n"
        "function(setup)\n  find_package(Z)\n  add_executable(app m.c)\nendfunction()\n"
        "add_subdirectory(src)\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_cmake_files, tmp_path, "CMakeLists.txt", text,
        "if(LINUX)\n  add_library(foo l.c)\nendif()\n",
        {"library", "executable", "subdirectory", "package"}, "cmake",
    )
    assert before[("library", "foo", 7, 2)].stable_id == scoped_stable_id(
        kind="library", lang="cmake", path="CMakeLists.txt", name="foo",
        scope=("elseif(APPLE)",),
    )


def test_css_selectors_are_scoped_by_rule_and_media(tmp_path: Path) -> None:
    text = (
        "@media (max-width: 600px) {\n  .btn { color: red; }\n}\n"
        "@media print {\n  .btn { color: blue; }\n}\n.btn:hover, #main { color: green; }\n"
        "@font-face { font-family: 'Demo'; src: url(d.woff); }\n"
        "@keyframes spin { from { opacity: 0; } }\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_css_files, tmp_path, "site.css", text,
        "@media (max-width: 600px) {\n  .btn { color: red; }\n}\n",
        {"class_selector", "id_selector", "media", "font_face", "keyframes"}, "css",
    )
    assert before[("class_selector", ".btn", 5, 2)].stable_id == scoped_stable_id(
        kind="class_selector", lang="css", path="site.css", name=".btn",
        scope=("@media print", ".btn"),
    )


def test_toml_tables_take_producer_ids_and_table_kinds_keep_theirs(tmp_path: Path) -> None:
    text = (
        "[package]\nname = \"demo\"\n"
        "[[bin]]\nname = \"first\"\npath = \"src/first.rs\"\n"
        "[[bin]]\nname = \"second\"\npath = \"src/second.rs\"\n[lib]\nname = \"demolib\"\n"
        "[workspace]\nmembers = []\n[profile.release]\nlto = true\n[[test]]\nname = \"t\"\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_toml_files, tmp_path, "Cargo.toml", text,
        "[[bin]]\nname = \"first\"\npath = \"src/first.rs\"\n",
        {"package", "binary", "library", "workspace", "table", "test"}, "toml",
    )
    assert before[("table", "profile.release", 13, 0)].stable_id == scoped_stable_id(
        kind="table", lang="toml", path="Cargo.toml", name="profile.release",
    )
    project = _symbols(analyze_toml_files, tmp_path / "py", "pyproject.toml",
                       "[project]\nname = \"p\"\n")
    assert [s.stable_id for s in project if s.kind == "project"] == [None]  # table kind


def test_json_package_and_bins_take_producer_ids(tmp_path: Path) -> None:
    text = (
        "{\n  \"name\": \"demo\",\n  \"bin\": {\"demo\": \"cli.js\"},\n"
        "  \"dependencies\": {\"lodash\": \"1.0.0\"}\n}\n"
    )
    by_pos = _at(_symbols(analyze_json_files, tmp_path, "package.json", text), {"package", "bin"})
    _assert_producer_ids(by_pos, "json", "package.json")
    assert by_pos[("bin", "demo", 3, 10)].stable_id == scoped_stable_id(
        kind="bin", lang="json", path="package.json", name="demo",
    )
    single = _symbols(analyze_json_files, tmp_path / "s", "package.json",
                      "{\n  \"name\": \"tool\",\n  \"bin\": \"cli.js\"\n}\n")
    _assert_producer_ids(_at(single, {"bin"}), "json", "package.json")
    composer = _symbols(analyze_json_files, tmp_path / "c", "composer.json",
                        "{\n  \"name\": \"v/p\",\n  \"require\": {\"a/b\": \"1\"}\n}\n")
    _assert_producer_ids(_at(composer, {"package"}), "json", "composer.json")


def test_ansible_tasks_are_scoped_by_play(tmp_path: Path) -> None:
    text = (
        "---\n- name: Web\n  hosts: web\n  tasks:\n"
        "    - name: Install\n      apt:\n        name: nginx\n"
        "    - name: Start\n      service:\n        name: nginx\n  handlers:\n"
        "    - name: Restart\n      service:\n        name: nginx\n"
        "- name: Db\n  hosts: db\n  tasks:\n"
        "    - name: Install\n      apt:\n        name: postgresql\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_ansible, tmp_path, "playbooks/site.yml", text,
        "    - name: Install\n      apt:\n        name: nginx\n",
        {"playbook", "task", "handler"}, "ansible",
    )
    assert before[("task", "Install", 18, 0)].stable_id == scoped_stable_id(
        kind="task", lang="ansible", path="playbooks/site.yml", name="Install",
        scope=("Db",),
    )


def test_android_permissions_and_components_hash_their_full_names(tmp_path: Path) -> None:
    text = (
        "<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\" "
        "package=\"com.demo\">\n"
        "  <uses-permission android:name=\"android.permission.CAMERA\"/>\n"
        "  <uses-permission android:name=\"com.other.CAMERA\"/>\n"
        "  <application>\n    <activity android:name=\".MainActivity\"/>\n"
        "  </application>\n</manifest>\n"
    )
    by_pos = _at(_symbols(analyze_xml_files, tmp_path, "AndroidManifest.xml", text),
                 {"permission", "component"})
    _assert_producer_ids(by_pos, "xml", "AndroidManifest.xml")
    perms = [s.stable_id for s in by_pos.values() if s.kind == "permission"]
    assert len(set(perms)) == 2  # one short name, two permissions
    assert by_pos[("permission", "CAMERA", 2, 2)].stable_id == declared_arity_stable_id(
        kind="permission", lang="xml", path="AndroidManifest.xml", name="CAMERA",
        qualified_name="android.permission.CAMERA", arity=None,
    )


def test_bash_aliases_are_scoped_by_enclosing_function(tmp_path: Path) -> None:
    text = (
        "#!/bin/bash\nsetup_a() {\n  alias ll='ls -l'\n}\n"
        "setup_b() {\n  alias ll='ls -la'\n}\nalias gs='git status'\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_bash, tmp_path, "bin/env.sh", text,
        "setup_a() {\n  alias ll='ls -l'\n}\n", {"alias"}, "bash",
    )
    assert before[("alias", "ll", 6, 2)].stable_id == scoped_stable_id(
        kind="alias", lang="bash", path="bin/env.sh", name="ll", scope=("setup_b",),
    )


def test_manifest_targets_mint_pass_scoped_ids(tmp_path: Path) -> None:
    """INV-fufij: a ``Package.swift`` target carries the language ``swift``
    but this pass is not swift's producer, so it rode the floor key."""
    text = (
        "// swift-tools-version:5.5\nimport PackageDescription\n"
        "let package = Package(name: \"Demo\", targets: [\n"
        "    .executableTarget(name: \"demo\", path: \"Sources/demo\"),\n"
        "])\n"
    )
    syms = [s for s in _symbols(analyze_manifest_targets, tmp_path, "Package.swift", text)
            if s.kind == "binary"]
    assert len(syms) == 1
    target_path = syms[0].meta["target_path"]
    assert syms[0].stable_id == pass_minted_stable_id(
        owner="manifest_targets", kind="binary", lang="swift", path="Package.swift",
        name="demo", scope=(target_path,),
    )
    assert syms[0].stable_id != floor_stable_id_key("binary", "swift", "Package.swift", "demo")
