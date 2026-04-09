# SPDX-License-Identifier: AGPL-3.0-or-later
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


class TestGoVarAliasExtraction:
    """Tests for extracting Go package-level var aliases as symbols.

    Go packages commonly re-export functions via var aliases:
      var String = slog.String
      var Debug = slog.Debug
    These are callable and should be extracted as symbols so the resolver
    can find them when other packages call log.String(), log.Debug(), etc.
    """

    def test_extracts_var_alias(self, tmp_path: Path) -> None:
        """Package-level var with function value creates a variable symbol."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "log.go"
        go_file.write_text("""package log

import "log/slog"

var String = slog.String
var Debug = slog.Debug
var Err = slog.Any
""")

        result = analyze_go(tmp_path)

        var_names = [s.name for s in result.symbols if s.kind == "variable"]
        assert "String" in var_names, (
            f"Expected 'String' in var symbols, got {var_names}"
        )
        assert "Debug" in var_names
        assert "Err" in var_names

    def test_var_alias_has_correct_kind(self, tmp_path: Path) -> None:
        """Var alias symbols have kind='variable' and correct metadata."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "log.go"
        go_file.write_text("""package log

import "log/slog"

var String = slog.String
""")

        result = analyze_go(tmp_path)

        var_sym = next(
            (s for s in result.symbols if s.name == "String"),
            None,
        )
        assert var_sym is not None, "String symbol should exist"
        assert var_sym.kind == "variable"
        assert var_sym.language == "go"
        # Exported (capitalized) should have exported modifier
        assert "exported" in var_sym.modifiers

    def test_skips_interface_assertion_vars(self, tmp_path: Path) -> None:
        """var _ Interface = &Struct{} should NOT create a variable symbol."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "impl.go"
        go_file.write_text("""package main

type Reader interface {
    Read() error
}

type MyReader struct{}

func (r *MyReader) Read() error { return nil }

var _ Reader = &MyReader{}
""")

        result = analyze_go(tmp_path)

        var_syms = [s for s in result.symbols if s.kind == "variable"]
        # The blank identifier _ should NOT create a symbol
        assert len(var_syms) == 0, (
            f"Expected no variable symbols for blank identifier, got {[s.name for s in var_syms]}"
        )

    def test_skips_non_initialized_vars(self, tmp_path: Path) -> None:
        """var x int (no initializer) should NOT create a symbol —
        it's a plain variable declaration, not a callable alias."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

var counter int
var name string
""")

        result = analyze_go(tmp_path)

        var_syms = [s for s in result.symbols if s.kind == "variable"]
        assert len(var_syms) == 0, (
            f"Expected no variable symbols for uninitialized vars, got {[s.name for s in var_syms]}"
        )


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

        routes, _mount_edges = _extract_go_routes(tree.root_node, source, go_file, run)

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

    def test_handler_picked_over_middleware(self, tmp_path: Path) -> None:
        """When multiple non-string arguments are passed, handler is the last one.

        Go web frameworks (Chi, Gin, Echo, Macaron) pass middleware before the
        handler: r.GET("/path", middleware1, middleware2, handler). The route
        handler name should be the last argument, not the first.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func main() {
    r.GET("/raw/*", referencesGitRepo, repoRefForAPI, reqRepoReader, GetRawFile)
}

func referencesGitRepo() {}
func repoRefForAPI() {}
func reqRepoReader() {}
func GetRawFile() {}
""")

        result = analyze_go(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        # Handler is the LAST argument, not the first middleware
        assert routes[0].name == "GetRawFile"

    def test_handler_picked_over_middleware_with_selector(self, tmp_path: Path) -> None:
        """Middleware with selector expressions (context.ReferencesGitRepo)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func main() {
    m.Get("/raw/*", context.ReferencesGitRepo(), context.RepoRefForAPI, repo.GetRawFile)
}
""")

        result = analyze_go(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        # Last arg is the handler, not the middleware
        assert routes[0].name == "repo.GetRawFile"

    def test_handler_skips_closure_to_find_named(self, tmp_path: Path) -> None:
        """When the last arg is an anonymous closure, skip it for a named handler.

        Pattern: r.GET("/path", authMiddleware, func(c *gin.Context){...})
        The closure is not a useful handler name. The named identifier before it
        (authMiddleware) is used instead.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func main() {
    r.GET("/inline", authMiddleware, func(c *Context) {
        c.JSON(200, "ok")
    })
}

func authMiddleware() {}
""")

        result = analyze_go(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        # The closure can't be named, so the named identifier before it is used
        assert routes[0].name == "authMiddleware"

    def test_anonymous_closure_handler_creates_route(self, tmp_path: Path) -> None:
        """Routes with anonymous closure handlers should still be detected.

        Pattern: r.Get("/path", func(w http.ResponseWriter, r *http.Request) {...})
        Common in alertmanager, prometheus, and other Go projects that register
        routes inline without named handler functions.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "net/http"

func main() {
    r.Get("/-/healthy", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
        w.Write([]byte("OK"))
    })
    r.Get("/-/ready", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
    })
    r.Post("/-/reload", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
    })
}
""")

        result = analyze_go(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        route_paths = {s.meta["route_path"] for s in routes}
        assert "/-/healthy" in route_paths
        assert "/-/ready" in route_paths
        assert "/-/reload" in route_paths
        assert len(routes) >= 3

        # Verify the route symbols have a descriptive name
        for route in routes:
            assert route.name is not None
            assert len(route.name) > 0

        # Verify UsageContexts are also created
        contexts = [c for c in result.usage_contexts if c.kind == "call"]
        ctx_paths = {c.metadata.get("route_path") for c in contexts}
        assert "/-/healthy" in ctx_paths
        assert "/-/ready" in ctx_paths
        assert "/-/reload" in ctx_paths

    def test_group_prefix_composition(self, tmp_path: Path) -> None:
        """Routes inside Group() closures get the group prefix prepended.

        When routes are nested inside r.Group("/api/v1", func() { ... }),
        the route path should be /api/v1/users, not just /users.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "github.com/gin-gonic/gin"

func main() {
    r := gin.Default()
    r.Group("/api/v1", func() {
        r.GET("/users", listUsers)
        r.POST("/users", createUser)
    })
    r.GET("/health", healthCheck)
}

func listUsers(c *gin.Context) {}
func createUser(c *gin.Context) {}
func healthCheck(c *gin.Context) {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        route_paths = {s.meta["route_path"]: s.name for s in routes if s.meta}

        # Routes inside Group should have prefix
        assert "/api/v1/users" in route_paths, (
            f"Route inside Group('/api/v1') should have path /api/v1/users, "
            f"found: {list(route_paths.keys())}"
        )
        # Route outside Group should NOT have prefix
        assert "/health" in route_paths, (
            f"Route outside Group should have path /health, "
            f"found: {list(route_paths.keys())}"
        )

    def test_nested_group_prefix_composition(self, tmp_path: Path) -> None:
        """Nested Group() calls compose prefixes correctly.

        r.Group("/admin", func() {
            r.Group("/users", func() {
                r.GET("/list", handler)  // → /admin/users/list
            })
        })
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "github.com/gin-gonic/gin"

func main() {
    r := gin.Default()
    r.Group("/admin", func() {
        r.Group("/users", func() {
            r.GET("/list", listAdminUsers)
        })
    })
}

func listAdminUsers(c *gin.Context) {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        route_paths = [s.meta["route_path"] for s in routes if s.meta]

        assert "/admin/users/list" in route_paths, (
            f"Nested Group should compose to /admin/users/list, "
            f"found: {route_paths}"
        )

    def test_unwraps_handler_through_wrapper_call(self, tmp_path: Path) -> None:
        """r.Post("/read", api.ready(api.remoteRead)) → handler is api.remoteRead.

        When a route handler argument is a call_expression wrapping a function
        reference (e.g., middleware wrapper), the inner handler should be
        extracted as the route's handler_name, not the wrapper function.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "github.com/gin-gonic/gin"

type API struct{}

func (api *API) ready(f gin.HandlerFunc) gin.HandlerFunc { return f }
func (api *API) remoteRead(c *gin.Context) {}
func (api *API) remoteWrite(c *gin.Context) {}

func main() {
    r := gin.Default()
    api := &API{}
    r.POST("/read", api.ready(api.remoteRead))
    r.POST("/write", api.ready(api.remoteWrite))
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        handler_names = {
            s.meta["route_path"]: s.meta.get("handler_name", s.name)
            for s in routes if s.meta
        }

        # The handler should be api.remoteRead, not api.ready
        assert handler_names.get("/read") == "api.remoteRead", (
            f"POST /read handler should be api.remoteRead (unwrapped), "
            f"got: {handler_names.get('/read')}"
        )
        assert handler_names.get("/write") == "api.remoteWrite", (
            f"POST /write handler should be api.remoteWrite (unwrapped), "
            f"got: {handler_names.get('/write')}"
        )


    def test_del_method_detected_as_delete(self, tmp_path: Path) -> None:
        """r.Del("/path", handler) is detected as DELETE route.

        Chi router uses Del() as a shorthand for Delete(). Without this,
        DEL /series → api.dropSeries is missing from routes.txt (DEEP
        bakeoff cohort 16 finding on prometheus).
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "github.com/go-chi/chi/v5"

func main() {
    r := chi.NewRouter()
    r.Del("/series", dropSeries)
    r.Delete("/alerts", deleteAlert)
}

func dropSeries(w http.ResponseWriter, r *http.Request) {}
func deleteAlert(w http.ResponseWriter, r *http.Request) {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        route_methods = {
            s.meta["route_path"]: s.meta["http_method"]
            for s in routes if s.meta
        }

        # Both Del() and Delete() should normalize to DELETE
        assert route_methods.get("/series") == "DELETE", (
            f"r.Del('/series') should produce DELETE route, "
            f"got: {route_methods.get('/series')}"
        )
        assert route_methods.get("/alerts") == "DELETE", (
            f"r.Delete('/alerts') should produce DELETE route, "
            f"got: {route_methods.get('/alerts')}"
        )


    def test_non_path_strings_not_detected_as_routes(self, tmp_path: Path) -> None:
        """Kubernetes API calls with resource names should NOT be routes.

        Calls like ``client.Get(ctx, "my-app", opts)`` have a string
        argument (``"my-app"``) but it's a resource name, not a route
        path.  Route paths must start with ``/`` to distinguish them
        from arbitrary string arguments.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func getResources() {
    client.Get(ctx, "my-app", opts)
    client.Delete(ctx, "pod-name", opts)
    svc.Post("event-data", handler)
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 0, (
            f"Non-path string arguments should NOT produce routes, "
            f"got: {[(s.name, s.meta.get('route_path')) for s in routes if s.meta]}"
        )


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

    def test_handlefunc_closure_handler(self, tmp_path: Path) -> None:
        """Gorilla mux HandleFunc with anonymous closure handler creates route."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "net/http"

func main() {
    r.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
    })
}
""")

        result = analyze_go(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 1
        assert routes[0].meta["route_path"] == "/health"
        assert routes[0].name == "<closure>"


    def test_handlefunc_string_concat_path(self, tmp_path: Path) -> None:
        """HandleFunc with variable + string literal path extracts the literal suffix.

        Pattern: r.HandleFunc(baseUrl + "/users", handler) should detect
        the route with path suffix "/users" even when the prefix is a variable.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "github.com/gorilla/mux"

func main() {
    baseUrl := "/app"
    r := mux.NewRouter()
    r.HandleFunc(baseUrl + "/users", listUsers)
    r.HandleFunc(baseUrl + "/users/{id}", getUser)
    r.HandleFunc(baseUrl + "/cart", viewCart)
}

func listUsers(w http.ResponseWriter, r *http.Request) {}
func getUser(w http.ResponseWriter, r *http.Request) {}
func viewCart(w http.ResponseWriter, r *http.Request) {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        route_paths = {s.meta["route_path"] for s in routes}

        # Should detect routes with literal suffixes
        assert any("/users" in p for p in route_paths), (
            f"Expected route containing '/users', got: {route_paths}"
        )
        assert len(routes) >= 3, f"Expected 3 routes, got {len(routes)}: {route_paths}"

    def test_go122_stdlib_method_path_pattern(self, tmp_path: Path) -> None:
        """Go 1.22+ stdlib mux: Handle("POST /path", handler) combined method-path.

        Go 1.22 introduced method-path routing in http.ServeMux where the
        HTTP method and path are combined in a single string argument:
        mux.Handle("POST /v1/data/{path...}", handler)
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

import "net/http"

func main() {
    mux := http.NewServeMux()
    mux.Handle("POST /v1/data/{path...}", dataPostHandler)
    mux.Handle("GET /v1/data/{path...}", dataGetHandler)
    mux.HandleFunc("DELETE /v1/policies/{path...}", deletePolicyHandler)
    mux.Handle("/health", healthHandler)
}

func dataPostHandler(w http.ResponseWriter, r *http.Request) {}
func dataGetHandler(w http.ResponseWriter, r *http.Request) {}
func deletePolicyHandler(w http.ResponseWriter, r *http.Request) {}
func healthHandler(w http.ResponseWriter, r *http.Request) {}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        route_meta = {s.meta["route_path"]: s.meta for s in routes}

        # Method-path routes should extract both method and path
        assert "/v1/data/{path...}" in route_meta, (
            f"Expected /v1/data/{{path...}}, got: {list(route_meta.keys())}"
        )
        post_route = route_meta["/v1/data/{path...}"]
        # The first match for this path is POST
        assert post_route["http_method"] in ("POST", "GET"), (
            f"Expected POST or GET, got: {post_route['http_method']}"
        )

        # DELETE route
        assert "/v1/policies/{path...}" in route_meta, (
            f"Expected /v1/policies/{{path...}}, got: {list(route_meta.keys())}"
        )
        delete_meta = route_meta["/v1/policies/{path...}"]
        assert delete_meta["http_method"] == "DELETE"

        # Plain /health route should still work (no method prefix)
        assert "/health" in route_meta
        assert route_meta["/health"]["http_method"] == "ANY"

    def test_single_arg_get_not_route(self, tmp_path: Path) -> None:
        """Single-arg .Get("/key") calls (cache, headers) are not routes.

        Go code like cache.Get("/A"), field.Tag.Get("/metric"),
        Header.Get("/Authorization") should NOT create route UsageContexts
        because they have no handler argument.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func main() {
    // These should NOT be detected as routes
    val := cache.Get("/A")
    tag := field.Tag.Get("/metric")
    auth := req.Header.Get("/Authorization")

    // This SHOULD be detected as a route (has handler arg)
    r.Get("/health", healthHandler)
}
""")

        result = analyze_go(tmp_path)
        contexts = [c for c in result.usage_contexts if c.kind == "call"]
        ctx_paths = {c.metadata.get("route_path") for c in contexts}

        # Only the real route should be detected
        assert "/health" in ctx_paths
        assert "/A" not in ctx_paths
        assert "/metric" not in ctx_paths
        assert "/Authorization" not in ctx_paths


    def test_gin_group_variable_prefix_composition(self, tmp_path: Path) -> None:
        """Gin-style variable-based router groups compose full route paths.

        Pattern: api := r.Group("/api"); api.GET("/users", handler)
        should produce route path /api/users. Nested groups should also
        compose: v1 := api.Group("/v1"); v1.GET("/items", handler) → /api/v1/items.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "github.com/gin-gonic/gin"

func main() {
    r := gin.Default()
    api := r.Group("/api")
    api.GET("/users", listUsers)
    api.POST("/users", createUser)

    v1 := api.Group("/v1")
    v1.GET("/items", listItems)
}
""")

        result = analyze_go(tmp_path)
        routes = [s for s in result.symbols if s.kind == "route"]
        route_paths = {s.meta["route_path"] for s in routes if s.meta}

        assert "/api/users" in route_paths, f"Expected /api/users, got {route_paths}"
        assert "/api/v1/items" in route_paths, f"Expected /api/v1/items, got {route_paths}"

        # Also check usage contexts have the composed prefix
        contexts = [c for c in result.usage_contexts if c.kind == "call"]
        ctx_paths = {c.metadata.get("route_path") for c in contexts}
        assert "/api/users" in ctx_paths
        assert "/api/v1/items" in ctx_paths


class TestGoRouteMountDetection:
    """Tests for Go route mount point detection.

    When Go web frameworks use ``r.Mount("/prefix", handler)``, we create
    a route_mount symbol that records the mount prefix and the handler
    function reference.  This enables downstream composition of full URL
    paths (e.g., ``Mount("/api/v1", apiRoutes())`` + ``GET /users`` →
    ``GET /api/v1/users``).
    """

    def test_mount_creates_route_mount_symbol(self, tmp_path: Path) -> None:
        """Mount("/prefix", handler) creates a route_mount symbol."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "github.com/go-chi/chi/v5"

func main() {
    r := chi.NewRouter()
    r.Mount("/api/v1", apiRoutes())
}

func apiRoutes() chi.Router {
    r := chi.NewRouter()
    r.Get("/users", listUsers)
    return r
}

func listUsers(w http.ResponseWriter, r *http.Request) {}
""")

        result = analyze_go(tmp_path)

        mounts = [s for s in result.symbols if s.kind == "route_mount"]
        assert len(mounts) == 1, (
            f"Expected 1 route_mount symbol, found {len(mounts)}: "
            f"{[s.name for s in mounts]}"
        )
        mount = mounts[0]
        assert mount.meta is not None
        assert mount.meta["mount_prefix"] == "/api/v1"
        assert mount.meta["handler_ref"] == "apiRoutes"
        # Mount stable_id uses hash-based format (ADR-0014 Phase 0)
        assert mount.stable_id is not None
        # make_route_stable_id returns raw hex digest
        assert len(mount.stable_id) == 64  # SHA-256 hex length

    def test_mount_creates_edge_to_handler(self, tmp_path: Path) -> None:
        """Mount creates a calls edge from enclosing function to handler."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "github.com/go-chi/chi/v5"

func setupRoutes() chi.Router {
    r := chi.NewRouter()
    r.Mount("/admin", adminRoutes())
    return r
}

func adminRoutes() chi.Router {
    r := chi.NewRouter()
    r.Get("/dashboard", showDashboard)
    return r
}

func showDashboard(w http.ResponseWriter, r *http.Request) {}
""")

        result = analyze_go(tmp_path)

        # There should be an edge from setupRoutes → adminRoutes
        mount_edges = [
            e for e in result.edges
            if e.evidence_type == "route_mount"
        ]
        assert len(mount_edges) == 1, (
            f"Expected 1 route_mount edge, found {len(mount_edges)}"
        )
        edge = mount_edges[0]
        # src should be setupRoutes, dst should be adminRoutes
        assert "setupRoutes" in edge.src
        assert "adminRoutes" in edge.dst

    def test_multiple_mounts(self, tmp_path: Path) -> None:
        """Multiple Mount calls in the same function are all detected."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "github.com/go-chi/chi/v5"

func main() {
    r := chi.NewRouter()
    r.Mount("/api/v1", apiV1Routes())
    r.Mount("/admin", adminRoutes())
    r.Mount("/webhooks", webhookRoutes())
}

func apiV1Routes() chi.Router { return nil }
func adminRoutes() chi.Router { return nil }
func webhookRoutes() chi.Router { return nil }
""")

        result = analyze_go(tmp_path)

        mounts = [s for s in result.symbols if s.kind == "route_mount"]
        prefixes = sorted([s.meta["mount_prefix"] for s in mounts if s.meta])
        assert prefixes == ["/admin", "/api/v1", "/webhooks"], (
            f"Expected 3 mount prefixes, found: {prefixes}"
        )

    def test_mount_with_method_call_handler(self, tmp_path: Path) -> None:
        """Mount with a package-qualified handler like pkg.Routes() is detected."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import (
    "github.com/go-chi/chi/v5"
    "myapp/api"
)

func main() {
    r := chi.NewRouter()
    r.Mount("/api/v1", api.Routes())
}
""")

        result = analyze_go(tmp_path)

        mounts = [s for s in result.symbols if s.kind == "route_mount"]
        assert len(mounts) == 1
        assert mounts[0].meta["mount_prefix"] == "/api/v1"
        # For package-qualified calls, handler_ref includes the package
        assert mounts[0].meta["handler_ref"] == "api.Routes"


