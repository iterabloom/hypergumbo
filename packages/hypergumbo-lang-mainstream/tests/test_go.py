"""Tests for Go analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from hypergumbo_lang_mainstream import go as go_module


class TestFindGoFiles:
    """Tests for Go file discovery."""

    def test_finds_go_files(self, tmp_path: Path) -> None:
        """Finds .go files."""
        from hypergumbo_lang_mainstream.go import find_go_files

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
        result = go_module.is_go_tree_sitter_available()
        assert result is True

    def test_is_go_tree_sitter_available_false(self) -> None:
        """Returns False when grammar is not available."""
        with patch.object(go_module._analyzer, "_check_grammar_available", return_value=False):
            assert go_module.is_go_tree_sitter_available() is False

    def test_is_go_tree_sitter_available_via_analyzer(self) -> None:
        """Availability check delegates to TreeSitterAnalyzer._check_grammar_available."""
        assert go_module.is_go_tree_sitter_available() == go_module._analyzer._check_grammar_available()


class TestAnalyzeGoFallback:
    """Tests for fallback behavior when tree-sitter-go unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-go unavailable."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "test.go").write_text("package main")

        with patch.object(go_module._analyzer, "_check_grammar_available", return_value=False):
            result = analyze_go(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason


class TestGoFunctionExtraction:
    """Tests for extracting Go functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts Go function declarations."""
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "test.go").write_text("package main")

        with patch.object(go_module._analyzer, "_check_grammar_available", return_value=True):
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
        from hypergumbo_lang_mainstream.go import analyze_go

        # Create a file with only comments
        (tmp_path / "empty.go").write_text("// Just a comment\npackage main\n")

        result = analyze_go(tmp_path)


        # Even package-only file should have no symbols
        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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


class TestGoAnonymousFunctionCalls:
    """Tests for call attribution inside anonymous functions (func literals)."""

    def test_call_inside_goroutine_attributed_to_caller(self, tmp_path: Path) -> None:
        """Calls inside goroutines are attributed to the containing function."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func helper() {
}

func main() {
    go func() {
        helper()
    }()
}
""")

        result = analyze_go(tmp_path)

        # Find call edges to helper
        call_edges = [e for e in result.edges if e.edge_type == "calls" and "helper" in e.dst]

        # There should be a call from main to helper (via the goroutine)
        assert len(call_edges) >= 1, "Call to helper inside goroutine should be detected"

        # The source should be 'main' (the containing named function)
        main_to_helper = [e for e in call_edges if "main" in e.src]
        assert len(main_to_helper) >= 1, \
            f"Call should be attributed to main function, got sources: {[e.src for e in call_edges]}"

    def test_call_inside_callback_attributed_to_caller(self, tmp_path: Path) -> None:
        """Calls inside callback func literals are attributed to the containing function."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func doWork() {}

func process(callback func()) {
    callback()
}

func main() {
    process(func() {
        doWork()
    })
}
""")

        result = analyze_go(tmp_path)

        # Find call edges to doWork
        call_edges = [e for e in result.edges if e.edge_type == "calls" and "doWork" in e.dst]

        # There should be a call from main to doWork (via the callback)
        assert len(call_edges) >= 1, "Call to doWork inside callback should be detected"

        # The source should be 'main' (the containing named function)
        main_calls = [e for e in call_edges if "main" in e.src]
        assert len(main_calls) >= 1, \
            f"Call should be attributed to main function, got sources: {[e.src for e in call_edges]}"


