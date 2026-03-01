"""Branch coverage tests for elixir.py analyzer.

Tests specific branch paths in the Elixir analyzer that may not be covered
by the main test suite. Focuses on:
- Alias and import hint extraction
- Module name extraction (nested modules)
- Function/macro name and signature extraction
- Phoenix route detection
- Call edge resolution with alias hints
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def make_elixir_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an Elixir file with given content."""
    (tmp_path / name).write_text(content)


def analyze(tmp_path: Path) -> dict:
    """Run behavior map and return parsed JSON result."""
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    return json.loads(out_path.read_text())


class TestElixirModuleExtraction:
    """Branch coverage for module extraction."""

    def test_simple_module(self, tmp_path: Path) -> None:
        """Test simple defmodule extraction."""
        make_elixir_file(tmp_path, "hello.ex", """
defmodule Hello do
  def greet, do: "Hello"
end
""")
        data = analyze(tmp_path)
        mod = next((n for n in data["nodes"] if n["name"] == "Hello"), None)
        assert mod is not None
        assert mod["kind"] == "module"

    def test_nested_module(self, tmp_path: Path) -> None:
        """Test nested module extraction."""
        make_elixir_file(tmp_path, "nested.ex", """
defmodule MyApp do
  defmodule Services do
    defmodule UserService do
      def get_user(id), do: id
    end
  end
end
""")
        data = analyze(tmp_path)
        # Should find nested module with full path
        mod = next((n for n in data["nodes"] if "UserService" in n["name"]), None)
        assert mod is not None
        assert "MyApp" in mod["name"] or "Services" in mod["name"]


class TestElixirFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_def_with_params(self, tmp_path: Path) -> None:
        """Test def with parameters."""
        make_elixir_file(tmp_path, "math.ex", """
defmodule Math do
  def add(a, b), do: a + b
end
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if "add" in n["name"]), None)
        assert func is not None
        assert func["kind"] == "function"
        assert "a" in func["signature"]
        assert "b" in func["signature"]

    def test_defp_private_function(self, tmp_path: Path) -> None:
        """Test defp (private function) extraction."""
        make_elixir_file(tmp_path, "private.ex", """
defmodule Private do
  defp helper(x), do: x * 2
end
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if "helper" in n["name"]), None)
        assert func is not None
        assert func["kind"] == "function"

    def test_def_no_params(self, tmp_path: Path) -> None:
        """Test def with no parameters."""
        make_elixir_file(tmp_path, "simple.ex", """
defmodule Simple do
  def constant, do: 42
end
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if "constant" in n["name"]), None)
        assert func is not None
        assert func["signature"] == "()"


class TestElixirMacroExtraction:
    """Branch coverage for macro extraction."""

    def test_defmacro(self, tmp_path: Path) -> None:
        """Test defmacro extraction."""
        make_elixir_file(tmp_path, "macros.ex", """
defmodule Macros do
  defmacro my_macro(expr), do: expr
end
""")
        data = analyze(tmp_path)
        macro = next((n for n in data["nodes"] if "my_macro" in n["name"]), None)
        assert macro is not None
        assert macro["kind"] == "macro"

    def test_defmacrop_private(self, tmp_path: Path) -> None:
        """Test defmacrop (private macro) extraction."""
        make_elixir_file(tmp_path, "private_macro.ex", """
defmodule PrivateMacro do
  defmacrop helper_macro(x), do: x
end
""")
        data = analyze(tmp_path)
        macro = next((n for n in data["nodes"] if "helper_macro" in n["name"]), None)
        assert macro is not None
        assert macro["kind"] == "macro"


class TestElixirAliasHints:
    """Branch coverage for alias and import hint extraction."""

    def test_simple_alias(self, tmp_path: Path) -> None:
        """Test simple alias directive."""
        make_elixir_file(tmp_path, "app.ex", """
defmodule App do
  alias MyApp.Services.UserService

  def run, do: :ok
end
""")
        data = analyze(tmp_path)
        # Should find module
        mod = next((n for n in data["nodes"] if n["name"] == "App"), None)
        assert mod is not None

    def test_alias_with_as(self, tmp_path: Path) -> None:
        """Test alias with as: option."""
        make_elixir_file(tmp_path, "alias_as.ex", """
defmodule AliasAs do
  alias MyApp.Services.UserService, as: Svc

  def run, do: :ok
end
""")
        data = analyze(tmp_path)
        mod = next((n for n in data["nodes"] if n["name"] == "AliasAs"), None)
        assert mod is not None

    def test_import_directive(self, tmp_path: Path) -> None:
        """Test import directive extraction."""
        make_elixir_file(tmp_path, "importer.ex", """
defmodule Importer do
  import MyApp.Math

  def run, do: :ok
end
""")
        data = analyze(tmp_path)
        # Should create import edge
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("MyApp.Math" in e["dst"] for e in edges)


class TestElixirUseDirective:
    """Branch coverage for use directive."""

    def test_use_module(self, tmp_path: Path) -> None:
        """Test use directive creates import edge."""
        make_elixir_file(tmp_path, "use_test.ex", """
defmodule UseTest do
  use GenServer

  def run, do: :ok
end
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("GenServer" in e["dst"] for e in edges)


class TestElixirCallResolution:
    """Branch coverage for call resolution."""

    def test_local_function_call(self, tmp_path: Path) -> None:
        """Test call to local function."""
        make_elixir_file(tmp_path, "local_call.ex", """
defmodule LocalCall do
  def helper, do: 1

  def main do
    helper()
  end
end
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("helper" in e["dst"] for e in edges)

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Test call to function in different module."""
        make_elixir_file(tmp_path, "utils.ex", """
defmodule Utils do
  def format(s), do: s
end
""")
        make_elixir_file(tmp_path, "app.ex", """
defmodule App do
  def run do
    format("test")
  end
end
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("format" in e["dst"] for e in edges)


class TestElixirPhoenixRoutes:
    """Branch coverage for Phoenix route detection."""

    def test_get_route(self, tmp_path: Path) -> None:
        """Test GET route detection."""
        make_elixir_file(tmp_path, "router.ex", """
defmodule MyApp.Router do
  get "/", PageController, :index
end
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        assert any("GET /" in r["name"] for r in routes)

    def test_post_route(self, tmp_path: Path) -> None:
        """Test POST route detection."""
        make_elixir_file(tmp_path, "router.ex", """
defmodule MyApp.Router do
  post "/users", UserController, :create
end
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        assert any("POST" in r["name"] and "users" in r["name"] for r in routes)

    def test_resources_route(self, tmp_path: Path) -> None:
        """Test resources macro creates multiple routes."""
        make_elixir_file(tmp_path, "router.ex", """
defmodule MyApp.Router do
  resources "/posts", PostController
end
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        # resources creates 7 RESTful routes
        post_routes = [r for r in routes if "posts" in r["name"]]
        assert len(post_routes) >= 7

    def test_put_route(self, tmp_path: Path) -> None:
        """Test PUT route detection."""
        make_elixir_file(tmp_path, "router.ex", """
defmodule MyApp.Router do
  put "/users/:id", UserController, :update
end
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        assert any("PUT" in r["name"] for r in routes)

    def test_delete_route(self, tmp_path: Path) -> None:
        """Test DELETE route detection."""
        make_elixir_file(tmp_path, "router.ex", """
defmodule MyApp.Router do
  delete "/users/:id", UserController, :destroy
end
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        assert any("DELETE" in r["name"] for r in routes)


class TestElixirSignatureExtraction:
    """Branch coverage for signature extraction."""

    def test_multiple_params(self, tmp_path: Path) -> None:
        """Test function with multiple parameters."""
        make_elixir_file(tmp_path, "multi.ex", """
defmodule Multi do
  def calculate(a, b, c), do: a + b + c
end
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if "calculate" in n["name"]), None)
        assert func is not None
        assert "a" in func["signature"]
        assert "b" in func["signature"]
        assert "c" in func["signature"]


class TestElixirMultipleFiles:
    """Branch coverage for multi-file analysis."""

    def test_multiple_modules(self, tmp_path: Path) -> None:
        """Test analysis of multiple Elixir files."""
        make_elixir_file(tmp_path, "mod1.ex", """
defmodule Mod1 do
  def func1, do: 1
end
""")
        make_elixir_file(tmp_path, "mod2.ex", """
defmodule Mod2 do
  def func2, do: 2
end
""")
        data = analyze(tmp_path)
        mod1 = next((n for n in data["nodes"] if n["name"] == "Mod1"), None)
        mod2 = next((n for n in data["nodes"] if n["name"] == "Mod2"), None)
        func1 = next((n for n in data["nodes"] if "func1" in n["name"]), None)
        func2 = next((n for n in data["nodes"] if "func2" in n["name"]), None)
        assert mod1 is not None
        assert mod2 is not None
        assert func1 is not None
        assert func2 is not None
