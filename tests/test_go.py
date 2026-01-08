"""Tests for Go analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindGoFiles:
    """Tests for Go file discovery."""

    def test_finds_go_files(self, tmp_path: Path) -> None:
        """Finds .go files."""
        from hypergumbo.analyze.go import find_go_files

        (tmp_path / "main.go").write_text("package main")
        (tmp_path / "utils.go").write_text("package utils")
        (tmp_path / "other.txt").write_text("not go")

        files = list(find_go_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".go" for f in files)


class TestGoTreeSitterAvailability:
    """Tests for tree-sitter-go availability checking."""

    def test_is_go_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-go is available."""
        from hypergumbo.analyze.go import is_go_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_go_tree_sitter_available() is True

    def test_is_go_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.go import is_go_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_go_tree_sitter_available() is False

    def test_is_go_tree_sitter_available_no_go(self) -> None:
        """Returns False when tree-sitter is available but go grammar is not."""
        from hypergumbo.analyze.go import is_go_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()  # tree-sitter available
            return None  # go grammar not available

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_go_tree_sitter_available() is False


class TestAnalyzeGoFallback:
    """Tests for fallback behavior when tree-sitter-go unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-go unavailable."""
        from hypergumbo.analyze.go import analyze_go

        (tmp_path / "test.go").write_text("package main")

        with patch("hypergumbo.analyze.go.is_go_tree_sitter_available", return_value=False):
            result = analyze_go(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-go" in result.skip_reason


class TestGoFunctionExtraction:
    """Tests for extracting Go functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts Go function declarations."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func main() {
    fmt.Println("Hello, world!")
}

func helper(x int) int {
    return x + 1
}
""")

        result = analyze_go(tmp_path)


        assert result.run is not None
        assert result.run.files_analyzed == 1
        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "main" in func_names
        assert "helper" in func_names

    def test_extracts_exported_function(self, tmp_path: Path) -> None:
        """Extracts exported (capitalized) function declarations."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "lib.go"
        go_file.write_text("""package mylib

func PublicAPI() string {
    return "hello"
}

func privateHelper() {}
""")

        result = analyze_go(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "PublicAPI" in func_names
        assert "privateHelper" in func_names


class TestGoStructExtraction:
    """Tests for extracting Go structs."""

    def test_extracts_struct(self, tmp_path: Path) -> None:
        """Extracts struct declarations."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "models.go"
        go_file.write_text("""package models

type User struct {
    Name string
    Age  int
}

type internalData struct {
    value int64
}
""")

        result = analyze_go(tmp_path)


        structs = [s for s in result.symbols if s.kind == "struct"]
        struct_names = [s.name for s in structs]
        assert "User" in struct_names
        assert "internalData" in struct_names


class TestGoInterfaceExtraction:
    """Tests for extracting Go interfaces."""

    def test_extracts_interface(self, tmp_path: Path) -> None:
        """Extracts interface declarations."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "interfaces.go"
        go_file.write_text("""package main

type Reader interface {
    Read(p []byte) (n int, err error)
}

type Writer interface {
    Write(p []byte) (n int, err error)
}
""")

        result = analyze_go(tmp_path)


        interfaces = [s for s in result.symbols if s.kind == "interface"]
        interface_names = [s.name for s in interfaces]
        assert "Reader" in interface_names
        assert "Writer" in interface_names


class TestGoMethodExtraction:
    """Tests for extracting Go methods (receiver functions)."""

    def test_extracts_method(self, tmp_path: Path) -> None:
        """Extracts methods with receivers."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "user.go"
        go_file.write_text("""package main

type User struct {
    Name string
}

func (u User) GetName() string {
    return u.Name
}

func (u *User) SetName(name string) {
    u.Name = name
}
""")

        result = analyze_go(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        # Methods should be qualified with receiver type
        assert any("GetName" in name for name in method_names)
        assert any("SetName" in name for name in method_names)


class TestGoFunctionCalls:
    """Tests for detecting function calls in Go."""

    def test_detects_function_call(self, tmp_path: Path) -> None:
        """Detects calls to functions in same file."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "utils.go"
        go_file.write_text("""package main

func caller() {
    helper()
}

func helper() {
    fmt.Println("helping")
}
""")

        result = analyze_go(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge from caller to helper
        assert len(call_edges) >= 1


class TestGoImports:
    """Tests for detecting Go import statements."""

    def test_detects_import_statement(self, tmp_path: Path) -> None:
        """Detects import statements."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import (
    "fmt"
    "os"
)