class TestGoTypeAliasExtraction:
    """Tests for extracting Go type aliases."""

    def test_extracts_type_alias(self, tmp_path: Path) -> None:
        """Extracts type alias declarations (not struct or interface)."""
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_core.analyze.base import find_child_by_type
        from hypergumbo_lang_mainstream.go import is_go_tree_sitter_available

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
        from hypergumbo_lang_mainstream.go import (
            _extract_symbols_from_file,
            is_go_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.go import (
            _extract_edges_from_file,
            is_go_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        """Route symbols have sha256-based stable_id (ADR-0014 §4)."""
        from hypergumbo_core.analyze.base import make_route_stable_id
        from hypergumbo_lang_mainstream.go import analyze_go

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
        assert routes[0].stable_id == make_route_stable_id("GET", "/test")

    def test_route_path_extraction(self, tmp_path: Path) -> None:
        """Route path is correctly extracted to metadata."""
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import (
            _extract_go_routes,
            is_go_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

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
        assert len(routes[0].stable_id) == 64  # sha256 hex digest (ADR-0014 §4)

    def test_no_routes_in_non_web_code(self, tmp_path: Path) -> None:
        """No routes detected in code without web framework patterns."""
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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


class TestGoGorillaMuxRoutes:
    """Tests for Gorilla mux route detection.

    Gorilla mux uses two patterns not covered by Gin/Echo/Fiber:
    1. HandleFunc/Handle: router.HandleFunc("/path", handler)
    2. Builder chain: router.Path("/path").Methods("GET").Handler(handler)
    """

    def test_handlefunc_pattern(self, tmp_path: Path) -> None:
        """Detects router.HandleFunc("/path", handler) pattern."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gorilla/mux"

func main() {
    r := mux.NewRouter()
    r.HandleFunc("/users", listUsers)
    r.HandleFunc("/users/{id}", getUser)
}

func listUsers(w http.ResponseWriter, r *http.Request) {}
func getUser(w http.ResponseWriter, r *http.Request) {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = {s.name for s in routes}

        assert "listUsers" in route_names
        assert "getUser" in route_names

        # Check metadata
        users_route = next(s for s in routes if s.name == "listUsers")
        assert users_route.meta["route_path"] == "/users"
        assert users_route.meta["http_method"] == "ANY"
        assert users_route.meta["handler_name"] == "listUsers"

    def test_handle_pattern(self, tmp_path: Path) -> None:
        """Detects router.Handle("/path", handler) pattern."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gorilla/mux"

func main() {
    r := mux.NewRouter()
    r.Handle("/api", apiHandler)
}

func apiHandler() {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].name == "apiHandler"
        assert routes[0].meta["route_path"] == "/api"
        assert routes[0].meta["http_method"] == "ANY"

    def test_path_handler_builder_chain(self, tmp_path: Path) -> None:
        """Detects router.Path("/path").Handler(handler) pattern (2-level chain)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gorilla/mux"

func main() {
    r := mux.NewRouter()
    r.Path("/api/v1").Handler(apiV1Handler)
}

func apiV1Handler() {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].name == "apiV1Handler"
        assert routes[0].meta["route_path"] == "/api/v1"
        assert routes[0].meta["http_method"] == "ANY"

    def test_path_methods_handler_builder_chain(self, tmp_path: Path) -> None:
        """Detects router.Path("/path").Methods("GET").Handler(h) (3-level chain)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gorilla/mux"

func main() {
    r := mux.NewRouter()
    r.Path("/users").Methods("GET").Handler(listUsers)
    r.Path("/users").Methods("POST").HandlerFunc(createUser)
}

func listUsers() {}
func createUser() {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        route_by_name = {s.name: s for s in routes}

        assert "listUsers" in route_by_name
        assert "createUser" in route_by_name

        assert route_by_name["listUsers"].meta["route_path"] == "/users"
        assert route_by_name["listUsers"].meta["http_method"] == "GET"
        assert route_by_name["createUser"].meta["route_path"] == "/users"
        assert route_by_name["createUser"].meta["http_method"] == "POST"

    def test_path_prefix_handler(self, tmp_path: Path) -> None:
        """Detects router.PathPrefix("/").Handler(handler) pattern."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gorilla/mux"

func main() {
    r := mux.NewRouter()
    r.PathPrefix("/static/").Handler(fileServer)
}

func fileServer() {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].name == "fileServer"
        assert routes[0].meta["route_path"] == "/static/"

    def test_handler_from_call_expression(self, tmp_path: Path) -> None:
        """Handler from function call: httpapi.NewHandler(arg)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gorilla/mux"

func main() {
    r := mux.NewRouter()
    r.Path("/api").Handler(httpapi.NewHandler(env))
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].meta["route_path"] == "/api"
        # Handler name extracted from the function call
        assert routes[0].meta["handler_name"] == "httpapi.NewHandler"

    def test_selector_handler_in_handlefunc(self, tmp_path: Path) -> None:
        """Detects HandleFunc with package-qualified handler: handlers.GetAPI."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gorilla/mux"

func main() {
    r := mux.NewRouter()
    r.HandleFunc("/api", handlers.GetAPI)
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].name == "handlers.GetAPI"
        assert routes[0].meta["route_path"] == "/api"

    def test_handlefunc_stable_id(self, tmp_path: Path) -> None:
        """Gorilla mux HandleFunc routes have sha256-based stable_id (ADR-0014 §4)."""
        from hypergumbo_core.analyze.base import make_route_stable_id
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func main() {
    r.HandleFunc("/test", handler)
}

func handler() {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].stable_id == make_route_stable_id("ANY", "/test")

    def test_builder_chain_stable_id_with_method(self, tmp_path: Path) -> None:
        """Builder chain with .Methods("GET") has sha256-based stable_id (ADR-0014 §4)."""
        from hypergumbo_core.analyze.base import make_route_stable_id
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func main() {
    r.Path("/test").Methods("GET").Handler(handler)
}

func handler() {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].stable_id == make_route_stable_id("GET", "/test")
        assert routes[0].meta["http_method"] == "GET"

    def test_route_stable_id_no_collision(self, tmp_path: Path) -> None:
        """Different routes with same HTTP method must have different stable_ids (ADR-0014)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func main() {
    r.GET("/users", listUsers)
    r.GET("/posts", listPosts)
}

func listUsers() {}
func listPosts() {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 2
        stable_ids = [s.stable_id for s in routes]
        assert len(set(stable_ids)) == len(stable_ids), f"stable_id collision: {stable_ids}"


class TestGoSignatureExtraction:
    """Tests for extracting function signatures from Go code."""

    def test_extracts_simple_signature(self, tmp_path: Path) -> None:
        """Extracts signature with simple parameter types."""
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import _import_path_to_dir_hint

        result = _import_path_to_dir_hint("github.com/example/src/foo/bar")
        assert result == "/src/foo/bar"

    def test_fallback_without_src(self) -> None:
        """Returns last 2 components for paths without /src/."""
        from hypergumbo_lang_mainstream.go import _import_path_to_dir_hint

        result = _import_path_to_dir_hint("github.com/example/genproto")
        assert result == "/example/genproto"

    def test_single_component(self) -> None:
        """Returns None for single-component paths."""
        from hypergumbo_lang_mainstream.go import _import_path_to_dir_hint

        result = _import_path_to_dir_hint("fmt")
        assert result is None


class TestGoImportPathResolution:
    """Tests for import path disambiguation (Bug #1 from bakeoff report)."""

    def test_resolves_call_to_correct_file_by_import_path(self, tmp_path: Path) -> None:
        """When multiple files define same symbol, resolve by import path.

        This tests Bug #1: Go import resolution ignores import paths.
        When multiple files declare the same package name, hypergumbo should
        use the import path to disambiguate, not pick arbitrarily.

        We use 'aaa_wrong' vs 'zzz_correct' naming to ensure alphabetical
        ordering would pick the WRONG file (aaa < zzz).

        Fixed in INV-007: ListNameResolver now tries progressively shorter
        path suffixes to find unique matches.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        # Create structure where alphabetically first file has 'wrong' definition
        # aaa_wrong comes before zzz_correct alphabetically
        wrong_proto = tmp_path / "src" / "aaa_wrong" / "genproto"
        wrong_proto.mkdir(parents=True)
        correct_proto = tmp_path / "src" / "zzz_correct" / "genproto"
        correct_proto.mkdir(parents=True)

        # Both files define same function in package hipstershop
        (wrong_proto / "demo.pb.go").write_text("""package hipstershop

func RegisterCheckoutServiceServer(s interface{}, srv interface{}) {
    // WRONG - should not be picked
}
""")

        (correct_proto / "demo.pb.go").write_text("""package hipstershop

func RegisterCheckoutServiceServer(s interface{}, srv interface{}) {
    // CORRECT - matches import path
}
""")

        # main.go imports zzz_correct's genproto (not aaa_wrong's)
        main_dir = tmp_path / "src" / "zzz_correct"
        (main_dir / "main.go").write_text("""package main

import (
    pb "github.com/example/src/zzz_correct/genproto"
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

        # The destination MUST be in zzz_correct/genproto, NOT aaa_wrong/genproto
        dst_id = main_calls[0].dst
        assert "zzz_correct" in dst_id, (
            f"Call should resolve to zzz_correct/genproto based on import path, got {dst_id}"
        )
        assert "aaa_wrong" not in dst_id, (
            f"Call should NOT resolve to aaa_wrong/genproto, got {dst_id}"
        )


class TestGoReceiverMethodCalls:
    """Tests for receiver.Method() call extraction (Bug #2 from bakeoff report)."""

    def test_extracts_receiver_method_call_local(self, tmp_path: Path) -> None:
        """Extracts method calls where method IS defined locally.

        This is a baseline test - method calls should work when the
        receiver type and method are both in our symbol registry.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

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
        from hypergumbo_lang_mainstream.go import analyze_go

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


class TestGoInterfaceImplementation:
    """Tests for Go interface-implementation assertion detection.

    Go uses compile-time assertions like ``var _ Interface = &Struct{}``
    to verify interface satisfaction. These should produce base_classes
    metadata on the struct symbol, which the inheritance linker then
    converts to ``implements`` edges.
    """

    def test_address_of_composite_literal(self, tmp_path: Path) -> None:
        """var _ Reader = &MyReader{} detects implementation."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Reader interface {
    Read(p []byte) (int, error)
}

type MyReader struct{}

func (r *MyReader) Read(p []byte) (int, error) {
    return 0, nil
}

var _ Reader = &MyReader{}
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyReader"), None)
        assert struct_sym is not None, "Should find MyReader struct"
        assert struct_sym.meta is not None, "MyReader should have meta"
        assert "base_classes" in struct_sym.meta, (
            f"MyReader should have base_classes metadata, got: {struct_sym.meta}"
        )
        assert "Reader" in struct_sym.meta["base_classes"]

    def test_nil_cast_pattern(self, tmp_path: Path) -> None:
        """var _ Writer = (*MyWriter)(nil) detects implementation."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Writer interface {
    Write(p []byte) (int, error)
}

type MyWriter struct{}

var _ Writer = (*MyWriter)(nil)
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyWriter"), None)
        assert struct_sym is not None
        assert struct_sym.meta is not None
        assert "base_classes" in struct_sym.meta
        assert "Writer" in struct_sym.meta["base_classes"]

    def test_multiple_interfaces_same_struct(self, tmp_path: Path) -> None:
        """Struct implementing multiple interfaces collects all."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Reader interface {
    Read(p []byte) (int, error)
}

type Closer interface {
    Close() error
}

type MyFile struct{}

var _ Reader = &MyFile{}
var _ Closer = &MyFile{}
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyFile"), None)
        assert struct_sym is not None
        assert struct_sym.meta is not None
        bases = struct_sym.meta["base_classes"]
        assert "Reader" in bases
        assert "Closer" in bases

    def test_assertion_across_files(self, tmp_path: Path) -> None:
        """Interface assertion in different file than struct definition."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "iface.go").write_text("""package main

type Handler interface {
    Handle() error
}
""")

        (tmp_path / "impl.go").write_text("""package main

type MyHandler struct{}

func (h *MyHandler) Handle() error {
    return nil
}

var _ Handler = &MyHandler{}
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyHandler"), None)
        assert struct_sym is not None
        assert struct_sym.meta is not None
        assert "Handler" in struct_sym.meta["base_classes"]

    def test_qualified_interface_type(self, tmp_path: Path) -> None:
        """var _ io.Reader = &MyReader{} uses qualified type name."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

import "io"

type MyReader struct{}

func (r *MyReader) Read(p []byte) (int, error) {
    return 0, nil
}

var _ io.Reader = &MyReader{}
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyReader"), None)
        assert struct_sym is not None
        assert struct_sym.meta is not None
        assert "io.Reader" in struct_sym.meta["base_classes"]

    def test_non_blank_identifier_ignored(self, tmp_path: Path) -> None:
        """var x Interface = &Struct{} is NOT an assertion (name != _)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Doer interface {
    Do()
}

type MyDoer struct{}

var x Doer = &MyDoer{}
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyDoer"), None)
        assert struct_sym is not None
        # Non-blank identifier — should NOT have base_classes
        assert struct_sym.meta is None or "base_classes" not in struct_sym.meta

    def test_unrecognized_rhs_pattern_ignored(self, tmp_path: Path) -> None:
        """var _ Interface = someFunc() — unrecognized RHS is safely ignored."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Doer interface {
    Do()
}

type MyDoer struct{}

func newDoer() Doer { return &MyDoer{} }

var _ Doer = newDoer()
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyDoer"), None)
        assert struct_sym is not None
        # RHS is a function call, not &Struct{} or (*Struct)(nil)
        assert struct_sym.meta is None or "base_classes" not in struct_sym.meta

    def test_var_blank_no_type_annotation(self, tmp_path: Path) -> None:
        """var _ = expr — blank identifier without type annotation is ignored."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type MyStruct struct{}

var _ = &MyStruct{}
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyStruct"), None)
        assert struct_sym is not None
        # No type annotation → not an interface assertion
        assert struct_sym.meta is None or "base_classes" not in struct_sym.meta

    def test_pointer_type_annotation_ignored(self, tmp_path: Path) -> None:
        """var _ *Iface = ... — pointer type annotation is not an interface assertion."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Doer interface {
    Do()
}

type MyDoer struct{}

var _ *Doer = nil
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyDoer"), None)
        assert struct_sym is not None
        assert struct_sym.meta is None or "base_classes" not in struct_sym.meta

    def test_qualified_composite_literal(self, tmp_path: Path) -> None:
        """var _ Interface = &pkg.Struct{} extracts the qualified struct name."""
        from hypergumbo_lang_mainstream.go import analyze_go

        # Simulate a file with a qualified composite literal on the RHS.
        # We use a sub-package style: the struct name in the type_spec is
        # "Config" but the assertion uses &settings.Config{}.
        # Since both are in the same analysis pass, the struct name from
        # the composite literal's qualified_type ("Config") should match.
        (tmp_path / "types.go").write_text("""package main

type Configurer interface {
    Configure()
}

type Config struct{}

var _ Configurer = &settings.Config{}
""")

        result = analyze_go(tmp_path)

        # The qualified composite literal extracts "Config" from qualified_type
        struct_sym = next((s for s in result.symbols if s.name == "Config"), None)
        assert struct_sym is not None
        assert struct_sym.meta is not None
        assert "Configurer" in struct_sym.meta["base_classes"]


class TestGoPackageQualifiedCallResolution:
    """Tests for correct resolution of package-qualified calls.

    When a call like ``bug.AddComment()`` uses a package alias (``bug``
    mapping to an import path), the resolver should NOT match a local
    method with the same short name (e.g., ``BugCache.AddComment``).
    Instead, it should use the import path hint to resolve to the
    correct package-level function.
    """

    def test_package_call_not_hijacked_by_local_method(self, tmp_path: Path) -> None:
        """bug.AddComment() resolves to the imported package, not a local method.

        Regression: the local-first check matched ``AddComment`` in local
        symbols (from ``BugCache.AddComment``), ignoring the import alias.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        # File 1: entities/bug package with AddComment function
        bug_dir = tmp_path / "entities" / "bug"
        bug_dir.mkdir(parents=True)
        (bug_dir / "op_add_comment.go").write_text("""package bug

func AddComment(text string) error {
    return nil
}
""")

        # File 2: cache package imports entities/bug and also has a method named AddComment
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "bug_cache.go").write_text("""package cache

import bug "entities/bug"

type BugCache struct{}

func (bc *BugCache) AddComment(text string) error {
    return bug.AddComment(text)
}
""")

        result = analyze_go(tmp_path)

        # Find the call edge from BugCache.AddComment
        bc_add = next(
            (s for s in result.symbols if s.name == "BugCache.AddComment"), None
        )
        assert bc_add is not None, "Should find BugCache.AddComment method"

        pkg_add = next(
            (s for s in result.symbols if s.name == "AddComment"
             and "entities" in s.id), None
        )
        assert pkg_add is not None, "Should find bug.AddComment function"

        # The edge from BugCache.AddComment should point to bug.AddComment
        # (the package function), NOT to itself (the local method)
        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == bc_add.id
        ]
        assert len(call_edges) >= 1, (
            f"Should have at least one call edge from BugCache.AddComment, "
            f"got edges: {[(e.src, e.dst) for e in result.edges if e.edge_type == 'calls']}"
        )
        # The target should be the package-level AddComment, not the local method
        targets = [e.dst for e in call_edges]
        assert pkg_add.id in targets, (
            f"Call should resolve to package-level bug.AddComment ({pkg_add.id}), "
            f"not local BugCache.AddComment. Got targets: {targets}"
        )
        assert bc_add.id not in targets, (
            "Call should NOT self-resolve to BugCache.AddComment"
        )


class TestGoGenericInterfaceAssertions:
    """Tests for generic interface assertion detection.

    Go generics (1.18+) allow type parameters in interfaces:
    ``var _ Interface[T] = &Struct{}``. These should produce the same
    ``base_classes`` metadata as non-generic assertions.
    """

    def test_simple_generic_interface(self, tmp_path: Path) -> None:
        """var _ Cache[string] = &StringCache{} detects implementation."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Cache[T any] interface {
    Get(key string) T
    Set(key string, value T)
}

type StringCache struct{}

var _ Cache[string] = &StringCache{}
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "StringCache"), None)
        assert struct_sym is not None, "Should find StringCache struct"
        assert struct_sym.meta is not None, "StringCache should have meta"
        assert "base_classes" in struct_sym.meta, (
            f"StringCache should have base_classes, got: {struct_sym.meta}"
        )
        assert "Cache" in struct_sym.meta["base_classes"]

    def test_multi_param_generic_interface(self, tmp_path: Path) -> None:
        """var _ SubCache[A, B, C] = &BugSubCache{} detects implementation."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type SubCache[K comparable, V any, E any] interface {
    Get(key K) (V, error)
}

type BugSubCache struct{}

var _ SubCache[string, int, float64] = &BugSubCache{}
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "BugSubCache"), None)
        assert struct_sym is not None, "Should find BugSubCache struct"
        assert struct_sym.meta is not None, "BugSubCache should have meta"
        assert "base_classes" in struct_sym.meta, (
            f"BugSubCache should have base_classes, got: {struct_sym.meta}"
        )
        assert "SubCache" in struct_sym.meta["base_classes"]

    def test_qualified_generic_interface(self, tmp_path: Path) -> None:
        """var _ entity.Interface[T] = &Struct{} uses qualified name."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

import "entity"

type MyImpl struct{}

var _ entity.Interface[string] = &MyImpl{}
""")

        result = analyze_go(tmp_path)

        struct_sym = next((s for s in result.symbols if s.name == "MyImpl"), None)
        assert struct_sym is not None, "Should find MyImpl struct"
        assert struct_sym.meta is not None, "MyImpl should have meta"
        assert "base_classes" in struct_sym.meta, (
            f"MyImpl should have base_classes, got: {struct_sym.meta}"
        )
        # Should use the full qualified name from the generic_type
        bases = struct_sym.meta["base_classes"]
        assert any("Interface" in b for b in bases), (
            f"Should have Interface in base_classes, got: {bases}"
        )


