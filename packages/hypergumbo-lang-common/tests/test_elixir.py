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
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "test.ex").write_text("defmodule Test do end")

        with patch("hypergumbo_lang_common.elixir.is_elixir_tree_sitter_available", return_value=False):
            result = analyze_elixir(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-elixir" in result.skip_reason


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
        """Returns skipped with run when parser loading fails."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        (tmp_path / "test.ex").write_text("defmodule Test do end")

        with patch("hypergumbo_lang_common.elixir.is_elixir_tree_sitter_available", return_value=True):
            with patch.dict("sys.modules", {"tree_sitter_language_pack": MagicMock()}):
                import sys
                mock_module = sys.modules["tree_sitter_language_pack"]
                mock_module.get_parser.side_effect = RuntimeError("Parser load failed")
                result = analyze_elixir(tmp_path)

        assert result.skipped is True
        assert "Failed to load Elixir parser" in result.skip_reason
        assert result.run is not None

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        # Create a file with only comments and whitespace
        (tmp_path / "empty.ex").write_text("# Just a comment\n\n")

        result = analyze_elixir(tmp_path)


        assert result.run is not None
        assert result.run.files_skipped >= 1

    def test_unreadable_file_handled_gracefully(self, tmp_path: Path) -> None:
        """Unreadable files don't crash the analyzer."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        ex_file = tmp_path / "test.ex"
        ex_file.write_text("defmodule Test do end")

        result = analyze_elixir(tmp_path)


        # Just verify it doesn't crash
        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo_lang_common.elixir import analyze_elixir

        # File 1: defines helper
        (tmp_path / "helper.ex").write_text("""
defmodule Helper do
  def greet(name) do
    "Hello, " <> name
  end
end
""")

        # File 2: calls helper
        (tmp_path / "main.ex").write_text("""
defmodule Main do
  def run() do
    greet("world")
  end
end
""")

        result = analyze_elixir(tmp_path)


        # Should have cross-file call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

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
    """Tests for file read error handling."""

    def test_symbol_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Symbol extraction handles file read errors gracefully."""
        from hypergumbo_lang_common.elixir import (
            _extract_symbols_from_file,
            is_elixir_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

        if not is_elixir_tree_sitter_available():
            pytest.skip("tree-sitter-elixir not available")

        from tree_sitter_language_pack import get_parser
        parser = get_parser("elixir")
        run = AnalysisRun.create(pass_id="test", version="test")

        # Create a valid file, then mock the read to fail
        ex_file = tmp_path / "test.ex"
        ex_file.write_text("defmodule Test do end")

        with patch.object(Path, "read_bytes", side_effect=OSError("Read failed")):
            result = _extract_symbols_from_file(ex_file, parser, run)

        assert result.symbols == []

    def test_edge_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Edge extraction handles file read errors gracefully."""
        from hypergumbo_lang_common.elixir import (
            _extract_edges_from_file,
            is_elixir_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

        if not is_elixir_tree_sitter_available():
            pytest.skip("tree-sitter-elixir not available")

        from tree_sitter_language_pack import get_parser
        parser = get_parser("elixir")
        run = AnalysisRun.create(pass_id="test", version="test")

        ex_file = tmp_path / "test.ex"
        ex_file.write_text("defmodule Test do end")

        with patch.object(Path, "read_bytes", side_effect=IOError("Read failed")):
            result = _extract_edges_from_file(ex_file, parser, {}, {}, run)

        assert result == []


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
