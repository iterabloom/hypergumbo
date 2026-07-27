# SPDX-License-Identifier: AGPL-3.0-or-later
"""D UFCS receiver gating + linker recovery (WI-situj / INV-vigaf).

The D analyzer misbound an unresolvable-receiver call ``thing.exists()`` to an
arbitrary same-named free function ``exists()`` at 0.85. WI-situj gates such a
call when the receiver is a known parameter: it emits an unresolved edge with a
``receiver_type_hint`` (typed) or none (untyped), withholding the misbind, and
the receiver_type_dispatch linker recovers the real UFCS free function by
matching its first-parameter type.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from hypergumbo_core.linkers.receiver_type_dispatch import (
    link_receiver_type_dispatch,
)
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_lang_extended1.d_lang import (
    _enclosing_function_node,
    _extract_param_var_types,
    _first_param_type,
    analyze_d,
)


def _link(result: object, repo: Path) -> object:
    ctx = LinkerContext(
        repo_root=repo, symbols=result.symbols, edges=result.edges,
    )
    return link_receiver_type_dispatch(ctx)


def _func_node(src: str) -> object:
    parser = get_parser("d")
    tree = parser.parse(src.encode())
    for node in _iter(tree.root_node):
        if node.type == "function_declaration":
            return node
    raise AssertionError("no function_declaration")  # pragma: no cover


def _iter(node: object):
    yield node
    for c in node.children:
        yield from _iter(c)


class TestDUfcsIntegration:
    def test_ufcs_receiver_type_stamped_on_free_function(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "lib.d").write_text(
            "bool exists(File f) { return true; }\n",
        )
        result = analyze_d(tmp_path)
        exists = next(
            s for s in result.symbols
            if s.kind == "function" and s.name == "exists"
        )
        assert (exists.meta or {}).get("ufcs_receiver_type") == "File"

    def test_typed_param_receiver_recovers_via_linker(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "lib.d").write_text(
            "bool exists(File f) { return true; }\n",
        )
        (tmp_path / "use.d").write_text(
            "void process(File thing) { thing.exists(); }\n",
        )
        result = analyze_d(tmp_path)
        exists = next(
            s for s in result.symbols
            if s.kind == "function" and s.name == "exists"
        )
        # analyzer emits unresolved + receiver_type_hint (not a misbind)
        unresolved = [
            e for e in result.edges
            if e.edge_type == "calls" and not e.is_resolved
            and (e.meta or {}).get("receiver_type_hint") == "File"
        ]
        assert len(unresolved) >= 1
        # linker recovers the UFCS free function
        linked = _link(result, tmp_path)
        resolved = [
            e for e in linked.edges
            if e.dst == exists.id and e.evidence_type == "ast_call_ufcs"
        ]
        assert len(resolved) == 1

    def test_variable_receiver_misbind_suppressed(
        self, tmp_path: Path,
    ) -> None:
        # `doThing()` takes no File param, so `thing.doThing()` must NOT bind.
        (tmp_path / "lib.d").write_text(
            "bool doThing() { return true; }\n",
        )
        (tmp_path / "use.d").write_text(
            "void process(File thing) { thing.doThing(); }\n",
        )
        result = analyze_d(tmp_path)
        do_thing = next(
            s for s in result.symbols
            if s.kind == "function" and s.name == "doThing"
        )
        misbinds = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst == do_thing.id
            and e.is_resolved
        ]
        assert misbinds == [], "thing.doThing() must not misbind to doThing()"
        linked = _link(result, tmp_path)
        assert [e for e in linked.edges if e.dst == do_thing.id] == []

    def test_builtin_typed_receiver_suppresses_without_hint(
        self, tmp_path: Path,
    ) -> None:
        # `int` params have a keyword type node (no identifier) → hint is None:
        # the misbind is still suppressed, just not recoverable.
        (tmp_path / "lib.d").write_text(
            "bool exists() { return true; }\n",
        )
        (tmp_path / "use.d").write_text(
            "void process(int thing) { thing.exists(); }\n",
        )
        result = analyze_d(tmp_path)
        exists = next(
            s for s in result.symbols
            if s.kind == "function" and s.name == "exists"
        )
        # unresolved edge emitted with NO receiver_type_hint
        gated = [
            e for e in result.edges
            if e.edge_type == "calls" and not e.is_resolved
            and (e.meta or {}).get("receiver_type_hint") is None
            and e.dst.endswith(":exists:unresolved")
        ]
        assert len(gated) >= 1
        # no misbind to the bare exists()
        assert [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst == exists.id and e.is_resolved
        ] == []

    def test_bare_call_not_gated(self, tmp_path: Path) -> None:
        # A receiver-less call still resolves (gate only fires on a variable
        # receiver present in var_types).
        (tmp_path / "m.d").write_text(
            "void helper() {}\nvoid main() { helper(); }\n",
        )
        result = analyze_d(tmp_path)
        helper = next(
            s for s in result.symbols
            if s.kind == "function" and s.name == "helper"
        )
        resolved = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst == helper.id and e.is_resolved
        ]
        assert len(resolved) >= 1

    def test_local_var_receiver_misbind_suppressed(
        self, tmp_path: Path,
    ) -> None:
        # Real-repro D funnel (dub: startsWith/toString/exists on LOCAL vars and
        # loop vars — NOT parameters). The param-only gate missed these. A UFCS
        # receiver that is any non-module VALUE (here a local ``string l``, whose
        # ``l.startsWith()`` means ``std.algorithm.startsWith``) must be withheld,
        # not misbound to an arbitrary internal free ``startsWith``.
        (tmp_path / "lib.d").write_text(
            "bool startsWith() { return true; }\n",
        )
        (tmp_path / "use.d").write_text(
            "void process() {\n"
            "  string l = getValue();\n"
            "  l.startsWith();\n"
            "}\n",
        )
        result = analyze_d(tmp_path)
        sw = next(
            s for s in result.symbols
            if s.kind == "function" and s.name == "startsWith"
        )
        misbinds = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst == sw.id and e.is_resolved
        ]
        assert misbinds == [], "l.startsWith() on a local must not misbind"

    def test_module_qualified_call_still_resolves(self, tmp_path: Path) -> None:
        # Recall guard: the gate withholds only VALUE (UFCS) receivers. A prefix
        # that IS an imported module resolves normally.
        (tmp_path / "helpers.d").write_text(
            "module helpers;\nvoid doIt() {}\n",
        )
        (tmp_path / "main.d").write_text(
            "import helpers;\nvoid run() { helpers.doIt(); }\n",
        )
        result = analyze_d(tmp_path)
        do_it = next(
            s for s in result.symbols
            if s.kind == "function" and s.name == "doIt"
        )
        resolved = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst == do_it.id and e.is_resolved
        ]
        assert len(resolved) >= 1, "module-qualified helpers.doIt() must resolve"


class TestDUfcsHelpers:
    def test_first_param_type_none_for_no_arg_function(self) -> None:
        node = _func_node("void main() {}\n")
        assert _first_param_type(node, b"void main() {}\n") is None

    def test_first_param_type_user_type(self) -> None:
        src = b"bool exists(File f) { return true; }\n"
        node = _func_node(src.decode())
        assert _first_param_type(node, src) == "File"

    def test_param_var_types_maps_name_to_type(self) -> None:
        src = b"void process(File thing) { }\n"
        node = _func_node(src.decode())
        assert _extract_param_var_types(node, src) == {"thing": "File"}

    def test_builtin_param_type_is_none(self) -> None:
        src = b"void process(int n) { }\n"
        node = _func_node(src.decode())
        assert _extract_param_var_types(node, src) == {"n": None}

    def test_enclosing_function_node_none_outside_function(self) -> None:
        parser = get_parser("d")
        tree = parser.parse(b"module m;\n")
        # the module_declaration node has no function ancestor
        assert _enclosing_function_node(tree.root_node.children[0]) is None


class TestDNestedLocalFunction:
    """INV-fahub (real-repro dub residual): a function defined INSIDE another
    function's body (a nested-local) is not callable by bare name from another
    scope/file, so it must not become a global resolver target. On dub, the
    nested ``exists`` in compilers/utils.d bound ~9 cross-file ``exists(x)``
    free-calls (which mean ``std.file.exists``)."""

    def test_nested_local_not_cross_file_target(self, tmp_path: Path) -> None:
        (tmp_path / "utils.d").write_text(
            "void outer() {\n"
            "  bool exists(string s) { return s.length > 0; }\n"
            "  auto ok = exists(\"x\");\n"
            "}\n"
        )
        (tmp_path / "caller.d").write_text(
            "void run() {\n"
            "  auto b = exists(\"path\");\n"
            "}\n"
        )
        result = analyze_d(tmp_path)
        nested = next(
            s for s in result.symbols
            if s.kind == "function" and s.name == "exists"
        )
        misbinds = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst == nested.id
            and "run" in e.src and e.is_resolved
        ]
        assert misbinds == [], (
            "cross-file bare call must not bind to a nested-local function"
        )