class TestGoReceiverTypeDisambiguation:
    """Tests for receiver-type method disambiguation.

    When multiple Go types define a method with the same name (e.g., String()),
    the analyzer should use the receiver variable's inferred type to resolve
    the call to the correct type's method, rather than picking alphabetically.

    Go patterns that establish variable types:
    - Short variable declaration: ``s := &Server{}`` → s has type Server
    - Function parameters: ``func foo(s *Server)`` → s has type Server
    - Var declaration: ``var s Server`` → s has type Server
    """

    def test_disambiguates_same_name_methods_via_composite_literal(self, tmp_path: Path) -> None:
        """Resolves s.String() to Server.String when s := &Server{}.

        Two types (Server and Client) both define String(). When we see
        ``s := &Server{}`` followed by ``s.String()``, the call should resolve
        to Server.String, not Client.String.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) String() string {
    return "server"
}

func (c *Client) String() string {
    return "client"
}

func main() {
    s := &Server{}
    _ = s.String()
}
""")

        result = analyze_go(tmp_path)

        # Find call edges from main function
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        # Should have a call to String
        string_calls = [e for e in main_calls if "String" in e.dst]
        assert len(string_calls) >= 1, (
            f"main should call String, found edges: {main_calls}"
        )

        # The call should resolve to Server.String, NOT Client.String
        assert any("Server.String" in e.dst for e in string_calls), (
            f"s.String() should resolve to Server.String (not Client.String), "
            f"found destinations: {[e.dst for e in string_calls]}"
        )

    def test_disambiguates_via_function_parameter(self, tmp_path: Path) -> None:
        """Resolves s.Get() to Server.Get when s is a *Server parameter.

        When a function parameter has a typed receiver, the call should resolve
        to the correct type's method.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) Get() string {
    return "server-get"
}

func (c *Client) Get() string {
    return "client-get"
}

func process(s *Server) {
    _ = s.Get()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        process_calls = [e for e in call_edges if "process" in e.src]

        # The call should resolve to Server.Get, NOT Client.Get
        get_calls = [e for e in process_calls if "Get" in e.dst]
        assert len(get_calls) >= 1, (
            f"process should call Get, found edges: {process_calls}"
        )
        assert any("Server.Get" in e.dst for e in get_calls), (
            f"s.Get() should resolve to Server.Get (not Client.Get), "
            f"found destinations: {[e.dst for e in get_calls]}"
        )

    def test_disambiguates_via_var_declaration(self, tmp_path: Path) -> None:
        """Resolves s.Close() to Server.Close when var s Server.

        Explicit var declarations with type annotations should be tracked.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) Close() {
}

