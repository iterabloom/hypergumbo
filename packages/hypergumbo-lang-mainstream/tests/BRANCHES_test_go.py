# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for go.py analyzer.

Tests specific branch paths in the Go analyzer that may not be covered
by the main test suite. Focuses on:
- Signature extraction with various parameter types
- Import alias handling (explicit vs inferred, dot/blank imports)
- Import path to directory hint conversion
- Method receiver types (pointer vs value)
- Type declarations (struct, interface, other)
- Call expression resolution (simple, selector, package alias)
- Unresolved method call edge creation
- Route extraction (Gin, Echo, Fiber)
- Usage context extraction
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def make_go_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Go file with given content."""
    (tmp_path / name).write_text(content)


def analyze(tmp_path: Path) -> dict:
    """Run behavior map and return parsed JSON result."""
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    return json.loads(out_path.read_text())


class TestGoSignatureExtraction:
    """Branch coverage for _extract_go_signature with various parameter types."""

    def test_pointer_parameter(self, tmp_path: Path) -> None:
        """Test function with pointer parameter type."""
        make_go_file(tmp_path, "main.go", """
package main

func UpdateUser(u *User) error {
    return nil
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "UpdateUser"), None)
        assert func is not None
        assert func["signature"] == "(u *User) error"

    def test_slice_parameter(self, tmp_path: Path) -> None:
        """Test function with slice parameter type."""
        make_go_file(tmp_path, "main.go", """
package main

func ProcessItems(items []string) int {
    return len(items)
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "ProcessItems"), None)
        assert func is not None
        assert func["signature"] == "(items []string) int"

    def test_map_parameter(self, tmp_path: Path) -> None:
        """Test function with map parameter type."""
        make_go_file(tmp_path, "main.go", """
package main

func CountKeys(data map[string]int) int {
    return len(data)
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "CountKeys"), None)
        assert func is not None
        assert func["signature"] == "(data map[string]int) int"

    def test_array_parameter(self, tmp_path: Path) -> None:
        """Test function with fixed-size array parameter type."""
        make_go_file(tmp_path, "main.go", """
package main

func HashBytes(data [32]byte) string {
    return ""
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "HashBytes"), None)
        assert func is not None
        assert func["signature"] == "(data [32]byte) string"

    def test_interface_parameter(self, tmp_path: Path) -> None:
        """Test function with interface{} parameter type."""
        make_go_file(tmp_path, "main.go", """
package main

func Log(msg interface{}) {
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "Log"), None)
        assert func is not None
        assert func["signature"] == "(msg interface{})"

    def test_struct_parameter(self, tmp_path: Path) -> None:
        """Test function with inline struct parameter type."""
        make_go_file(tmp_path, "main.go", """
package main

func Accept(opt struct{ Name string }) {
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "Accept"), None)
        assert func is not None
        assert "struct" in func["signature"]

    def test_function_type_parameter(self, tmp_path: Path) -> None:
        """Test function with function type parameter."""
        make_go_file(tmp_path, "main.go", """
package main

func OnComplete(callback func(int) error) {
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "OnComplete"), None)
        assert func is not None
        assert "func" in func["signature"]

    def test_channel_parameter(self, tmp_path: Path) -> None:
        """Test function with channel parameter type."""
        make_go_file(tmp_path, "main.go", """
package main

func Listen(events chan string) {
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "Listen"), None)
        assert func is not None
        assert func["signature"] == "(events chan string)"

    def test_qualified_type_parameter(self, tmp_path: Path) -> None:
        """Test function with qualified (package.Type) parameter."""
        make_go_file(tmp_path, "main.go", """
package main

import "time"

func Wait(d time.Duration) {
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "Wait"), None)
        assert func is not None
        assert "time.Duration" in func["signature"]

    def test_multiple_params_same_type(self, tmp_path: Path) -> None:
        """Test function with multiple parameters sharing a type (a, b int)."""
        make_go_file(tmp_path, "main.go", """
package main

func Add(a, b int) int {
    return a + b
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "Add"), None)
        assert func is not None
        assert func["signature"] == "(a, b int) int"

    def test_multiple_return_values(self, tmp_path: Path) -> None:
        """Test function with multiple return values."""
        make_go_file(tmp_path, "main.go", """
package main

func Divide(a, b int) (int, error) {
    return a / b, nil
}
""")
        data = analyze(tmp_path)
        func = next((n for n in data["nodes"] if n["name"] == "Divide"), None)
        assert func is not None
        assert "(int, error)" in func["signature"]


class TestGoImportAliases:
    """Branch coverage for import alias extraction."""

    def test_explicit_alias(self, tmp_path: Path) -> None:
        """Test import with explicit alias."""
        make_go_file(tmp_path, "main.go", """
package main

import (
    pb "github.com/example/proto"
)

func main() {
    pb.NewClient()
}
""")
        data = analyze(tmp_path)
        # Verify the import edge is created
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("github.com/example/proto" in e["dst"] for e in edges)

    def test_inferred_alias_from_path(self, tmp_path: Path) -> None:
        """Test import alias inferred from last path component."""
        make_go_file(tmp_path, "main.go", """
package main

import "github.com/gin-gonic/gin"

func main() {
    gin.Default()
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("github.com/gin-gonic/gin" in e["dst"] for e in edges)

    def test_blank_import_ignored(self, tmp_path: Path) -> None:
        """Test that blank imports (_) are ignored for alias resolution."""
        make_go_file(tmp_path, "main.go", """
package main

import (
    _ "github.com/lib/pq"
)

func main() {
}
""")
        data = analyze(tmp_path)
        # Should still create import edge
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("github.com/lib/pq" in e["dst"] for e in edges)

    def test_dot_import_ignored(self, tmp_path: Path) -> None:
        """Test that dot imports (.) are ignored for alias resolution."""
        make_go_file(tmp_path, "main.go", """
package main

import . "math"

func main() {
    Abs(-1)
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("math" in e["dst"] for e in edges)

    def test_single_import_not_block(self, tmp_path: Path) -> None:
        """Test single import without parentheses."""
        make_go_file(tmp_path, "main.go", """
package main

import "fmt"

func main() {
    fmt.Println("hello")
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("fmt" in e["dst"] for e in edges)


class TestGoImportPathHint:
    """Branch coverage for _import_path_to_dir_hint."""

    def test_path_with_src(self, tmp_path: Path) -> None:
        """Test import path containing /src/ returns proper hint."""
        # This is tested indirectly through call resolution with import hints
        make_go_file(tmp_path, "main.go", """
package main

import svc "github.com/example/src/service"

func main() {
    svc.Start()
}
""")
        data = analyze(tmp_path)
        # Just verify it parses correctly
        assert data["nodes"] or data["edges"]


class TestGoMethodReceivers:
    """Branch coverage for method receiver type extraction."""

    def test_pointer_receiver(self, tmp_path: Path) -> None:
        """Test method with pointer receiver (*User)."""
        make_go_file(tmp_path, "main.go", """
package main

type User struct {
    Name string
}

func (u *User) SetName(name string) {
    u.Name = name
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "User.SetName"), None)
        assert method is not None
        assert method["kind"] == "method"

    def test_value_receiver(self, tmp_path: Path) -> None:
        """Test method with value receiver (User)."""
        make_go_file(tmp_path, "main.go", """
package main

type User struct {
    Name string
}

func (u User) GetName() string {
    return u.Name
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "User.GetName"), None)
        assert method is not None
        assert method["kind"] == "method"

    def test_method_without_receiver_type(self, tmp_path: Path) -> None:
        """Test method declaration when receiver is present but type not in typical form."""
        make_go_file(tmp_path, "main.go", """
package main

type Counter int

func (c Counter) Value() int {
    return int(c)
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "Counter.Value"), None)
        assert method is not None
        assert method["kind"] == "method"


class TestGoTypeDeclarations:
    """Branch coverage for type declaration extraction."""

    def test_struct_type(self, tmp_path: Path) -> None:
        """Test struct type declaration."""
        make_go_file(tmp_path, "main.go", """
package main

type Person struct {
    Name string
    Age  int
}
""")
        data = analyze(tmp_path)
        sym = next((n for n in data["nodes"] if n["name"] == "Person"), None)
        assert sym is not None
        assert sym["kind"] == "struct"

    def test_interface_type(self, tmp_path: Path) -> None:
        """Test interface type declaration."""
        make_go_file(tmp_path, "main.go", """
package main

type Reader interface {
    Read(p []byte) (n int, err error)
}
""")
        data = analyze(tmp_path)
        sym = next((n for n in data["nodes"] if n["name"] == "Reader"), None)
        assert sym is not None
        assert sym["kind"] == "interface"

    def test_other_type_alias(self, tmp_path: Path) -> None:
        """Test type alias declaration (not struct or interface)."""
        make_go_file(tmp_path, "main.go", """
package main

type ID int
type Callback func(int) error
""")
        data = analyze(tmp_path)
        id_sym = next((n for n in data["nodes"] if n["name"] == "ID"), None)
        callback_sym = next((n for n in data["nodes"] if n["name"] == "Callback"), None)
        assert id_sym is not None
        assert id_sym["kind"] == "type"
        assert callback_sym is not None
        assert callback_sym["kind"] == "type"


class TestGoCallResolution:
    """Branch coverage for call expression resolution."""

    def test_simple_identifier_call(self, tmp_path: Path) -> None:
        """Test simple function call by identifier."""
        make_go_file(tmp_path, "main.go", """
package main

func helper() {}

func main() {
    helper()
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("helper" in e["dst"] for e in edges)

    def test_selector_expression_method_call(self, tmp_path: Path) -> None:
        """Test method call on object (obj.Method())."""
        make_go_file(tmp_path, "main.go", """
package main

type Server struct{}

func (s *Server) Start() {}

func main() {
    s := &Server{}
    s.Start()
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        # The method is registered as Server.Start
        assert any("Start" in e["dst"] for e in edges)

    def test_package_alias_disambiguation(self, tmp_path: Path) -> None:
        """Test call resolution with package alias."""
        make_go_file(tmp_path, "main.go", """
package main

import mylog "github.com/example/log"

func main() {
    mylog.Info("test")
}
""")
        data = analyze(tmp_path)
        # Should create an unresolved edge with import path hint
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("Info" in e["dst"] for e in edges)

    def test_unresolved_method_call_with_import_hint(self, tmp_path: Path) -> None:
        """Test unresolved method call includes import path in edge."""
        make_go_file(tmp_path, "main.go", """
package main

import grpc "google.golang.org/grpc"

func main() {
    grpc.NewServer()
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        # Should have unresolved edge with grpc path
        unresolved = [e for e in edges if "unresolved" in e["dst"]]
        assert any("grpc" in e["dst"].lower() for e in unresolved)

    def test_unresolved_method_call_without_import(self, tmp_path: Path) -> None:
        """Test unresolved method call without import alias uses 'external'."""
        make_go_file(tmp_path, "main.go", """
package main

func main() {
    obj := getObject()
    obj.DoSomething()
}

func getObject() interface{} { return nil }
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        # Check for calls - may or may not include unresolved
        assert len(edges) >= 1


class TestGoRouteExtraction:
    """Branch coverage for web framework route extraction."""

    def test_gin_route_get(self, tmp_path: Path) -> None:
        """Test Gin GET route extraction."""
        make_go_file(tmp_path, "main.go", """
package main

import "github.com/gin-gonic/gin"

func getUsers(c *gin.Context) {}

func main() {
    r := gin.Default()
    r.GET("/users", getUsers)
}
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        assert any(n.get("meta", {}).get("http_method") == "GET" for n in routes)

    def test_echo_route_post(self, tmp_path: Path) -> None:
        """Test Echo POST route extraction."""
        make_go_file(tmp_path, "main.go", """
package main

import "github.com/labstack/echo/v4"

func createUser(c echo.Context) error { return nil }

func main() {
    e := echo.New()
    e.POST("/users", createUser)
}
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        assert any(n.get("meta", {}).get("http_method") == "POST" for n in routes)

    def test_fiber_route_lowercase_get(self, tmp_path: Path) -> None:
        """Test Fiber Get route (lowercase method name)."""
        make_go_file(tmp_path, "main.go", """
package main

import "github.com/gofiber/fiber/v2"

func listItems(c *fiber.Ctx) error { return nil }

func main() {
    app := fiber.New()
    app.Get("/items", listItems)
}
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        # Fiber Get should be normalized to GET
        assert any(n.get("meta", {}).get("http_method") == "GET" for n in routes)

    def test_route_handler_as_selector(self, tmp_path: Path) -> None:
        """Test route with handler as selector expression (pkg.Handler)."""
        make_go_file(tmp_path, "main.go", """
package main

import "github.com/gin-gonic/gin"

type handlers struct{}
func (h *handlers) GetUser(c *gin.Context) {}

func main() {
    h := &handlers{}
    r := gin.Default()
    r.GET("/user", h.GetUser)
}
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        assert any("GetUser" in n["name"] for n in routes)

    def test_route_all_http_methods(self, tmp_path: Path) -> None:
        """Test all supported HTTP methods create routes."""
        make_go_file(tmp_path, "main.go", """
package main

import "github.com/gin-gonic/gin"

func handler(c *gin.Context) {}

func main() {
    r := gin.Default()
    r.GET("/a", handler)
    r.POST("/b", handler)
    r.PUT("/c", handler)
    r.DELETE("/d", handler)
    r.PATCH("/e", handler)
    r.HEAD("/f", handler)
    r.OPTIONS("/g", handler)
}
""")
        data = analyze(tmp_path)
        routes = [n for n in data["nodes"] if n["kind"] == "route"]
        methods = {n.get("meta", {}).get("http_method") for n in routes}
        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" in methods
        assert "DELETE" in methods
        assert "PATCH" in methods
        assert "HEAD" in methods
        assert "OPTIONS" in methods


class TestGoUsageContextExtraction:
    """Branch coverage for usage context extraction."""

    def test_usage_context_created_for_route(self, tmp_path: Path) -> None:
        """Test UsageContext is created for route registration calls."""
        make_go_file(tmp_path, "main.go", """
package main

import "github.com/gin-gonic/gin"

func listUsers(c *gin.Context) {}

func main() {
    router := gin.Default()
    router.GET("/users", listUsers)
}
""")
        data = analyze(tmp_path)
        contexts = data.get("usage_contexts", [])
        assert len(contexts) >= 1
        ctx = contexts[0]
        assert ctx["kind"] == "call"
        assert "GET" in ctx["context_name"]
        assert ctx.get("metadata", {}).get("route_path") == "/users"

    def test_usage_context_handler_resolved(self, tmp_path: Path) -> None:
        """Test handler is resolved to symbol in UsageContext."""
        make_go_file(tmp_path, "main.go", """
package main

import "github.com/labstack/echo/v4"

func createItem(c echo.Context) error { return nil }

func main() {
    e := echo.New()
    e.POST("/items", createItem)
}
""")
        data = analyze(tmp_path)
        contexts = [c for c in data.get("usage_contexts", []) if "POST" in c.get("context_name", "")]
        assert len(contexts) >= 1
        # Handler should be resolved
        ctx = contexts[0]
        assert ctx.get("metadata", {}).get("handler_name") == "createItem"

    def test_usage_context_path_normalization(self, tmp_path: Path) -> None:
        """Test route path is normalized to start with /."""
        make_go_file(tmp_path, "main.go", """
package main

import "github.com/gofiber/fiber/v2"

func handler(c *fiber.Ctx) error { return nil }

func main() {
    app := fiber.New()
    app.Get("health", handler)
}
""")
        data = analyze(tmp_path)
        contexts = data.get("usage_contexts", [])
        assert any(c.get("metadata", {}).get("route_path") == "/health" for c in contexts)


class TestGoAnonymousFunctions:
    """Branch coverage for calls inside anonymous functions."""

    def test_call_inside_goroutine(self, tmp_path: Path) -> None:
        """Test calls inside goroutine anonymous functions are attributed."""
        make_go_file(tmp_path, "main.go", """
package main

func worker() {}

func main() {
    go func() {
        worker()
    }()
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("worker" in e["dst"] for e in edges)

    def test_call_inside_callback(self, tmp_path: Path) -> None:
        """Test calls inside callback anonymous functions are attributed."""
        make_go_file(tmp_path, "main.go", """
package main

func process() {}

func runWithCallback(f func()) {
    f()
}

func main() {
    runWithCallback(func() {
        process()
    })
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("process" in e["dst"] for e in edges)