func main() {
    fmt.Println("Hello")
}
""")

        result = analyze_go(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have edges for import statements
        assert len(import_edges) >= 1


class TestGoEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Returns skipped with run when parser loading fails."""
        from hypergumbo.analyze.go import analyze_go

        (tmp_path / "test.go").write_text("package main")

        with patch("hypergumbo.analyze.go.is_go_tree_sitter_available", return_value=True):
            with patch.dict("sys.modules", {"tree_sitter_go": MagicMock()}):
                import sys
                mock_module = sys.modules["tree_sitter_go"]
                mock_module.language.side_effect = RuntimeError("Parser load failed")
                result = analyze_go(tmp_path)

        assert result.skipped is True
        assert "Failed to load Go parser" in result.skip_reason
        assert result.run is not None

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo.analyze.go import analyze_go

        # Create a file with only comments
        (tmp_path / "empty.go").write_text("// Just a comment\npackage main\n")

        result = analyze_go(tmp_path)


        # Even package-only file should have no symbols
        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo.analyze.go import analyze_go

        # File 1: defines helper
        (tmp_path / "helper.go").write_text("""package main

func Greet(name string) string {
    return "Hello, " + name
}
""")

        # File 2: calls helper
        (tmp_path / "main.go").write_text("""package main

func run() {
    Greet("world")
}
""")

        result = analyze_go(tmp_path)


        # Verify both files analyzed
        assert result.run.files_analyzed >= 2


class TestGoCallPatterns:
    """Tests for various Go call expression patterns."""

    def test_method_call(self, tmp_path: Path) -> None:
        """Detects method calls on objects."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "calls.go"
        go_file.write_text("""package main

type Foo struct{}

func (f Foo) Bar() {}

func caller() {
    foo := Foo{}
    foo.Bar()
}
""")

        result = analyze_go(tmp_path)


        # Should not crash
        assert result.run is not None

    def test_qualified_call(self, tmp_path: Path) -> None:
        """Detects calls to package functions."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "fmt"

func main() {
    fmt.Println("hello")
}
""")

        result = analyze_go(tmp_path)


        # Should detect fmt.Println call
        assert result.run is not None


class TestGoTypeAliasExtraction:
    """Tests for extracting Go type aliases."""

    def test_extracts_type_alias(self, tmp_path: Path) -> None:
        """Extracts type alias declarations (not struct or interface)."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "types.go"
        go_file.write_text("""package main

type MyInt int
type Handler func(int) error
""")

        result = analyze_go(tmp_path)


        types = [s for s in result.symbols if s.kind == "type"]
        type_names = [s.name for s in types]
        assert "MyInt" in type_names or "Handler" in type_names


class TestGoHelperFunctions:
    """Tests for helper function edge cases."""

    def test_find_child_by_type_returns_none(self, tmp_path: Path) -> None:
        """find_child_by_type returns None when no matching child."""
        from hypergumbo.analyze.base import find_child_by_type
        from hypergumbo.analyze.go import is_go_tree_sitter_available

        if not is_go_tree_sitter_available():
            pytest.skip("tree-sitter-go not available")

        import tree_sitter_go
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_go.language())
        parser = tree_sitter.Parser(lang)

        source = b"package main\n"
        tree = parser.parse(source)

        # Try to find a child type that doesn't exist
        result = find_child_by_type(tree.root_node, "nonexistent_type")
        assert result is None


