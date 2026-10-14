# SPDX-License-Identifier: AGPL-3.0-or-later
"""A PHP grouped ``use`` registers its aliases (INV-nuvug).

``use Foo\\{Bar, Baz};`` (PHP 7.0+) registered NOTHING, so a call through
``Bar`` landed in the ``external`` module slot and matched no catalogue row,
while the single-import form of the identical call resolved. Two separate
reasons, and both had to be fixed:

1. ``_extract_use_aliases`` scanned the DIRECT children of
   ``namespace_use_declaration`` for ``namespace_use_clause``. tree-sitter puts
   a grouped use's clauses one level down, in a ``namespace_use_group``, and
   the shared prefix (``Foo``) on the declaration as a ``namespace_name``.
2. It required a ``qualified_name`` child, and a grouped clause's child is a
   bare ``name`` (``Bar``) or a qualified tail (``Sub\\Bar``), relative to the
   prefix.

Same failure shape as INV-zuvib in rust.py: a direct-children lookup against a
grammar that wraps the grouped form in an extra node, degrading to NO alias
rather than a wrong one.

Measured before the fix: 0 grouped uses among 5,323 namespace imports in the
local PHP corpus (koel, grpc, rabbitmq), so this is a correctness fix with no
corpus-level effect there; it is not claimed as recall.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.php import _extract_use_aliases, _get_php_parser


def _aliases(text: str) -> dict[str, str]:
    source = text.encode()
    parser = _get_php_parser()
    assert parser is not None
    return _extract_use_aliases(parser.parse(source), source)


@pytest.mark.parametrize(("text", "expected"), [
    pytest.param("<?php use Foo\\{Bar, Baz};",
                 {"Bar": "Foo\\Bar", "Baz": "Foo\\Baz"}, id="grouped"),
    pytest.param("<?php use Foo\\{Bar as B, Baz};",
                 {"B": "Foo\\Bar", "Baz": "Foo\\Baz"}, id="alias-inside-group"),
    pytest.param("<?php use Foo\\{Sub\\Bar, Qux};",
                 {"Bar": "Foo\\Sub\\Bar", "Qux": "Foo\\Qux"}, id="qualified-tail"),
    pytest.param("<?php use function Foo\\{helper, other};",
                 {"helper": "Foo\\helper", "other": "Foo\\other"}, id="use-function"),
    pytest.param("<?php use Foo\\{Bar, function f, const C};",
                 {"Bar": "Foo\\Bar", "f": "Foo\\f", "C": "Foo\\C"}, id="mixed-kinds"),
    pytest.param("<?php use App\\Models\\{User, Post};",
                 {"User": "App\\Models\\User", "Post": "App\\Models\\Post"},
                 id="multi-segment-prefix"),
])
def test_a_grouped_use_registers_every_leaf(text: str, expected: dict[str, str]) -> None:
    assert _aliases(text) == expected


@pytest.mark.parametrize(("text", "expected"), [
    pytest.param("<?php use Foo\\Bar;", {"Bar": "Foo\\Bar"}, id="single"),
    pytest.param("<?php use Foo\\Bar as B;", {"B": "Foo\\Bar"}, id="single-aliased"),
    pytest.param("<?php use Foo\\Bar, Baz\\Qux;",
                 {"Bar": "Foo\\Bar", "Qux": "Baz\\Qux"}, id="comma-list"),
    pytest.param("<?php use Foo;", {}, id="bare-global-name-unchanged"),
])
def test_the_ungrouped_forms_are_unchanged(text: str, expected: dict[str, str]) -> None:
    """Control: the forms that already worked, including the one that
    deliberately registers nothing (a bare global name maps to itself)."""
    assert _aliases(text) == expected


def test_a_call_through_a_grouped_import_names_its_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filed repro, on the shipped command: both forms now reach the
    same module slot."""
    from hypergumbo_core.cli import main

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "single.php").write_text(
        "<?php\nnamespace App;\nuse function Foo\\helper;\nfunction a() { return helper(); }\n")
    (repo / "grouped.php").write_text(
        "<?php\nnamespace App2;\nuse function Foo\\{helper, other};\n"
        "function b() { return helper(); }\n")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    out = tmp_path / "survey.json"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        main(["survey", str(repo), "--out", str(out)])
    calls = {
        e["src"].split(":")[-2]: e["dst"]
        for e in json.loads(out.read_text())["edges"] if e["type"] == "calls"
    }
    assert calls["a"].startswith("php:Foo\\helper:")
    assert calls["b"].startswith("php:Foo\\helper:")
