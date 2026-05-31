# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parametric test for the WI-huzuv mechanical dst_ref sweep.

Verifies that the sweep-target analyzers attach a structured
``dst_ref=ExternalRef(...)`` to unresolved-call edges whenever the
analyzer already has a module hint at the emit site. Each fixture
follows the same shape: declare an import, then call a function
imported from that module so the analyzer reaches its
``make_unresolved_edge`` branch with a non-"external" ``module_hint``.

Coverage:

- WI-huzuv (PR #3991, 2026-05-31) — kotlin, php, scala, swift.
- WI-nigah Tier 1 (2026-05-31) — groovy, the 5th mechanical-equivalent
  case (``path_hint`` already in scope at the call site).
- WI-nigah Tier 2 (2026-05-31) — lua. Different shape from Tier 1:
  the analyzer tracks ``require_aliases`` (``M = require("foo")``)
  instead of ``import ... as`` aliases, and the retrofit threads
  ``require_aliases[receiver]`` through both the method-call fallback
  site and the dot-call fallback site.

Tcl is part of the WI-nigah Tier 2 sweep but lives in the
``hypergumbo-lang-extended1`` package; its dst_ref test lives in
``packages/hypergumbo-lang-extended1/tests/test_tcl_dst_ref.py``
(per the same-package test-placement rule).
"""
import pytest
from pathlib import Path

from hypergumbo_core.ir import ExternalRef


def _check_grammar_or_skip(check_fn, lang):
    if not check_fn():
        pytest.skip(f"tree-sitter-{lang} not available")


def test_kotlin_unresolved_call_carries_dst_ref(tmp_path: Path) -> None:
    from hypergumbo_lang_mainstream.kotlin import (
        analyze_kotlin,
        is_kotlin_tree_sitter_available,
    )
    _check_grammar_or_skip(is_kotlin_tree_sitter_available, "kotlin")

    (tmp_path / "Main.kt").write_text('''
import com.example.helpers.doWork

fun main() {
    doWork()
}
''')
    result = analyze_kotlin(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "doWork" in e.dst
    ]
    assert unresolved_calls, "expected an unresolved call edge for doWork"
    edge = unresolved_calls[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated when module_hint is a real path; got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "kotlin"
    assert "com.example.helpers" in edge.dst_ref.module_path
    assert edge.dst_ref.name == "doWork"


def test_php_unresolved_call_carries_dst_ref(tmp_path: Path) -> None:
    from hypergumbo_lang_mainstream.php import (
        analyze_php,
        is_php_tree_sitter_available,
    )
    _check_grammar_or_skip(is_php_tree_sitter_available, "php")

    (tmp_path / "main.php").write_text('''<?php
use App\\Helpers\\doWork;

function main() {
    doWork();
}
''')
    result = analyze_php(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "doWork" in e.dst
    ]
    assert unresolved_calls, "expected an unresolved call edge for doWork"
    edge = unresolved_calls[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated when module_hint is a real path; got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "php"
    assert "App" in edge.dst_ref.module_path or "Helpers" in edge.dst_ref.module_path
    assert edge.dst_ref.name == "doWork"


def test_scala_unresolved_call_carries_dst_ref(tmp_path: Path) -> None:
    from hypergumbo_lang_mainstream.scala import (
        analyze_scala,
        is_scala_tree_sitter_available,
    )
    _check_grammar_or_skip(is_scala_tree_sitter_available, "scala")

    (tmp_path / "Main.scala").write_text('''
import com.example.helpers.doWork

object Main {
  def main(args: Array[String]): Unit = {
    doWork()
  }
}
''')
    result = analyze_scala(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "doWork" in e.dst
    ]
    assert unresolved_calls, "expected an unresolved call edge for doWork"
    edge = unresolved_calls[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated when module_hint is a real path; got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "scala"
    assert "com.example.helpers" in edge.dst_ref.module_path
    assert edge.dst_ref.name == "doWork"


def test_swift_unresolved_call_carries_dst_ref(tmp_path: Path) -> None:
    from hypergumbo_lang_mainstream.swift import (
        analyze_swift,
        is_swift_tree_sitter_available,
    )
    _check_grammar_or_skip(is_swift_tree_sitter_available, "swift")

    (tmp_path / "Main.swift").write_text('''
import HelpersModule

func main() {
    HelpersModule.doWork()
}
''')
    result = analyze_swift(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "doWork" in e.dst
    ]
    assert unresolved_calls, "expected an unresolved call edge for doWork"
    edge = unresolved_calls[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated when module_hint is a real path; got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "swift"
    assert "HelpersModule" in edge.dst_ref.module_path
    assert edge.dst_ref.name == "doWork"


def test_groovy_unresolved_call_carries_dst_ref(tmp_path: Path) -> None:
    """WI-nigah Tier 1: groovy is the 5th mechanical-equivalent case.

    The analyzer already tracks ``import X as Y`` aliases and threads
    ``path_hint`` into resolver lookups; the retrofit only needed to
    propagate ``path_hint`` to ``make_unresolved_edge`` as the structured
    ``dst_ref`` form. Test shape mirrors kotlin/scala/php/swift above.
    """
    from hypergumbo_lang_mainstream.groovy import (
        analyze_groovy,
        is_groovy_tree_sitter_available,
    )
    _check_grammar_or_skip(is_groovy_tree_sitter_available, "groovy")

    # ``import com.example.helpers.Helpers as H`` registers H as an alias
    # whose path_hint is the qualified module. Calling ``H.doWork()``
    # inside a function routes the unresolved-edge through the
    # import-aliased branch (groovy.py only emits edges from calls inside
    # an enclosing function).
    (tmp_path / "Main.groovy").write_text('''
import com.example.helpers.Helpers as H

def run() {
    H.doWork()
}
''')
    result = analyze_groovy(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "doWork" in e.dst
    ]
    assert unresolved_calls, "expected an unresolved call edge for doWork"
    edge = unresolved_calls[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated when path_hint is a real path; got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "groovy"
    assert "com.example.helpers" in edge.dst_ref.module_path
    assert edge.dst_ref.name == "doWork"


def test_lua_unresolved_dot_call_carries_dst_ref(tmp_path: Path) -> None:
    """WI-nigah Tier 2: lua dot call through a ``require_aliases`` receiver.

    ``M.doWork()`` where ``M = require("foo.bar")`` and ``foo.bar.doWork``
    is not a known project symbol reaches the dot-call unresolved-edge
    branch (``lua.py`` site 2). The retrofit threads ``require_aliases[M]``
    as both ``module_hint`` and structured ``dst_ref``.
    """
    from hypergumbo_lang_mainstream.lua import (
        analyze_lua,
        is_lua_tree_sitter_available,
    )
    _check_grammar_or_skip(is_lua_tree_sitter_available, "lua")

    (tmp_path / "main.lua").write_text('''
local M = require("foo.bar")

function run()
    M.doWork()
end
''')
    result = analyze_lua(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "doWork" in e.dst
    ]
    assert unresolved_calls, "expected an unresolved call edge for doWork"
    edge = unresolved_calls[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated when require_aliases tracks the "
        f"receiver; got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "lua"
    assert edge.dst_ref.module_path == "foo.bar"
    assert edge.dst_ref.name == "doWork"


def test_lua_unresolved_method_call_carries_dst_ref(tmp_path: Path) -> None:
    """WI-nigah Tier 2: lua method call through a ``require_aliases`` receiver.

    ``M:doWork()`` where ``M = require("foo.bar")`` reaches the method-call
    unresolved-edge fallback branch (``lua.py`` site 1). The retrofit
    threads ``require_aliases[M]`` as both ``module_hint`` and structured
    ``dst_ref``.
    """
    from hypergumbo_lang_mainstream.lua import (
        analyze_lua,
        is_lua_tree_sitter_available,
    )
    _check_grammar_or_skip(is_lua_tree_sitter_available, "lua")

    (tmp_path / "main.lua").write_text('''
local M = require("foo.bar")

function run()
    M:doWork()
end
''')
    result = analyze_lua(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "doWork" in e.dst
    ]
    assert unresolved_calls, "expected an unresolved call edge for doWork"
    edge = unresolved_calls[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated when require_aliases tracks the "
        f"receiver; got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "lua"
    assert edge.dst_ref.module_path == "foo.bar"
    assert edge.dst_ref.name == "doWork"


def test_lua_direct_call_leaves_dst_ref_none(tmp_path: Path) -> None:
    """WI-nigah Tier 2: lua bare-name direct call has no module info.

    ``unknownFunc()`` with no receiver and no enclosing require alias
    reaches the direct-call unresolved-edge branch (``lua.py`` site 3).
    The retrofit must leave ``dst_ref`` as None — the analyzer has no
    structured-module signal to attach.
    """
    from hypergumbo_lang_mainstream.lua import (
        analyze_lua,
        is_lua_tree_sitter_available,
    )
    _check_grammar_or_skip(is_lua_tree_sitter_available, "lua")

    (tmp_path / "main.lua").write_text('''
function run()
    unknownFunc()
end
''')
    result = analyze_lua(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "unknownFunc" in e.dst
    ]
    if unresolved_calls:
        edge = unresolved_calls[0]
        assert edge.dst_ref is None, (
            "dst_ref must be None for bare direct calls with no "
            "import-tracker signal"
        )


def test_sweep_external_sentinel_leaves_dst_ref_none(tmp_path: Path) -> None:
    """Sanity check: when the analyzer has no module hint (sentinel "external"),
    dst_ref stays None — the retrofit must not invent a path. This guards
    against accidentally populating dst_ref with the literal sentinel."""
    from hypergumbo_lang_mainstream.kotlin import (
        analyze_kotlin,
        is_kotlin_tree_sitter_available,
    )
    _check_grammar_or_skip(is_kotlin_tree_sitter_available, "kotlin")

    # No import statement — call to undeclared function falls back to "external".
    (tmp_path / "Main.kt").write_text('''
fun main() {
    unknownFunc()
}
''')
    result = analyze_kotlin(tmp_path)

    unresolved_calls = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "unknownFunc" in e.dst
    ]
    if unresolved_calls:
        edge = unresolved_calls[0]
        assert edge.dst_ref is None, (
            "dst_ref must be None when module_hint defaults to 'external' sentinel"
        )