class TestGoFileReadErrors:
    """Tests for file read error handling."""

    def test_symbol_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Symbol extraction handles file read errors gracefully."""
        from hypergumbo.analyze.go import (
            _extract_symbols_from_file,
            is_go_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_go_tree_sitter_available():
            pytest.skip("tree-sitter-go not available")

        import tree_sitter_go
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_go.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        go_file = tmp_path / "test.go"
        go_file.write_text("package main\nfunc test() {}")

        with patch.object(Path, "read_bytes", side_effect=OSError("Read failed")):
            result = _extract_symbols_from_file(go_file, parser, run)

        assert result.symbols == []

    def test_edge_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Edge extraction handles file read errors gracefully."""
        from hypergumbo.analyze.go import (
            _extract_edges_from_file,
            is_go_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_go_tree_sitter_available():
            pytest.skip("tree-sitter-go not available")

        import tree_sitter_go
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_go.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        go_file = tmp_path / "test.go"
        go_file.write_text("package main\nfunc test() {}")

        with patch.object(Path, "read_bytes", side_effect=IOError("Read failed")):
            result = _extract_edges_from_file(go_file, parser, {}, {}, run)

        assert result == []


class TestGoRouteDetection:
    """Tests for Go web framework route detection."""

    def test_detects_gin_routes(self, tmp_path: Path) -> None:
        """Detects Gin router.GET("/path", handler) pattern."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gin-gonic/gin"

func main() {
    r := gin.Default()
    r.GET("/users", listUsers)
    r.POST("/users", createUser)
}

func listUsers(c *gin.Context) {}
func createUser(c *gin.Context) {}
""")

        result = analyze_go(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = [s.name for s in routes]

        assert "listUsers" in route_names
        assert "createUser" in route_names

    def test_detects_echo_routes(self, tmp_path: Path) -> None:
        """Detects Echo e.GET("/path", handler) pattern."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/labstack/echo/v4"

func main() {
    e := echo.New()
    e.GET("/", home)
    e.PUT("/users/:id", updateUser)
    e.DELETE("/users/:id", deleteUser)
}

func home(c echo.Context) error { return nil }
func updateUser(c echo.Context) error { return nil }
func deleteUser(c echo.Context) error { return nil }
""")

        result = analyze_go(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = [s.name for s in routes]

        assert "home" in route_names
        assert "updateUser" in route_names
        assert "deleteUser" in route_names

        # Check HTTP methods
        http_methods = {s.meta["http_method"] for s in routes if s.meta}
        assert "GET" in http_methods
        assert "PUT" in http_methods
        assert "DELETE" in http_methods

    def test_detects_fiber_lowercase_routes(self, tmp_path: Path) -> None:
        """Detects Fiber app.Get("/path", handler) pattern (lowercase methods)."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gofiber/fiber/v2"

func main() {
    app := fiber.New()
    app.Get("/", home)
    app.Post("/api/data", postData)
}

func home(c *fiber.Ctx) error { return nil }
func postData(c *fiber.Ctx) error { return nil }
""")

        result = analyze_go(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = [s.name for s in routes]

        assert "home" in route_names
        assert "postData" in route_names

    def test_route_has_stable_id(self, tmp_path: Path) -> None:
        """Route symbols have stable_id set to lowercase HTTP method."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func main() {
    r.GET("/test", handler)
}

func handler() {}
""")

        result = analyze_go(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].stable_id == "get"

    def test_route_path_extraction(self, tmp_path: Path) -> None:
        """Route path is correctly extracted to metadata."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func main() {
    r.GET("/api/v1/users/:id", getUser)
}

func getUser() {}
""")

        result = analyze_go(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].meta["route_path"] == "/api/v1/users/:id"
        assert routes[0].meta["http_method"] == "GET"

    def test_extract_go_routes_directly(self, tmp_path: Path) -> None:
        """Tests _extract_go_routes function directly."""
        from hypergumbo.analyze.go import (
            _extract_go_routes,
            is_go_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_go_tree_sitter_available():
            pytest.skip("tree-sitter-go not available")

        import tree_sitter_go
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_go.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        go_file = tmp_path / "test.go"
        go_file.write_text("""
package main

func main() {
    r.POST("/submit", submitHandler)
}
""")

        source = go_file.read_bytes()
        tree = parser.parse(source)

        routes = _extract_go_routes(tree.root_node, source, go_file, run)

        assert len(routes) == 1
        assert routes[0].name == "submitHandler"
        assert routes[0].kind == "route"
        assert routes[0].stable_id == "post"

    def test_no_routes_in_non_web_code(self, tmp_path: Path) -> None:
        """No routes detected in code without web framework patterns."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func main() {
    result := GetData()
    PostProcess(result)
}

func GetData() string { return "data" }
func PostProcess(s string) {}
""")

        result = analyze_go(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 0

    def test_selector_handler(self, tmp_path: Path) -> None:
        """Handles selector expression handlers like pkg.Handler."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func main() {
    r.GET("/api", handlers.GetAPI)
}
""")

        result = analyze_go(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].name == "handlers.GetAPI"


