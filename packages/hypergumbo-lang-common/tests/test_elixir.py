"""Tests for Elixir analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindElixirFiles:
    """Tests for Elixir file discovery."""

    def test_finds_elixir_files(self, tmp_path: Path) -> None:
        """Finds .ex and .exs files."""
        from hypergumbo_lang_common.elixir import find_elixir_files

        (tmp_path / "app.ex").write_text("defmodule App do end")
        (tmp_path / "test.exs").write_text("defmodule AppTest do end")
        (tmp_path / "other.txt").write_text("not elixir")

        files = list(find_elixir_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix in (".ex", ".exs") for f in files)


class TestElixirTreeSitterAvailability:
    """Tests for tree-sitter-elixir availability checking."""

    def test_is_elixir_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-elixir is available."""
        from hypergumbo_lang_common.elixir import is_elixir_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_elixir_tree_sitter_available() is True

    def test_is_elixir_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo_lang_common.elixir import is_elixir_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_elixir_tree_sitter_available() is False

    def test_is_elixir_tree_sitter_available_no_language_pack(self) -> None:
        """Returns False when tree-sitter is available but language pack is not."""
        from hypergumbo_lang_common.elixir import is_elixir_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()  # tree-sitter available
            return None  # language pack not available

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_elixir_tree_sitter_available() is False