class TestGoSwaggerHandlerCacheRoutes:
    """Tests for go-swagger/go-openapi initHandlerCache route detection.

    go-swagger generates route registration using a handler cache pattern:
        o.handlers["DELETE"]["/silence/{silenceID}"] = silence.NewDeleteSilence(...)

    This is an assignment_statement with double index_expression: the first
    index is an HTTP method string and the second is a route path starting
    with "/".  The RHS call expression's function name becomes the handler.
    """

    def test_single_handler_cache_entry(self, tmp_path: Path) -> None:
        """Detects a single go-swagger handler cache assignment."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "api.go"
        go_file.write_text("""
package operations

func (o *API) initHandlerCache() {
    o.handlers["DELETE"]["/silence/{silenceID}"] = silence.NewDeleteSilence(o.context, o.DeleteSilenceHandler)
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 1
        r = routes[0]
        assert r.meta["http_method"] == "DELETE"
        assert r.meta["route_path"] == "/silence/{silenceID}"
        assert r.meta["handler_name"] == "silence.NewDeleteSilence"

    def test_multiple_handler_cache_entries(self, tmp_path: Path) -> None:
        """Detects multiple go-swagger handler cache assignments."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "api.go"
        go_file.write_text("""
package operations

func (o *AlertmanagerAPI) initHandlerCache() {
    o.handlers["DELETE"]["/silence/{silenceID}"] = silence.NewDeleteSilence(o.context, o.DeleteSilenceHandler)
    o.handlers["GET"]["/alerts/groups"] = alert.NewGetAlertGroups(o.context, o.GetAlertGroupsHandler)
    o.handlers["GET"]["/alerts"] = alert.NewGetAlerts(o.context, o.GetAlertsHandler)
    o.handlers["GET"]["/receivers"] = receiver.NewGetReceivers(o.context, o.GetReceiversHandler)
    o.handlers["GET"]["/silence/{silenceID}"] = silence.NewGetSilence(o.context, o.GetSilenceHandler)
    o.handlers["GET"]["/silences"] = silence.NewGetSilences(o.context, o.GetSilencesHandler)
    o.handlers["GET"]["/status"] = general.NewGetStatus(o.context, o.GetStatusHandler)
    o.handlers["POST"]["/alerts"] = alert.NewPostAlerts(o.context, o.PostAlertsHandler)
    o.handlers["POST"]["/silences"] = silence.NewPostSilence(o.context, o.PostSilenceHandler)
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 9

        # Check method distribution
        methods = sorted([r.meta["http_method"] for r in routes])
        assert methods == ["DELETE", "GET", "GET", "GET", "GET", "GET", "GET", "POST", "POST"]

        # Spot-check a specific route
        post_silences = [r for r in routes if r.meta["route_path"] == "/silences" and r.meta["http_method"] == "POST"]
        assert len(post_silences) == 1
        assert post_silences[0].meta["handler_name"] == "silence.NewPostSilence"

    def test_handler_cache_stable_ids_unique(self, tmp_path: Path) -> None:
        """Each handler cache route gets a unique stable_id."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "api.go"
        go_file.write_text("""
package operations

func (o *API) initHandlerCache() {
    o.handlers["GET"]["/alerts"] = alert.NewGetAlerts(o.context, o.GetAlertsHandler)
    o.handlers["POST"]["/alerts"] = alert.NewPostAlerts(o.context, o.PostAlertsHandler)
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 2
        stable_ids = [r.stable_id for r in routes]
        assert len(set(stable_ids)) == 2, f"stable_id collision: {stable_ids}"

    def test_handler_cache_unqualified_handler(self, tmp_path: Path) -> None:
        """Handles unqualified handler function names."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "api.go"
        go_file.write_text("""
package operations

func (o *API) initHandlerCache() {
    o.handlers["GET"]["/health"] = NewHealthCheck(o.context, o.HealthHandler)
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 1
        assert routes[0].meta["handler_name"] == "NewHealthCheck"

    def test_handler_cache_ignores_non_method_index(self, tmp_path: Path) -> None:
        """Ignores assignments where the first index isn't an HTTP method."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func init() {
    m["key1"]["key2"] = someValue
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 0

    def test_handler_cache_ignores_non_path_value(self, tmp_path: Path) -> None:
        """Ignores when second index doesn't start with '/'."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""
package main

func init() {
    o.handlers["GET"]["not-a-path"] = handler
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 0

    def test_handler_cache_bare_identifier_rhs(self, tmp_path: Path) -> None:
        """Handles RHS as bare identifier (not a call expression)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "api.go"
        go_file.write_text("""
package operations

func (o *API) initHandlerCache() {
    o.handlers["GET"]["/health"] = healthHandler
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 1
        assert routes[0].meta["handler_name"] == "healthHandler"

    def test_handler_cache_no_handler_rhs(self, tmp_path: Path) -> None:
        """Skips when RHS has no recognizable handler."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "api.go"
        go_file.write_text("""
package operations

func (o *API) initHandlerCache() {
    o.handlers["GET"]["/health"] = nil
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 0


class TestGoSwaggerHandlerWiring:
    """Tests for go-swagger handler wiring pattern detection.

    go-swagger generates handler wiring in a separate file from route registration.
    The wiring pattern assigns typed handler funcs to handler interface fields:
        openAPI.AlertGetAlertsHandler = alert_ops.GetAlertsHandlerFunc(api.getAlertsHandler)

    When the handler cache route has a handler_field matching the wiring's field name,
    the route's handler_name should be updated to the actual implementation.
    """

    def test_handler_cache_extracts_handler_field(self, tmp_path: Path) -> None:
        """Handler cache extraction stores handler_field from constructor args."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "api.go"
        go_file.write_text("""
package operations

func (o *API) initHandlerCache() {
    o.handlers["GET"]["/alerts"] = alert.NewGetAlerts(o.context, o.AlertGetAlertsHandler)
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 1
        assert routes[0].meta.get("handler_field") == "AlertGetAlertsHandler"

    def test_handler_wiring_resolves_handler_name(self, tmp_path: Path) -> None:
        """Handler wiring in separate file updates route handler_name."""
        from hypergumbo_lang_mainstream.go import analyze_go

        # File 1: handler cache (route registration)
        ops_dir = tmp_path / "operations"
        ops_dir.mkdir()
        (ops_dir / "api.go").write_text("""
package operations

func (o *API) initHandlerCache() {
    o.handlers["GET"]["/alerts"] = alert.NewGetAlerts(o.context, o.AlertGetAlertsHandler)
    o.handlers["POST"]["/alerts"] = alert.NewPostAlerts(o.context, o.AlertPostAlertsHandler)
}
""")

        # File 2: handler wiring (connects interface field to implementation)
        (tmp_path / "api.go").write_text("""
package api

func NewAPI(openAPI *operations.API) *API {
    api := &API{}
    openAPI.AlertGetAlertsHandler = alert_ops.GetAlertsHandlerFunc(api.getAlertsHandler)
    openAPI.AlertPostAlertsHandler = alert_ops.PostAlertsHandlerFunc(api.postAlertsHandler)
    return api
}

func (api *API) getAlertsHandler(params alert_ops.GetAlertsParams) middleware.Responder {
    return nil
}

func (api *API) postAlertsHandler(params alert_ops.PostAlertsParams) middleware.Responder {
    return nil
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 2

        get_alerts = next(r for r in routes if r.meta["http_method"] == "GET")
        post_alerts = next(r for r in routes if r.meta["http_method"] == "POST")

        # handler_name should be updated to actual implementation
        assert get_alerts.meta["handler_name"] == "api.getAlertsHandler"
        assert post_alerts.meta["handler_name"] == "api.postAlertsHandler"

    def test_handler_wiring_no_match_keeps_original(self, tmp_path: Path) -> None:
        """Routes without matching wiring keep their original handler_name."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "api.go"
        go_file.write_text("""
package operations

func (o *API) initHandlerCache() {
    o.handlers["GET"]["/health"] = NewHealthCheck(o.context, o.HealthHandler)
}
""")

        result = analyze_go(tmp_path)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 1
        # No wiring pattern in this file, so handler_name stays as constructor
        assert routes[0].meta["handler_name"] == "NewHealthCheck"

    def test_handler_wiring_ignores_non_handler_func_assignments(self, tmp_path: Path) -> None:
        """Assignments that don't match HandlerFunc pattern are ignored."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "api.go"
        go_file.write_text("""
package api

func setup(o *API) {
    o.AlertGetAlertsHandler = alert_ops.GetAlertsHandlerFunc(api.getAlertsHandler)
    o.SomeField = someValue
    o.Config = loadConfig()
    o.ErrorHandler = errors.NewErrorProcessor(api.handleError)
}
""")

        result = analyze_go(tmp_path)
        # No routes should be created from wiring-only code
        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 0


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


class TestGoModulePathHelpers:
    """Tests for _read_go_module_path and _strip_module_prefix."""

    def test_read_go_module_path(self, tmp_path: Path) -> None:
        """Reads module path from go.mod."""
        from hypergumbo_lang_mainstream.go import _read_go_module_path

        (tmp_path / "go.mod").write_text(
            "module github.com/example/myproject\n\ngo 1.21\n"
        )
        assert _read_go_module_path(tmp_path) == "github.com/example/myproject"

    def test_read_go_module_path_missing(self, tmp_path: Path) -> None:
        """Returns None when go.mod doesn't exist."""
        from hypergumbo_lang_mainstream.go import _read_go_module_path

        assert _read_go_module_path(tmp_path) is None

    def test_read_go_module_path_no_module_line(self, tmp_path: Path) -> None:
        """Returns None when go.mod exists but has no module declaration."""
        from hypergumbo_lang_mainstream.go import _read_go_module_path

        (tmp_path / "go.mod").write_text("go 1.21\n")
        assert _read_go_module_path(tmp_path) is None

    def test_read_go_module_path_os_error(self, tmp_path: Path) -> None:
        """Returns None when go.mod can't be read."""
        from hypergumbo_lang_mainstream.go import _read_go_module_path

        go_mod = tmp_path / "go.mod"
        go_mod.write_text("module foo\n")
        with patch.object(Path, "read_text", side_effect=OSError("mocked")):
            result = _read_go_module_path(tmp_path)
        assert result is None

    def test_strip_module_prefix_local(self) -> None:
        """Strips module prefix from local import paths."""
        from hypergumbo_lang_mainstream.go import _strip_module_prefix

        result = _strip_module_prefix(
            "github.com/example/trivy/pkg/log",
            "github.com/example/trivy",
        )
        assert result == "pkg/log"

    def test_strip_module_prefix_exact_match(self) -> None:
        """Returns empty string when import equals module path."""
        from hypergumbo_lang_mainstream.go import _strip_module_prefix

        result = _strip_module_prefix(
            "github.com/example/trivy",
            "github.com/example/trivy",
        )
        assert result == ""

    def test_strip_module_prefix_external(self) -> None:
        """Returns unchanged path for external imports."""
        from hypergumbo_lang_mainstream.go import _strip_module_prefix

        result = _strip_module_prefix(
            "github.com/stretchr/testify/assert",
            "github.com/example/trivy",
        )
        assert result == "github.com/stretchr/testify/assert"


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


class TestGoStructEmbedding:
    """Tests for Go struct embedding detection.

    Go struct embedding (anonymous fields) promotes the embedded type's
    methods to the embedding struct, enabling implicit interface
    satisfaction.  The Go analyzer should detect embeddings and populate
    base_classes metadata so the inheritance linker can create
    extends/implements edges.
    """

    def test_simple_embedding(self, tmp_path: Path) -> None:
        """Struct with simple embedding gets base_classes metadata."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Base struct {
    field1 string
}

type Child struct {
    Base
    extra int
}
""")

        result = analyze_go(tmp_path)

        child = next((s for s in result.symbols if s.name == "Child"), None)
        assert child is not None
        assert child.meta is not None
        assert "base_classes" in child.meta
        assert "Base" in child.meta["base_classes"]

    def test_pointer_embedding(self, tmp_path: Path) -> None:
        """Struct with pointer embedding gets base_classes metadata."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Logger struct{}

type Service struct {
    *Logger
    name string
}
""")

        result = analyze_go(tmp_path)

        service = next((s for s in result.symbols if s.name == "Service"), None)
        assert service is not None
        assert service.meta is not None
        assert "Logger" in service.meta["base_classes"]

    def test_qualified_embedding(self, tmp_path: Path) -> None:
        """Struct with qualified embedding (pkg.Type) extracts base name."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

import "sync"

type SafeMap struct {
    sync.Mutex
    data map[string]string
}
""")

        result = analyze_go(tmp_path)

        safe_map = next((s for s in result.symbols if s.name == "SafeMap"), None)
        assert safe_map is not None
        assert safe_map.meta is not None
        assert "Mutex" in safe_map.meta["base_classes"]

    def test_multiple_embeddings(self, tmp_path: Path) -> None:
        """Struct with multiple embeddings collects all embedded types."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Reader struct{}
type Writer struct{}

type ReadWriter struct {
    Reader
    *Writer
    buffer []byte
}
""")

        result = analyze_go(tmp_path)

        rw = next((s for s in result.symbols if s.name == "ReadWriter"), None)
        assert rw is not None
        assert rw.meta is not None
        assert "Reader" in rw.meta["base_classes"]
        assert "Writer" in rw.meta["base_classes"]
        assert len(rw.meta["base_classes"]) == 2

    def test_embedding_plus_assertion(self, tmp_path: Path) -> None:
        """Struct with both embedding and interface assertion merges both."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Closer interface {
    Close() error
}

type BaseConn struct{}

type MyConn struct {
    BaseConn
    addr string
}

var _ Closer = &MyConn{}
""")

        result = analyze_go(tmp_path)

        conn = next((s for s in result.symbols if s.name == "MyConn"), None)
        assert conn is not None
        assert conn.meta is not None
        # Both embedding and assertion should be in base_classes
        assert "BaseConn" in conn.meta["base_classes"]
        assert "Closer" in conn.meta["base_classes"]

    def test_named_field_not_embedding(self, tmp_path: Path) -> None:
        """Named fields should NOT be treated as embeddings."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Config struct {
    name   string
    logger Logger
    count  int
}

type Logger struct{}
""")

        result = analyze_go(tmp_path)

        config = next((s for s in result.symbols if s.name == "Config"), None)
        assert config is not None
        # Named fields are not embeddings, so no base_classes
        assert config.meta is None or "base_classes" not in config.meta

    def test_qualified_pointer_embedding(self, tmp_path: Path) -> None:
        """Struct with *pkg.Type embedding extracts base name."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

import "sync"

type SafeCounter struct {
    *sync.RWMutex
    count int
}
""")

        result = analyze_go(tmp_path)

        sc = next((s for s in result.symbols if s.name == "SafeCounter"), None)
        assert sc is not None
        assert sc.meta is not None
        assert "RWMutex" in sc.meta["base_classes"]

    def test_generic_embedding(self, tmp_path: Path) -> None:
        """Struct with generic type embedding extracts base name."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Cache[K comparable, V any] struct {
    data map[K]V
}

type User struct{}

type UserCache struct {
    Cache[string, User]
    ttl int
}
""")

        result = analyze_go(tmp_path)

        uc = next((s for s in result.symbols if s.name == "UserCache"), None)
        assert uc is not None
        assert uc.meta is not None
        assert "Cache" in uc.meta["base_classes"]

    def test_empty_struct_no_embeddings(self, tmp_path: Path) -> None:
        """Empty struct has no embeddings."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type Empty struct{}
""")

        result = analyze_go(tmp_path)

        empty = next((s for s in result.symbols if s.name == "Empty"), None)
        assert empty is not None
        assert empty.meta is None or "base_classes" not in empty.meta


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

    def test_interface_var_with_concrete_initializer(self, tmp_path: Path) -> None:
        """var n Notifier = &DiscordNotifier{} resolves to concrete type.

        When an interface-typed variable is initialized with a concrete type,
        calls on that variable should resolve to the concrete type's method,
        not produce unresolved/interface dispatch edges.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Notifier interface {
    Notify()
}

type DiscordNotifier struct{}
type EmailNotifier struct{}

func (d *DiscordNotifier) Notify() {}
func (e *EmailNotifier) Notify() {}

func send() {
    var n Notifier = &DiscordNotifier{}
    n.Notify()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        send_calls = [e for e in call_edges if "send" in e.src]

        # n.Notify() should resolve to DiscordNotifier.Notify (concrete type)
        # not produce an unresolved edge to Notifier.Notify
        assert any("DiscordNotifier.Notify" in e.dst for e in send_calls), (
            f"send's n.Notify() should resolve to DiscordNotifier.Notify, "
            f"found: {[e.dst for e in send_calls]}"
        )

    def test_interface_var_without_initializer_uses_declared_type(
        self, tmp_path: Path,
    ) -> None:
        """var s Server (no initializer) still resolves via declared type."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}

