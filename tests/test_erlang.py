"""Tests for Erlang language analyzer.

Erlang is a functional, concurrent programming language designed for
telecommunications and distributed systems. It runs on the BEAM VM.

Key constructs: -module, fun_decl, -record, -behaviour, -export.

Test strategy:
- Module detection (-module)
- Function detection with arity
- Record detection
- Macro detection
- Behaviour implementation
- Function calls (local and remote)
- Import statements
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import erlang as erlang_module
from hypergumbo.analyze.erlang import analyze_erlang


def make_erl_file(tmp: Path, name: str, content: str) -> Path:
    """Create a .erl file for testing."""
    f = tmp / name
    f.write_text(content, encoding="utf-8")
    return f


class TestErlangAnalyzer:
    """Test Erlang symbol and edge detection."""

    def test_detects_module(self, tmp_path: Path) -> None:
        """Detect module definitions."""
        make_erl_file(
            tmp_path,
            "myapp.erl",
            """
-module(myapp).
-export([start/0]).

start() ->
    ok.
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "myapp" in names

        mod = next(s for s in result.symbols if s.name == "myapp")
        assert mod.kind == "module"
        assert mod.language == "erlang"

    def test_detects_functions_with_arity(self, tmp_path: Path) -> None:
        """Detect function definitions with arity."""
        make_erl_file(
            tmp_path,
            "math.erl",
            """
-module(math).
-export([add/2, double/1]).

add(A, B) ->
    A + B.

double(X) ->
    X * 2.
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "add/2" in names
        assert "double/1" in names

        add = next(s for s in result.symbols if s.name == "add/2")
        assert add.kind == "function"
        assert add.meta is not None
        assert add.meta.get("arity") == 2

    def test_detects_records(self, tmp_path: Path) -> None:
        """Detect record definitions."""
        make_erl_file(
            tmp_path,
            "models.erl",
            """
-module(models).

-record(user, {name, email, age}).
-record(product, {id, title, price}).
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "user" in names
        assert "product" in names

        user = next(s for s in result.symbols if s.name == "user")
        assert user.kind == "record"

    def test_detects_macros(self, tmp_path: Path) -> None:
        """Detect macro definitions."""
        make_erl_file(
            tmp_path,
            "constants.erl",
            """
-module(constants).

-define(PI, 3.14159).
-define(MAX_SIZE, 1024).
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "PI" in names
        assert "MAX_SIZE" in names

        pi = next(s for s in result.symbols if s.name == "PI")
        assert pi.kind == "macro"

    def test_detects_type_alias(self, tmp_path: Path) -> None:
        """Detect type alias definitions."""
        make_erl_file(
            tmp_path,
            "types.erl",
            """
-module(types).

-type user() :: #{name := binary(), age := integer()}.
-type response() :: {ok, term()} | {error, term()}.
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "user" in names
        assert "response" in names

        user = next(s for s in result.symbols if s.name == "user")
        assert user.kind == "type"

    def test_detects_import(self, tmp_path: Path) -> None:
        """Detect import statements as import edges."""
        make_erl_file(
            tmp_path,
            "app.erl",
            """
-module(app).
-import(lists, [map/2, filter/2]).
-import(io, [format/2]).

-export([run/0]).

run() ->
    ok.
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert len(imports) >= 2

        # Should have imports for lists and io
        import_dsts = [e.dst for e in imports]
        assert any("lists" in dst for dst in import_dsts)
        assert any("io" in dst for dst in import_dsts)

    def test_detects_behaviour(self, tmp_path: Path) -> None:
        """Detect behaviour implementation as import edge."""
        make_erl_file(
            tmp_path,
            "server.erl",
            """
-module(server).
-behaviour(gen_server).

-export([init/1]).

init([]) ->
    {ok, #{}}.
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert len(imports) >= 1

        # Should have import to gen_server
        import_dsts = [e.dst for e in imports]
        assert any("gen_server" in dst for dst in import_dsts)

    def test_detects_local_function_calls(self, tmp_path: Path) -> None:
        """Detect local function call edges."""
        make_erl_file(
            tmp_path,
            "app.erl",
            """
-module(app).
-export([main/0, helper/1]).

helper(X) ->
    X * 2.

main() ->
    helper(21).
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

        # main should call helper
        main_sym = next(s for s in result.symbols if s.name == "main/0")
        helper_sym = next(s for s in result.symbols if s.name == "helper/1")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (main_sym.id, helper_sym.id) in edge_pairs

    def test_detects_remote_function_calls(self, tmp_path: Path) -> None:
        """Detect remote function calls (module:function)."""
        # First module
        make_erl_file(
            tmp_path,
            "utils.erl",
            """
-module(utils).
-export([double/1]).

double(X) ->
    X * 2.
""",
        )
        # Second module calling first
        make_erl_file(
            tmp_path,
            "app.erl",
            """
-module(app).
-export([quadruple/1]).

quadruple(X) ->
    utils:double(utils:double(X)).
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]

        # quadruple should call utils:double
        quad_sym = next(s for s in result.symbols if s.name == "quadruple/1")
        double_sym = next(s for s in result.symbols if s.name == "double/1")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (quad_sym.id, double_sym.id) in edge_pairs

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handle empty Erlang file gracefully."""
        make_erl_file(tmp_path, "empty.erl", "")
        result = analyze_erlang(tmp_path)
        assert not result.skipped

    def test_handles_syntax_error(self, tmp_path: Path) -> None:
        """Handle files with syntax errors gracefully."""
        make_erl_file(tmp_path, "bad.erl", "-module(bad.\n-export([")
        result = analyze_erlang(tmp_path)
        # Should not crash, may produce partial results
        assert result is not None

    def test_handles_header_files(self, tmp_path: Path) -> None:
        """Handle .hrl header files."""
        make_erl_file(
            tmp_path,
            "records.hrl",
            """
-record(config, {host, port, timeout}).
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "config" in names

    def test_multiple_function_clauses(self, tmp_path: Path) -> None:
        """Handle functions with multiple clauses (pattern matching)."""
        make_erl_file(
            tmp_path,
            "fib.erl",
            """
-module(fib).
-export([fib/1]).

fib(0) -> 0;
fib(1) -> 1;
fib(N) -> fib(N-1) + fib(N-2).
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        # Should detect fib as a single function
        funcs = [s for s in result.symbols if s.kind == "function"]
        fib_funcs = [f for f in funcs if f.name.startswith("fib")]
        assert len(fib_funcs) >= 1

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Analysis skips gracefully when tree-sitter unavailable."""
        make_erl_file(tmp_path, "test.erl", "-module(test).")

        with patch.object(
            erlang_module,
            "is_erlang_tree_sitter_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="Erlang analysis skipped"):
                result = erlang_module.analyze_erlang(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason


class TestErlangSignatureExtraction:
    """Tests for Erlang function signature extraction."""

    def test_positional_params(self, tmp_path: Path) -> None:
        """Extracts signature with positional parameters."""
        make_erl_file(
            tmp_path,
            "calc.erl",
            """
-module(calc).
-export([add/2]).

add(X, Y) ->
    X + Y.
""",
        )
        result = analyze_erlang(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "add" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature == "(X, Y)"

    def test_no_params_function(self, tmp_path: Path) -> None:
        """Extracts signature for function with no parameters."""
        make_erl_file(
            tmp_path,
            "simple.erl",
            """
-module(simple).
-export([answer/0]).

answer() ->
    42.
""",
        )
        result = analyze_erlang(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "answer" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"

    def test_pattern_matching_params(self, tmp_path: Path) -> None:
        """Extracts signature with pattern matching in parameters."""
        make_erl_file(
            tmp_path,
            "pattern.erl",
            """
-module(pattern).
-export([greet/1]).

greet({name, Name}) ->
    io:format("Hello ~s~n", [Name]).
""",
        )
        result = analyze_erlang(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "greet" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature == "({name, Name})"
