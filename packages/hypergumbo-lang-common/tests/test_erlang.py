# SPDX-License-Identifier: AGPL-3.0-or-later
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

from hypergumbo_lang_common import erlang as erlang_module
from hypergumbo_lang_common.erlang import analyze_erlang, is_erlang_tree_sitter_available


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
            erlang_module._analyzer,
            "_check_grammar_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="erlang analysis skipped"):
                result = erlang_module.analyze_erlang(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason


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


class TestErlangImportAliases:
    """Tests for Erlang import alias tracking (ADR-0007)."""

    def test_extracts_import_aliases(self, tmp_path: Path) -> None:
        """Extracts function -> module mapping from -import statements."""
        from hypergumbo_lang_common.erlang import _extract_import_aliases
        from tree_sitter_language_pack import get_parser

        source = b"""
-module(test).
-import(lists, [map/2, filter/2]).
-import(string, [join/2]).
"""
        parser = get_parser("erlang")
        tree = parser.parse(source)

        aliases = _extract_import_aliases(tree.root_node, source)

        assert aliases["map"] == "lists"
        assert aliases["filter"] == "lists"
        assert aliases["join"] == "string"

    def test_import_alias_used_for_path_hint(self, tmp_path: Path) -> None:
        """Imported functions use module as path_hint for resolution."""
        # Module with a function
        make_erl_file(
            tmp_path,
            "myutils.erl",
            """
-module(myutils).
-export([process/1]).

process(X) ->
    X * 2.
""",
        )
        # Module that imports and calls it without module prefix
        make_erl_file(
            tmp_path,
            "app.erl",
            """
-module(app).
-import(myutils, [process/1]).
-export([run/1]).

run(X) ->
    process(X).
""",
        )

        result = analyze_erlang(tmp_path)
        assert not result.skipped

        # Should have a call edge from run to process
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        run_sym = next(s for s in result.symbols if s.name == "run/1")
        process_sym = next(s for s in result.symbols if s.name == "process/1")

        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (run_sym.id, process_sym.id) in edge_pairs


class TestIsErlangTreeSitterAvailable:
    """Tests for is_erlang_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        """Returns True when tree-sitter-language-pack is installed."""
        assert is_erlang_tree_sitter_available() is True

    def test_returns_false_when_unavailable(self) -> None:
        """Returns False when tree-sitter-language-pack is not installed."""
        with patch.object(
            erlang_module._analyzer,
            "_check_grammar_available",
            return_value=False,
        ):
            assert is_erlang_tree_sitter_available() is False


class TestErlangBehaviourCallbacks:
    """Tests for OTP behaviour callback edge creation.

    When a module declares -behaviour(gen_server), the OTP framework invokes
    callback functions like init/1, handle_call/3, etc. The analyzer creates
    invokes_callback edges from the module to each implemented callback,
    making the OTP contract visible in the graph.
    """

    def test_gen_server_callbacks_detected(self, tmp_path: Path) -> None:
        """gen_server callbacks get invokes_callback edges."""
        make_erl_file(
            tmp_path,
            "my_server.erl",
            """
-module(my_server).
-behaviour(gen_server).

-export([init/1, handle_call/3, handle_cast/2]).

init([]) ->
    {ok, #{}}.

handle_call(Request, _From, State) ->
    {reply, ok, State}.

handle_cast(_Msg, State) ->
    {noreply, State}.
""",
        )
        result = analyze_erlang(tmp_path)
        assert not result.skipped

        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 3

        callback_dsts = {e.dst for e in callback_edges}
        func_syms = {s.id: s for s in result.symbols if s.kind == "function"}
        callback_names = {func_syms[dst].name for dst in callback_dsts if dst in func_syms}
        assert "init/1" in callback_names
        assert "handle_call/3" in callback_names
        assert "handle_cast/2" in callback_names

        # All edges should come from the module symbol
        module_sym = next(s for s in result.symbols if s.kind == "module")
        for e in callback_edges:
            assert e.src == module_sym.id
            assert e.confidence == 0.90

    def test_supervisor_init_callback(self, tmp_path: Path) -> None:
        """supervisor behaviour creates callback edge for init/1."""
        make_erl_file(
            tmp_path,
            "my_sup.erl",
            """
-module(my_sup).
-behaviour(supervisor).

-export([init/1]).

init([]) ->
    {ok, {{one_for_one, 5, 10}, []}}.
""",
        )
        result = analyze_erlang(tmp_path)

        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 1
        func_syms = {s.id: s for s in result.symbols if s.kind == "function"}
        assert func_syms[callback_edges[0].dst].name == "init/1"

    def test_unimplemented_callbacks_skipped(self, tmp_path: Path) -> None:
        """Only implemented callbacks get edges; missing ones are skipped."""
        make_erl_file(
            tmp_path,
            "partial_server.erl",
            """
-module(partial_server).
-behaviour(gen_server).

-export([init/1]).

init([]) ->
    {ok, #{}}.
""",
        )
        result = analyze_erlang(tmp_path)

        # Only init/1 is implemented, others are not
        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 1

    def test_unknown_behaviour_no_callbacks(self, tmp_path: Path) -> None:
        """Unknown behaviour names don't produce callback edges."""
        make_erl_file(
            tmp_path,
            "custom.erl",
            """
-module(custom).
-behaviour(my_custom_behaviour).

-export([init/1]).

init([]) ->
    ok.
""",
        )
        result = analyze_erlang(tmp_path)

        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 0

    def test_imports_edge_still_created(self, tmp_path: Path) -> None:
        """Behaviour import edge coexists with callback edges."""
        make_erl_file(
            tmp_path,
            "both.erl",
            """
-module(both).
-behaviour(gen_server).

-export([init/1]).

init([]) ->
    {ok, #{}}.
""",
        )
        result = analyze_erlang(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]

        # Both import and callback edges should exist
        assert any("gen_server" in e.dst for e in import_edges)
        assert len(callback_edges) == 1


class TestErlangDocstrings:
    """Tests for Erlang comment extraction via populate_docstrings_from_tree."""

    def test_percent_comment_on_function(self, tmp_path: Path) -> None:
        """Extracts %% comment preceding a function."""
        make_erl_file(
            tmp_path,
            "myapp.erl",
            "-module(myapp).\n"
            "-export([start/0]).\n"
            "%% Starts the application.\n"
            "start() -> ok.\n",
        )
        result = analyze_erlang(tmp_path)
        func = next(
            (s for s in result.symbols if "start" in s.name and s.kind == "function"),
            None,
        )
        assert func is not None
        assert func.docstring == "Starts the application."

    def test_percent_comment_on_module(self, tmp_path: Path) -> None:
        """Extracts %% comment preceding the module attribute."""
        make_erl_file(
            tmp_path,
            "myapp.erl",
            "%% Main application module.\n"
            "-module(myapp).\n"
            "-export([run/0]).\n"
            "run() -> ok.\n",
        )
        result = analyze_erlang(tmp_path)
        mod = next(
            (s for s in result.symbols if s.kind == "module"),
            None,
        )
        assert mod is not None
        assert mod.docstring == "Main application module."

    def test_no_comment_no_docstring(self, tmp_path: Path) -> None:
        """Function without preceding comment has no docstring."""
        make_erl_file(
            tmp_path,
            "myapp.erl",
            "-module(myapp).\n"
            "-export([run/0]).\n"
            "run() -> ok.\n",
        )
        result = analyze_erlang(tmp_path)
        func = next(
            (s for s in result.symbols if "run" in s.name and s.kind == "function"),
            None,
        )
        assert func is not None
        assert func.docstring is None

    def test_find_erlang_files(self, tmp_path: Path) -> None:
        """find_erlang_files yields .erl and .hrl files."""
        from hypergumbo_lang_common.erlang import find_erlang_files

        (tmp_path / "mod.erl").write_text("-module(mod).\n")
        (tmp_path / "hdr.hrl").write_text("-define(X, 1).\n")
        (tmp_path / "other.txt").write_text("not erlang\n")
        files = list(find_erlang_files(tmp_path))
        names = {f.name for f in files}
        assert "mod.erl" in names
        assert "hdr.hrl" in names
        assert "other.txt" not in names

    def test_local_call_resolves_by_base_name(self, tmp_path: Path) -> None:
        """Local calls resolve to same-file functions by base_name (without arity)."""
        (tmp_path / "my_module.erl").write_text(
            "-module(my_module).\n"
            "-export([run/0, helper/1]).\n"
            "run() -> helper(42).\n"
            "helper(X) -> X + 1.\n",
        )
        result = analyze_erlang(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # run/0 should call helper/1 via local resolution
        run_calls = [e for e in call_edges if "run" in e.src and "helper" in e.dst]
        assert len(run_calls) == 1

    def test_unresolved_local_call_no_crash(self, tmp_path: Path) -> None:
        """Calling a function that doesn't exist locally or globally doesn't crash."""
        (tmp_path / "caller.erl").write_text(
            "-module(caller).\n"
            "run() -> nonexistent_function(42).\n",
        )
        result = analyze_erlang(tmp_path)
        # Should not crash; nonexistent call just produces no edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"
                      and "nonexistent" in e.dst]
        assert len(call_edges) == 0
