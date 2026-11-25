# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for V language analyzer.

Tests for the tree-sitter-based V language analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_extended1.v_lang import (
    analyze_v,
    find_v_files,
    is_v_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def v_repo(tmp_path: Path) -> Path:
    """Create a minimal V project for testing."""
    src = tmp_path / "src"
    src.mkdir()

    # Main file
    (src / "main.v").write_text(
        '''module main

import os
import math

fn main() {
    println("Hello, V!")
    result := helper()
    println(result)
}

fn helper() int {
    return 42
}

pub fn add(a int, b int) int {
    return a + b
}

pub fn greet(name string) string {
    return "Hello, " + name
}
'''
    )

    # Types file
    (src / "types.v").write_text(
        '''module main

struct User {
    name string
    age  int
}

pub struct Point {
    x f64
    y f64
}

enum Color {
    red
    green
    blue
}

pub enum Status {
    active
    inactive
    pending
}

interface Printable {
    print()
}

pub interface Serializable {
    serialize() string
}

fn process_user(u User) string {
    return u.name
}
'''
    )

    return tmp_path


class TestFindVFiles:
    """Tests for finding V files."""

    def test_finds_v_files(self, v_repo: Path) -> None:
        """Should find all .v files recursively."""
        files = list(find_v_files(v_repo))
        assert len(files) == 2
        names = {f.name for f in files}
        assert "main.v" in names
        assert "types.v" in names

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty iterator for directory with no V files."""
        files = list(find_v_files(tmp_path))
        assert files == []


class TestIsVTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when tree-sitter-language-pack is installed."""
        assert is_v_tree_sitter_available() is True

    def test_returns_false_when_unavailable(self) -> None:
        """Should return False when tree-sitter-language-pack is not installed."""
        import hypergumbo_lang_extended1.v_lang as v_module
        with patch.object(v_module._analyzer, "_check_grammar_available", return_value=False):
            assert v_module.is_v_tree_sitter_available() is False


class TestAnalyzeV:
    """Tests for the V analyzer."""

    def test_skips_when_unavailable(self, v_repo: Path) -> None:
        """Should skip analysis and warn when tree-sitter is unavailable."""
        import hypergumbo_lang_extended1.v_lang as v_module

        with patch.object(v_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="v analysis skipped"):
                result = v_module.analyze_v(v_repo)

        assert result.skipped is True
        assert "v" in result.skip_reason
        assert result.symbols == []
        assert result.edges == []

    def test_extracts_functions(self, v_repo: Path) -> None:
        """Should extract function declarations."""
        result = analyze_v(v_repo)

        assert not result.skipped
        assert result.symbols

        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = {s.name for s in funcs}

        assert "main" in func_names
        assert "helper" in func_names
        assert "add" in func_names
        assert "greet" in func_names
        assert "process_user" in func_names

    def test_extracts_structs(self, v_repo: Path) -> None:
        """Should extract struct declarations."""
        result = analyze_v(v_repo)

        # A struct is kind ``struct`` (WI-lilab: it was ``class``).
        structs = [s for s in result.symbols if s.kind == "struct"]
        struct_names = {s.name for s in structs}

        assert "User" in struct_names
        assert "Point" in struct_names
        assert not [s for s in result.symbols if s.kind == "class"]

    def test_extracts_enums(self, v_repo: Path) -> None:
        """Should extract enum declarations."""
        result = analyze_v(v_repo)

        enums = [s for s in result.symbols if s.kind == "enum"]
        enum_names = {s.name for s in enums}

        assert "Color" in enum_names
        assert "Status" in enum_names

    def test_extracts_interfaces(self, v_repo: Path) -> None:
        """Should extract interface declarations."""
        result = analyze_v(v_repo)

        interfaces = [s for s in result.symbols if s.kind == "interface"]
        interface_names = {s.name for s in interfaces}

        assert "Printable" in interface_names
        assert "Serializable" in interface_names

    def test_public_visibility(self, v_repo: Path) -> None:
        """Should track public/private visibility."""
        result = analyze_v(v_repo)

        add_fn = next((s for s in result.symbols if s.name == "add"), None)
        assert add_fn is not None
        assert add_fn.meta is not None
        assert add_fn.meta.get("is_public") is True

        helper_fn = next((s for s in result.symbols if s.name == "helper"), None)
        assert helper_fn is not None
        assert helper_fn.meta is not None
        assert helper_fn.meta.get("is_public") is False

        # Public struct
        point = next((s for s in result.symbols if s.name == "Point"), None)
        assert point is not None
        assert point.meta.get("is_public") is True

        # Private struct
        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta.get("is_public") is False

    def test_function_signatures(self, v_repo: Path) -> None:
        """Should include function signatures."""
        result = analyze_v(v_repo)

        add_fn = next((s for s in result.symbols if s.name == "add"), None)
        assert add_fn is not None
        assert add_fn.signature is not None
        assert "int" in add_fn.signature

    def test_struct_field_count(self, v_repo: Path) -> None:
        """Should count struct fields."""
        result = analyze_v(v_repo)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta is not None
        assert user.meta.get("field_count") == 2  # name, age

        point = next((s for s in result.symbols if s.name == "Point"), None)
        assert point is not None
        assert point.meta is not None
        assert point.meta.get("field_count") == 2  # x, y

    def test_enum_variant_count(self, v_repo: Path) -> None:
        """Should count enum variants."""
        result = analyze_v(v_repo)

        color = next((s for s in result.symbols if s.name == "Color"), None)
        assert color is not None
        assert color.meta is not None
        assert color.meta.get("variant_count") == 3  # red, green, blue

        status = next((s for s in result.symbols if s.name == "Status"), None)
        assert status is not None
        assert status.meta is not None
        assert status.meta.get("variant_count") == 3  # active, inactive, pending

    def test_extracts_imports(self, v_repo: Path) -> None:
        """Should extract import edges."""
        result = analyze_v(v_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        import_targets = {e.dst for e in import_edges}

        assert any("os" in t for t in import_targets)
        assert any("math" in t for t in import_targets)

    def test_extracts_call_edges(self, v_repo: Path) -> None:
        """Should extract call edges between functions."""
        result = analyze_v(v_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) > 0

        # Check that main calls helper
        main_calls = [e for e in call_edges if "main" in e.src]
        callee_names = {e.dst.split(":")[-1] for e in main_calls}
        assert "helper" in callee_names or any("helper" in e.dst for e in main_calls)

    def test_pass_id(self, v_repo: Path) -> None:
        """Should have correct pass origin."""
        result = analyze_v(v_repo)

        for sym in result.symbols:
            assert sym.origin == [PASS_ID]

        for edge in result.edges:
            assert edge.origin == [PASS_ID]

    def test_analysis_run_metadata(self, v_repo: Path) -> None:
        """Should include analysis run metadata."""
        result = analyze_v(v_repo)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should return empty result for repository with no V files."""
        result = analyze_v(tmp_path)

        assert not result.skipped
        assert result.symbols == []
        assert result.edges == []
        assert result.run is not None

    def test_stable_ids(self, v_repo: Path) -> None:
        """Should generate stable IDs for symbols."""
        result1 = analyze_v(v_repo)
        result2 = analyze_v(v_repo)

        ids1 = {s.id for s in result1.symbols}
        ids2 = {s.id for s in result2.symbols}

        assert ids1 == ids2

    def test_span_info(self, v_repo: Path) -> None:
        """Should include accurate span information."""
        result = analyze_v(v_repo)

        for sym in result.symbols:
            assert sym.span is not None
            assert sym.path is not None
            assert sym.span.start_line > 0
            assert sym.span.end_line >= sym.span.start_line


class TestUnresolvedCalls:
    """Tests for handling unresolved function calls."""

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        """Should handle calls to undefined functions."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.v").write_text(
            '''module main

fn main() {
    unknown_function()
}
'''
        )

        result = analyze_v(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1

        # Should have lower confidence for unresolved target
        assert call_edges[0].confidence == 0.50
        assert ":unresolved" in call_edges[0].dst
        assert "unknown_function" in call_edges[0].dst


class TestVCyclomaticComplexity:
    """INV-loguk slice C: callable V symbols carry non-null CC + LOC.
    Real-grammar verification (if_expression/for_statement +
    expression_case/default_case match arms + &&/||)."""

    def test_branchy_function_has_cc_and_loc(self, tmp_path: Path) -> None:
        (tmp_path / "f.v").write_text("""module main

fn classify(n int) int {
    mut r := 0
    if n < 0 && n > -100 {
        r = 1
    } else if n == 0 {
        r = 2
    }
    for i := 0; i < n; i++ {
        r += i
    }
    match n {
        1 { r = 10 }
        else { r = 20 }
    }
    return r
}
""")
        result = analyze_v(tmp_path)
        fn = next(s for s in result.symbols
                  if s.kind == "function" and s.name == "classify")
        # base 1 + 2 if_expression + && + for + expression_case + default_case = 7
        assert fn.cyclomatic_complexity == 7
        assert fn.line_span is not None and fn.line_span >= 4

    def test_straight_line_function_cc_is_one(self, tmp_path: Path) -> None:
        (tmp_path / "g.v").write_text("module main\n\nfn g(x int) int {\n    return x\n}\n")
        result = analyze_v(tmp_path)
        fn = next(s for s in result.symbols
                  if s.kind == "function" and s.name == "g")
        assert fn.cyclomatic_complexity == 1
        assert fn.line_span is not None

    def test_callables_non_null_non_callables_null(self, tmp_path: Path) -> None:
        (tmp_path / "m.v").write_text("""module main

struct Point {
    x int
    y int
}

fn f(x int) int {
    if x > 0 {
        return 1
    }
    return 0
}
""")
        result = analyze_v(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert funcs
        for s in funcs:
            assert s.cyclomatic_complexity is not None, s.name
            assert s.line_span is not None, s.name
        for s in result.symbols:
            if s.kind != "function":
                assert s.cyclomatic_complexity is None, (s.kind, s.name)


_KIND_SLOT_SOURCE = """\
module main

pub struct Point {
    x int
    y int
}

struct T {
    v int
}

enum Color {
    red
    green
}

interface Shape {
    area() f64
}

fn (p Point) sum() int {
    return p.x + p.y
}

fn helper(x int) int {
    return x + 1
}

fn main() {
    helper(1)
}
"""


def _kind_slot_result(tmp_path: Path):
    (tmp_path / "app.v").write_text(_KIND_SLOT_SOURCE)
    return analyze_v(tmp_path)


def test_the_v_id_fixture_reaches_every_kind(tmp_path: Path) -> None:
    result = _kind_slot_result(tmp_path)
    got = sorted((s.kind, s.name) for s in result.symbols)
    assert got == [('enum', 'Color'), ('function', 'helper'), ('function', 'main'), ('interface', 'Shape'), ('method', 'sum'), ('struct', 'Point'), ('struct', 'T')], got


def test_every_v_node_id_names_its_symbols_own_kind(tmp_path: Path) -> None:
    """ADR-0036 Ruling 2: the last slot of a node id IS ``Symbol.kind``.
    The ids said ``fn`` on a function and ``struct`` on the ``class`` a struct
    was emitted as (the kind is ``struct`` since WI-lilab). ``struct T`` is the single-letter name the grammar cannot
    parse: that recovery site took its id word from a table, so only a run
    shows it."""
    result = _kind_slot_result(tmp_path)
    wrong = [(s.id, s.kind) for s in result.symbols if s.id.rsplit(":", 1)[1] != s.kind]
    assert wrong == []


def test_a_v_calls_edge_starts_and_ends_on_a_symbol(tmp_path: Path) -> None:
    """The analyzer builds the edge's source id a second time, from the
    enclosing declaration. It must be the id the Symbol carries, or the edge
    starts on no node."""
    result = _kind_slot_result(tmp_path)
    ids = {s.id for s in result.symbols}
    edges = [e for e in result.edges if e.edge_type == "calls"]
    assert edges
    assert [e.src for e in edges if e.src not in ids] == []
    assert any(e.dst in ids for e in edges)


def test_a_v_union_is_kind_union_and_a_struct_kind_struct(tmp_path: Path) -> None:
    """The grammar parses ``union U {...}`` as a ``struct_declaration`` whose
    keyword child is ``union``; both were emitted as ``class`` with a stable id
    hashing ``struct`` (WI-lilab). The struct beside it is the control."""
    (tmp_path / "types.v").write_text(
        "union Num {\n\ti int\n\tf f32\n}\n\npub struct Point {\n\tx int\n}\n"
    )
    got = sorted((s.kind, s.name) for s in analyze_v(tmp_path).symbols)
    assert got == [("struct", "Point"), ("union", "Num")], got


_METHOD_SOURCE = """\
module main

struct Point {
\tx int
}

fn (p Point) norm(k int) int {
\treturn p.x + k
}

fn (p Point) twice() int {
\treturn p.norm(2) + helper(1)
}

fn helper(x int) int {
\treturn x
}

fn main() {
\tp := Point{x: 1}
\tp.twice()
\thelper(3)
}
"""


def test_a_v_function_with_a_receiver_is_a_method(tmp_path: Path) -> None:
    """A V ``fn (p Point) norm()`` has a receiver: it is kind ``method``, the
    way the Go analyzer reports a function with a receiver (WI-lilab
    follow-up). A function without one stays ``function`` (the control). The
    name stays the declared word, as before."""
    (tmp_path / "m.v").write_text(_METHOD_SOURCE)
    got = sorted((s.kind, s.name) for s in analyze_v(tmp_path).symbols)
    assert got == [("function", "helper"), ("function", "main"),
                   ("method", "norm"), ("method", "twice"), ("struct", "Point")], got


def test_a_call_inside_a_v_method_starts_on_the_method(tmp_path: Path) -> None:
    """A call inside a method starts on the method's own node id: the caller
    id is rebuilt from the enclosing declaration, so it must carry the
    method kind too. (A call through a selector, ``p.norm(2)``, emits no edge
    at all, before and after; not this change.)"""
    (tmp_path / "m.v").write_text(_METHOD_SOURCE)
    result = analyze_v(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    calls = sorted((by_id[e.src].name, by_id[e.dst].name) for e in result.edges
                   if e.edge_type == "calls" and e.src in by_id and e.dst in by_id)
    assert calls == [("main", "helper"), ("twice", "helper")], calls
    twice = next(s for s in result.symbols if s.name == "twice")
    assert any(e.src == twice.id for e in result.edges if e.edge_type == "calls")
    assert [e.src for e in result.edges if e.edge_type == "calls" and e.src not in by_id] == []


class TestVResolvedBy:
    """``meta["resolved_by"]`` on a resolved call (audit 0030)."""

    def test_call_found_in_run_registry_is_name(self, tmp_path: Path) -> None:
        (tmp_path / "main.v").write_text("""module main

fn helper() int {
    return 1
}

fn main() {
    helper()
}
""")
        result = analyze_v(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls" and e.is_resolved]
        assert len(calls) == 1
        assert "helper" in calls[0].dst
        assert calls[0].meta["resolved_by"] == "name"