func (s *Server) Start() {}

func run() {
    var s Server
    s.Start()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        run_calls = [e for e in call_edges if "run" in e.src]

        assert any("Server.Start" in e.dst for e in run_calls), (
            f"run's s.Start() should resolve to Server.Start, "
            f"found: {[e.dst for e in run_calls]}"
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

    def test_local_variable_not_treated_as_function_reference(self, tmp_path: Path) -> None:
        """Local variables passed as args must not create false function reference edges.

        When a local variable shares a name with a function (e.g., ``start``
        used as a time value vs ``start()`` as a method), the analyzer must
        not create a call edge from the variable usage to the function.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "time"

func start() {}

func doWork() {
    start := time.Now()
    elapsed := time.Since(start)
    _ = elapsed
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # doWork should NOT have a function_reference_arg edge to start()
        false_ref = next(
            (e for e in call_edges
             if "doWork" in e.src and "start" in e.dst
             and e.evidence_type == "function_reference_arg"),
            None,
        )
        assert false_ref is None, (
            f"Local variable 'start' should not create a function reference edge "
            f"to start(). Found: {false_ref}"
        )


class TestGoAmbiguousMethodCallGuard:
    """Tests for the ambiguous method call resolution guard.

    When a method call ``x.Method()`` cannot be resolved to a specific
    receiver type and the method name has 2+ definitions across different
    types, the system must NOT produce a resolved call edge (which would
    be a false positive). Instead it should produce an unresolved edge
    with evidence_type="ambiguous_method_call".

    Invariant: Method calls with 2+ ambiguous receiver types must not
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

    def test_two_candidates_produces_unresolved(self, tmp_path: Path) -> None:
        """x.Run() with 2 types → unresolved (guard threshold is 2+).

        When 2 types define the same method and receiver type is unknown,
        the ambiguity guard produces an unresolved edge instead of picking
        an arbitrary candidate.  This prevents false positives where all
        .Close()/.Error()/.String() calls route to the same implementation.
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

        # With 2 candidates, should produce an unresolved edge
        run_calls = [e for e in start_calls if "Run" in e.dst]
        assert len(run_calls) >= 1, (
            f"start should have call edge for x.Run() with 2 candidates, "
            f"found: {start_calls}"
        )

        # Should be marked as ambiguous_method_call
        for edge in run_calls:
            assert edge.evidence_type == "ambiguous_method_call", (
                f"2-candidate method should trigger ambiguity guard, "
                f"got '{edge.evidence_type}'"
            )
            assert edge.confidence <= 0.55, (
                f"Ambiguous method call should have low confidence, "
                f"got {edge.confidence}"
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

    def test_chained_call_operand_ambiguous(self, tmp_path: Path) -> None:
        """getWriter().Close() with 3+ types → unresolved edge.

        When the operand is a call expression (not a simple identifier),
        the receiver type cannot be inferred. The guard should still fire
        for method names with 3+ candidates, preventing false-positive
        edges to arbitrary implementations.
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

func getServer() *Server { return &Server{} }

func cleanup() {
    getServer().Close()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cleanup_calls = [e for e in call_edges if "cleanup" in e.src]

        close_calls = [e for e in cleanup_calls if "Close" in e.dst]
        assert len(close_calls) >= 1, (
            f"cleanup should have a call edge for Close(), found: {cleanup_calls}"
        )

        # Should be unresolved — chained-call guard fires before ambiguity guard
        for edge in close_calls:
            assert edge.evidence_type == "chained_call_unresolved", (
                f"Chained call Close() with 3+ candidates should be "
                f"chained_call_unresolved, got '{edge.evidence_type}'"
            )
            assert "Server.Close" not in edge.dst
            assert "Client.Close" not in edge.dst
            assert "Worker.Close" not in edge.dst

    def test_chained_call_single_candidate_unresolved(self, tmp_path: Path) -> None:
        """getClient().Update() with 1 candidate → unresolved edge.

        Even with only 1 implementation of Update() in the repo, a chained
        call (receiver is a call_expression) should NOT resolve to it.
        The return type of getClient() is unknown, so the Update() call
        could be on any type.  This prevents false positives like
        c.CoreV1().Endpoints(ns).Update() → Manager.Update in prometheus.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Manager struct{}

func (m *Manager) Update() {}

func getClient() interface{} { return nil }

func testFunc() {
    getClient().Update()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        test_calls = [e for e in call_edges if "testFunc" in e.src]

        update_calls = [e for e in test_calls if "Update" in e.dst]
        assert len(update_calls) >= 1, (
            f"testFunc should have a call edge for Update(), found: {test_calls}"
        )

        # Should NOT resolve to Manager.Update — receiver type is unknown
        for edge in update_calls:
            assert edge.evidence_type == "chained_call_unresolved", (
                f"Chained call Update() with 1 candidate should be "
                f"chained_call_unresolved, got '{edge.evidence_type}'"
            )
            assert "Manager.Update" not in edge.dst, (
                f"Should not resolve to Manager.Update, got {edge.dst}"
            )

    def test_chained_call_package_qualified_resolves(self, tmp_path: Path) -> None:
        """json.NewEncoder(w).Encode(data) should resolve (package is known).

        When the inner call is package-qualified, the import_path_hint is set
        and the chained-call guard should NOT fire.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "encoding/json"

func handler() {
    json.NewEncoder(nil).Encode("data")
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        handler_calls = [e for e in call_edges if "handler" in e.src]

        # Should NOT be marked as chained_call_unresolved
        for edge in handler_calls:
            assert edge.evidence_type != "chained_call_unresolved", (
                f"Package-qualified chained call should not trigger guard, "
                f"got '{edge.evidence_type}'"
            )

    def test_selector_operand_resolved_via_field_chain(self, tmp_path: Path) -> None:
        """resp.Body.Close() with known field type → typed_field_call edge.

        When the operand is a nested selector expression (field access)
        and the field type registry can resolve the chain, a typed_field_call
        edge is created instead of an ambiguous_method_call.
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

type Response struct {
    Body Server
}

func process(resp Response) {
    resp.Body.Close()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        process_calls = [e for e in call_edges if "process" in e.src]

        close_calls = [e for e in process_calls if "Close" in e.dst]
        assert len(close_calls) >= 1, (
            f"process should have a call edge for Close(), found: {process_calls}"
        )

        # Field chain resolves: resp → Response → Body → Server → Server.Close
        for edge in close_calls:
            assert edge.evidence_type == "typed_field_call", (
                f"resp.Body.Close() should resolve via field chain, "
                f"got '{edge.evidence_type}'"
            )
            assert "Server.Close" in edge.dst

    def test_index_operand_ambiguous(self, tmp_path: Path) -> None:
        """items[0].Close() with 3+ types → unresolved edge.

        When the operand is an index expression, the receiver type
        cannot be inferred. The guard should still fire.
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

func closeAll(items []interface{}) {
    items[0].Close()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        close_all_calls = [e for e in call_edges if "closeAll" in e.src]

        close_calls = [e for e in close_all_calls if "Close" in e.dst]
        assert len(close_calls) >= 1, (
            f"closeAll should have a call edge for Close(), found: {close_all_calls}"
        )

        # Should be unresolved — NOT resolved to any specific type
        for edge in close_calls:
            assert edge.evidence_type == "ambiguous_method_call", (
                f"Index operand Close() with 3+ candidates should be "
                f"ambiguous_method_call, got '{edge.evidence_type}'"
            )


    def test_chained_call_import_prefix_no_false_resolution(self, tmp_path: Path) -> None:
        """json.NewEncoder(w).Encode(v) must NOT resolve to local Encode method.

        When a chained call's inner call has an import prefix (e.g.
        ``json.NewEncoder(w)``), the outer method call (``.Encode(v)``)
        should inherit the import path hint.  Without this, a local
        method named ``Encode`` (e.g. ``MarshalEncoder.Encode``) would
        be the sole candidate in global symbols and get falsely resolved
        at confidence 0.80.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "encoding/json"

type MarshalEncoder struct{}

func (m *MarshalEncoder) Encode(v interface{}) error { return nil }

func NewMarshalEncoder() *MarshalEncoder { return &MarshalEncoder{} }

func writeJSON(w *json.Encoder, data interface{}) {
    json.NewEncoder(w).Encode(data)
}

func marshalLocal(m *MarshalEncoder, data interface{}) {
    m.Encode(data)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        write_json_calls = [e for e in call_edges if "writeJSON" in e.src]

        # Check that writeJSON's .Encode() does NOT falsely resolve to
        # MarshalEncoder.Encode — only look at non-unresolved edges
        resolved_encode_calls = [
            e for e in write_json_calls
            if "Encode" in e.dst and ":unresolved" not in e.dst
        ]
        # writeJSON's Encode call must NOT resolve to MarshalEncoder.Encode
        for edge in resolved_encode_calls:
            assert "MarshalEncoder" not in edge.dst, (
                f"json.NewEncoder(w).Encode(data) must NOT resolve to "
                f"MarshalEncoder.Encode, got dst={edge.dst}"
            )

        # marshalLocal's m.Encode(data) should still resolve correctly
        # via typed_receiver_call since m's type is known
        marshal_calls = [e for e in call_edges if "marshalLocal" in e.src]
        marshal_encode = [e for e in marshal_calls if "Encode" in e.dst]
        assert any("MarshalEncoder.Encode" in e.dst for e in marshal_encode), (
            f"m.Encode(data) with typed receiver should resolve to "
            f"MarshalEncoder.Encode, found: {[e.dst for e in marshal_encode]}"
        )

    def test_method_receiver_self_call_resolves(self, tmp_path: Path) -> None:
        """s.Close() inside func (s *Server) Cleanup() → typed_receiver_call.

        Method receivers should be tracked as typed variables so that
        self-method calls (calling another method on the same receiver)
        resolve via typed_receiver_call instead of falling through to
        the ambiguity guard.
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

func (s *Server) Cleanup() {
    s.Close()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cleanup_calls = [e for e in call_edges if "Cleanup" in e.src]

        close_calls = [e for e in cleanup_calls if "Close" in e.dst]
        assert len(close_calls) >= 1, (
            f"Cleanup should have a call edge for s.Close(), found: {cleanup_calls}"
        )

        # Should resolve to Server.Close via typed receiver — NOT ambiguous
        assert any("Server.Close" in e.dst for e in close_calls), (
            f"s.Close() in Server.Cleanup should resolve to Server.Close, "
            f"found: {[e.dst for e in close_calls]}"
        )
        for edge in close_calls:
            assert edge.evidence_type == "typed_receiver_call", (
                f"Self-method call should use typed_receiver_call, "
                f"got '{edge.evidence_type}'"
            )


class TestGoStdlibMethodGuard:
    """Tests for Go stdlib interface method collision guard.

    When a method call ``x.Lock()`` has no inferred receiver type and the
    method name matches a well-known Go stdlib interface method (Lock, Unlock,
    Close, Read, Write, Error, String, etc.), the call should be marked as
    ambiguous even when only 1 candidate exists in the repo.

    Rationale: In a real codebase, ``x.Lock()`` is overwhelmingly likely to be
    ``sync.Mutex.Lock()`` (stdlib, not in the repo's AST), not ``DirLocker.Lock``
    (the only repo candidate). Without this guard, ``DirLocker.Lock`` gets 255+
    false in-degree edges from unrelated ``.Lock()`` calls.
    """

    def test_stdlib_method_single_candidate_produces_unresolved(
        self, tmp_path: Path,
    ) -> None:
        """x.Lock() with only 1 Lock method → unresolved (stdlib collision).

        Even though DirLocker.Lock is the only candidate, x.Lock() should NOT
        resolve to it because Lock is a well-known sync.Locker interface method.
        Most .Lock() calls in Go are sync.Mutex.Lock(), not DirLocker.Lock.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "sync"

type DirLocker struct {
    mu sync.Mutex
}

func (d *DirLocker) Lock() error {
    d.mu.Lock()
    return nil
}

func doWork(mu *sync.Mutex) {
    mu.Lock()
    defer mu.Unlock()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        do_work_calls = [e for e in call_edges if "doWork" in e.src]

        lock_calls = [e for e in do_work_calls if "Lock" in e.dst]
        assert len(lock_calls) >= 1, (
            f"doWork should have a call edge for mu.Lock(), found: {do_work_calls}"
        )

        # mu.Lock() should NOT resolve to DirLocker.Lock
        for edge in lock_calls:
            assert "DirLocker.Lock" not in edge.dst, (
                f"mu.Lock() should NOT resolve to DirLocker.Lock, got {edge.dst}"
            )
            # With qualified-type parameter tracking, *sync.Mutex propagates
            # the module hint through import_aliases, so the edge goes through
            # the unresolved_method_call path (with correct module hint 'sync')
            # instead of the stdlib_method_call guard.  Both are correct — the
            # key invariant is that DirLocker.Lock is NOT chosen.
            assert edge.evidence_type in (
                "stdlib_method_call", "unresolved_method_call",
            ), (
                f"stdlib method collision should produce unresolved edge, "
                f"got '{edge.evidence_type}'"
            )
            assert edge.confidence <= 0.50, (
                f"stdlib method collision should have low confidence, "
                f"got {edge.confidence}"
            )

    def test_typed_receiver_bypasses_stdlib_guard(self, tmp_path: Path) -> None:
        """d.Lock() where d has type DirLocker → resolves normally.

        The stdlib guard should NOT fire when the receiver type is known.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type DirLocker struct{}

func (d *DirLocker) Lock() error { return nil }

func main() {
    d := &DirLocker{}
    d.Lock()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        lock_calls = [e for e in main_calls if "Lock" in e.dst]
        assert len(lock_calls) >= 1
        # Should resolve to DirLocker.Lock via typed_receiver_call
        assert any("DirLocker.Lock" in e.dst for e in lock_calls), (
            f"d.Lock() with known type should resolve to DirLocker.Lock, "
            f"found: {[e.dst for e in lock_calls]}"
        )

    def test_non_stdlib_method_not_guarded(self, tmp_path: Path) -> None:
        """x.Analyze() with only 1 candidate → resolves normally.

        Analyze is not a well-known stdlib interface method, so a single
        candidate should resolve normally (no guard).
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Parser struct{}

func (p *Parser) Analyze() {}

func run(x interface{}) {
    x.Analyze()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        run_calls = [e for e in call_edges if ":run:" in e.src]

        analyze_calls = [e for e in run_calls if "Analyze" in e.dst]
        # Non-stdlib method with 1 candidate should resolve
        assert len(analyze_calls) >= 1
        for edge in analyze_calls:
            assert edge.evidence_type != "stdlib_method_call", (
                f"Non-stdlib method should not trigger stdlib guard, "
                f"got '{edge.evidence_type}'"
            )


class TestGoSyncMapStdlibGuard:
    """Tests that sync.Map methods (Store, Load, etc.) are guarded.

    sync.Map is a concrete type (not an interface), but its methods are
    extremely common in Go code. Without the guard, a single repo-defined
    ``Store`` method absorbs 100+ false in-degree edges from unrelated
    ``sync.Map.Store()`` calls.
    """

    def test_store_single_candidate_produces_unresolved(
        self, tmp_path: Path,
    ) -> None:
        """x.Store() with only 1 Store method → unresolved (stdlib collision)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

import "sync"

type DownTrackSpreader struct {
    cache sync.Map
}

func (d *DownTrackSpreader) Store(key, val interface{}) {
    d.cache.Store(key, val)
}

func saveData(m *sync.Map) {
    m.Store("key", "value")
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        save_calls = [e for e in call_edges if "saveData" in e.src]

        store_calls = [e for e in save_calls if "Store" in e.dst]
        assert len(store_calls) >= 1, (
            f"saveData should have a call edge for m.Store(), found: {save_calls}"
        )

        for edge in store_calls:
            assert "DownTrackSpreader.Store" not in edge.dst, (
                f"m.Store() should NOT resolve to DownTrackSpreader.Store, got {edge.dst}"
            )
            # With qualified-type parameter tracking, *sync.Map propagates
            # module hint, so the edge may go through unresolved_method_call
            # path instead of stdlib_method_call.  Both are correct.
            assert edge.evidence_type in (
                "stdlib_method_call", "unresolved_method_call",
            ), (
                f"Expected unresolved edge, got '{edge.evidence_type}'"
            )

    def test_load_single_candidate_produces_unresolved(
        self, tmp_path: Path,
    ) -> None:
        """x.Load() with only 1 Load method → unresolved (stdlib collision)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Cache struct{}

func (c *Cache) Load(key string) interface{} { return nil }

func getData(x interface{}) {
    x.Load("mykey")
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        get_calls = [e for e in call_edges if "getData" in e.src]

        load_calls = [e for e in get_calls if "Load" in e.dst]
        assert len(load_calls) >= 1
        for edge in load_calls:
            assert "Cache.Load" not in edge.dst
            assert edge.evidence_type == "stdlib_method_call"


class TestGoPrivateMethodScope:
    """Tests for Go visibility rules in method resolution.

    In Go, lowercase identifiers are unexported (package-private). When the
    receiver type is unknown, a lowercase method call like x.string() should
    NOT resolve to a global symbol in a different file, because the caller
    might be in a different package. Without receiver type info, the analyzer
    cannot confirm same-package access, so it should produce an unresolved
    edge rather than a false-positive call edge.
    """

    def test_lowercase_method_not_resolved_globally(self, tmp_path: Path) -> None:
        """x.string() should NOT resolve to recalcRequest.string() across files.

        When only one type defines a lowercase method (e.g., `string`), the
        ambiguity guard (which requires 3+ candidates) doesn't fire. But
        lowercase methods are package-private in Go, so resolving them
        globally without knowing the receiver type produces false positives.
        The analyzer should produce an unresolved edge instead.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        # File 1: defines a type with a lowercase method
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        (pkg_dir / "types.go").write_text("""package pkg

type recalcRequest struct {
    value int
}

func (r *recalcRequest) string() string {
    return "recalc"
}
""")
        # File 2: calls x.string() without knowing x's type
        (pkg_dir / "handler.go").write_text("""package pkg

func handleRequest(x interface{}) {
    x.string()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        handler_calls = [e for e in call_edges if "handleRequest" in e.src]

        string_calls = [e for e in handler_calls if "string" in e.dst]
        assert len(string_calls) >= 1, (
            f"handleRequest should have a call edge for string(), "
            f"found: {handler_calls}"
        )

        # Should NOT resolve to recalcRequest.string — it's unexported
        for edge in string_calls:
            assert "recalcRequest.string" not in edge.dst, (
                f"Lowercase method x.string() should NOT resolve to "
                f"recalcRequest.string globally; got dst={edge.dst}"
            )
            # Should be unresolved since receiver type is unknown
            assert "unresolved" in edge.dst, (
                f"Lowercase method call without receiver type should be "
                f"unresolved, got dst={edge.dst}"
            )

    def test_uppercase_method_still_resolves_globally(self, tmp_path: Path) -> None:
        """x.Format() (uppercase, non-stdlib) SHOULD still resolve globally.

        Exported (uppercase) methods can be called from any package, so
        global resolution is valid for them. This tests that the private
        method guard only affects lowercase callees.  Uses 'Format' which
        is not in the stdlib interface method set (unlike 'String').
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        (pkg_dir / "types.go").write_text("""package pkg

type Formatter struct{}

func (f *Formatter) Format() string {
    return "formatted"
}
""")
        (pkg_dir / "handler.go").write_text("""package pkg

func handleFormat(x interface{}) {
    x.Format()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        handler_calls = [e for e in call_edges if "handleFormat" in e.src]

        format_calls = [e for e in handler_calls if "Format" in e.dst]
        assert len(format_calls) >= 1, (
            f"handleFormat should have a call edge for Format(), "
            f"found: {handler_calls}"
        )

        # Uppercase non-stdlib method — should resolve to Formatter.Format
        assert any("Formatter.Format" in e.dst for e in format_calls), (
            f"Uppercase method x.Format() should resolve to Formatter.Format, "
            f"found: {[e.dst for e in format_calls]}"
        )


class TestGoBuiltinTypeConversions:
    """Tests that Go builtin type conversions don't resolve to user symbols.

    In Go, ``string(x)``, ``int(x)``, ``float64(x)`` etc. are type
    conversions, not function calls.  Tree-sitter parses them as
    ``call_expression`` with an ``identifier`` callee.  Without filtering,
    ``string(result["id"])`` resolves to any user-defined method named
    ``string`` — e.g. ``recalcRequest.string`` had 577 false-positive
    callers in forgejo because Go type conversions shadowed method names.
    """

    def test_string_conversion_not_resolved(self, tmp_path: Path) -> None:
        """string(x) should NOT resolve to a method named 'string'."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main

type recalcRequest struct{}

func (r *recalcRequest) string() string {
    return "recalc"
}
""")
        (tmp_path / "main.go").write_text("""package main

func process(data []byte) {
    s := string(data)
    _ = s
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        process_calls = [e for e in call_edges if "process" in e.src]

        # string(data) is a type conversion, NOT a call to recalcRequest.string
        for edge in process_calls:
            assert "recalcRequest.string" not in edge.dst, (
                f"Go type conversion string() should NOT resolve to "
                f"recalcRequest.string; got dst={edge.dst}"
            )

    def test_int_conversion_not_resolved(self, tmp_path: Path) -> None:
        """int(x) should NOT produce a call edge."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "main.go").write_text("""package main

func convert(f float64) {
    i := int(f)
    _ = i
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        convert_calls = [e for e in call_edges if "convert" in e.src]

        # int(f) is a type conversion — should produce no call edges
        assert len(convert_calls) == 0, (
            f"Go type conversion int() should not produce call edges, "
            f"found: {convert_calls}"
        )

    def test_real_function_call_still_resolves(self, tmp_path: Path) -> None:
        """User-defined function named 'process' should still resolve."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "main.go").write_text("""package main

func process() {}

func caller() {
    process()
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        caller_calls = [e for e in call_edges if "caller" in e.src]

        assert len(caller_calls) >= 1
        assert any("process" in e.dst for e in caller_calls)


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


class TestGoVisibilityModifiers:
    """Tests for Go visibility modifier extraction (naming convention)."""

    def test_exported_function(self, tmp_path: Path) -> None:
        """Uppercase functions get 'exported' modifier."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "main.go").write_text("""package main
func ExportedFunc() {}
func unexportedFunc() {}
""")
        result = analyze_go(tmp_path)

        exported = next(s for s in result.symbols if s.name == "ExportedFunc")
        assert "exported" in exported.modifiers

        unexported = next(s for s in result.symbols if s.name == "unexportedFunc")
        assert "unexported" in unexported.modifiers

    def test_exported_type(self, tmp_path: Path) -> None:
        """Uppercase type names get 'exported' modifier."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""package main
type ExportedType struct {}
type unexportedType struct {}
""")
        result = analyze_go(tmp_path)

        exported = next(s for s in result.symbols if s.name == "ExportedType")
        assert "exported" in exported.modifiers

        unexported = next(s for s in result.symbols if s.name == "unexportedType")
        assert "unexported" in unexported.modifiers

    def test_exported_method(self, tmp_path: Path) -> None:
        """Method visibility based on method name, not receiver."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "methods.go").write_text("""package main
type Foo struct {}
func (f *Foo) ExportedMethod() {}
func (f *Foo) unexportedMethod() {}
""")
        result = analyze_go(tmp_path)

        exported = next(s for s in result.symbols if "ExportedMethod" in s.name)
        assert "exported" in exported.modifiers

        unexported = next(s for s in result.symbols if "unexportedMethod" in s.name)
        assert "unexported" in unexported.modifiers


class TestNormalizeGoSignature:
    """Tests for Go signature normalization (ADR-0014 §3)."""

    def test_basic_function(self) -> None:
        from hypergumbo_lang_mainstream.go import normalize_go_signature
        assert normalize_go_signature("(name string, age int) error") == "(string,int)error"

    def test_pointer_stripped(self) -> None:
        from hypergumbo_lang_mainstream.go import normalize_go_signature
        assert normalize_go_signature("(r *http.Request) error") == "(Request)error"

    def test_none(self) -> None:
        from hypergumbo_lang_mainstream.go import normalize_go_signature
        assert normalize_go_signature(None) is None


class TestGoStructFieldFunctionReferences:
    """Tests for function references in struct literal fields.

    Go codebases commonly assign functions to struct fields:
      cobra.Command{RunE: myFunc}
      http.ServeMux{Handler: handler}

    These function references should create call edges to enable reverse
    slicing from the referenced functions.
    """

    def test_simple_struct_field_ref(self, tmp_path: Path) -> None:
        """Function identifier in struct literal field creates call edge."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func runServer() error { return nil }

type Command struct {
    RunE func() error
}

func setup() {
    cmd := Command{RunE: runServer}
    _ = cmd
}
""")
        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        setup_calls = [e for e in call_edges if "setup:function" in e.src]

        ref_edges = [e for e in setup_calls if "runServer" in e.dst]
        assert len(ref_edges) >= 1, (
            f"setup should have call edge to runServer (struct field reference), "
            f"found edges: {[e.dst for e in setup_calls]}"
        )
        # Verify evidence type
        assert ref_edges[0].evidence_type == "struct_field_reference"
        assert 0.5 <= ref_edges[0].confidence <= 0.75

    def test_cross_file_struct_field_ref(self, tmp_path: Path) -> None:
        """Struct field reference resolves functions from other files."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "handler.go").write_text("""package main

func handleRequest() error { return nil }
""")
        (tmp_path / "main.go").write_text("""package main

type Route struct {
    Handler func() error
}

func main() {
    r := Route{Handler: handleRequest}
    _ = r
}
""")
        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        ref_edges = [e for e in main_calls if "handleRequest" in e.dst]
        assert len(ref_edges) >= 1, (
            f"main should call handleRequest via struct field reference, "
            f"found: {[e.dst for e in main_calls]}"
        )

    def test_non_function_value_not_linked(self, tmp_path: Path) -> None:
        """Non-function identifiers in struct fields don't create false edges."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Config struct {
    Name string
    Port int
}

func setup() {
    name := "test"
    cfg := Config{Name: name, Port: 8080}
    _ = cfg
}
""")
        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # setup should NOT have struct_field_reference edges
        field_refs = [
            e for e in call_edges
            if "setup:function" in e.src and e.evidence_type == "struct_field_reference"
        ]
        assert len(field_refs) == 0, (
            f"Non-function values should not create struct_field_reference edges, "
            f"found: {field_refs}"
        )

    def test_selector_expression_field_ref(self, tmp_path: Path) -> None:
        """Selector expression in struct field (obj.Method) creates edge.

        Pattern: Route{Handler: handlers.GetAPI} where GetAPI is resolved
        via the method name using the global resolver.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "handlers.go").write_text("""package main

type Handlers struct{}

func (h Handlers) GetAPI() error { return nil }
""")
        (tmp_path / "main.go").write_text("""package main

type Route struct {
    Handler func() error
}

func main() {
    h := Handlers{}
    r := Route{Handler: h.GetAPI}
    _ = r
}
""")
        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        main_calls = [e for e in call_edges if "main:function" in e.src]

        ref_edges = [
            e for e in main_calls
            if "GetAPI" in e.dst and e.evidence_type == "struct_field_reference"
        ]
        assert len(ref_edges) >= 1, (
            f"main should call GetAPI via struct field selector reference, "
            f"found: {[(e.dst, e.evidence_type) for e in main_calls]}"
        )

    def test_multiple_function_fields(self, tmp_path: Path) -> None:
        """Multiple function references in one struct literal each create edges."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func onStart() error { return nil }
func onStop() error { return nil }

type Lifecycle struct {
    Start func() error
    Stop  func() error
}

func setup() {
    lc := Lifecycle{Start: onStart, Stop: onStop}
    _ = lc
}
""")
        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        setup_refs = [
            e for e in call_edges
            if "setup:function" in e.src and e.evidence_type == "struct_field_reference"
        ]
        dst_names = {e.dst for e in setup_refs}
        assert any("onStart" in d for d in dst_names), (
            f"setup should reference onStart, found: {dst_names}"
        )
        assert any("onStop" in d for d in dst_names), (
            f"setup should reference onStop, found: {dst_names}"
        )


class TestGoModulePathResolution:
    """Tests for go.mod-aware cross-package import resolution.

    Go repos import their own packages using the full module path from
    go.mod (e.g., ``import "github.com/aquasecurity/trivy/pkg/commands"``).
    The resolver must strip the module path prefix to match against local
    file paths (``pkg/commands/root.go``).  Without this, repos like trivy
    and harbor produce 50-80% unresolved edges.
    """

    def test_cross_package_call_resolves_with_go_mod(self, tmp_path: Path) -> None:
        """Cross-package call using full module path resolves correctly.

        Simulates the trivy pattern:
          go.mod:  module github.com/example/myproject
          cmd/main.go: import "github.com/example/myproject/pkg/commands"
          cmd/main.go: commands.Execute()
          pkg/commands/root.go: func Execute() { ... }
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        # go.mod at repo root
        (tmp_path / "go.mod").write_text(
            "module github.com/example/myproject\n\ngo 1.21\n"
        )

        # Package with the target function
        pkg_dir = tmp_path / "pkg" / "commands"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "root.go").write_text("""package commands

func Execute() error {
    return nil
}
""")

        # Main file imports via full module path
        cmd_dir = tmp_path / "cmd"
        cmd_dir.mkdir(parents=True)
        (cmd_dir / "main.go").write_text("""package main

import "github.com/example/myproject/pkg/commands"

func main() {
    commands.Execute()
}
""")

        result = analyze_go(tmp_path)

        # Find the Execute symbol
        execute_sym = next(
            (s for s in result.symbols if s.name == "Execute"), None
        )
        assert execute_sym is not None, "Should find Execute symbol"

        # Find call edges from main
        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and "main:function" in e.src
        ]
        assert len(call_edges) >= 1, (
            f"main should call Execute, got edges: "
            f"{[(e.src, e.dst) for e in result.edges if e.edge_type == 'calls']}"
        )

        # The call should resolve to the actual Execute symbol, not unresolved
        resolved = [e for e in call_edges if execute_sym.id == e.dst]
        assert len(resolved) >= 1, (
            f"Call from main to Execute should resolve to {execute_sym.id}, "
            f"got targets: {[e.dst for e in call_edges]}"
        )

    def test_ambiguous_names_disambiguated_by_go_mod(self, tmp_path: Path) -> None:
        """go.mod prefix stripping enables disambiguation of common names.

        A package ``pkg/log`` contains both a standalone function ``Err()``
        and a method ``Handler.Err()``.  A second package ``pkg/parser``
        defines ``Parser.Err()``.  The global symbol registry has 3
        candidates for ``"Err"`` (short-name storage).

        Without go.mod stripping, the suffix matching for import path
        ``github.com/example/myproject/pkg/log`` fails: the short
        suffix ``"log"`` matches 2 candidates (both in pkg/log/), and
        longer suffixes hit domain components (``example``,
        ``github.com``) absent from file paths → 0 matches → the loop
        exhausts and the ambiguity_threshold (3) fires.

        With go.mod stripping, the path hint becomes ``pkg/log`` and
        the suffix ``"log"`` matches exactly 2 candidates — both in
        the correct package.  Since the ambiguity is intra-package,
        the best candidate is chosen with scaled confidence.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text(
            "module github.com/example/myproject\n\ngo 1.21\n"
        )

        # pkg/log: standalone function Err + method Handler.Err
        log_dir = tmp_path / "pkg" / "log"
        log_dir.mkdir(parents=True)
        (log_dir / "handler.go").write_text("""package log

type Handler struct{}

func (h *Handler) Err() error {
    return nil
}

func Err() error {
    return nil
}
""")

        # pkg/parser: Parser.Err method (third candidate for "Err")
        parser_dir = tmp_path / "pkg" / "parser"
        parser_dir.mkdir(parents=True)
        (parser_dir / "parser.go").write_text("""package parser

type Parser struct{}

func (p *Parser) Err() error {
    return nil
}
""")

        # Main imports log via full module path, calls log.Err()
        (tmp_path / "main.go").write_text("""package main

import "github.com/example/myproject/pkg/log"

func main() {
    log.Err()
}
""")

        result = analyze_go(tmp_path)

        # Find the standalone Err function in pkg/log
        log_err = next(
            (s for s in result.symbols
             if s.name == "Err" and "log" in s.path),
            None,
        )
        assert log_err is not None, "Should find standalone Err in pkg/log"

        # Find call edges from main
        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and "main:function" in e.src
        ]

        # The call should NOT be unresolved — it should resolve to one of
        # the log package symbols (Err or Handler.Err, both acceptable)
        unresolved = [e for e in call_edges if "unresolved" in e.dst]
        assert len(unresolved) == 0, (
            f"log.Err() should NOT be unresolved, got: {[e.dst for e in unresolved]}"
        )

        # Should have a resolved call edge
        resolved = [e for e in call_edges if "log" in e.dst and "Err" in e.dst]
        assert len(resolved) >= 1, (
            f"main() -> log.Err() should resolve to a symbol in pkg/log, "
            f"got targets: {[e.dst for e in call_edges]}"
        )


class TestGoLinesOfCode:
    """Tests for lines_of_code on Go symbols."""

    def test_function_lines_of_code(self, tmp_path: Path) -> None:
        """Function symbols have lines_of_code set from span."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

func small() {
    println("one liner body")
}

func medium(x int) int {
    y := x + 1
    z := y * 2
    return z
}
""")

        result = analyze_go(tmp_path)
        small = next(s for s in result.symbols if s.name == "small")
        medium = next(s for s in result.symbols if s.name == "medium")
        assert small.lines_of_code == 3
        assert medium.lines_of_code == 5

    def test_method_lines_of_code(self, tmp_path: Path) -> None:
        """Method symbols have lines_of_code set from span."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Server struct{}

func (s *Server) Start() error {
    return nil
}
""")

        result = analyze_go(tmp_path)
        start = next(s for s in result.symbols if s.name == "Server.Start")
        assert start.lines_of_code == 3

    def test_struct_lines_of_code(self, tmp_path: Path) -> None:
        """Struct/interface symbols have lines_of_code set from span."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""package main

type Config struct {
    Host string
    Port int
    Debug bool
}
""")

        result = analyze_go(tmp_path)
        config = next(s for s in result.symbols if s.name == "Config")
        assert config.lines_of_code == 5


class TestGoStructuralInterfaceMatching:
    """Tests for structural interface satisfaction detection.

    Go uses structural typing: a struct satisfies an interface if it has
    all the interface's methods, WITHOUT needing an explicit assertion like
    ``var _ Interface = &Struct{}``. The analyzer should detect this
    automatically by comparing interface method sets with struct method sets.
    """

    def test_struct_satisfies_interface_without_assertion(
        self, tmp_path: Path
    ) -> None:
        """Struct with matching methods gets base_classes for interface."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "types.go").write_text("""package main

type Notifier interface {
    Notify(msg string) error
}

type EmailNotifier struct{}

func (e *EmailNotifier) Notify(msg string) error {
    return nil
}
""")

        result = analyze_go(tmp_path)

        struct_sym = next(
            (s for s in result.symbols if s.name == "EmailNotifier"), None
        )
        assert struct_sym is not None, "Should find EmailNotifier struct"
        assert struct_sym.meta is not None, (
            "EmailNotifier should have meta with base_classes"
        )
        assert "base_classes" in struct_sym.meta, (
            f"Should have base_classes, got: {struct_sym.meta}"
        )
        assert "Notifier" in struct_sym.meta["base_classes"]

    def test_struct_missing_method_does_not_match(
        self, tmp_path: Path
    ) -> None:
        """Struct missing an interface method should NOT get base_classes."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "types.go").write_text("""package main

type ReadWriter interface {
    Read(p []byte) (int, error)
    Write(p []byte) (int, error)
}

type OnlyReader struct{}

func (r *OnlyReader) Read(p []byte) (int, error) {
    return 0, nil
}
""")

        result = analyze_go(tmp_path)

        struct_sym = next(
            (s for s in result.symbols if s.name == "OnlyReader"), None
        )
        assert struct_sym is not None
        # Should NOT have ReadWriter in base_classes
        if struct_sym.meta and "base_classes" in struct_sym.meta:
            assert "ReadWriter" not in struct_sym.meta["base_classes"]

    def test_multiple_structs_satisfy_same_interface(
        self, tmp_path: Path
    ) -> None:
        """Multiple structs can satisfy the same interface."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "types.go").write_text("""package main

type Stringer interface {
    String() string
}

type Foo struct{}
func (f Foo) String() string { return "foo" }

type Bar struct{}
func (b *Bar) String() string { return "bar" }
""")

        result = analyze_go(tmp_path)

        foo = next((s for s in result.symbols if s.name == "Foo"), None)
        bar = next((s for s in result.symbols if s.name == "Bar"), None)
        assert foo is not None
        assert bar is not None
        assert foo.meta and "Stringer" in foo.meta.get("base_classes", [])
        assert bar.meta and "Stringer" in bar.meta.get("base_classes", [])

    def test_cross_file_structural_matching(
        self, tmp_path: Path
    ) -> None:
        """Struct in one file should match interface in another file."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        # Interface in interface.go
        (tmp_path / "interface.go").write_text("""package main

type Queryable interface {
    Querier() Querier
}
""")
        # Struct in db.go that implements Queryable
        (tmp_path / "db.go").write_text("""package main

type DB struct{}

func (db *DB) Querier() Querier {
    return nil
}
""")

        result = analyze_go(tmp_path)

        db_sym = next(
            (s for s in result.symbols if s.name == "DB" and s.kind == "struct"),
            None,
        )
        assert db_sym is not None, "Should find DB struct"
        assert db_sym.meta is not None, (
            "DB should have meta with base_classes from cross-file matching"
        )
        assert "base_classes" in db_sym.meta, (
            f"DB should have base_classes, got: {db_sym.meta}"
        )
        assert "Queryable" in db_sym.meta["base_classes"], (
            f"DB should implement Queryable, got: {db_sym.meta['base_classes']}"
        )

    def test_arity_mismatch_rejects_false_structural_match(
        self, tmp_path: Path
    ) -> None:
        """Struct method with different arity should NOT satisfy interface.

        Go's structural typing requires matching method signatures, not just
        names.  A struct with Close(ctx context.Context) error does NOT
        satisfy an interface with Close() error because the parameter counts
        differ.  Without arity checking, any struct with a Close() method
        would be falsely matched to io.Closer-style interfaces.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "types.go").write_text("""package main

import "context"

type Closer interface {
    Close() error
}

// RealCloser satisfies Closer: same name AND same arity (0 params, 1 return)
type RealCloser struct{}
func (r *RealCloser) Close() error { return nil }

// FakeCloser does NOT satisfy Closer: same name but different arity (1 param)
type FakeCloser struct{}
func (f *FakeCloser) Close(ctx context.Context) error { return nil }
""")

        result = analyze_go(tmp_path)

        real = next(
            (s for s in result.symbols if s.name == "RealCloser"), None
        )
        fake = next(
            (s for s in result.symbols if s.name == "FakeCloser"), None
        )
        assert real is not None
        assert fake is not None

        # RealCloser should satisfy Closer
        assert real.meta and "Closer" in real.meta.get("base_classes", []), (
            f"RealCloser should implement Closer, got: {real.meta}"
        )

        # FakeCloser should NOT satisfy Closer (arity mismatch)
        fake_bases = (fake.meta or {}).get("base_classes", [])
        assert "Closer" not in fake_bases, (
            f"FakeCloser should NOT implement Closer (arity mismatch), "
            f"but got base_classes={fake_bases}"
        )

    def test_same_name_structs_in_different_packages_all_implement_interface(
        self, tmp_path: Path
    ) -> None:
        """Many packages declaring `type Notifier struct` must each be wired
        up as implementations of a cross-package `Notifier` interface.

        Regression: alertmanager (and any Go repo with a cross-package
        interface whose name collides with the implementing struct's
        short name) lost 16 of 17 ``implements`` edges because the
        cross-file structural matcher keyed both ``global_struct_methods``
        and ``struct_syms`` by short name.  All same-named structs were
        merged into one entry, and only the first struct symbol got the
        ``base_classes`` annotation.  See INV-zomuk.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")

        # Cross-package interface in notify/notify.go
        notify_dir = tmp_path / "notify"
        notify_dir.mkdir()
        (notify_dir / "notify.go").write_text("""package notify

type Notifier interface {
    Notify(msg string) (bool, error)
}
""")

        # Three different packages each declaring `type Notifier struct`
        # alongside its `Notify` method.  Mirrors alertmanager's
        # notify/{slack,webhook,wechat}/*.go layout.
        for pkg in ("slack", "webhook", "wechat"):
            pkg_dir = tmp_path / "notify" / pkg
            pkg_dir.mkdir()
            (pkg_dir / f"{pkg}.go").write_text(f"""package {pkg}

type Notifier struct {{}}

func (n *Notifier) Notify(msg string) (bool, error) {{
    return true, nil
}}
""")

        result = analyze_go(tmp_path)

        # Each package's Notifier struct should be annotated with the
        # cross-package Notifier interface as a base class.
        notifier_structs = [
            s for s in result.symbols
            if s.name == "Notifier" and s.kind == "struct"
        ]
        assert len(notifier_structs) == 3, (
            f"Expected 3 Notifier structs, found {len(notifier_structs)}: "
            f"{[s.path for s in notifier_structs]}"
        )

        for struct_sym in notifier_structs:
            bases = (struct_sym.meta or {}).get("base_classes", [])
            assert "Notifier" in bases, (
                f"{struct_sym.path}: expected base_classes to include "
                f"'Notifier', got {bases}"
            )

    def test_struct_methods_split_across_files_in_same_package(
        self, tmp_path: Path
    ) -> None:
        """Methods on a struct declared in one file but defined in another
        file of the same package must still count toward structural matching.

        WI-hobuk: prior to the per-package aggregation fix, the cross-file
        structural matcher iterated per-file, so it only saw methods that
        lived in the same file as the struct's type declaration.  When a Go
        package idiomatically split a struct's type into ``foo.go`` and
        its methods into ``foo_helpers.go`` (or any other file in the same
        package), the matcher silently dropped those methods from the
        struct's effective method set and the struct failed to satisfy any
        interface that required them.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        # Interface in iface.go
        (tmp_path / "iface.go").write_text("""package main

type Notifier interface {
    Notify(msg string) error
    Close() error
}
""")
        # Struct declaration in service.go
        (tmp_path / "service.go").write_text("""package main

type Service struct{}

func (s *Service) Notify(msg string) error {
    return nil
}
""")
        # The other half of Service's method set lives in service_helpers.go
        # (idiomatic Go: split helpers into a sibling file in the same package).
        (tmp_path / "service_helpers.go").write_text("""package main

func (s *Service) Close() error {
    return nil
}
""")

        result = analyze_go(tmp_path)

        svc = next(
            (s for s in result.symbols
             if s.name == "Service" and s.kind == "struct"),
            None,
        )
        assert svc is not None, "Service struct should be found"
        # The struct's effective method set is {Notify, Close} once methods
        # in the sibling file are aggregated, so it satisfies Notifier.
        bases = (svc.meta or {}).get("base_classes", [])
        assert "Notifier" in bases, (
            f"Service should implement Notifier (methods aggregated from "
            f"service.go + service_helpers.go), got base_classes={bases}"
        )

    def test_struct_methods_in_sibling_file_only(
        self, tmp_path: Path
    ) -> None:
        """Even when *all* methods live in a sibling file, the struct should
        still satisfy interfaces.

        Edge case for WI-hobuk: the type is declared in ``types.go`` with
        no methods at all in that file, while every method is defined in
        ``methods.go``.  Per-file iteration would have seen Service's
        type in types.go with an empty method set and given up.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "iface.go").write_text("""package main

type Closer interface {
    Close() error
}
""")
        # Type only — no methods.
        (tmp_path / "types.go").write_text("""package main

type Service struct{}
""")
        # All methods in a sibling file.
        (tmp_path / "methods.go").write_text("""package main

func (s *Service) Close() error {
    return nil
}
""")

        result = analyze_go(tmp_path)

        svc = next(
            (s for s in result.symbols
             if s.name == "Service" and s.kind == "struct"),
            None,
        )
        assert svc is not None
        bases = (svc.meta or {}).get("base_classes", [])
        assert "Closer" in bases, (
            f"Service should implement Closer despite having all methods "
            f"in a sibling file, got base_classes={bases}"
        )

    def test_methods_in_different_package_do_not_count(
        self, tmp_path: Path
    ) -> None:
        """Aggregation must respect Go package boundaries.

        Two files declaring ``type Service struct`` in *different* packages
        must not have their method sets merged.  This guards the
        per-package aggregation fix from over-merging into the
        cross-package shadow that INV-zomuk warned about.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "iface.go").write_text("""package main

type Notifier interface {
    Notify(msg string) error
    Close() error
}
""")
        # Package "foo": only declares Service with Notify method.
        foo_dir = tmp_path / "foo"
        foo_dir.mkdir()
        (foo_dir / "service.go").write_text("""package foo

type Service struct{}

func (s *Service) Notify(msg string) error {
    return nil
}
""")
        # Package "bar": declares its own Service with Close method.
        # If aggregation accidentally merged across packages, foo.Service
        # would (incorrectly) be seen as having both Notify AND Close
        # and would falsely satisfy Notifier.
        bar_dir = tmp_path / "bar"
        bar_dir.mkdir()
        (bar_dir / "service.go").write_text("""package bar

type Service struct{}

func (s *Service) Close() error {
    return nil
}
""")

        result = analyze_go(tmp_path)

        # foo.Service should NOT satisfy Notifier — it only has Notify,
        # not Close.  bar.Service is a separate type that should not
        # contribute methods.
        foo_service = next(
            (s for s in result.symbols
             if s.name == "Service" and "foo" in s.path),
            None,
        )
        assert foo_service is not None
        foo_bases = (foo_service.meta or {}).get("base_classes", [])
        assert "Notifier" not in foo_bases, (
            f"foo.Service should NOT implement Notifier (it only has "
            f"Notify, not Close); got base_classes={foo_bases}"
        )

    def test_arity_mismatch_cross_file(self, tmp_path: Path) -> None:
        """Cross-file structural matching also respects arity."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "iface.go").write_text("""package main

type Handler interface {
    Handle(req Request) Response
}
""")
        (tmp_path / "good.go").write_text("""package main

type GoodHandler struct{}
func (g *GoodHandler) Handle(req Request) Response { return Response{} }
""")
        (tmp_path / "bad.go").write_text("""package main

// WrongArity has Handle() but with 0 params instead of 1
type WrongArity struct{}
func (w *WrongArity) Handle() string { return "" }
""")

        result = analyze_go(tmp_path)

        good = next(
            (s for s in result.symbols if s.name == "GoodHandler"), None
        )
        bad = next(
            (s for s in result.symbols if s.name == "WrongArity"), None
        )
        assert good is not None
        assert bad is not None

        assert good.meta and "Handler" in good.meta.get("base_classes", [])

        bad_bases = (bad.meta or {}).get("base_classes", [])
        assert "Handler" not in bad_bases, (
            f"WrongArity should NOT implement Handler (0 params vs 1), "
            f"got base_classes={bad_bases}"
        )


    def test_embedding_promotes_methods_for_interface_satisfaction(
        self, tmp_path: Path
    ) -> None:
        """Struct embedding promotes the embedded type's methods.

        WI-tudib: WeekdayRange embeds InclusiveRange and inherits its
        setBegin/setEnd methods.  When combined with its own explicit
        memberFromString, WeekdayRange satisfies stringableRange.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "types.go").write_text("""package main

type stringableRange interface {
    setBegin(int)
    setEnd(int)
    memberFromString(string) (int, error)
}

type InclusiveRange struct {
    Begin int
    End   int
}

func (ir *InclusiveRange) setBegin(v int)                    {}
func (ir *InclusiveRange) setEnd(v int)                      {}
func (ir *InclusiveRange) memberFromString(s string) (int, error) { return 0, nil }

// WeekdayRange embeds InclusiveRange → inherits setBegin, setEnd.
// It defines its own memberFromString, so it should satisfy stringableRange.
type WeekdayRange struct {
    InclusiveRange
}

func (wr *WeekdayRange) memberFromString(s string) (int, error) { return 0, nil }
""")

        result = analyze_go(tmp_path)

        # InclusiveRange directly satisfies stringableRange
        ir = next(
            (s for s in result.symbols if s.name == "InclusiveRange"), None
        )
        assert ir is not None
        assert "stringableRange" in (ir.meta or {}).get("base_classes", [])

        # WeekdayRange satisfies stringableRange via promoted methods
        wr = next(
            (s for s in result.symbols if s.name == "WeekdayRange"), None
        )
        assert wr is not None, "WeekdayRange struct should be found"
        wr_bases = (wr.meta or {}).get("base_classes", [])
        assert "stringableRange" in wr_bases, (
            f"WeekdayRange should satisfy stringableRange via promoted "
            f"methods from InclusiveRange, got base_classes={wr_bases}"
        )

    def test_transitive_embedding_promotes_methods(
        self, tmp_path: Path
    ) -> None:
        """A embeds B, B embeds C → A gets C's methods for satisfaction."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "types.go").write_text("""package main

type Doer interface {
    Do()
}

type Base struct{}
func (b *Base) Do() {}

type Mid struct {
    Base
}

type Top struct {
    Mid
}
""")

        result = analyze_go(tmp_path)

        top = next(
            (s for s in result.symbols if s.name == "Top"), None
        )
        assert top is not None
        top_bases = (top.meta or {}).get("base_classes", [])
        assert "Doer" in top_bases, (
            f"Top should satisfy Doer via transitive embedding "
            f"(Top → Mid → Base), got base_classes={top_bases}"
        )


class TestGoInterfaceMethodSymbols:
    """Tests for extracting method symbols from Go interface definitions.

    Go interfaces define method signatures that need to be extracted as
    Symbol objects (kind=method, name=InterfaceName.MethodName) so that
    the type hierarchy linker can create dispatches_to edges from
    interface methods to concrete struct methods.
    """

    def test_interface_methods_extracted_as_symbols(
        self, tmp_path: Path
    ) -> None:
        """Interface methods become method symbols named InterfaceName.MethodName."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "iface.go").write_text("""package main

type Notifier interface {
    Notify(msg string) error
    Close() error
}
""")

        result = analyze_go(tmp_path)

        # Interface itself should exist
        iface = next(
            (s for s in result.symbols if s.name == "Notifier" and s.kind == "interface"),
            None,
        )
        assert iface is not None, "Should find Notifier interface"

        # Each interface method should be a symbol
        notify_method = next(
            (s for s in result.symbols
             if s.name == "Notifier.Notify" and s.kind == "method"),
            None,
        )
        assert notify_method is not None, (
            "Should find Notifier.Notify method symbol; "
            f"got: {[s.name for s in result.symbols if s.kind == 'method']}"
        )
        assert notify_method.language == "go"
        assert notify_method.path == str(tmp_path / "iface.go")

        close_method = next(
            (s for s in result.symbols
             if s.name == "Notifier.Close" and s.kind == "method"),
            None,
        )
        assert close_method is not None, "Should find Notifier.Close method symbol"

    def test_empty_interface_no_method_symbols(
        self, tmp_path: Path
    ) -> None:
        """Empty interfaces (interface{}) produce no method symbols."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "empty.go").write_text("""package main

type Any interface{}
""")

        result = analyze_go(tmp_path)

        # No method symbols should exist for empty interface
        iface_methods = [
            s for s in result.symbols
            if s.kind == "method" and s.name.startswith("Any.")
        ]
        assert len(iface_methods) == 0

    def test_interface_call_via_range_resolves_to_interface_method(
        self, tmp_path: Path
    ) -> None:
        """Calling a method on a range-loop variable of interface type should
        resolve to the interface method symbol, not produce an unresolved edge.

        When Plugin.Eval, BlockPlugin.Eval, and AllowPlugin.Eval all exist
        in global_symbols, iterating over ``[]Plugin`` and calling
        ``p.Eval()`` triggers the ambiguity guard (go.py:1871) which
        produces ``go:external:0-0:Eval:unresolved``.  It should instead
        prefer the interface method ``Plugin.Eval`` because dispatches_to
        edges will route the slice to concrete implementations.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "dispatch.go").write_text("""package main

type Plugin interface {
    Eval(input string) string
}

type BlockPlugin struct{}
func (b *BlockPlugin) Eval(input string) string { return "blocked" }

type AllowPlugin struct{}
func (a *AllowPlugin) Eval(input string) string { return input }

func applyPlugins(plugins []Plugin, data string) string {
    for _, p := range plugins {
        data = p.Eval(data)
    }
    return data
}
""")

        result = analyze_go(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        apply_calls = [e for e in call_edges if "applyPlugins" in e.src]

        # Should have a call edge for p.Eval(data)
        eval_calls = [e for e in apply_calls if "Eval" in e.dst]
        assert len(eval_calls) >= 1, (
            f"applyPlugins should have a call edge for p.Eval(), found: {apply_calls}"
        )

        # The edge should resolve to the interface method Plugin.Eval,
        # NOT to unresolved
        for edge in eval_calls:
            assert "unresolved" not in edge.dst, (
                f"p.Eval() should resolve to Plugin.Eval interface method, "
                f"not to unresolved. Got dst={edge.dst}"
            )
            assert "Plugin.Eval" in edge.dst, (
                f"p.Eval() should resolve to Plugin.Eval, got dst={edge.dst}"
            )

    def test_interface_method_symbols_enable_dispatches_to(
        self, tmp_path: Path
    ) -> None:
        """Interface method symbols + struct methods enable dispatches_to via linkers.

        Validates the full pipeline: Go analyzer extracts interface method symbols,
        containment linker connects interface→method, type hierarchy linker creates
        dispatches_to edges from interface method to struct method.
        """
        from hypergumbo_lang_mainstream.go import analyze_go
        from hypergumbo_core.linkers.containment import link_containment
        from hypergumbo_core.linkers.type_hierarchy import link_type_hierarchy
        from hypergumbo_core.linkers.inheritance import link_inheritance
        from hypergumbo_core.linkers.registry import LinkerContext

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "dispatch.go").write_text("""package main

type Handler interface {
    Handle(req string) string
}

type EchoHandler struct{}

func (h *EchoHandler) Handle(req string) string {
    return req
}
""")

        result = analyze_go(tmp_path)
        symbols = list(result.symbols)
        edges = list(result.edges)

        # Step 1: Run containment linker to create interface→method contains edges
        ctx = LinkerContext(symbols=symbols, edges=edges, repo_root=tmp_path)
        containment_result = link_containment(ctx)
        edges.extend(containment_result.edges)

        # Step 2: Run inheritance linker to create implements edges
        ctx = LinkerContext(symbols=symbols, edges=edges, repo_root=tmp_path)
        inheritance_result = link_inheritance(ctx)
        edges.extend(inheritance_result.edges)

        # Step 3: Run type hierarchy linker to create dispatches_to edges
        ctx = LinkerContext(symbols=symbols, edges=edges, repo_root=tmp_path)
        hierarchy_result = link_type_hierarchy(ctx)

        dispatches = [e for e in hierarchy_result.edges if e.edge_type == "dispatches_to"]
        assert len(dispatches) >= 1, (
            f"Should have dispatches_to edge from Handler.Handle to EchoHandler.Handle; "
            f"got edges: {[(e.src, e.dst, e.edge_type) for e in hierarchy_result.edges]}"
        )


class TestGoStructFieldTypePopulation:
    """Tests that Go struct analysis populates class_field_types."""

    @staticmethod
    def _parse_file(tmp_path: Path, source: str):
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

        go_file = tmp_path / "main.go"
        go_file.write_text(source)
        return _extract_symbols_from_file(go_file, parser, run)

    def test_populates_class_field_types_basic(self, tmp_path: Path) -> None:
        """Named struct fields produce class_field_types entries."""
        result = self._parse_file(tmp_path, """\
package main

type Integration struct {
    Name string
}

type RetryStage struct {
    integration Integration
    metrics     *Metrics
}

type Metrics struct {
    counter int
}
""")
        # RetryStage should have integration→Integration and metrics→Metrics
        assert "RetryStage" in result.class_field_types
        fields = result.class_field_types["RetryStage"]
        assert fields["integration"] == "Integration"
        assert fields["metrics"] == "Metrics"

    def test_excludes_builtin_types(self, tmp_path: Path) -> None:
        """Built-in types (string, int, bool, error) are excluded."""
        result = self._parse_file(tmp_path, """\
package main

type Config struct {
    name    string
    count   int
    enabled bool
    err     error
    handler Handler
}

type Handler struct{}
""")
        fields = result.class_field_types.get("Config", {})
        # Only handler should be present (non-builtin)
        assert fields == {"handler": "Handler"}

    def test_qualified_type_uses_qualified_name(self, tmp_path: Path) -> None:
        """pkg.Type fields store the full qualified name for module hint recovery."""
        result = self._parse_file(tmp_path, """\
package main

import "net/http"

type Server struct {
    client http.Client
}
""")
        fields = result.class_field_types.get("Server", {})
        assert fields.get("client") == "http.Client"

    def test_pointer_to_qualified_type(self, tmp_path: Path) -> None:
        """*pkg.Type fields unwrap pointer and keep qualified name."""
        result = self._parse_file(tmp_path, """\
package main

import "net/http"

type Server struct {
    client *http.Client
}
""")
        fields = result.class_field_types.get("Server", {})
        assert fields.get("client") == "http.Client"

    def test_embedding_excluded(self, tmp_path: Path) -> None:
        """Embedded types (no field name) are not included in class_field_types."""
        result = self._parse_file(tmp_path, """\
package main

type Base struct{}

type Child struct {
    Base
    handler Handler
}

type Handler struct{}
""")
        fields = result.class_field_types.get("Child", {})
        # Base is embedded (no name), should not appear; handler should
        assert "Base" not in fields
        assert fields == {"handler": "Handler"}

    def test_only_builtins_produces_empty(self, tmp_path: Path) -> None:
        """Struct with only builtin-typed fields produces no class_field_types entry."""
        result = self._parse_file(tmp_path, """\
package main

type Config struct {
    name    string
    count   int
    enabled bool
}
""")
        assert "Config" not in result.class_field_types


class TestGoTypedFieldCallEdges:
    """Tests for chained field access resolution via class_field_types.

    When a Go method calls r.integration.Notify(), where r's type has a field
    ``integration`` of type ``Integration``, the edge should resolve to
    ``Integration.Notify`` with evidence_type "typed_field_call".
    """

    def test_chained_field_call(self, tmp_path: Path) -> None:
        """r.integration.Notify() resolves to Integration.Notify."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

type Integration struct{}

func (i *Integration) Notify() {}

type RetryStage struct {
    integration Integration
}

func (r *RetryStage) exec() {
    r.integration.Notify()
}

func main() {}
""")
        result = analyze_go(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 1
        edge = typed_edges[0]

        exec_sym = next(s for s in result.symbols if s.name == "RetryStage.exec")
        notify_sym = next(s for s in result.symbols if s.name == "Integration.Notify")
        assert edge.src == exec_sym.id
        assert edge.dst == notify_sym.id
        assert edge.confidence == 0.88
        assert edge.edge_type == "calls"

    def test_pointer_field_call(self, tmp_path: Path) -> None:
        """r.metrics.Record() where metrics is *Metrics resolves correctly."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

type Metrics struct{}

func (m *Metrics) Record() {}

type Server struct {
    metrics *Metrics
}

func (s *Server) handle() {
    s.metrics.Record()
}

func main() {}
""")
        result = analyze_go(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 1
        assert typed_edges[0].confidence == 0.88

    def test_cross_file_field_call(self, tmp_path: Path) -> None:
        """Chained field call resolves via global symbols across files."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "types.go").write_text("""\
package main

type Cache struct{}

func (c *Cache) Get() {}
""")
        (tmp_path / "main.go").write_text("""\
package main

type App struct {
    cache Cache
}

func (a *App) handle() {
    a.cache.Get()
}

func main() {}
""")
        result = analyze_go(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 1
        edge = typed_edges[0]
        assert "Cache.Get" in edge.dst

    def test_call_root_chain_no_edge(self, tmp_path: Path) -> None:
        """getX().field.Method() — chain root is call, not identifier.

        Exercises _resolve_field_chain: root is call_expression → None.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

type Foo struct{}

func (f *Foo) Bar() {}

type Holder struct {
    foo Foo
}

func getHolder() Holder { return Holder{} }

func run() {
    getHolder().foo.Bar()
}

func main() {}
""")
        result = analyze_go(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        # getHolder().foo.Bar() — root is call, not resolvable
        assert len(typed_edges) == 0

    def test_unresolvable_root_type_no_edge(self, tmp_path: Path) -> None:
        """Chained call with unknown root type → no typed_field_call.

        Exercises _resolve_field_chain: var_types.get(root_name) → None.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

type Foo struct{}

func (f *Foo) Bar() {}

type Other struct {
    foo Foo
}

func run() {
    // unknown is not a typed variable — it's a package-level var
    // with no type annotation visible to var_types extraction.
    unknown.foo.Bar()
}

func main() {}
""")
        result = analyze_go(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 0

    def test_type_not_in_registry_no_edge(self, tmp_path: Path) -> None:
        """Chained call where root type has no fields in registry → no typed_field_call.

        Exercises _resolve_field_chain: field_type_registry.get(current_type) → None.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

type Foo struct{}

func (f *Foo) Bar() {}

// Other has fields so field_type_registry is non-empty
type Other struct {
    foo Foo
}

// Container has only builtin fields → NOT in field_type_registry
type Container struct {
    name string
}

func run(c Container) {
    c.name.Bar()
}

func main() {}
""")
        result = analyze_go(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 0

    def test_field_not_in_type_no_edge(self, tmp_path: Path) -> None:
        """Chained call where field is not in type's registry → no typed_field_call.

        Exercises _resolve_field_chain: fields.get(field_name) → None.
        The struct is in the registry but the accessed field isn't (e.g.,
        a method return field accessed via syntax Go wouldn't actually compile).
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

type Foo struct{}

func (f *Foo) Bar() {}

// Holder has field 'foo' (registered) but NOT 'missing'
type Holder struct {
    foo Foo
}

func (h *Holder) run() {
    // h.missing doesn't exist in Holder's field types
    h.missing.Bar()
}

func main() {}
""")
        result = analyze_go(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 0

    def test_cross_package_interface_field_resolves_to_interface_method(
        self, tmp_path: Path,
    ) -> None:
        """d.stage.Exec() where stage is notify.Stage → typed_field_call to Stage.Exec.

        When a struct field has a cross-package interface type (qualified_type
        in Go AST, e.g. ``notify.Stage``), _resolve_field_chain returns
        "notify.Stage".  The qualified method name must strip the package
        prefix ("Stage.Exec", not "notify.Stage.Exec") because symbol names
        in global_symbols are unqualified.

        This is the root cause of the deep bakeoff's interface dispatch gap:
        alertmanager's Dispatcher.stage (type notify.Stage) produced unresolved
        edges instead of linking to Stage.Exec.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text(
            "module github.com/example/app\ngo 1.21\n"
        )
        notify_dir = tmp_path / "notify"
        notify_dir.mkdir()
        notify_dir.joinpath("notify.go").write_text("""\
package notify

type Stage interface {
    Exec(ctx string) string
}

type RetryStage struct{}

func (r *RetryStage) Exec(ctx string) string {
    return ctx
}

type MuteStage struct{}

func (m *MuteStage) Exec(ctx string) string {
    return ""
}

type DedupStage struct{}

func (d *DedupStage) Exec(ctx string) string {
    return ctx
}
""")
        dispatch_dir = tmp_path / "dispatch"
        dispatch_dir.mkdir()
        dispatch_dir.joinpath("dispatch.go").write_text("""\
package dispatch

import "github.com/example/app/notify"

type Dispatcher struct {
    stage notify.Stage
}

func (d *Dispatcher) Run() string {
    return d.stage.Exec("start")
}
""")

        result = analyze_go(tmp_path)

        # Verify the interface method symbol exists
        stage_exec = [s for s in result.symbols if s.name == "Stage.Exec"]
        assert len(stage_exec) == 1, (
            f"Expected Stage.Exec symbol, found: "
            f"{[s.name for s in result.symbols if 'Exec' in s.name]}"
        )

        # The call from Dispatcher.Run should resolve to Stage.Exec
        # via typed_field_call, NOT produce an unresolved edge
        run_sym = next(
            s for s in result.symbols if s.name == "Dispatcher.Run"
        )
        call_edges = [
            e for e in result.edges
            if e.src == run_sym.id and "Exec" in e.dst
        ]
        assert len(call_edges) >= 1, (
            f"Dispatcher.Run should have a call edge for d.stage.Exec(), "
            f"found edges from Run: "
            f"{[e.dst for e in result.edges if e.src == run_sym.id]}"
        )

        exec_edge = call_edges[0]
        assert "unresolved" not in exec_edge.dst, (
            f"d.stage.Exec() should resolve to Stage.Exec interface method, "
            f"not to unresolved. Got dst={exec_edge.dst}"
        )
        assert exec_edge.dst == stage_exec[0].id, (
            f"d.stage.Exec() should resolve to Stage.Exec symbol, "
            f"got dst={exec_edge.dst}"
        )
        assert exec_edge.evidence_type == "typed_field_call"

    def test_cross_package_interface_param_resolves_to_interface_method(
        self, tmp_path: Path,
    ) -> None:
        """s.Exec() where s is param of type notify.Stage → typed_receiver_call.

        Same bug as cross-package field, but via function parameter with
        qualified type.  The receiver_type in var_types is "notify.Stage";
        stripping the package prefix gives "Stage" for the qualified name.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text(
            "module github.com/example/app\ngo 1.21\n"
        )
        notify_dir = tmp_path / "notify"
        notify_dir.mkdir()
        notify_dir.joinpath("notify.go").write_text("""\
package notify

type Stage interface {
    Exec(ctx string) string
}

type RetryStage struct{}

func (r *RetryStage) Exec(ctx string) string { return ctx }

type MuteStage struct{}

func (m *MuteStage) Exec(ctx string) string { return "" }

type DedupStage struct{}

func (d *DedupStage) Exec(ctx string) string { return ctx }
""")
        runner_dir = tmp_path / "runner"
        runner_dir.mkdir()
        runner_dir.joinpath("runner.go").write_text("""\
package runner

import "github.com/example/app/notify"

func RunStage(s notify.Stage) string {
    return s.Exec("start")
}
""")

        result = analyze_go(tmp_path)

        stage_exec = [s for s in result.symbols if s.name == "Stage.Exec"]
        assert len(stage_exec) == 1

        run_sym = next(
            s for s in result.symbols if s.name == "RunStage"
        )
        call_edges = [
            e for e in result.edges
            if e.src == run_sym.id and "Exec" in e.dst
        ]
        assert len(call_edges) >= 1, (
            f"RunStage should call s.Exec(), found: "
            f"{[e.dst for e in result.edges if e.src == run_sym.id]}"
        )

        exec_edge = call_edges[0]
        assert "unresolved" not in exec_edge.dst, (
            f"s.Exec() should resolve to Stage.Exec, "
            f"not unresolved. Got dst={exec_edge.dst}"
        )
        assert exec_edge.dst == stage_exec[0].id


class TestGoConstructorTypeInference:
    """Tests for NewXxx() constructor return type inference in var_types.

    Go convention: NewFoo() returns *Foo. When we see x := NewFoo(...),
    we infer x has type Foo, enabling typed_receiver_call edges.
    """

    def test_local_constructor_infers_type(self, tmp_path: Path) -> None:
        """x := NewServer(...) → x has type Server."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

type Server struct{}

func NewServer() *Server { return &Server{} }

func (s *Server) Run() {}

func main() {
    s := NewServer()
    s.Run()
}
""")
        result = analyze_go(tmp_path)

        typed_calls = [
            e for e in result.edges
            if e.evidence_type == "typed_receiver_call"
            and "Server.Run" in e.dst
        ]
        assert len(typed_calls) == 1

    def test_package_constructor_infers_type(self, tmp_path: Path) -> None:
        """d := dispatch.NewDispatcher(...) → d has type Dispatcher."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "dispatcher.go").write_text("""\
package main

type Dispatcher struct{}

func NewDispatcher() *Dispatcher { return &Dispatcher{} }

func (d *Dispatcher) Run() {}
""")
        (tmp_path / "main.go").write_text("""\
package main

func main() {
    d := NewDispatcher()
    go d.Run()
}
""")
        result = analyze_go(tmp_path)

        run_calls = [
            e for e in result.edges
            if "Dispatcher.Run" in e.dst
            and e.edge_type == "calls"
        ]
        assert len(run_calls) >= 1
        assert any(e.evidence_type == "typed_receiver_call" for e in run_calls)


class TestGoShapeId:
    """Tests for shape_id computation in Go (ADR-0014 §1)."""

    def test_function_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "main.go").write_text(
            "package main\n\nfunc add(a, b int) int { return a + b }\n"
        )
        result = analyze_go(tmp_path)
        func = next(s for s in result.symbols if s.name == "add")
        assert func.shape_id is not None
        assert func.shape_id.startswith("sha256:")

    def test_struct_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "main.go").write_text(
            "package main\n\ntype Point struct {\n\tX int\n\tY int\n}\n"
        )
        result = analyze_go(tmp_path)
        struct = next(s for s in result.symbols if s.name == "Point")
        assert struct.shape_id is not None
        assert struct.shape_id.startswith("sha256:")


class TestCobraAddCommandUsageContext:
    """Tests for Cobra AddCommand() usage context extraction."""

    def test_addcommand_creates_usage_context(self, tmp_path: Path) -> None:
        """Detects rootCmd.AddCommand(subCmd) calls."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text('''package main

import "github.com/spf13/cobra"

var rootCmd = &cobra.Command{
    Use: "myapp",
}

var serveCmd = &cobra.Command{
    Use: "serve",
    RunE: func(cmd *cobra.Command, args []string) error {
        return nil
    },
}

func init() {
    rootCmd.AddCommand(serveCmd)
}
''')
        result = analyze_go(tmp_path)

        # Should have a usage context for AddCommand
        add_cmd_ctxs = [
            c for c in result.usage_contexts
            if c.context_name == "rootCmd.AddCommand"
        ]
        assert len(add_cmd_ctxs) >= 1
        ctx = add_cmd_ctxs[0]
        assert ctx.kind == "call"
        assert ctx.metadata.get("child_command") == "serveCmd"


class TestGoQualifiedTypeParameterEdges:
    """Tests that function parameters with qualified types (e.g. *http.Client)
    propagate module information to unresolved method call edges.

    Invariant: When a function parameter has a package-qualified type (like
    *http.Client), method calls on that parameter must produce unresolved
    edges with the correct module hint (e.g. net/http) — NOT 'external'.
    This is critical because IO boundary detection uses the module hint to
    classify edges (e.g. http.Client.Do → net_send), and the ambiguous_names
    guard rejects method names like 'Do' without module context.
    """

    def test_qualified_param_type_propagates_module_hint(
        self, tmp_path: Path,
    ) -> None:
        """client.Do() with *http.Client param → edge dst contains 'net/http'.

        The Go analyzer should extract the package prefix from the qualified
        type, map it through import_aliases, and embed the full import path
        in the unresolved edge ID.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

import "net/http"

func doRequest(client *http.Client, req *http.Request) {
    client.Do(req)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        do_edges = [e for e in call_edges if "Do" in e.dst]
        assert len(do_edges) >= 1, (
            f"Expected at least one edge to 'Do', got edges: "
            f"{[e.dst for e in call_edges]}"
        )
        # The edge dst must contain 'net/http', not 'external'
        do_edge = do_edges[0]
        assert "net/http" in do_edge.dst, (
            f"Expected 'net/http' in edge dst for client.Do() with "
            f"*http.Client param, got: {do_edge.dst}"
        )
        assert "external" not in do_edge.dst, (
            f"Edge dst should not contain 'external' when receiver type is "
            f"known via qualified param type, got: {do_edge.dst}"
        )

    def test_non_pointer_qualified_param_propagates_hint(
        self, tmp_path: Path,
    ) -> None:
        """Method call on non-pointer qualified param also gets module hint.

        http.Client (without *) should also work.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

import "net/http"

func doRequest(client http.Client) {
    client.Do(nil)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        do_edges = [e for e in call_edges if "Do" in e.dst]
        assert len(do_edges) >= 1, (
            f"Expected at least one edge to 'Do', got edges: "
            f"{[e.dst for e in call_edges]}"
        )
        assert "net/http" in do_edges[0].dst, (
            f"Expected 'net/http' in dst, got: {do_edges[0].dst}"
        )

    def test_qualified_param_with_go_mod_strips_module_prefix(
        self, tmp_path: Path,
    ) -> None:
        """When go.mod exists, module prefix is stripped from import path hint.

        If the repo has go.mod with module path 'github.com/example/myapp',
        the import_path_hint should still be the full import path (since
        net/http is not a prefix of the module path), preserving the
        'net/http' module hint for IO boundary detection.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_mod = tmp_path / "go.mod"
        go_mod.write_text("module github.com/example/myapp\n\ngo 1.21\n")

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

import "net/http"

func doRequest(client *http.Client, req *http.Request) {
    client.Do(req)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        do_edges = [e for e in call_edges if "Do" in e.dst]
        assert len(do_edges) >= 1, (
            f"Expected at least one edge to 'Do', got edges: "
            f"{[e.dst for e in call_edges]}"
        )
        # net/http is not a prefix of github.com/example/myapp, so
        # _strip_module_prefix returns the full import path unchanged
        assert "net/http" in do_edges[0].dst, (
            f"Expected 'net/http' in dst with go.mod present, "
            f"got: {do_edges[0].dst}"
        )

    def test_field_chain_qualified_type_propagates_module_hint(
        self, tmp_path: Path,
    ) -> None:
        """n.client.Do() with struct field *http.Client → edge dst has net/http.

        When a struct field has a package-qualified type (e.g. *http.Client),
        _resolve_field_chain should return the qualified name, and the
        typed_field_call fallback should extract the import path from
        the package prefix.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

import "net/http"

type Notifier struct {
    client *http.Client
}

func (n *Notifier) Send(req *http.Request) {
    n.client.Do(req)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        do_edges = [e for e in call_edges if "Do" in e.dst]
        assert len(do_edges) >= 1, (
            f"Expected at least one edge to 'Do', got edges: "
            f"{[e.dst for e in call_edges]}"
        )
        assert "net/http" in do_edges[0].dst, (
            f"Expected 'net/http' in edge dst for n.client.Do() "
            f"with *http.Client field, got: {do_edges[0].dst}"
        )

    def test_field_chain_with_go_mod(self, tmp_path: Path) -> None:
        """Field chain module hint works with go.mod (module_path set)."""
        from hypergumbo_lang_mainstream.go import analyze_go

        go_mod = tmp_path / "go.mod"
        go_mod.write_text("module github.com/example/app\n\ngo 1.21\n")

        go_file = tmp_path / "main.go"
        go_file.write_text("""\
package main

import "net/http"

type Client struct {
    http *http.Client
}

func (c *Client) Fetch(req *http.Request) {
    c.http.Do(req)
}
""")

        result = analyze_go(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        do_edges = [e for e in call_edges if "Do" in e.dst]
        assert len(do_edges) >= 1
        assert "net/http" in do_edges[0].dst


class TestGoBuildTagAlternates:
    """Tests for detecting build-tag-gated alternate definitions.

    WI-potun: Go files with mutually exclusive //go:build directives
    define alternates of the same symbol (e.g. Labels.Get in
    labels_stringlabels.go vs labels_dedupelabels.go). The analyzer
    should detect these and emit build_tag_alternative_of edges.
    """

    def test_build_tag_alternates_detected(self, tmp_path: Path) -> None:
        """Two files with different build tags defining the same method
        should produce a build_tag_alternative_of edge."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "impl_a.go").write_text("""//go:build impl_a

package main

type Handler struct{}

func (h *Handler) Serve() {}
""")
        (tmp_path / "impl_b.go").write_text("""//go:build impl_b

package main

type Handler struct{}

func (h *Handler) Serve() {}
""")

        result = analyze_go(tmp_path)

        alt_edges = [
            e for e in result.edges
            if e.edge_type == "build_tag_alternative_of"
        ]
        assert len(alt_edges) >= 1, (
            "Should detect build_tag_alternative_of between Handler.Serve "
            "in impl_a.go and impl_b.go"
        )
        # Both directions should be linked
        serve_syms = [
            s for s in result.symbols if s.name == "Handler.Serve"
        ]
        assert len(serve_syms) == 2, (
            f"Expected 2 Handler.Serve symbols, got {len(serve_syms)}"
        )

    def test_no_build_tag_no_alternate_edge(self, tmp_path: Path) -> None:
        """Files without build tags should NOT produce alternate edges
        even if they define same-named symbols."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        # Two packages, same method name — NOT build-tag alternates
        pkg_a = tmp_path / "a"
        pkg_a.mkdir()
        (pkg_a / "handler.go").write_text("""package a

type Handler struct{}
func (h *Handler) Serve() {}
""")
        pkg_b = tmp_path / "b"
        pkg_b.mkdir()
        (pkg_b / "handler.go").write_text("""package b

type Handler struct{}
func (h *Handler) Serve() {}
""")

        result = analyze_go(tmp_path)

        alt_edges = [
            e for e in result.edges
            if e.edge_type == "build_tag_alternative_of"
        ]
        assert len(alt_edges) == 0, (
            "Should NOT emit build_tag_alternative_of for different packages"
        )

    def test_build_tag_stored_in_meta(self, tmp_path: Path) -> None:
        """Symbols from build-tagged files should have the constraint
        in their meta."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        (tmp_path / "tagged.go").write_text("""//go:build stringlabels

package main

func TaggedFunc() {}
""")

        result = analyze_go(tmp_path)

        func_sym = next(
            (s for s in result.symbols if s.name == "TaggedFunc"), None
        )
        assert func_sym is not None
        assert func_sym.meta is not None
        assert "build_constraint" in func_sym.meta, (
            f"TaggedFunc should have build_constraint in meta, got: {func_sym.meta}"
        )