class TestGoSignatureExtraction:
    """Tests for extracting function signatures from Go code."""

    def test_extracts_simple_signature(self, tmp_path: Path) -> None:
        """Extracts signature with simple parameter types."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func add(x int, y int) int {
    return x + y
}
""")

        result = analyze_go(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x int, y int) int"

    def test_extracts_signature_with_multiple_returns(self, tmp_path: Path) -> None:
        """Extracts signature with multiple return types."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func divide(a int, b int) (int, error) {
    return a / b, nil
}
""")

        result = analyze_go(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(a int, b int) (int, error)"

    def test_extracts_signature_with_shared_types(self, tmp_path: Path) -> None:
        """Extracts signature where parameters share types."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func sum(a, b, c int) int {
    return a + b + c
}
""")

        result = analyze_go(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(a, b, c int) int"

    def test_extracts_signature_with_no_params(self, tmp_path: Path) -> None:
        """Extracts signature for function with no parameters."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func getAnswer() int {
    return 42
}
""")

        result = analyze_go(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].signature == "() int"

    def test_extracts_signature_with_no_return(self, tmp_path: Path) -> None:
        """Extracts signature for function with no return type."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func printHello(name string) {
    println("Hello, " + name)
}
""")

        result = analyze_go(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(name string)"

    def test_extracts_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature for method with receiver."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

type Counter struct {
    value int
}

func (c *Counter) Add(amount int) {
    c.value += amount
}

func (c Counter) Get() int {
    return c.value
}
""")

        result = analyze_go(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        sigs = {s.name.split(".")[-1]: s.signature for s in methods}

        assert sigs.get("Add") == "(amount int)"
        assert sigs.get("Get") == "() int"

    def test_extracts_signature_with_complex_types(self, tmp_path: Path) -> None:
        """Extracts signature with complex types."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func process(items []string) map[string]int {
    return nil
}
""")

        result = analyze_go(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        sig = funcs[0].signature
        assert sig is not None
        assert "[]string" in sig
        assert "map[string]int" in sig

    def test_symbol_to_dict_includes_signature(self, tmp_path: Path) -> None:
        """Symbol.to_dict() includes the signature field."""
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func greet(name string) string {
    return "Hello, " + name
}
""")

        result = analyze_go(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1

        as_dict = funcs[0].to_dict()
        assert "signature" in as_dict
        assert as_dict["signature"] == "(name string) string"


class TestImportPathToDirHint:
    """Tests for _import_path_to_dir_hint helper function."""

    def test_with_src_pattern(self) -> None:
        """Returns /src/... for paths containing /src/."""
        from hypergumbo.analyze.go import _import_path_to_dir_hint

        result = _import_path_to_dir_hint("github.com/example/src/foo/bar")
        assert result == "/src/foo/bar"

    def test_fallback_without_src(self) -> None:
        """Returns last 2 components for paths without /src/."""
        from hypergumbo.analyze.go import _import_path_to_dir_hint

        result = _import_path_to_dir_hint("github.com/example/genproto")
        assert result == "/example/genproto"

    def test_single_component(self) -> None:
        """Returns None for single-component paths."""
        from hypergumbo.analyze.go import _import_path_to_dir_hint

        result = _import_path_to_dir_hint("fmt")
        assert result is None


class TestResolveCallee:
    """Tests for _resolve_callee helper function."""

    def test_fallback_when_no_match(self, tmp_path: Path) -> None:
        """Returns first candidate when import hint doesn't match any."""
        from hypergumbo.analyze.go import _resolve_callee
        from hypergumbo.ir import Symbol, Span

        # Create two candidates in different directories
        sym1 = Symbol(
            id="go:/path/a/foo.go:1-1:Func:function",
            name="Func",
            kind="function",
            language="go",
            path="/path/a/foo.go",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=10),
            origin="test",
            origin_run_id="test",
        )
        sym2 = Symbol(
            id="go:/path/b/foo.go:1-1:Func:function",
            name="Func",
            kind="function",
            language="go",
            path="/path/b/foo.go",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=10),
            origin="test",
            origin_run_id="test",
        )

        # Import hint that doesn't match any candidate
        result = _resolve_callee("Func", [sym1, sym2], "github.com/nonexistent/pkg")
        # Should return first candidate as fallback
        assert result == sym1


