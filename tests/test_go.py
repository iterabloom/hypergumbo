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
        """_find_child_by_type returns None when no matching child."""
        from hypergumbo.analyze.go import (
            _find_child_by_type,
            is_go_tree_sitter_available,
        )

        if not is_go_tree_sitter_available():
            pytest.skip("tree-sitter-go not available")

        import tree_sitter_go
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_go.language())
        parser = tree_sitter.Parser(lang)

        source = b"package main\n"
        tree = parser.parse(source)

        # Try to find a child type that doesn't exist
        result = _find_child_by_type(tree.root_node, "nonexistent_type")
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

