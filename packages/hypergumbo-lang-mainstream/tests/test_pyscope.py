# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ``_pyscope.py`` (the lexical scope substrate, identity:F1/F4a).

Exercises every :class:`Binding` variant and every :class:`ScopeStack` accessor
branch in isolation — the frame chain, the immediate/enclosing lookups, the
``NestedDef``-only unwrap, and the ``enclosing_lookup_enabled`` kill-switch — so
the module reaches 100% coverage without driving the full ``py.py`` pipeline.
"""

from __future__ import annotations

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_lang_mainstream._pyscope import (
    Alias,
    InferredType,
    IoModule,
    ModuleImport,
    NamedImport,
    NestedDef,
    Scope,
    ScopeStack,
)


def _fn(name: str, sid: str | None = None) -> Symbol:
    return Symbol(
        id=sid or f"python:m.py:1-1:{name}:function",
        name=name,
        kind="function",
        path="m.py",
        span=Span(1, 0, 1, 0),
        language="python",
        origin="test",
    )


class TestBindingVariants:
    def test_nested_def_holds_symbol(self):
        s = _fn("helper")
        assert NestedDef(s).symbol is s

    def test_alias_holds_target(self):
        assert Alias("g").target == "g"

    def test_inferred_type_holds_class(self):
        c = Symbol(
            id="python:m.py:1-1:C:class", name="C", kind="class",
            path="m.py", span=Span(1, 0, 1, 0), language="python", origin="test",
        )
        assert InferredType(c).class_symbol is c

    def test_io_module_holds_catalog(self):
        assert IoModule("file").catalog_module == "file"

    def test_named_import_holds_module_and_original(self):
        b = NamedImport("pkg.mod", "orig")
        assert (b.module, b.original) == ("pkg.mod", "orig")

    def test_module_import_holds_module(self):
        assert ModuleImport("pkg.mod").module == "pkg.mod"


class TestScopeStackImmediate:
    def test_immediate_empty_stack(self):
        assert ScopeStack(frames=[]).immediate() == {}

    def test_immediate_returns_top_frame_bindings(self):
        h = _fn("h")
        top = Scope(owner_id="f", bindings={"h": NestedDef(h)})
        assert ScopeStack(frames=[top]).immediate() == {"h": NestedDef(h)}

    def test_immediate_symbols_unwraps_only_nested_def(self):
        h = _fn("h")
        top = Scope(
            owner_id="f",
            bindings={"h": NestedDef(h), "g": Alias("other")},  # Alias dropped
        )
        assert ScopeStack(frames=[top]).immediate_symbols() == {"h": h}

    def test_immediate_symbols_empty_stack(self):
        assert ScopeStack(frames=[]).immediate_symbols() == {}

    def test_lookup_immediate_hits_nested_def(self):
        h = _fn("h")
        top = Scope(owner_id="f", bindings={"h": NestedDef(h)})
        assert ScopeStack(frames=[top]).lookup_immediate("h") is h

    def test_lookup_immediate_skips_non_nested_def(self):
        top = Scope(owner_id="f", bindings={"g": Alias("other")})
        assert ScopeStack(frames=[top]).lookup_immediate("g") is None

    def test_lookup_immediate_absent(self):
        assert ScopeStack(frames=[Scope("f", {})]).lookup_immediate("x") is None


class TestScopeStackEnclosing:
    def _two_level(self, enabled: bool = True):
        outer_h = _fn("h", "python:m.py:1-1:outer.h:function")
        outer = Scope(owner_id="outer", bindings={"h": NestedDef(outer_h)})
        inner = Scope(owner_id="inner", bindings={})
        stack = ScopeStack(frames=[outer, inner], enclosing_lookup_enabled=enabled)
        return stack, outer_h

    def test_enclosing_finds_outer_nested_def(self):
        stack, outer_h = self._two_level()
        assert stack.lookup_enclosing("h") is outer_h

    def test_enclosing_kill_switch_returns_none(self):
        stack, _ = self._two_level(enabled=False)
        assert stack.lookup_enclosing("h") is None

    def test_enclosing_single_frame_stack_returns_none(self):
        # frames[:-1] is empty -> nothing to search in the enclosing chain.
        top = Scope(owner_id="f", bindings={"h": NestedDef(_fn("h"))})
        assert ScopeStack(frames=[top]).lookup_enclosing("h") is None

    def test_enclosing_absent_name_returns_none(self):
        stack, _ = self._two_level()
        assert stack.lookup_enclosing("missing") is None

    def test_enclosing_skips_non_nested_def_binding(self):
        outer = Scope(owner_id="outer", bindings={"h": Alias("g")})
        inner = Scope(owner_id="inner", bindings={})
        assert ScopeStack(frames=[outer, inner]).lookup_enclosing("h") is None

    def test_enclosing_innermost_of_rest_wins(self):
        # Grandparent and parent both bind "h"; the parent (innermost of the
        # OUTER frames) must win over the grandparent.
        gp_h = _fn("h", "python:m.py:1-1:gp.h:function")
        p_h = _fn("h", "python:m.py:1-1:p.h:function")
        gp = Scope(owner_id="gp", bindings={"h": NestedDef(gp_h)})
        p = Scope(owner_id="p", bindings={"h": NestedDef(p_h)})
        caller = Scope(owner_id="c", bindings={})
        assert ScopeStack(frames=[gp, p, caller]).lookup_enclosing("h") is p_h


class TestScopeStackLocalShadowing:
    """LEGB "L": a caller/enclosing local binding (param/assignment/global)
    shadows a same-named enclosing def, so lookup_enclosing returns None."""

    def test_caller_local_shadows_enclosing_def(self):
        outer_h = _fn("h", "python:m.py:1-1:outer.h:function")
        outer = Scope(owner_id="outer", bindings={"h": NestedDef(outer_h)})
        # caller binds 'h' locally (e.g. a parameter) -> shadows outer.h.
        caller = Scope(owner_id="c", bindings={}, local_names=frozenset({"h"}))
        assert ScopeStack(frames=[outer, caller]).lookup_enclosing("h") is None

    def test_intermediate_local_shadows_further_out_def(self):
        gp_h = _fn("h", "python:m.py:1-1:gp.h:function")
        gp = Scope(owner_id="gp", bindings={"h": NestedDef(gp_h)})
        # middle binds 'h' locally (non-def) -> shadows the grandparent def.
        middle = Scope(owner_id="mid", bindings={}, local_names=frozenset({"h"}))
        caller = Scope(owner_id="c", bindings={})
        assert ScopeStack(frames=[gp, middle, caller]).lookup_enclosing("h") is None

    def test_enclosing_def_resolves_when_no_local_shadow(self):
        gp_h = _fn("h", "python:m.py:1-1:gp.h:function")
        gp = Scope(owner_id="gp", bindings={"h": NestedDef(gp_h)})
        middle = Scope(owner_id="mid", bindings={}, local_names=frozenset({"other"}))
        caller = Scope(owner_id="c", bindings={}, local_names=frozenset({"unrelated"}))
        assert ScopeStack(frames=[gp, middle, caller]).lookup_enclosing("h") is gp_h

    def test_empty_stack_lookup_enclosing_none(self):
        assert ScopeStack(frames=[]).lookup_enclosing("h") is None
