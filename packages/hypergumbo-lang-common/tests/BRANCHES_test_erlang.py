# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for erlang.py analyzer.

Tests specific branch paths in the Erlang analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function ID format
- Enclosing function detection edge cases
- Module prefix registration for remote calls
- Multiple function clauses detection
- Cross-file resolution with same-named functions
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_common.erlang import analyze_erlang, find_erlang_files

def make_erl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an Erlang file with given content."""
    (tmp_path / name).write_text(content)

class TestErlangHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("erlang", "src/app.erl", 10, 15, "start/0", "function")
        assert symbol_id == "erlang:src/app.erl:10-15:start/0:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("erlang", "lib/utils.erl")
        assert file_id == "erlang:lib/utils.erl:1-1:file:file"

class TestEnclosingFunctionDetection:
    """Branch coverage for _get_enclosing_function_erlang."""

    def test_nested_call_finds_enclosing(self, tmp_path: Path) -> None:
        """Test call in nested expression finds enclosing function."""
        make_erl_file(tmp_path, "nested.erl", """
-module(nested).
-export([outer/1, inner/1]).

inner(X) ->
    X * 2.

outer(X) ->
    case X of
        0 -> 0;
        N -> inner(N)
    end.
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        outer_sym = next(s for s in result.symbols if s.name == "outer/1")
        inner_sym = next(s for s in result.symbols if s.name == "inner/1")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (outer_sym.id, inner_sym.id) in edge_pairs

    def test_call_in_list_comprehension(self, tmp_path: Path) -> None:
        """Test call in list comprehension finds enclosing function."""
        make_erl_file(tmp_path, "listcomp.erl", """
-module(listcomp).
-export([process/1, transform/1]).

transform(X) ->
    X * 2.

process(List) ->
    [transform(X) || X <- List].
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        process_sym = next(s for s in result.symbols if s.name == "process/1")
        transform_sym = next(s for s in result.symbols if s.name == "transform/1")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (process_sym.id, transform_sym.id) in edge_pairs

class TestRemoteCallResolution:
    """Branch coverage for remote call (module:function) resolution."""

    def test_three_file_chain_resolution(self, tmp_path: Path) -> None:
        """Test call resolution across three files with remote calls."""
        make_erl_file(tmp_path, "base.erl", """
-module(base).
-export([base_fn/1]).

base_fn(X) ->
    X * 2.
""")
        make_erl_file(tmp_path, "middle.erl", """
-module(middle).
-export([middle_fn/1]).

middle_fn(X) ->
    base:base_fn(X) + 1.
""")
        make_erl_file(tmp_path, "top.erl", """
-module(top).
-export([top_fn/1]).

top_fn(X) ->
    middle:middle_fn(middle:middle_fn(X)).
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        names = [m.name for m in modules]
        assert "base" in names
        assert "middle" in names
        assert "top" in names

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have calls between files
        base_calls = [e for e in call_edges if "base_fn" in e.dst]
        assert len(base_calls) >= 1

class TestSameNamedFunctions:
    """Branch coverage for same-named functions in different modules."""

    def test_same_function_name_different_modules(self, tmp_path: Path) -> None:
        """Test functions with same name in different modules are distinct."""
        make_erl_file(tmp_path, "mod_a.erl", """
-module(mod_a).
-export([process/1]).

process(X) ->
    X * 2.
""")
        make_erl_file(tmp_path, "mod_b.erl", """
-module(mod_b).
-export([process/1]).

process(X) ->
    X + 1.
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        process_funcs = [s for s in result.symbols if s.name == "process/1"]
        # Should have two distinct functions
        assert len(process_funcs) == 2

        # Check they're in different paths
        paths = [f.path for f in process_funcs]
        assert len(set(paths)) == 2

class TestFunctionClausesEdgeCases:
    """Branch coverage for multiple function clause handling."""

    def test_complex_pattern_matching(self, tmp_path: Path) -> None:
        """Test function with complex pattern matching in clauses."""
        make_erl_file(tmp_path, "patterns.erl", """
-module(patterns).
-export([classify/1]).

classify([]) ->
    empty;
classify([_]) ->
    single;
classify([_|_]) ->
    multiple.
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        classify = next((s for s in result.symbols if "classify" in s.name), None)
        assert classify is not None
        assert classify.kind == "function"

    def test_guard_clauses(self, tmp_path: Path) -> None:
        """Test function with guard expressions."""
        make_erl_file(tmp_path, "guards.erl", """
-module(guards).
-export([safe_div/2]).

safe_div(_, 0) ->
    undefined;
safe_div(A, B) when B > 0 ->
    A / B;
safe_div(A, B) ->
    -A / -B.
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        safe_div = next((s for s in result.symbols if "safe_div" in s.name), None)
        assert safe_div is not None

class TestTypeDefinitions:
    """Branch coverage for type definitions."""

    def test_multiple_type_definitions(self, tmp_path: Path) -> None:
        """Test multiple type definitions in one file."""
        make_erl_file(tmp_path, "types.erl", """
-module(types).

-type id() :: integer().
-type name() :: binary() | string().
-type user() :: #{id := id(), name := name()}.
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        types = [s for s in result.symbols if s.kind == "type"]
        names = [t.name for t in types]
        assert "id" in names
        assert "name" in names
        assert "user" in names

class TestMacroDefinitions:
    """Branch coverage for macro extraction."""

    def test_macro_with_params(self, tmp_path: Path) -> None:
        """Test macro with parameters is extracted."""
        make_erl_file(tmp_path, "macros.erl", """
-module(macros).

-define(DEBUG(Msg), io:format("DEBUG: ~p~n", [Msg])).
-define(DOUBLE(X), (X) * 2).
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        macros = [s for s in result.symbols if s.kind == "macro"]
        names = [m.name for m in macros]
        assert "DEBUG" in names
        assert "DOUBLE" in names

class TestFindErlangFiles:
    """Branch coverage for file discovery."""

    def test_finds_hrl_files(self, tmp_path: Path) -> None:
        """Test .hrl header files are discovered."""
        (tmp_path / "include").mkdir()
        (tmp_path / "include" / "defs.hrl").write_text("-define(X, 1).")

        files = list(find_erlang_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".hrl"

    def test_finds_nested_erl_files(self, tmp_path: Path) -> None:
        """Test .erl files in nested directories are found."""
        src = tmp_path / "src" / "app"
        src.mkdir(parents=True)
        (src / "main.erl").write_text("-module(main).")

        files = list(find_erlang_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "main.erl"

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_module_only_file(self, tmp_path: Path) -> None:
        """Test file with only module declaration."""
        make_erl_file(tmp_path, "empty.erl", "-module(empty).")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        assert any(m.name == "empty" for m in modules)

    def test_file_without_module(self, tmp_path: Path) -> None:
        """Test file without module declaration (header-like)."""
        make_erl_file(tmp_path, "constants.hrl", """
-define(MAX, 100).
-define(MIN, 0).
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        # Should still extract macros even without module
        macros = [s for s in result.symbols if s.kind == "macro"]
        names = [m.name for m in macros]
        assert "MAX" in names
        assert "MIN" in names

class TestSignatureEdgeCases:
    """Branch coverage for signature extraction."""

    def test_tuple_params(self, tmp_path: Path) -> None:
        """Test function with tuple parameters extracts signature."""
        make_erl_file(tmp_path, "tuples.erl", """
-module(tuples).
-export([point_add/2]).

point_add({X1, Y1}, {X2, Y2}) ->
    {X1 + X2, Y1 + Y2}.
""")
        result = analyze_erlang(tmp_path)
        funcs = [s for s in result.symbols if "point_add" in s.name]
        assert len(funcs) == 1
        # Signature should include tuple structure
        assert "{" in funcs[0].signature

    def test_record_pattern_params(self, tmp_path: Path) -> None:
        """Test function with record pattern in parameters."""
        make_erl_file(tmp_path, "records.erl", """
-module(records).
-record(user, {name, age}).
-export([get_name/1]).

get_name(#user{name = Name}) ->
    Name.
""")
        result = analyze_erlang(tmp_path)
        funcs = [s for s in result.symbols if "get_name" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature is not None

class TestBehaviourEdgeCases:
    """Branch coverage for behaviour handling."""

    def test_multiple_behaviours(self, tmp_path: Path) -> None:
        """Test file implementing multiple behaviours."""
        make_erl_file(tmp_path, "multi_behaviour.erl", """
-module(multi_behaviour).
-behaviour(gen_server).
-behaviour(supervisor).

-export([init/1]).

init([]) ->
    {ok, #{}}.
""")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        dests = [e.dst for e in imports]
        assert any("gen_server" in d for d in dests)
        assert any("supervisor" in d for d in dests)