func (c *Client) Close() {
}

func main() {
    var s Server
    s.Close()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        close_calls = [e for e in main_calls if "Close" in e.dst]
        assert len(close_calls) >= 1, (
            f"main should call Close, found edges: {main_calls}"
        )
        assert any("Server.Close" in e.dst for e in close_calls), (
            f"s.Close() should resolve to Server.Close, "
            f"found destinations: {[e.dst for e in close_calls]}"
        )

    def test_higher_confidence_for_typed_resolution(self, tmp_path: Path) -> None:
        """Typed receiver resolution should have higher confidence than untyped.

        When we know the receiver type, the confidence should be 0.85 (type-tracked)
        rather than 0.80 * 1/sqrt(N) (ListNameResolver ambiguous, scaled by candidates).
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) Handle() {}
func (c *Client) Handle() {}

func main() {
    s := &Server{}
    s.Handle()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]
        handle_calls = [e for e in main_calls if "Handle" in e.dst]
        assert len(handle_calls) >= 1

        # Typed resolution should have higher confidence than ambiguous
        assert handle_calls[0].confidence >= 0.80, (
            f"Typed receiver resolution should have confidence >= 0.80, "
            f"got {handle_calls[0].confidence}"
        )

    def test_evidence_type_for_typed_resolution(self, tmp_path: Path) -> None:
        """Typed receiver resolution should use 'typed_receiver_call' evidence type."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) Run() {}
func (c *Client) Run() {}

func main() {
    s := &Server{}
    s.Run()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]
        run_calls = [e for e in main_calls if "Run" in e.dst]
        assert len(run_calls) >= 1

        assert run_calls[0].evidence_type == "typed_receiver_call", (
            f"Expected evidence_type='typed_receiver_call', got '{run_calls[0].evidence_type}'"
        )

    def test_disambiguates_cross_file_via_global_symbols(self, tmp_path: Path) -> None:
        """Resolves s.String() via global_symbols when method is in another file.

        When the method definition is in a different file, it won't be in
        local_symbols but should be found via global_symbols using the
        qualified name (Type.Method).
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        # File 1: Define types and methods
        types_file = tmp_path / "types.go"
        types_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) String() string {
    return "server"
}

func (c *Client) String() string {
    return "client"
}
""")

        # File 2: Use the types
        main_file = tmp_path / "main.go"
        main_file.write_text("""package main

func main() {
    s := &Server{}
    _ = s.String()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        string_calls = [e for e in main_calls if "String" in e.dst]
        assert len(string_calls) >= 1, (
            f"main should call String, found edges: {main_calls}"
        )
        # Should resolve to Server.String via global_symbols
        assert any("Server.String" in e.dst for e in string_calls), (
            f"s.String() should resolve to Server.String across files, "
            f"found destinations: {[e.dst for e in string_calls]}"
        )

    def test_function_scoped_var_types(self, tmp_path: Path) -> None:
        """Each function has its own variable type scope.

        When the same variable name 's' appears in foo() as &Server{}
        and in bar() as &Client{}, each function should resolve s.Run()
        to the correct type's method — Server.Run and Client.Run respectively.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) Run() {}
func (c *Client) Run() {}

func foo() {
    s := &Server{}
    s.Run()
}

func bar() {
    s := &Client{}
    s.Run()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        foo_calls = [e for e in call_edges if "foo" in e.src]
        bar_calls = [e for e in call_edges if "bar" in e.src]

        # foo's s is Server → should resolve to Server.Run
        assert any("Server.Run" in e.dst for e in foo_calls), (
            f"foo's s.Run() should resolve to Server.Run, found: {[e.dst for e in foo_calls]}"
        )
        # bar's s is Client → should resolve to Client.Run
        assert any("Client.Run" in e.dst for e in bar_calls), (
            f"bar's s.Run() should resolve to Client.Run, found: {[e.dst for e in bar_calls]}"
        )

    def test_parameter_types_per_function(self, tmp_path: Path) -> None:
        """Each function resolves parameter types independently.

        first() uses ``s := &Server{}`` while second() has ``s *Client``
        as a parameter. Each should resolve s.Do() to the correct type.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) Do() {}
func (c *Client) Do() {}

func first() {
    s := &Server{}
    s.Do()
}

func second(s *Client) {
    s.Do()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        first_calls = [e for e in call_edges if "first" in e.src]
        second_calls = [e for e in call_edges if "second" in e.src]

        # first() should resolve s.Do() to Server.Do
        assert any("Server.Do" in e.dst for e in first_calls), (
            f"first's s.Do() should resolve to Server.Do, "
            f"found: {[e.dst for e in first_calls]}"
        )
        # second() has s as *Client param → should resolve to Client.Do
        assert any("Client.Do" in e.dst for e in second_calls), (
            f"second's s.Do() should resolve to Client.Do, "
            f"found: {[e.dst for e in second_calls]}"
        )

    def test_skips_builtin_type_parameters(self, tmp_path: Path) -> None:
        """Parameters with builtin types (string, int) are not tracked.

        Built-in types don't have user-defined methods, so tracking them
        would waste memory and never produce useful disambiguation.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}

func (s *Server) Name() string {
    return "server"
}

func process(name string, s *Server) {
    _ = s.Name()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        process_calls = [e for e in call_edges if "process" in e.src]

        # Should resolve s.Name() to Server.Name
        name_calls = [e for e in process_calls if "Name" in e.dst]
        assert len(name_calls) >= 1
        assert any("Server.Name" in e.dst for e in name_calls)

    def test_var_declaration_with_pointer_type(self, tmp_path: Path) -> None:
        """var s *Server should track s as type Server."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) Start() {}
func (c *Client) Start() {}

func main() {
    var s *Server
    s.Start()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]
        start_calls = [e for e in main_calls if "Start" in e.dst]

        assert len(start_calls) >= 1
        assert any("Server.Start" in e.dst for e in start_calls)


class TestGoFunctionReferenceArgs:
    """Tests for function-reference-as-argument call edge detection.

    When a known function/method identifier is passed as an argument to
    another function call (e.g., ``r.Get("/path", handler)``), the analyzer
    should create a call edge from the enclosing function to the referenced
    function. This enables reverse slices for route handlers and callback
    patterns.
    """

    def test_simple_function_reference_arg(self, tmp_path: Path) -> None:
        """Function identifier passed as argument creates call edge.

        Pattern: ``register(handler)`` where handler is a known function.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func handler() {}

func register(fn func()) {
    fn()
}

func main() {
    register(handler)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        # main should have an edge to handler (via function reference)
        handler_refs = [e for e in main_calls if "handler" in e.dst]
        assert len(handler_refs) >= 1, (
            f"main should have call edge to handler (function reference arg), "
            f"found edges: {[e.dst for e in main_calls]}"
        )

    def test_route_handler_reference(self, tmp_path: Path) -> None:
        """Route handler pattern creates call edge for reverse slices.

        Pattern: ``m.Get("/issues", ViewIssue)`` should create edge
        from the enclosing function to ViewIssue.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "routes.go"
        go_file.write_text("""package main

func ViewIssue() {}
func ListIssues() {}

func setupRoutes() {
    m.Get("/issues", ListIssues)
    m.Get("/issues/:id", ViewIssue)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        setup_calls = [e for e in call_edges if "setupRoutes" in e.src]

        # setupRoutes should have edges to both handlers
        assert any("ViewIssue" in e.dst for e in setup_calls), (
            f"setupRoutes should have call edge to ViewIssue, "
            f"found: {[e.dst for e in setup_calls]}"
        )
        assert any("ListIssues" in e.dst for e in setup_calls), (
            f"setupRoutes should have call edge to ListIssues, "
            f"found: {[e.dst for e in setup_calls]}"
        )

    def test_function_reference_evidence_type(self, tmp_path: Path) -> None:
        """Function reference args should have 'function_reference_arg' evidence type."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func handler() {}

func main() {
    register(handler)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]
        handler_refs = [e for e in main_calls if "handler" in e.dst]

        assert len(handler_refs) >= 1
        assert handler_refs[0].evidence_type == "function_reference_arg", (
            f"Expected evidence_type='function_reference_arg', "
            f"got '{handler_refs[0].evidence_type}'"
        )

    def test_selector_expression_reference(self, tmp_path: Path) -> None:
        """Selector expression as argument: ``register(pkg.Handler)``.

        When a selector expression (like handlers.Get) appears as an argument,
        it should be resolved as a function reference.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Handlers struct{}

func (h *Handlers) GetAPI() {}

func setup() {
    h := &Handlers{}
    m.Get("/api", h.GetAPI)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        setup_calls = [e for e in call_edges if "setup" in e.src]

        # Should have edge to GetAPI via selector reference
        assert any("GetAPI" in e.dst for e in setup_calls), (
            f"setup should have call edge to GetAPI, "
            f"found: {[e.dst for e in setup_calls]}"
        )

    def test_does_not_create_edge_for_non_function_args(self, tmp_path: Path) -> None:
        """String and numeric literals as arguments should NOT create edges."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func main() {
    fmt.Println("hello", 42)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        # Should NOT have edges for "hello" or 42
        for e in main_calls:
            assert "hello" not in e.dst
            assert "42" not in e.dst

    def test_cross_file_function_reference(self, tmp_path: Path) -> None:
        """Function reference to a function defined in another file.

        When the referenced function is not in local_symbols, it should
        be resolved via the global symbol registry.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        # File 1: Define handler
        (tmp_path / "handler.go").write_text("""package main

func ViewIssue() {}
""")

        # File 2: Register route
        (tmp_path / "routes.go").write_text("""package main

func setupRoutes() {
    m.Get("/issues/:id", ViewIssue)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        setup_calls = [e for e in call_edges if "setupRoutes" in e.src]

        # Should resolve ViewIssue via global symbols
        assert any("ViewIssue" in e.dst for e in setup_calls), (
            f"setupRoutes should have call edge to ViewIssue (cross-file), "
            f"found: {[e.dst for e in setup_calls]}"
        )

    def test_function_reference_lower_confidence(self, tmp_path: Path) -> None:
        """Function reference args should have lower confidence than direct calls."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func handler() {}

func direct() {
    handler()
}

func indirect() {
    register(handler)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        direct_call = next(
            (e for e in call_edges if "direct" in e.src and "handler" in e.dst),
            None,
        )
        indirect_ref = next(
            (e for e in call_edges if "indirect" in e.src and "handler" in e.dst),
            None,
        )

        assert direct_call is not None, "direct() should have call edge to handler"
        assert indirect_ref is not None, "indirect() should have ref edge to handler"

        # Function reference should have lower confidence
        assert indirect_ref.confidence < direct_call.confidence, (
            f"Function reference confidence ({indirect_ref.confidence}) should be "
            f"lower than direct call confidence ({direct_call.confidence})"
        )


class TestGoAmbiguousMethodCallGuard:
    """Tests for the ambiguous method call resolution guard.

    When a method call ``x.Method()`` cannot be resolved to a specific
    receiver type and the method name has 3+ definitions across different
    types, the system must NOT produce a resolved call edge (which would
    be a false positive). Instead it should produce an unresolved edge
    with evidence_type="ambiguous_method_call".

    Invariant: Method calls with 3+ ambiguous receiver types must not
    produce resolved call edges.
    """

    def test_ambiguous_method_three_plus_types_produces_unresolved(self, tmp_path: Path) -> None:
        """x.Close() with 3 types defining Close() → unresolved edge.

        When Server, Client, and Worker all define Close(), and x's type
        cannot be inferred, the call should produce an unresolved edge
        rather than picking an arbitrary candidate.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}
type Worker struct{}

func (s *Server) Close() {}
func (c *Client) Close() {}
func (w *Worker) Close() {}

func cleanup(x interface{}) {
    x.Close()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cleanup_calls = [e for e in call_edges if "cleanup" in e.src]

        # Should have an edge for x.Close()
        close_calls = [e for e in cleanup_calls if "Close" in e.dst]
        assert len(close_calls) >= 1, (
            f"cleanup should have a call edge for x.Close(), found: {cleanup_calls}"
        )

        # The edge should be UNRESOLVED (ambiguous_method_call), not resolved
        # to any specific type
        for edge in close_calls:
            assert edge.evidence_type == "ambiguous_method_call", (
                f"x.Close() with 3+ candidates should have evidence_type='ambiguous_method_call', "
                f"got '{edge.evidence_type}'"
            )
            assert edge.confidence <= 0.55, (
                f"Ambiguous method call should have low confidence, got {edge.confidence}"
            )
            # Should NOT resolve to any specific type
            assert "Server.Close" not in edge.dst, (
                f"Should not resolve to Server.Close, got {edge.dst}"
            )
            assert "Client.Close" not in edge.dst, (
                f"Should not resolve to Client.Close, got {edge.dst}"
            )
            assert "Worker.Close" not in edge.dst, (
                f"Should not resolve to Worker.Close, got {edge.dst}"
            )

    def test_two_candidates_still_resolves(self, tmp_path: Path) -> None:
        """x.Run() with only 2 types → still resolves (guard threshold is 3+).

        The ambiguity guard only activates at 3+ candidates. With 2 candidates,
        the ListNameResolver picks one with 1/sqrt(2) confidence.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func (s *Server) Run() {}
func (c *Client) Run() {}

func start(x interface{}) {
    x.Run()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        start_calls = [e for e in call_edges if "start" in e.src]

        # With 2 candidates, should still produce a resolved edge
        run_calls = [e for e in start_calls if "Run" in e.dst]
        assert len(run_calls) >= 1, (
            f"start should have call edge for x.Run() even with 2 candidates, "
            f"found: {start_calls}"
        )

        # Should NOT be marked as ambiguous_method_call
        for edge in run_calls:
            assert edge.evidence_type != "ambiguous_method_call", (
                "2-candidate method should not trigger ambiguity guard"
            )

    def test_package_qualified_calls_unaffected(self, tmp_path: Path) -> None:
        """fmt.Println() is never guarded even if many packages define Println.

        Package-qualified calls should bypass the ambiguity guard entirely
        since the package alias resolves to a specific import path.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "fmt"

func main() {
    fmt.Println("hello")
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        # No edge should have evidence_type="ambiguous_method_call"
        for edge in main_calls:
            assert edge.evidence_type != "ambiguous_method_call", (
                f"Package-qualified call should not be guarded, got {edge.evidence_type}"
            )

    def test_typed_receiver_bypasses_guard(self, tmp_path: Path) -> None:
        """s.Close() where s has known type bypasses guard even with 3+ candidates.

        When the variable type is inferred (e.g., s := &Server{}), the call
        is resolved via typed_receiver_call and should NOT trigger the guard.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}
type Worker struct{}

func (s *Server) Close() {}
func (c *Client) Close() {}
func (w *Worker) Close() {}

func main() {
    s := &Server{}
    s.Close()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        close_calls = [e for e in main_calls if "Close" in e.dst]
        assert len(close_calls) >= 1, (
            f"main should have call edge for s.Close(), found: {main_calls}"
        )

        # Should resolve to Server.Close (typed) — NOT ambiguous
        assert any("Server.Close" in e.dst for e in close_calls), (
            f"s.Close() should resolve to Server.Close, found: {[e.dst for e in close_calls]}"
        )
        assert all(e.evidence_type != "ambiguous_method_call" for e in close_calls), (
            "Typed receiver call should not be marked as ambiguous"
        )


class TestGoEnclosingFunctionAttribution:
    """Tests for correct enclosing function attribution with same-name methods.

    When Server.Get and Client.Get both exist, _get_enclosing_function should
    correctly attribute edges to the right enclosing method via qualified name
    lookup when the short name is ambiguous.
    """

    def test_same_name_methods_attributed_correctly(self, tmp_path: Path) -> None:
        """Server.Get and Client.Get both calling helper() → correct attribution.

        When two types define the same method name, edges from within
        each method should be attributed to the correct qualified symbol
        (Server.Get vs Client.Get), not to whichever happened to be
        last in symbol_by_name.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}
type Client struct{}

func helper() {}

func (s *Server) Get() {
    helper()
}

func (c *Client) Get() {
    helper()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # Find edges to helper
        helper_edges = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_edges) >= 2, (
            f"Both Server.Get and Client.Get should call helper(), "
            f"found {len(helper_edges)} edges: {helper_edges}"
        )

        # Both Server.Get and Client.Get should be sources
        sources = {e.src for e in helper_edges}
        has_server_get = any("Server.Get" in s for s in sources)
        has_client_get = any("Client.Get" in s for s in sources)
        assert has_server_get, (
            f"Server.Get should be a source of helper() call, sources: {sources}"
        )
        assert has_client_get, (
            f"Client.Get should be a source of helper() call, sources: {sources}"
        )