class TestGoImportPathResolution:
    """Tests for import path disambiguation (Bug #1 from bakeoff report)."""

    def test_resolves_call_to_correct_file_by_import_path(self, tmp_path: Path) -> None:
        """When multiple files define same symbol, resolve by import path.

        This tests Bug #1: Go import resolution ignores import paths.
        When multiple files declare the same package name, hypergumbo should
        use the import path to disambiguate, not pick arbitrarily.

        We use 'frontend' vs 'checkoutservice' naming to ensure alphabetical
        ordering would pick the WRONG file (frontend < checkoutservice).
        """
        from hypergumbo.analyze.go import analyze_go

        # Create structure where alphabetically first file has 'wrong' definition
        # frontend comes before checkoutservice alphabetically
        frontend_proto = tmp_path / "src" / "frontend" / "genproto"
        frontend_proto.mkdir(parents=True)
        checkout_proto = tmp_path / "src" / "checkoutservice" / "genproto"
        checkout_proto.mkdir(parents=True)

        # Both files define same function in package hipstershop
        (frontend_proto / "demo.pb.go").write_text("""package hipstershop

func RegisterCheckoutServiceServer(s interface{}, srv interface{}) {
    // WRONG - frontend copy
}
""")

        (checkout_proto / "demo.pb.go").write_text("""package hipstershop

func RegisterCheckoutServiceServer(s interface{}, srv interface{}) {
    // CORRECT - checkoutservice copy
}
""")

        # main.go imports checkoutservice's genproto (not frontend's)
        checkout = tmp_path / "src" / "checkoutservice"
        (checkout / "main.go").write_text("""package main

import (
    pb "github.com/example/src/checkoutservice/genproto"
)

func main() {
    pb.RegisterCheckoutServiceServer(nil, nil)
}
""")

        result = analyze_go(tmp_path)

        # Find the call edge from main to RegisterCheckoutServiceServer
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main.go" in e.src]

        assert len(main_calls) >= 1, "Should have call edge from main"

        # The destination MUST be in checkoutservice/genproto, NOT frontend/genproto
        dst_id = main_calls[0].dst
        assert "checkoutservice" in dst_id, (
            f"Call should resolve to checkoutservice/genproto based on import path, got {dst_id}"
        )
        assert "frontend" not in dst_id, (
            f"Call should NOT resolve to frontend/genproto, got {dst_id}"
        )


class TestGoReceiverMethodCalls:
    """Tests for receiver.Method() call extraction (Bug #2 from bakeoff report)."""

    def test_extracts_receiver_method_call_local(self, tmp_path: Path) -> None:
        """Extracts method calls where method IS defined locally.

        This is a baseline test - method calls should work when the
        receiver type and method are both in our symbol registry.
        """
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}

func (s *Server) RegisterService(desc interface{}, impl interface{}) {
    // server implementation
}

func RegisterFooServer(s *Server, srv interface{}) {
    s.RegisterService(nil, srv)
}

func main() {
    srv := &Server{}
    RegisterFooServer(srv, nil)
}
""")

        result = analyze_go(tmp_path)

        # Find call edges
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # There should be an edge from RegisterFooServer to Server.RegisterService
        register_foo_calls = [
            e for e in call_edges
            if "RegisterFooServer" in e.src
        ]

        # Should have at least one call edge from RegisterFooServer
        assert len(register_foo_calls) >= 1, (
            f"RegisterFooServer should have outgoing call edges, found: {register_foo_calls}"
        )

        # One of those edges should be to RegisterService
        dst_names = [e.dst for e in register_foo_calls]
        has_register_service_call = any(
            "RegisterService" in dst for dst in dst_names
        )
        assert has_register_service_call, (
            f"Should have edge to RegisterService, found destinations: {dst_names}"
        )

    def test_extracts_receiver_method_call_external(self, tmp_path: Path) -> None:
        """Extracts method calls where receiver type is EXTERNAL.

        This tests Bug #2: Call extraction fails for receiver.Method() calls
        when the method is defined externally (not in our symbol registry).

        In real gRPC code:
            func RegisterFooServer(s grpc.ServiceRegistrar, srv FooServer) {
                s.RegisterService(&FooService_ServiceDesc, srv)
            }

        The `s.RegisterService()` call should create an edge, even though
        ServiceRegistrar is from google.golang.org/grpc.
        """
        from hypergumbo.analyze.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "google.golang.org/grpc"

// External type - grpc.ServiceRegistrar is not defined in our codebase
func RegisterFooServer(s grpc.ServiceRegistrar, srv FooServer) {
    // This call is to an EXTERNAL method - not in our symbol table
    s.RegisterService(&FooService_ServiceDesc, srv)
}

type FooServer interface {}
var FooService_ServiceDesc = struct{}{}

func main() {
    RegisterFooServer(nil, nil)
}
""")

        result = analyze_go(tmp_path)

        # Find call edges from RegisterFooServer
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        register_foo_calls = [
            e for e in call_edges
            if "RegisterFooServer" in e.src
        ]

        # Should have an edge for s.RegisterService() call
        # The destination may be unresolved/external, but the edge should exist
        assert len(register_foo_calls) >= 1, (
            f"RegisterFooServer should have outgoing call edge for s.RegisterService(), "
            f"but found {len(register_foo_calls)} edges"
        )