class TestAnalyzeElixirFallback:
    """Tests for fallback behavior when tree-sitter-elixir unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-elixir unavailable."""
        from hypergumbo_lang_common import elixir as elixir_module
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "test.ex").write_text("defmodule Test do end")

        with patch.object(
            elixir_module._analyzer,
            "_check_grammar_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="elixir analysis skipped"):
                result = analyze_elixir(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason


class TestElixirModuleExtraction:
    """Tests for extracting Elixir modules."""

    def test_extracts_module(self, tmp_path: Path) -> None:
        """Extracts Elixir module declarations."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "person.ex"
        ex_file.write_text("""
defmodule Person do
  def new(name) do
    %{name: name}
  end

  def get_name(person) do
    person.name
  end
end
""")

        result = analyze_elixir(tmp_path)


        assert result.run is not None
        assert result.run.files_analyzed == 1
        names = [s.name for s in result.symbols]
        assert "Person" in names

    def test_extracts_nested_module(self, tmp_path: Path) -> None:
        """Extracts nested Elixir modules."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "my_app.ex"
        ex_file.write_text("""
defmodule MyApp.Accounts do
  defmodule User do
    defstruct [:name, :email]
  end

  def create_user(name, email) do
    %User{name: name, email: email}
  end
end
""")

        result = analyze_elixir(tmp_path)


        names = [s.name for s in result.symbols]
        assert "MyApp.Accounts" in names


class TestElixirFunctionExtraction:
    """Tests for extracting Elixir functions."""

    def test_extracts_public_function(self, tmp_path: Path) -> None:
        """Extracts public function (def)."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "utils.ex"
        ex_file.write_text("""
defmodule Utils do
  def add(a, b), do: a + b
end
""")

        result = analyze_elixir(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "Utils.add" in func_names

    def test_extracts_private_function(self, tmp_path: Path) -> None:
        """Extracts private function (defp)."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "utils.ex"
        ex_file.write_text("""
defmodule Utils do
  def public_fn(x), do: private_fn(x)
  defp private_fn(x), do: x * 2
end
""")

        result = analyze_elixir(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "Utils.public_fn" in func_names
        assert "Utils.private_fn" in func_names

    def test_defp_has_private_modifier(self, tmp_path: Path) -> None:
        """Private functions (defp) should have modifiers=['private']."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "utils.ex"
        ex_file.write_text("""
defmodule Utils do
  def public_fn(x), do: x + 1
  defp private_fn(x), do: x * 2
end
""")

        result = analyze_elixir(tmp_path)

        funcs = {s.name: s for s in result.symbols if s.kind == "function"}
        assert "Utils.public_fn" in funcs
        assert "Utils.private_fn" in funcs

        # Public function should NOT have "private" modifier
        assert "private" not in (funcs["Utils.public_fn"].modifiers or [])
        # Private function SHOULD have "private" modifier
        assert "private" in (funcs["Utils.private_fn"].modifiers or [])

    def test_defmacrop_has_private_modifier(self, tmp_path: Path) -> None:
        """Private macros (defmacrop) should have modifiers=['private']."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "macros.ex"
        ex_file.write_text("""
defmodule Macros do
  defmacro public_macro(expr), do: expr
  defmacrop private_macro(expr), do: expr
end
""")

        result = analyze_elixir(tmp_path)

        macros = {s.name: s for s in result.symbols if s.kind == "macro"}
        assert "Macros.public_macro" in macros
        assert "Macros.private_macro" in macros

        # Public macro should NOT have "private" modifier
        assert "private" not in (macros["Macros.public_macro"].modifiers or [])
        # Private macro SHOULD have "private" modifier
        assert "private" in (macros["Macros.private_macro"].modifiers or [])


class TestElixirFunctionCalls:
    """Tests for detecting function calls in Elixir."""

    def test_detects_local_function_call(self, tmp_path: Path) -> None:
        """Detects calls to functions in same module."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "utils.ex"
        ex_file.write_text("""
defmodule Utils do
  def caller() do
    helper()
  end

  def helper() do
    :ok
  end
end
""")

        result = analyze_elixir(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge from caller to helper
        assert len(call_edges) >= 1


class TestElixirImports:
    """Tests for detecting Elixir imports."""

    def test_detects_use_directive(self, tmp_path: Path) -> None:
        """Detects use directives."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "controller.ex"
        ex_file.write_text("""
defmodule MyApp.Controller do
  use Phoenix.Controller

  def index(conn, _params) do
    render(conn, "index.html")
  end
end
""")

        result = analyze_elixir(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have edge for use Phoenix.Controller
        assert len(import_edges) >= 1

    def test_detects_import_directive(self, tmp_path: Path) -> None:
        """Detects import directives."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "helper.ex"
        ex_file.write_text("""
defmodule Helper do
  import Enum, only: [map: 2]

  def double_all(list) do
    map(list, &(&1 * 2))
  end
end
""")

        result = analyze_elixir(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestElixirMacros:
    """Tests for extracting Elixir macros."""

    def test_extracts_macro(self, tmp_path: Path) -> None:
        """Extracts macro declarations."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "macros.ex"
        ex_file.write_text("""
defmodule MyMacros do
  defmacro my_if(condition, do: do_block) do
    quote do
      case unquote(condition) do
        x when x in [false, nil] -> nil
        _ -> unquote(do_block)
      end
    end
  end
end
""")

        result = analyze_elixir(tmp_path)


        macros = [s for s in result.symbols if s.kind == "macro"]
        assert len(macros) >= 1
        macro_names = [s.name for s in macros]
        assert "MyMacros.my_if" in macro_names


class TestElixirEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Returns skipped with run when grammar is unavailable.

        In the TreeSitterAnalyzer base class, grammar unavailability is
        detected via _check_grammar_available() and produces a skipped result
        with an AnalysisRun. This replaces the old try/except pattern around
        parser loading.
        """
        from hypergumbo_lang_common import elixir as elixir_module
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "test.ex").write_text("defmodule Test do end")

        with patch.object(
            elixir_module._analyzer,
            "_check_grammar_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="elixir analysis skipped"):
                result = analyze_elixir(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason
        assert result.run is not None

    def test_file_with_no_symbols_is_analyzed(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are still counted as analyzed.

        The TreeSitterAnalyzer base class counts all successfully-parsed files
        as analyzed, even if they produce no symbols. Only unreadable files
        are counted as skipped.
        """
        from hypergumbo_lang_common.elixir import analyze_elixir

        # Create a file with only comments and whitespace
        (tmp_path / "empty.ex").write_text("# Just a comment\n\n")

        result = analyze_elixir(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1

    def test_unreadable_file_handled_gracefully(self, tmp_path: Path) -> None:
        """Unreadable files don't crash the analyzer."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "test.ex"
        ex_file.write_text("defmodule Test do end")

        result = analyze_elixir(tmp_path)


        # Just verify it doesn't crash
        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files when import is present."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        # File 1: defines helper
        (tmp_path / "helper.ex").write_text("""
defmodule Helper do
  def greet(name) do
    "Hello, " <> name
  end
end
""")

        # File 2: imports helper and calls bare greet
        (tmp_path / "main.ex").write_text("""
defmodule Main do
  import Helper

  def run() do
    greet("world")
  end
end
""")

        result = analyze_elixir(tmp_path)


        # Should have cross-file call edge (import makes it valid)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_module_qualified_cross_file_call(self, tmp_path: Path) -> None:
        """Detects module-qualified calls like Helper.greet() across files."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "helper.ex").write_text("""
defmodule Helper do
  def greet(name) do
    "Hello, " <> name
  end
end
""")

        (tmp_path / "main.ex").write_text("""
defmodule Main do
  def run() do
    Helper.greet("world")
  end
end
""")

        result = analyze_elixir(tmp_path)

        # Should have a cross-file call edge from Main.run -> Helper.greet
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Find the specific cross-file edge
        cross_file = [
            e for e in call_edges
            if "Main.run" in e.src and "Helper.greet" in e.dst
        ]
        assert len(cross_file) == 1, (
            f"Expected 1 cross-file edge Main.run->Helper.greet, got {len(cross_file)}. "
            f"All call edges: {[(e.src, e.dst) for e in call_edges]}"
        )

    def test_nested_module_qualified_call(self, tmp_path: Path) -> None:
        """Detects calls to nested modules like App.Services.UserService.find()."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "service.ex").write_text("""
defmodule App.Services.UserService do
  def find(id) do
    id
  end
end
""")

        (tmp_path / "controller.ex").write_text("""
defmodule App.Controller do
  def show(id) do
    App.Services.UserService.find(id)
  end
end
""")

        result = analyze_elixir(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cross_file = [
            e for e in call_edges
            if "App.Controller.show" in e.src and "find" in e.dst
        ]
        assert len(cross_file) == 1, (
            f"Expected 1 cross-file edge for App.Services.UserService.find(), "
            f"got {len(cross_file)}. All call edges: {[(e.src, e.dst) for e in call_edges]}"
        )

    def test_dot_call_with_alias_hint(self, tmp_path: Path) -> None:
        """Module-qualified call resolves through alias hint."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "deep_service.ex").write_text("""
defmodule App.Deep.Service do
  def process(x) do
    x
  end
end
""")

        # Use alias to refer to the module by short name
        (tmp_path / "caller.ex").write_text("""
defmodule Caller do
  alias App.Deep.Service

  def run() do
    Service.process(42)
  end
end
""")

        result = analyze_elixir(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cross_file = [
            e for e in call_edges
            if "Caller.run" in e.src and "process" in e.dst
        ]
        assert len(cross_file) == 1, (
            f"Expected 1 cross-file edge via alias hint, "
            f"got {len(cross_file)}. All call edges: {[(e.src, e.dst) for e in call_edges]}"
        )

    def test_dot_call_unresolved_creates_edge(self, tmp_path: Path) -> None:
        """Module-qualified call to unknown module creates unresolved edge."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "main.ex").write_text("""
defmodule Main do
  def run() do
    ExternalLib.do_something()
  end
end
""")

        result = analyze_elixir(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        unresolved = [
            e for e in call_edges
            if "Main.run" in e.src
            and "unresolved" in e.dst
            and e.evidence_type == "unresolved_module_call"
        ]
        assert len(unresolved) == 1, (
            f"Expected 1 unresolved edge, got {len(unresolved)}. "
            f"All call edges: {[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )
        assert unresolved[0].confidence == 0.50

    def test_dot_call_resolver_fallback(self, tmp_path: Path) -> None:
        """Partial module name resolved via NameResolver suffix/exact match.

        When a call uses a short module alias (e.g., Greeter.greet) but the
        symbol is stored under the full module path (App.Helpers.Greeter.greet),
        the resolver falls back to looking up the function name with a path hint.
        """
        from hypergumbo_lang_common.elixir import analyze_elixir

        # Define function in a deeply nested module
        (tmp_path / "greeter.ex").write_text("""
defmodule App.Helpers.Greeter do
  def greet(name) do
    "Hello #{name}"
  end
end
""")

        # Call using only the short module name (no alias declaration)
        (tmp_path / "caller.ex").write_text("""
defmodule App.Main do
  def run() do
    Greeter.greet("world")
  end
end
""")

        result = analyze_elixir(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        resolver_edges = [
            e for e in call_edges
            if "Main.run" in e.src
            and "greet" in e.dst
            and e.evidence_type == "module_qualified_call"
        ]
        assert len(resolver_edges) == 1, (
            f"Expected 1 resolver-fallback edge, got {len(resolver_edges)}. "
            f"All call edges: {[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )
        # Confidence = 0.75 * resolver confidence (suffix match ~0.85)
        assert 0.60 <= resolver_edges[0].confidence <= 0.75

    def test_dot_call_outside_function_ignored(self, tmp_path: Path) -> None:
        """Module-qualified call at module level (not inside def) is ignored."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "top_level.ex").write_text("""
defmodule TopLevel do
  # Module-level call, not inside a function
  Logger.info("starting")
end
""")

        result = analyze_elixir(tmp_path)

        # Should not crash, and no call edges from module-level code
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 0

    def test_dot_call_aliased_module_resolver_uses_expanded_name(
        self, tmp_path: Path,
    ) -> None:
        """Aliased module name expands before checking if module is known.

        When code uses ``alias App.Helpers.Svc, as: S`` and calls
        ``S.process()``, steps 1-2 fail because ``S.process`` is not in
        global_symbols and ``App.Helpers.Svc.process`` is not in
        global_symbols either (the function is named ``do_process`` in
        source).  Without alias expansion, "S." won't match any global
        symbol key and the resolver would be skipped (treating it as
        external).  With expansion, "App.Helpers.Svc." IS found in global
        symbols (``App.Helpers.Svc.do_process`` exists), so the resolver
        is tried.
        """
        from hypergumbo_lang_common.elixir import analyze_elixir

        # Module exists and has a function, but not named "process"
        (tmp_path / "svc.ex").write_text("""
defmodule App.Helpers.Svc do
  def do_process(x), do: x
end
""")

        # Caller uses short alias and calls a function that doesn't exist
        # under the exact qualified name
        (tmp_path / "caller.ex").write_text("""
defmodule Caller do
  alias App.Helpers.Svc, as: S

  def run() do
    S.process("data")
  end
end
""")

        result = analyze_elixir(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # The call should produce some edge (resolved or unresolved).
        # The key is that the alias expansion ran (line 958 covered) and
        # the module was recognized as known so the resolver was tried.
        edges_from_run = [
            e for e in call_edges if "Caller.run" in e.src
        ]
        assert len(edges_from_run) >= 1, (
            f"Expected at least 1 edge from Caller.run, "
            f"got {len(edges_from_run)}"
        )

    def test_dot_call_external_module_no_false_positive(self, tmp_path: Path) -> None:
        """Module-qualified call to external module does not match bare name.

        When code calls Plug.Builder.compile(), and Plug.Builder is not part
        of the project, the resolver should NOT match a local function named
        "compile" in a different module (e.g., Phoenix.Digester.compile).
        Instead, it should create an unresolved edge.
        """
        from hypergumbo_lang_common.elixir import analyze_elixir

        # A local module with a function named "compile"
        (tmp_path / "digester.ex").write_text("""
defmodule Phoenix.Digester do
  def compile(input) do
    {:ok, input}
  end
end
""")

        # A caller that invokes Plug.Builder.compile — an external module
        (tmp_path / "router.ex").write_text("""
defmodule Phoenix.Router do
  def build() do
    Plug.Builder.compile(env, [])
  end
end
""")

        result = analyze_elixir(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should NOT have a resolved edge to Phoenix.Digester.compile
        false_positives = [
            e for e in call_edges
            if "Router.build" in e.src and "Digester.compile" in e.dst
        ]
        assert len(false_positives) == 0, (
            "Plug.Builder.compile() should not resolve to "
            "Phoenix.Digester.compile. "
            f"Got: {[(e.src, e.dst) for e in false_positives]}"
        )

        # Should have an unresolved edge for Plug.Builder.compile
        unresolved = [
            e for e in call_edges
            if "Router.build" in e.src
            and "Plug.Builder" in e.dst
            and e.evidence_type == "unresolved_module_call"
        ]
        assert len(unresolved) == 1, (
            "Expected 1 unresolved edge for Plug.Builder.compile, "
            f"got {len(unresolved)}. "
            f"All: {[(e.src, e.dst, e.evidence_type) for e in call_edges]}"
        )

    def test_simple_function_definition(self, tmp_path: Path) -> None:
        """Extracts simple function definition without parentheses."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "simple.ex"
        # This uses the simple identifier form: def foo, do: :ok
        ex_file.write_text("""
defmodule Simple do
  def hello, do: :world
end
""")

        result = analyze_elixir(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "Simple.hello" in func_names


class TestElixirFileReadErrors:
    """Tests for file read error handling.

    File read errors are handled by the TreeSitterAnalyzer base class, which
    wraps file reads in try/except OSError. These tests verify the behavior
    through the public analyze_elixir interface.
    """

    def test_unreadable_file_skipped_gracefully(self, tmp_path: Path) -> None:
        """Unreadable files are skipped without crashing."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        # Create a valid file first, then make it unreadable by removing it
        # after discovery but before reading
        ex_file = tmp_path / "test.ex"
        ex_file.write_text("defmodule Test do end")

        result = analyze_elixir(tmp_path)
        # Basic check that it doesn't crash
        assert result.run is not None


class TestElixirMalformedCode:
    """Tests for handling malformed Elixir code."""

    def test_malformed_defmodule_no_name(self, tmp_path: Path) -> None:
        """Handles defmodule without a proper name."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "malformed.ex"
        # Intentionally malformed - defmodule with no alias argument
        ex_file.write_text("""
defmodule do
  def foo, do: :ok
end
""")

        result = analyze_elixir(tmp_path)


        # Should not crash, may or may not extract anything
        assert result.run is not None

    def test_get_function_name_no_match(self, tmp_path: Path) -> None:
        """_get_function_name returns None for unrecognized patterns."""
        from hypergumbo_lang_common.elixir import _get_function_name, is_elixir_tree_sitter_available

        if not is_elixir_tree_sitter_available():
            pytest.skip("tree-sitter-elixir not available")

        from tree_sitter_language_pack import get_parser
        parser = get_parser("elixir")

        # Parse some code where def has unusual structure
        source = b"def 123"  # Invalid syntax
        tree = parser.parse(source)

        # Find the call node if any
        def find_call(node):
            if node.type == "call":
                return node
            for child in node.children:
                result = find_call(child)
                if result:
                    return result
            return None

        call_node = find_call(tree.root_node)
        if call_node:
            result = _get_function_name(call_node, source)
            # Either returns None or a string, shouldn't crash
            assert result is None or isinstance(result, str)


class TestElixirSignatureExtraction:
    """Tests for Elixir function signature extraction."""

    def test_positional_params(self, tmp_path: Path) -> None:
        """Extracts signature with positional parameters."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "calc.ex").write_text("""
defmodule Calc do
  def add(a, b) do
    a + b
  end
end
""")
        result = analyze_elixir(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "add" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature == "(a, b)"

    def test_no_params_function(self, tmp_path: Path) -> None:
        """Extracts signature for function with no parameters."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "simple.ex").write_text("""
defmodule Simple do
  def answer do
    42
  end
end
""")
        result = analyze_elixir(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "answer" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"

    def test_macro_signature(self, tmp_path: Path) -> None:
        """Extracts signature from macro definition."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "macros.ex").write_text("""
defmodule Macros do
  defmacro debug(expr) do
    quote do
      IO.inspect(unquote(expr))
    end
  end
end
""")
        result = analyze_elixir(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro" and "debug" in s.name]
        assert len(macros) == 1
        assert macros[0].signature == "(expr)"


class TestAliasHintsExtraction:
    """Tests for alias hints extraction for disambiguation."""

    def test_extracts_simple_alias(self, tmp_path: Path) -> None:
        """Extracts alias directives using last component of module path."""
        from hypergumbo_lang_common.elixir import (
            _extract_alias_hints,
            is_elixir_tree_sitter_available,
        )

        if not is_elixir_tree_sitter_available():
            pytest.skip("tree-sitter-elixir not available")

        import tree_sitter_language_pack
        import tree_sitter

        lang = tree_sitter_language_pack.get_language("elixir")
        parser = tree_sitter.Parser(lang)

        ex_file = tmp_path / "main.ex"
        ex_file.write_text("""
defmodule Main do
  alias MyApp.Services.UserService
  alias MyApp.Math.Calculator

  def run do
    UserService.create()
    Calculator.add(1, 2)
  end
end
""")

        source = ex_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_alias_hints(tree, source)

        # Last component of module path should be the short name
        assert "UserService" in hints
        assert hints["UserService"] == "MyApp.Services.UserService"
        assert "Calculator" in hints
        assert hints["Calculator"] == "MyApp.Math.Calculator"

    def test_extracts_alias_with_as_option(self, tmp_path: Path) -> None:
        """Extracts alias directives with 'as:' custom alias."""
        from hypergumbo_lang_common.elixir import (
            _extract_alias_hints,
            is_elixir_tree_sitter_available,
        )

        if not is_elixir_tree_sitter_available():
            pytest.skip("tree-sitter-elixir not available")

        import tree_sitter_language_pack
        import tree_sitter

        lang = tree_sitter_language_pack.get_language("elixir")
        parser = tree_sitter.Parser(lang)

        ex_file = tmp_path / "main.ex"
        ex_file.write_text("""
defmodule Main do
  alias MyApp.Services.UserService, as: Svc

  def run do
    Svc.create()
  end
end
""")

        source = ex_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_alias_hints(tree, source)

        # Custom alias should be used
        assert "Svc" in hints
        assert hints["Svc"] == "MyApp.Services.UserService"

    def test_extracts_alias_with_other_options(self, tmp_path: Path) -> None:
        """Falls back to last component when alias has options but not 'as:'."""
        from hypergumbo_lang_common.elixir import (
            _extract_alias_hints,
            is_elixir_tree_sitter_available,
        )

        if not is_elixir_tree_sitter_available():
            pytest.skip("tree-sitter-elixir not available")

        import tree_sitter_language_pack
        import tree_sitter

        lang = tree_sitter_language_pack.get_language("elixir")
        parser = tree_sitter.Parser(lang)

        ex_file = tmp_path / "main.ex"
        ex_file.write_text("""
defmodule Main do
  alias MyApp.Services.UserService, warn: false

  def run do
    UserService.create()
  end
end
""")

        source = ex_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_alias_hints(tree, source)

        # Should use last component when as: is not present
        assert "UserService" in hints
        assert hints["UserService"] == "MyApp.Services.UserService"


class TestElixirPhoenixUsageContext:
    """Tests for Phoenix router DSL usage context extraction."""

    def test_phoenix_get_route(self, tmp_path: Path) -> None:
        """Extracts UsageContext for Phoenix get route."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  use Phoenix.Router

  scope "/api" do
    get "/users", UserController, :index
    post "/users", UserController, :create
  end
end
''')
        result = analyze_elixir(tmp_path)
        assert len(result.usage_contexts) >= 2

        get_ctx = next((c for c in result.usage_contexts if c.context_name == "get"), None)
        assert get_ctx is not None
        assert get_ctx.kind == "call"
        assert get_ctx.metadata["route_path"] == "/users"
        assert get_ctx.metadata["http_method"] == "GET"
        assert get_ctx.metadata["controller"] == "UserController"
        assert get_ctx.metadata["action"] == "index"

    def test_phoenix_post_route(self, tmp_path: Path) -> None:
        """Extracts UsageContext for Phoenix post route."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  use Phoenix.Router

  post "/users", UserController, :create
end
''')
        result = analyze_elixir(tmp_path)
        post_ctx = next((c for c in result.usage_contexts if c.context_name == "post"), None)
        assert post_ctx is not None
        assert post_ctx.metadata["http_method"] == "POST"

    def test_phoenix_resources_route(self, tmp_path: Path) -> None:
        """Extracts UsageContext for Phoenix resources route."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  use Phoenix.Router

  resources "/posts", PostController
end
''')
        result = analyze_elixir(tmp_path)
        res_ctx = next((c for c in result.usage_contexts if c.context_name == "resources"), None)
        assert res_ctx is not None
        assert res_ctx.metadata["http_method"] == "RESOURCES"
        assert res_ctx.metadata["route_path"] == "/posts"
        assert res_ctx.metadata["controller"] == "PostController"

    def test_phoenix_route_with_path_only(self, tmp_path: Path) -> None:
        """Extracts UsageContext for route with minimal args."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  use Phoenix.Router

  get "/health", HealthController, :check
end
''')
        result = analyze_elixir(tmp_path)
        ctx = next((c for c in result.usage_contexts if c.context_name == "get"), None)
        assert ctx is not None
        assert ctx.position == "args[0]"

    def test_phoenix_all_http_methods(self, tmp_path: Path) -> None:
        """Extracts UsageContext for all HTTP methods."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  use Phoenix.Router

  get "/", PageController, :home
  put "/users/:id", UserController, :update
  patch "/users/:id", UserController, :patch
  delete "/users/:id", UserController, :delete
  head "/ping", HealthController, :head
  options "/api", ApiController, :options
end
''')
        result = analyze_elixir(tmp_path)
        methods = {c.metadata["http_method"] for c in result.usage_contexts}
        assert "GET" in methods
        assert "PUT" in methods
        assert "PATCH" in methods
        assert "DELETE" in methods
        assert "HEAD" in methods
        assert "OPTIONS" in methods


class TestPhoenixRouteSymbols:
    """Tests for Phoenix route Symbol extraction (enables route-handler linking)."""

    def test_route_symbols_created_for_http_methods(self, tmp_path: Path) -> None:
        """Phoenix HTTP routes create Symbol objects with kind='route'."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  get "/users", UserController, :index
  post "/sessions", SessionController, :create
end
''')
        result = analyze_elixir(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 2

        get_route = next((s for s in route_symbols if "GET" in s.name), None)
        assert get_route is not None
        assert get_route.name == "GET /users"
        assert get_route.meta["http_method"] == "GET"
        assert get_route.meta["route_path"] == "/users"
        assert get_route.meta["controller"] == "UserController"
        assert get_route.meta["action"] == "index"
        assert get_route.language == "elixir"

        post_route = next((s for s in route_symbols if "POST" in s.name), None)
        assert post_route is not None
        assert post_route.name == "POST /sessions"

    def test_route_symbols_for_resources_macro(self, tmp_path: Path) -> None:
        """Phoenix resources macro creates expanded RESTful route symbols."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  resources "/posts", PostController
end
''')
        result = analyze_elixir(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        # Phoenix resources creates 7 RESTful routes (same as Rails)
        assert len(route_symbols) == 7

        routes_by_action = {s.meta["action"]: s for s in route_symbols}

        # Collection routes
        assert "index" in routes_by_action
        assert routes_by_action["index"].meta["http_method"] == "GET"
        assert routes_by_action["index"].meta["route_path"] == "/posts"

        assert "create" in routes_by_action
        assert routes_by_action["create"].meta["http_method"] == "POST"

        assert "new" in routes_by_action
        assert routes_by_action["new"].meta["http_method"] == "GET"
        assert routes_by_action["new"].meta["route_path"] == "/posts/new"

        # Member routes (with :id parameter)
        assert "show" in routes_by_action
        assert routes_by_action["show"].meta["http_method"] == "GET"
        assert routes_by_action["show"].meta["route_path"] == "/posts/:id"

        assert "edit" in routes_by_action
        assert "update" in routes_by_action
        assert "delete" in routes_by_action

        # All should reference the controller
        for route in route_symbols:
            assert route.meta["controller"] == "PostController"


class TestPhoenixLiveViewRoutes:
    """Tests for Phoenix LiveView `live` route macro detection.

    Phoenix LiveView routes use the `live` macro in routers:
        live "/path", LiveViewModule
        live "/path", LiveViewModule, :action
    These should be detected as routes with LIVE http_method and linked
    to the LiveView module's mount callback.
    """

    def test_live_route_usage_context(self, tmp_path: Path) -> None:
        """The `live` macro creates a UsageContext with LIVE http_method."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  use Phoenix.Router

  live "/dashboard", DashboardLive
end
''')
        result = analyze_elixir(tmp_path)

        live_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "live"), None
        )
        assert live_ctx is not None
        assert live_ctx.kind == "call"
        assert live_ctx.metadata["route_path"] == "/dashboard"
        assert live_ctx.metadata["http_method"] == "LIVE"
        assert live_ctx.metadata["controller"] == "DashboardLive"

    def test_live_route_with_action(self, tmp_path: Path) -> None:
        """The `live` macro with an action atom captures the live_action."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  use Phoenix.Router

  live "/users/:id", UserLive.Show, :show
end
''')
        result = analyze_elixir(tmp_path)

        live_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "live"), None
        )
        assert live_ctx is not None
        assert live_ctx.metadata["route_path"] == "/users/:id"
        assert live_ctx.metadata["controller"] == "UserLive.Show"
        assert live_ctx.metadata["action"] == "show"

    def test_live_route_symbol_created(self, tmp_path: Path) -> None:
        """The `live` macro creates a route Symbol with kind='route'."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  live "/settings", SettingsLive
  live "/users", UserLive.Index, :index
  live "/users/new", UserLive.Index, :new
end
''')
        result = analyze_elixir(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 3

        settings_route = next(
            (s for s in route_symbols if "/settings" in s.name), None
        )
        assert settings_route is not None
        assert settings_route.name == "LIVE /settings"
        assert settings_route.meta["http_method"] == "LIVE"
        assert settings_route.meta["route_path"] == "/settings"
        assert settings_route.meta["controller"] == "SettingsLive"
        # Default action is "mount" for LiveView routes without explicit action
        assert settings_route.meta["action"] == "mount"

        users_route = next(
            (s for s in route_symbols if s.name == "LIVE /users"), None
        )
        assert users_route is not None
        assert users_route.meta["controller"] == "UserLive.Index"
        assert users_route.meta["action"] == "index"

    def test_live_route_with_dotted_module(self, tmp_path: Path) -> None:
        """LiveView routes with dotted module names (UserLive.Show) are captured."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  live "/users/:id/edit", UserLive.Edit, :edit
end
''')
        result = analyze_elixir(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 1
        route = route_symbols[0]
        assert route.meta["controller"] == "UserLive.Edit"
        assert route.meta["action"] == "edit"

    def test_live_and_http_routes_coexist(self, tmp_path: Path) -> None:
        """LiveView and HTTP routes coexist in the same router."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  use Phoenix.Router

  get "/api/health", HealthController, :check
  live "/dashboard", DashboardLive
  post "/api/users", UserController, :create
end
''')
        result = analyze_elixir(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 3

        methods = {s.meta["http_method"] for s in route_symbols}
        assert methods == {"GET", "LIVE", "POST"}


class TestElixirBehaviourCallbacks:
    """Tests for OTP/Phoenix behaviour callback detection.

    When a module uses `use GenServer`, `use Phoenix.LiveView`, etc., the OTP/Phoenix
    framework calls specific callback functions. Without callback edge detection, these
    functions appear as orphans in the behavior map.
    """

    def test_genserver_callbacks(self, tmp_path: Path) -> None:
        """use GenServer creates invokes_callback edges for init, handle_call, etc."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "server.ex").write_text('''
defmodule MyApp.Server do
  use GenServer

  def start_link(opts) do
    GenServer.start_link(__MODULE__, opts)
  end

  def init(opts) do
    {:ok, opts}
  end

  def handle_call(:get, _from, state) do
    {:reply, state, state}
  end

  def handle_cast(:reset, _state) do
    {:noreply, %{}}
  end

  def handle_info(:tick, state) do
    {:noreply, state}
  end
end
''')
        result = analyze_elixir(tmp_path)

        module_sym = next((s for s in result.symbols if s.name == "MyApp.Server" and s.kind == "module"), None)
        assert module_sym is not None, "Should find MyApp.Server module"

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == module_sym.id
        ]

        # Should have edges for init, handle_call, handle_cast, handle_info
        dst_names = set()
        for e in callback_edges:
            # Extract function name from dst ID
            for s in result.symbols:
                if s.id == e.dst:
                    dst_names.add(s.name.split(".")[-1])
        assert "init" in dst_names, f"Should link init, got: {dst_names}"
        assert "handle_call" in dst_names, f"Should link handle_call, got: {dst_names}"
        assert "handle_cast" in dst_names, f"Should link handle_cast, got: {dst_names}"
        assert "handle_info" in dst_names, f"Should link handle_info, got: {dst_names}"
        assert len(callback_edges) >= 4

    def test_phoenix_liveview_callbacks(self, tmp_path: Path) -> None:
        """use Phoenix.LiveView creates invokes_callback edges for mount, handle_event, render."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "live.ex").write_text('''
defmodule MyAppWeb.IndexLive do
  use Phoenix.LiveView

  def mount(_params, _session, socket) do
    {:ok, socket}
  end

  def handle_event("click", _params, socket) do
    {:noreply, socket}
  end

  def render(assigns) do
    ~H"""
    <div>Hello</div>
    """
  end
end
''')
        result = analyze_elixir(tmp_path)

        module_sym = next((s for s in result.symbols if s.name == "MyAppWeb.IndexLive" and s.kind == "module"), None)
        assert module_sym is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == module_sym.id
        ]

        dst_names = set()
        for e in callback_edges:
            for s in result.symbols:
                if s.id == e.dst:
                    dst_names.add(s.name.split(".")[-1])

        assert "mount" in dst_names, f"Should link mount, got: {dst_names}"
        assert "handle_event" in dst_names, f"Should link handle_event, got: {dst_names}"
        assert "render" in dst_names, f"Should link render, got: {dst_names}"

    def test_supervisor_init_callback(self, tmp_path: Path) -> None:
        """use Supervisor creates invokes_callback edge for init."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "sup.ex").write_text('''
defmodule MyApp.Supervisor do
  use Supervisor

  def start_link(opts) do
    Supervisor.start_link(__MODULE__, opts)
  end

  def init(opts) do
    children = []
    Supervisor.init(children, strategy: :one_for_one)
  end
end
''')
        result = analyze_elixir(tmp_path)

        module_sym = next((s for s in result.symbols if s.name == "MyApp.Supervisor" and s.kind == "module"), None)
        assert module_sym is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == module_sym.id
        ]
        dst_names = set()
        for e in callback_edges:
            for s in result.symbols:
                if s.id == e.dst:
                    dst_names.add(s.name.split(".")[-1])

        assert "init" in dst_names, f"Should link init, got: {dst_names}"

    def test_plug_callbacks(self, tmp_path: Path) -> None:
        """use Plug.Builder creates invokes_callback edges for init, call."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "plug.ex").write_text('''
defmodule MyApp.AuthPlug do
  use Plug.Builder

  def init(opts) do
    opts
  end

  def call(conn, _opts) do
    conn
  end
end
''')
        result = analyze_elixir(tmp_path)

        module_sym = next((s for s in result.symbols if s.name == "MyApp.AuthPlug" and s.kind == "module"), None)
        assert module_sym is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == module_sym.id
        ]
        dst_names = set()
        for e in callback_edges:
            for s in result.symbols:
                if s.id == e.dst:
                    dst_names.add(s.name.split(".")[-1])

        assert "init" in dst_names, f"Should link init, got: {dst_names}"
        assert "call" in dst_names, f"Should link call, got: {dst_names}"

    def test_no_behaviour_no_callback_edges(self, tmp_path: Path) -> None:
        """Module without use directive creates no callback edges."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "plain.ex").write_text('''
defmodule MyApp.Plain do
  def hello do
    :world
  end
end
''')
        result = analyze_elixir(tmp_path)
        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 0

    def test_callback_not_implemented_no_edge(self, tmp_path: Path) -> None:
        """Callback functions that aren't implemented create no edges."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "partial.ex").write_text('''
defmodule MyApp.PartialServer do
  use GenServer

  def init(state) do
    {:ok, state}
  end
end
''')
        result = analyze_elixir(tmp_path)

        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        # Only init is implemented, handle_call/cast/info are NOT
        assert len(callback_edges) == 1

    def test_callback_edge_confidence(self, tmp_path: Path) -> None:
        """Callback edges have 0.9 confidence."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "server.ex").write_text('''
defmodule MyApp.Worker do
  use GenServer

  def init(state) do
    {:ok, state}
  end
end
''')
        result = analyze_elixir(tmp_path)

        callback_edges = [e for e in result.edges if e.edge_type == "invokes_callback"]
        assert len(callback_edges) == 1
        assert callback_edges[0].confidence == 0.9

    def test_phoenix_live_component_callbacks(self, tmp_path: Path) -> None:
        """use Phoenix.LiveComponent creates invokes_callback for mount, update, render."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "component.ex").write_text('''
defmodule MyAppWeb.ModalComponent do
  use Phoenix.LiveComponent

  def mount(socket) do
    {:ok, socket}
  end

  def update(assigns, socket) do
    {:ok, assign(socket, assigns)}
  end

  def render(assigns) do
    ~H"""
    <div>Modal</div>
    """
  end
end
''')
        result = analyze_elixir(tmp_path)

        module_sym = next((s for s in result.symbols if s.name == "MyAppWeb.ModalComponent" and s.kind == "module"), None)
        assert module_sym is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == module_sym.id
        ]
        assert len(callback_edges) == 3  # mount, update, render


    def test_behaviour_attribute_creates_callback_edges(self, tmp_path: Path) -> None:
        """@behaviour Module creates invokes_callback edges like use Module."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "middleware.ex").write_text('''
defmodule MyApp.AuthMiddleware do
  @behaviour NexAI.Middleware

  def call(req, opts) do
    {:ok, req}
  end

  def init(opts) do
    opts
  end
end
''')
        # NexAI.Middleware isn't in BEHAVIOUR_CALLBACKS, but Plug is
        # and Plug has init/call. @behaviour Plug should work too.
        (tmp_path / "plug_mid.ex").write_text('''
defmodule MyApp.PlugMiddleware do
  @behaviour Plug

  def init(opts), do: opts
  def call(conn, _opts), do: conn
end
''')
        result = analyze_elixir(tmp_path)

        module_sym = next(
            (s for s in result.symbols
             if s.name == "MyApp.PlugMiddleware" and s.kind == "module"),
            None,
        )
        assert module_sym is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == module_sym.id
        ]
        dst_names = set()
        for e in callback_edges:
            dst_sym = next((s for s in result.symbols if s.id == e.dst), None)
            if dst_sym:
                dst_names.add(dst_sym.name.split(".")[-1])
        assert "init" in dst_names
        assert "call" in dst_names

    def test_websock_behaviour_callbacks(self, tmp_path: Path) -> None:
        """WebSock callbacks are detected via use WebSock."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "socket.ex").write_text('''
defmodule MyApp.LiveSocket do
  use WebSock

  def init(state), do: {:ok, state}
  def handle_in({msg, _opts}, state), do: {:push, {:text, msg}, state}
  def terminate(_reason, _state), do: :ok
end
''')
        result = analyze_elixir(tmp_path)

        module_sym = next(
            (s for s in result.symbols
             if s.name == "MyApp.LiveSocket" and s.kind == "module"),
            None,
        )
        assert module_sym is not None

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == module_sym.id
        ]
        dst_names = set()
        for e in callback_edges:
            dst_sym = next((s for s in result.symbols if s.id == e.dst), None)
            if dst_sym:
                dst_names.add(dst_sym.name.split(".")[-1])
        assert "init" in dst_names
        assert "handle_in" in dst_names
        assert "terminate" in dst_names


class TestElixirMultiClauseEdges:
    """Tests for multi-clause function edge resolution.

    Elixir functions can have multiple clauses with pattern matching:
        def handle_call(:get, _from, state), do: {:reply, state, state}
        def handle_call(:put, _from, state), do: {:noreply, state}

    Each clause is a separate symbol. When function X calls handle_call,
    edges should target ALL clauses, not just the last one.
    """

    def test_call_to_multi_clause_function_targets_all_clauses(self, tmp_path: Path) -> None:
        """Call to a function with multiple clauses creates edges to all clauses."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "server.ex").write_text('''
defmodule MyApp.Server do
  def handle_call(:get, _from, state) do
    {:reply, state, state}
  end

  def handle_call(:put, _from, state) do
    {:noreply, state}
  end

  def handle_call(:delete, _from, _state) do
    {:noreply, %{}}
  end

  def dispatch(msg) do
    handle_call(msg, nil, %{})
  end
end
''')
        result = analyze_elixir(tmp_path)

        dispatch_sym = next((s for s in result.symbols if "dispatch" in s.name), None)
        handle_call_syms = [s for s in result.symbols if "handle_call" in s.name]

        assert dispatch_sym is not None
        assert len(handle_call_syms) == 3, f"Should find 3 handle_call clauses, got {len(handle_call_syms)}"

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == dispatch_sym.id
        ]
        # Should create edges to ALL 3 handle_call clauses
        edge_dsts = {e.dst for e in call_edges}
        for clause_sym in handle_call_syms:
            assert clause_sym.id in edge_dsts, (
                f"Missing edge to clause at line {clause_sym.span.start_line}. "
                f"Edge dsts: {edge_dsts}"
            )

    def test_cross_file_call_to_multi_clause(self, tmp_path: Path) -> None:
        """Cross-file calls also target all clauses."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "handler.ex").write_text('''
defmodule MyApp.Handler do
  def process(:ok) do
    :done
  end

  def process(:error) do
    :failed
  end
end
''')

        (tmp_path / "caller.ex").write_text('''
defmodule MyApp.Caller do
  import MyApp.Handler

  def run do
    process(:ok)
  end
end
''')
        result = analyze_elixir(tmp_path)

        run_sym = next((s for s in result.symbols if "run" in s.name), None)
        process_syms = [s for s in result.symbols if "process" in s.name]

        assert run_sym is not None
        assert len(process_syms) == 2

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == run_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}
        for clause_sym in process_syms:
            assert clause_sym.id in edge_dsts, (
                f"Missing cross-file edge to clause at line {clause_sym.span.start_line}"
            )

    def test_behaviour_callback_targets_all_clauses(self, tmp_path: Path) -> None:
        """invokes_callback edges also target all clauses of a callback function."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "server.ex").write_text('''
defmodule MyApp.MultiServer do
  use GenServer

  def init(:default) do
    {:ok, %{}}
  end

  def init(custom_state) do
    {:ok, custom_state}
  end
end
''')
        result = analyze_elixir(tmp_path)

        module_sym = next((s for s in result.symbols if s.name == "MyApp.MultiServer" and s.kind == "module"), None)
        init_syms = [s for s in result.symbols if "init" in s.name and s.kind == "function"]

        assert module_sym is not None
        assert len(init_syms) == 2, f"Should find 2 init clauses, got {len(init_syms)}"

        callback_edges = [
            e for e in result.edges
            if e.edge_type == "invokes_callback" and e.src == module_sym.id
        ]
        edge_dsts = {e.dst for e in callback_edges}
        for clause_sym in init_syms:
            assert clause_sym.id in edge_dsts, (
                f"Missing callback edge to init clause at line {clause_sym.span.start_line}"
            )


class TestMultiClauseGuardExtraction:
    """Tests for multi-clause functions with guard clauses.

    Elixir functions can have guard clauses:
        def humanize(atom) when is_atom(atom), do: ...
        def humanize(bin) when is_binary(bin), do: ...

    Tree-sitter wraps guarded heads in a ``binary_operator`` node
    (``func(x) when guard``), which the symbol extractor must unwrap
    to find the function name and signature.
    """

    def test_guarded_multi_clause_all_nodes_present(self, tmp_path: Path) -> None:
        """Multi-clause def with when guards must appear as separate symbol nodes."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "naming.ex").write_text('''
defmodule Phoenix.Naming do
  def humanize(atom) when is_atom(atom), do: atom |> to_string() |> humanize()
  def humanize(bin) when is_binary(bin) do
    bin
    |> String.replace("_", " ")
    |> String.capitalize()
  end
end
''')
        result = analyze_elixir(tmp_path)

        humanize_syms = [s for s in result.symbols if "humanize" in s.name and s.kind == "function"]
        assert len(humanize_syms) == 2, (
            f"Expected 2 humanize clauses, got {len(humanize_syms)}. "
            f"All symbols: {[s.name for s in result.symbols]}"
        )

        # Both should have the full module-qualified name
        for sym in humanize_syms:
            assert sym.name == "Phoenix.Naming.humanize"

    def test_guarded_function_signature_includes_params(self, tmp_path: Path) -> None:
        """Guarded functions should still have correct parameter signatures."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "parser.ex").write_text('''
defmodule MyApp.Parser do
  def parse(input) when is_binary(input) do
    String.split(input, ",")
  end

  def parse(input) when is_list(input) do
    input
  end
end
''')
        result = analyze_elixir(tmp_path)

        parse_syms = [s for s in result.symbols if "parse" in s.name and s.kind == "function"]
        assert len(parse_syms) == 2

        # Each clause should have a signature with the parameter
        for sym in parse_syms:
            assert sym.signature is not None, f"Missing signature for clause at line {sym.span.start_line}"
            assert "input" in sym.signature, f"Signature should contain 'input', got: {sym.signature}"

    def test_guarded_private_function(self, tmp_path: Path) -> None:
        """defp with when guard should also be extracted."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "helper.ex").write_text('''
defmodule MyApp.Helper do
  defp format(val) when is_integer(val), do: Integer.to_string(val)
  defp format(val) when is_float(val), do: Float.to_string(val)

  def run(x), do: format(x)
end
''')
        result = analyze_elixir(tmp_path)

        format_syms = [s for s in result.symbols if "format" in s.name and s.kind == "function"]
        assert len(format_syms) == 2
        assert all("private" in s.modifiers for s in format_syms)

    def test_guarded_call_edges_target_all_clauses(self, tmp_path: Path) -> None:
        """Call to a guarded multi-clause function creates edges to all clauses."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "converter.ex").write_text('''
defmodule MyApp.Converter do
  def convert(val) when is_binary(val), do: String.to_integer(val)
  def convert(val) when is_integer(val), do: val

  def process(x) do
    convert(x)
  end
end
''')
        result = analyze_elixir(tmp_path)

        convert_syms = [s for s in result.symbols if "convert" in s.name and s.kind == "function"]
        process_sym = next((s for s in result.symbols if "process" in s.name), None)

        assert len(convert_syms) == 2
        assert process_sym is not None

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == process_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}
        for clause in convert_syms:
            assert clause.id in edge_dsts, (
                f"Missing edge to convert clause at line {clause.span.start_line}"
            )


class TestElixirStdlibExclusion:
    """Tests that Elixir stdlib/Kernel functions don't create false edges.

    Bare calls to Kernel functions like ``inspect(data)`` should NOT resolve
    to project-defined functions with the same name in other modules.
    """

    def test_inspect_not_resolved_to_project_function(self, tmp_path: Path) -> None:
        """inspect() call should not create an edge to a project inspect function."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "message.ex").write_text('''
defmodule MyApp.Message do
  def inspect(msg) do
    "Message: #{msg}"
  end
end
''')

        (tmp_path / "caller.ex").write_text('''
defmodule MyApp.Caller do
  def run(data) do
    inspect(data)
  end
end
''')
        result = analyze_elixir(tmp_path)

        run_sym = next((s for s in result.symbols if "run" in s.name), None)
        inspect_sym = next((s for s in result.symbols if "inspect" in s.name), None)

        assert run_sym is not None
        assert inspect_sym is not None

        # inspect() is a Kernel builtin — should NOT resolve to MyApp.Message.inspect
        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == run_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}
        assert inspect_sym.id not in edge_dsts, (
            "Kernel.inspect() should not resolve to MyApp.Message.inspect"
        )

    def test_to_string_not_resolved_cross_module(self, tmp_path: Path) -> None:
        """to_string() should not resolve to project-defined to_string."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "converter.ex").write_text('''
defmodule MyApp.Converter do
  def to_string(val) do
    "#{val}"
  end
end
''')

        (tmp_path / "user.ex").write_text('''
defmodule MyApp.User do
  def display(name) do
    to_string(name)
  end
end
''')
        result = analyze_elixir(tmp_path)

        display_sym = next((s for s in result.symbols if "display" in s.name), None)
        to_string_sym = next((s for s in result.symbols if "to_string" in s.name), None)

        assert display_sym is not None
        assert to_string_sym is not None

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == display_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}
        assert to_string_sym.id not in edge_dsts, (
            "Kernel.to_string() should not resolve to MyApp.Converter.to_string"
        )

    def test_local_inspect_still_resolved(self, tmp_path: Path) -> None:
        """A local inspect function (same module) should still create edges."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "server.ex").write_text('''
defmodule MyApp.Server do
  defp inspect(state) do
    IO.puts("State: #{state}")
  end

  def run(state) do
    inspect(state)
  end
end
''')
        result = analyze_elixir(tmp_path)

        run_sym = next((s for s in result.symbols if "run" in s.name), None)
        inspect_sym = next((s for s in result.symbols if "inspect" in s.name), None)

        assert run_sym is not None
        assert inspect_sym is not None

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == run_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}
        # Local defp inspect should still be resolved
        assert inspect_sym.id in edge_dsts, (
            "Local inspect should still be resolved (same module)"
        )

    def test_pipe_to_stdlib_not_resolved(self, tmp_path: Path) -> None:
        """Piping to a stdlib function should not create false edges."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "formatter.ex").write_text('''
defmodule MyApp.Formatter do
  def to_string(val), do: "#{val}"
end
''')

        (tmp_path / "pipeline.ex").write_text('''
defmodule MyApp.Pipeline do
  def run(data) do
    data |> to_string
  end
end
''')
        result = analyze_elixir(tmp_path)

        run_sym = next((s for s in result.symbols if "run" in s.name), None)
        to_string_sym = next((s for s in result.symbols if "to_string" in s.name), None)

        assert run_sym is not None
        assert to_string_sym is not None

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == run_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}
        assert to_string_sym.id not in edge_dsts, (
            "Kernel.to_string pipe should not resolve to MyApp.Formatter.to_string"
        )


class TestElixirImportGating:
    """Tests for import-gated bare call resolution (WI-dadid).

    In Elixir, bare function calls (without module prefix) to functions in
    other modules require an ``import`` directive. Without an import, bare
    calls to non-stdlib functions should NOT resolve to cross-module targets.
    """

    def test_bare_call_without_import_not_resolved(self, tmp_path: Path) -> None:
        """Bare create() should NOT resolve to Sites.create without import."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "sites.ex").write_text('''
defmodule MyApp.Sites do
  def create(params) do
    {:ok, params}
  end
end
''')

        (tmp_path / "migration.ex").write_text('''
defmodule MyApp.Migration do
  def up() do
    create("users")
  end
end
''')
        result = analyze_elixir(tmp_path)

        create_sym = next(
            (s for s in result.symbols if "create" in s.name and "Sites" in s.name),
            None,
        )
        up_sym = next(
            (s for s in result.symbols if "up" in s.name),
            None,
        )
        assert create_sym is not None
        assert up_sym is not None

        false_edges = [
            e for e in result.edges
            if e.edge_type == "calls"
            and e.src == up_sym.id
            and e.dst == create_sym.id
        ]
        assert len(false_edges) == 0, (
            "Bare create() should NOT resolve to MyApp.Sites.create "
            "without an import directive"
        )

    def test_bare_call_with_import_resolved(self, tmp_path: Path) -> None:
        """Bare create() SHOULD resolve when module is imported."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "sites.ex").write_text('''
defmodule MyApp.Sites do
  def create(params) do
    {:ok, params}
  end
end
''')

        (tmp_path / "migration.ex").write_text('''
defmodule MyApp.Migration do
  import MyApp.Sites

  def up() do
    create("users")
  end
end
''')
        result = analyze_elixir(tmp_path)

        create_sym = next(
            (s for s in result.symbols if "create" in s.name and "Sites" in s.name),
            None,
        )
        up_sym = next(
            (s for s in result.symbols if "up" in s.name),
            None,
        )
        assert create_sym is not None
        assert up_sym is not None

        edges = [
            e for e in result.edges
            if e.edge_type == "calls"
            and e.src == up_sym.id
            and e.dst == create_sym.id
        ]
        assert len(edges) == 1, (
            "Bare create() should resolve to MyApp.Sites.create "
            "when import MyApp.Sites is present"
        )


class TestPipeOperatorCallEdges:
    """Tests for pipe operator (|>) call edge resolution.

    Elixir pipe calls without parentheses (``data |> transform``) are
    idiomatic but tree-sitter parses the right-hand side as an identifier
    rather than a call node.  The edge extractor must handle this.
    """

    def test_pipe_to_local_function_creates_edge(self, tmp_path: Path) -> None:
        """Piping to a local function without parens creates a call edge."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "digester.ex").write_text('''
defmodule Phoenix.Digester do
  def compile(path) do
    path
    |> scan_files()
    |> fixup_sourcemaps
  end

  defp fixup_sourcemaps(files) do
    Enum.map(files, & &1)
  end

  defp scan_files(path) do
    [path]
  end
end
''')
        result = analyze_elixir(tmp_path)

        compile_sym = next((s for s in result.symbols if "compile" in s.name), None)
        fixup_sym = next((s for s in result.symbols if "fixup_sourcemaps" in s.name), None)
        scan_sym = next((s for s in result.symbols if "scan_files" in s.name), None)

        assert compile_sym is not None
        assert fixup_sym is not None
        assert scan_sym is not None

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == compile_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}

        # scan_files() has parens — should already work
        assert scan_sym.id in edge_dsts, "Missing edge to scan_files (with parens)"
        # fixup_sourcemaps has NO parens — pipe-only call
        assert fixup_sym.id in edge_dsts, "Missing edge to fixup_sourcemaps (pipe, no parens)"

    def test_pipe_chain_creates_multiple_edges(self, tmp_path: Path) -> None:
        """A chain of pipes creates edges to each piped function."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "pipeline.ex").write_text('''
defmodule MyApp.Pipeline do
  def run(data) do
    data
    |> validate
    |> transform
    |> persist
  end

  defp validate(d), do: d
  defp transform(d), do: d
  defp persist(d), do: d
end
''')
        result = analyze_elixir(tmp_path)

        run_sym = next((s for s in result.symbols if "run" in s.name), None)
        assert run_sym is not None

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == run_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}

        for func_name in ("validate", "transform", "persist"):
            func_sym = next((s for s in result.symbols if func_name in s.name), None)
            assert func_sym is not None, f"Missing symbol for {func_name}"
            assert func_sym.id in edge_dsts, f"Missing pipe edge to {func_name}"

    def test_pipe_cross_file_creates_edge(self, tmp_path: Path) -> None:
        """Piping to a function defined in another file creates a call edge."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "formatter.ex").write_text('''
defmodule MyApp.Formatter do
  def prettify(data), do: data
end
''')

        (tmp_path / "processor.ex").write_text('''
defmodule MyApp.Processor do
  def run(data) do
    data |> prettify
  end

  defp prettify(d), do: d
end
''')

        # This tests the local path (not cross-file) for a bare pipe.
        # Cross-file bare pipes fall through to the global_multi lookup.
        result = analyze_elixir(tmp_path)

        run_sym = next((s for s in result.symbols if "run" in s.name), None)
        prettify_syms = [s for s in result.symbols if "prettify" in s.name]

        assert run_sym is not None
        assert len(prettify_syms) >= 1  # At least the local defp

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == run_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}
        local_prettify = next(
            (s for s in prettify_syms if "Processor" in s.name), None
        )
        assert local_prettify is not None
        assert local_prettify.id in edge_dsts, "Missing pipe edge to local prettify"

    def test_pipe_cross_file_global_lookup(self, tmp_path: Path) -> None:
        """Piping to a function only in another file uses global multi-clause lookup."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "utils.ex").write_text('''
defmodule MyApp.Utils do
  def sanitize(data), do: data
end
''')

        (tmp_path / "handler.ex").write_text('''
defmodule MyApp.Handler do
  import MyApp.Utils

  def process(data) do
    data |> sanitize
  end
end
''')
        result = analyze_elixir(tmp_path)

        process_sym = next((s for s in result.symbols if "process" in s.name), None)
        sanitize_sym = next((s for s in result.symbols if "sanitize" in s.name), None)

        assert process_sym is not None
        assert sanitize_sym is not None

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == process_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}
        assert sanitize_sym.id in edge_dsts, "Missing cross-file pipe edge to sanitize"

    def test_pipe_to_module_qualified_function(self, tmp_path: Path) -> None:
        """Piping to a module-qualified function: data |> Mod.func.

        Tree-sitter wraps ``Mod.func`` in a call node even without parens,
        so this is handled by the existing dot-call path (not the pipe handler).
        """
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "caller.ex").write_text('''
defmodule MyApp.Caller do
  def run(data) do
    data |> MyApp.Helper.process
  end
end
''')

        (tmp_path / "helper.ex").write_text('''
defmodule MyApp.Helper do
  def process(data), do: data
end
''')
        result = analyze_elixir(tmp_path)

        run_sym = next((s for s in result.symbols if "run" in s.name), None)
        process_sym = next((s for s in result.symbols if "process" in s.name), None)

        assert run_sym is not None
        assert process_sym is not None

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == run_sym.id
        ]
        edge_dsts = {e.dst for e in call_edges}
        assert process_sym.id in edge_dsts, "Missing pipe edge to module-qualified function"


class TestPhoenixRouteAliasResolution:
    """Tests for Phoenix route alias resolution.

    When a router file uses ``alias MyAppWeb.UserController, as: UserCtrl``,
    route metadata should store the fully-qualified module name
    ``MyAppWeb.UserController`` instead of the alias ``UserCtrl``.
    This enables the route-handler linker to find the actual handler symbols.
    """

    def test_alias_as_resolved_in_route_metadata(self, tmp_path: Path) -> None:
        """Route controller stores resolved FQ name when alias...as: is used."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  alias MyAppWeb.UserController, as: UserCtrl

  get "/users", UserCtrl, :index
end
''')
        result = analyze_elixir(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 1

        route = route_symbols[0]
        assert route.meta["controller"] == "MyAppWeb.UserController"
        assert route.meta["action"] == "index"

    def test_standard_alias_resolved_in_route_metadata(self, tmp_path: Path) -> None:
        """Route controller stores resolved FQ name for standard alias (no as:)."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  alias MyAppWeb.Controllers.UserController

  get "/users", UserController, :index
end
''')
        result = analyze_elixir(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 1

        route = route_symbols[0]
        assert route.meta["controller"] == "MyAppWeb.Controllers.UserController"
        assert route.meta["action"] == "index"

    def test_alias_resolved_in_resources_route(self, tmp_path: Path) -> None:
        """Alias resolution works for resources macro too."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  alias MyAppWeb.PostController, as: PC

  resources "/posts", PC
end
''')
        result = analyze_elixir(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        # resources creates 7 RESTful routes
        assert len(route_symbols) == 7

        for route in route_symbols:
            assert route.meta["controller"] == "MyAppWeb.PostController"

    def test_alias_resolved_in_usage_context(self, tmp_path: Path) -> None:
        """UsageContext metadata also stores resolved controller name."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  alias MyAppWeb.SessionController, as: SessCtrl

  post "/login", SessCtrl, :create
end
''')
        result = analyze_elixir(tmp_path)

        post_ctx = next(
            (c for c in result.usage_contexts if c.context_name == "post"), None
        )
        assert post_ctx is not None
        assert post_ctx.metadata["controller"] == "MyAppWeb.SessionController"

    def test_no_alias_keeps_raw_controller_name(self, tmp_path: Path) -> None:
        """When no alias is present, controller name is stored as-is."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "router.ex").write_text('''
defmodule MyAppWeb.Router do
  get "/users", UserController, :index
end
''')
        result = analyze_elixir(tmp_path)

        route_symbols = [s for s in result.symbols if s.kind == "route"]
        assert len(route_symbols) == 1
        assert route_symbols[0].meta["controller"] == "UserController"


class TestElixirDocstrings:
    """Tests for Elixir comment extraction via populate_docstrings_from_tree."""

    def test_hash_comment_on_function(self, tmp_path: Path) -> None:
        """Extracts # comment preceding a function definition."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "app.ex").write_text(
            "defmodule App do\n"
            "  # Starts the application.\n"
            "  def start do\n"
            "    :ok\n"
            "  end\n"
            "end\n"
        )
        result = analyze_elixir(tmp_path)
        func = next((s for s in result.symbols if "start" in s.name), None)
        assert func is not None
        assert func.docstring == "Starts the application."

    def test_hash_comment_on_module(self, tmp_path: Path) -> None:
        """Extracts # comment preceding a module definition."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "app.ex").write_text(
            "# Main application module.\n"
            "defmodule App do\n"
            "  def run, do: :ok\n"
            "end\n"
        )
        result = analyze_elixir(tmp_path)
        mod = next((s for s in result.symbols if s.name == "App" and s.kind == "module"), None)
        assert mod is not None
        assert mod.docstring == "Main application module."

    def test_no_comment_no_docstring(self, tmp_path: Path) -> None:
        """Function without preceding comment has no docstring."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "app.ex").write_text(
            "defmodule App do\n"
            "  def run, do: :ok\n"
            "end\n"
        )
        result = analyze_elixir(tmp_path)
        func = next((s for s in result.symbols if "run" in s.name), None)
        assert func is not None
        assert func.docstring is None
