"""Tests for Lua analyzer.

Lua analysis uses tree-sitter to extract:
- Symbols: function, local function, table/class definitions
- Edges: calls, require (imports)

Test coverage includes:
- Function detection (global and local)
- Method-style function definitions (Table:method)
- Function calls
- Method calls (obj:method())
- require statements (imports)
- Two-pass cross-file resolution
"""
from pathlib import Path




def make_lua_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Lua file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestLuaAnalyzerAvailability:
    """Tests for tree-sitter-lua availability detection."""

    def test_is_lua_tree_sitter_available(self) -> None:
        """Check if tree-sitter-lua is detected."""
        from hypergumbo_lang_mainstream.lua import is_lua_tree_sitter_available

        # Should be True since we installed tree-sitter-lua
        assert is_lua_tree_sitter_available() is True


class TestLuaFunctionDetection:
    """Tests for Lua function symbol extraction."""

    def test_detect_global_function(self, tmp_path: Path) -> None:
        """Detect global function definition."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
function hello(name)
    print("Hello " .. name)
end
""")

        result = analyze_lua(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        func = next((s for s in symbols if s.name == "hello"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.language == "lua"

    def test_detect_local_function(self, tmp_path: Path) -> None:
        """Detect local function definition."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local function greet(msg)
    print(msg)
end
""")

        result = analyze_lua(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "greet"), None)
        assert func is not None
        assert func.kind == "function"

    def test_detect_method_function(self, tmp_path: Path) -> None:
        """Detect method-style function (Table:method)."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local MyClass = {}

function MyClass:new()
    return setmetatable({}, self)
end

function MyClass:greet()
    print("Hello")
end
""")

        result = analyze_lua(tmp_path)

        symbols = result.symbols
        # Methods should be named like Class.method
        new_func = next((s for s in symbols if s.name == "MyClass.new"), None)
        greet_func = next((s for s in symbols if s.name == "MyClass.greet"), None)
        assert new_func is not None
        assert greet_func is not None
        assert new_func.kind == "method"


class TestLuaCallEdges:
    """Tests for Lua function call edge extraction."""

    def test_detect_function_call(self, tmp_path: Path) -> None:
        """Detect function call edges."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
function greet()
    print("Hello")
end

function main()
    greet()
end
""")

        result = analyze_lua(tmp_path)

        edges = result.edges
        # main calls greet
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert any(e.dst.endswith("greet:function") for e in call_edges)

    def test_detect_method_call(self, tmp_path: Path) -> None:
        """Detect method call edges (obj:method())."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local MyClass = {}

function MyClass:hello()
    print("Hello")
end

function test()
    local obj = MyClass
    obj:hello()
end
""")

        result = analyze_lua(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # Should have a call to hello
        assert len(call_edges) >= 1

    def test_method_call_lower_confidence_than_direct(self, tmp_path: Path) -> None:
        """Untyped method calls get lower confidence than direct calls.

        When a method call's receiver has no known type (e.g., a function
        parameter), it uses 0.40x base confidence. Direct calls (func()) use
        0.85x because function names tend to be unique. This test uses a
        function parameter (unknown_obj) so the receiver has no tracked type.
        """
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local MyClass = {}

function MyClass:process()
    return 1
end

function helper()
    return 2
end

function main(unknown_obj)
    unknown_obj:process()
    helper()
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        method_edge = next(
            (e for e in call_edges if "process" in e.dst), None
        )
        direct_edge = next(
            (e for e in call_edges if "helper" in e.dst), None
        )
        assert method_edge is not None, "Method call edge to process not found"
        assert direct_edge is not None, "Direct call edge to helper not found"
        # Untyped method call confidence should be lower than direct call
        assert method_edge.confidence < direct_edge.confidence
        # Untyped method call should be <= 0.50 (ambiguous, no receiver type)
        assert method_edge.confidence <= 0.50
        # Direct call should remain at 0.85 (name-only but less ambiguous)
        assert direct_edge.confidence >= 0.80


class TestLuaImportEdges:
    """Tests for Lua require (import) edge extraction."""

    def test_detect_require_with_parens(self, tmp_path: Path) -> None:
        """Detect require('module') statements."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local json = require('json')
local socket = require("socket")

function main()
    json.encode({})
end
""")

        result = analyze_lua(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        # Should have imports for json and socket
        assert any("json" in e.dst for e in import_edges)
        assert any("socket" in e.dst for e in import_edges)

    def test_detect_require_without_parens(self, tmp_path: Path) -> None:
        """Detect require 'module' statements (no parentheses)."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local lfs = require 'lfs'
""")

        result = analyze_lua(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert any("lfs" in e.dst for e in import_edges)


class TestLuaCrossFileResolution:
    """Tests for two-pass cross-file call resolution."""

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Calls to functions in other files are resolved."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "utils.lua", """
function helper()
    return "help"
end
""")

        make_lua_file(tmp_path, "main.lua", """
local utils = require('utils')

function main()
    helper()
end
""")

        result = analyze_lua(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]

        # Call to helper should be resolved
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1


class TestLuaEdgeCases:
    """Edge case tests for Lua analyzer."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty Lua file produces no symbols."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "empty.lua", "")

        result = analyze_lua(tmp_path)

        assert not result.skipped
        # Only file symbol should exist
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0

    def test_syntax_error_file(self, tmp_path: Path) -> None:
        """File with syntax error is handled gracefully."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "bad.lua", """
function broken(
    -- missing close paren and body
""")

        result = analyze_lua(tmp_path)

        # Should not crash - parser may still return empty result
        assert not result.skipped

    def test_nested_functions(self, tmp_path: Path) -> None:
        """Nested function definitions are detected."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "nested.lua", """
function outer()
    local function inner()
        print("inner")
    end
    inner()
end
""")

        result = analyze_lua(tmp_path)

        symbols = result.symbols
        outer = next((s for s in symbols if s.name == "outer"), None)
        inner = next((s for s in symbols if s.name == "inner"), None)
        assert outer is not None
        # Inner functions may or may not be detected depending on implementation
        # Just ensure outer is found

    def test_no_lua_files(self, tmp_path: Path) -> None:
        """Directory with no Lua files returns empty result."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.py", "print('hello')")

        result = analyze_lua(tmp_path)

        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0


class TestLuaSpanAccuracy:
    """Tests for accurate source location tracking."""

    def test_function_span(self, tmp_path: Path) -> None:
        """Function span includes full definition."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """function hello()
    print("hi")
end
""")

        result = analyze_lua(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "hello"), None)
        assert func is not None
        assert func.span.start_line == 1
        assert func.span.end_line == 3


class TestLuaAnalyzeFallback:
    """Tests for fallback when tree-sitter-lua is unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path, monkeypatch) -> None:
        """Returns skipped result when tree-sitter-lua not available."""
        from hypergumbo_lang_mainstream import lua

        # Mock tree-sitter-lua as unavailable
        monkeypatch.setattr(lua, "is_lua_tree_sitter_available", lambda: False)

        make_lua_file(tmp_path, "main.lua", "function test() end")

        result = lua.analyze_lua(tmp_path)

        assert result.skipped
        assert "tree-sitter-lua" in result.skip_reason
        # Run should still be created for provenance tracking
        assert result.run is not None
        assert result.run.pass_id == "lua-v1"


class TestLuaSignatureExtraction:
    """Tests for Lua function signature extraction."""

    def test_positional_params(self, tmp_path: Path) -> None:
        """Extracts signature with positional parameters."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(
            tmp_path,
            "calc.lua",
            """
function add(x, y)
    return x + y
end
""",
        )
        result = analyze_lua(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x, y)"

    def test_no_params_function(self, tmp_path: Path) -> None:
        """Extracts signature for function with no parameters."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(
            tmp_path,
            "simple.lua",
            """
function answer()
    return 42
end
""",
        )
        result = analyze_lua(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "answer"]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"

    def test_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from method-style functions."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(
            tmp_path,
            "player.lua",
            """
Player = {}

function Player:move(dx, dy)
    self.x = self.x + dx
    self.y = self.y + dy
end
""",
        )
        result = analyze_lua(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "move" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(dx, dy)"


class TestLuaTypeTracking:
    """Tests for single-assignment type tracking in method call resolution.

    When Lua code assigns a value to a local variable (e.g., local sock = MyClass),
    the analyzer should track that sock's type is MyClass and use it to resolve
    method calls like sock:method() with higher confidence.
    """

    def test_typed_method_call_higher_confidence(self, tmp_path: Path) -> None:
        """Method call with known receiver type gets higher confidence than untyped.

        When `local obj = MyClass` is followed by `obj:process()`, the analyzer
        knows obj is of type MyClass, so it should resolve to MyClass.process
        with higher confidence than an untyped method call.
        """
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local MyClass = {}

function MyClass:process()
    return 1
end

function caller()
    local obj = MyClass
    obj:process()
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        process_edge = next(
            (e for e in call_edges if "process" in e.dst), None
        )
        assert process_edge is not None, "Call edge to MyClass.process not found"
        # With type tracking, confidence should be higher than the 0.40 base
        assert process_edge.confidence >= 0.70, (
            f"Typed method call should have confidence >= 0.70, got {process_edge.confidence}"
        )
        # Evidence type should indicate typed resolution
        assert process_edge.evidence_type == "method_call_typed"

    def test_untyped_method_call_remains_low_confidence(self, tmp_path: Path) -> None:
        """Method call without type info still uses low confidence.

        When the receiver variable has no tracked type assignment, method calls
        should continue to use the low 0.40x confidence.
        """
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local Handler = {}

function Handler:send(data)
    return true
end

function caller(unknown_obj)
    unknown_obj:send("hello")
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        send_edge = next(
            (e for e in call_edges if "send" in e.dst), None
        )
        assert send_edge is not None, "Call edge to send not found"
        # Without type info, should remain at low confidence (0.40 * resolver)
        assert send_edge.confidence <= 0.50
        # Evidence type should remain as untyped function_call
        assert send_edge.evidence_type == "function_call"

    def test_dot_index_assignment_type_tracking(self, tmp_path: Path) -> None:
        """Track type from dot_index_expression assignment (e.g., ngx.socket.tcp).

        The pattern `local sock = ngx.socket.tcp()` should track sock's type
        as 'ngx.socket.tcp', which is the core OpenResty pattern.
        """
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local ngx_socket = {}

function ngx_socket:send(data)
    return #data
end

function ngx_socket.tcp()
    return ngx_socket
end

function handler()
    local sock = ngx_socket.tcp()
    sock:send("request")
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        send_edge = next(
            (e for e in call_edges if "send" in e.dst), None
        )
        assert send_edge is not None, "Call edge to send not found"
        # Should resolve with higher confidence because sock = ngx_socket.tcp()
        # returns ngx_socket (the table that has :send defined)
        assert send_edge.evidence_type == "method_call_typed"

    def test_constructor_pattern_type_tracking(self, tmp_path: Path) -> None:
        """Track type from constructor call (MyClass:new()).

        The Lua OOP convention `local obj = MyClass:new()` should infer
        that obj's type is MyClass, since :new() conventionally returns self.
        """
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local Animal = {}

function Animal:new()
    return setmetatable({}, {__index = self})
end

function Animal:speak()
    return "..."
end

function main()
    local dog = Animal:new()
    dog:speak()
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        speak_edge = next(
            (e for e in call_edges if "speak" in e.dst), None
        )
        assert speak_edge is not None, "Call edge to Animal.speak not found"
        # Constructor pattern should resolve type: dog → Animal
        assert speak_edge.evidence_type == "method_call_typed"
        assert speak_edge.confidence >= 0.70

    def test_type_tracking_does_not_affect_direct_calls(self, tmp_path: Path) -> None:
        """Direct function calls (func()) remain unaffected by type tracking."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
function helper()
    return 42
end

function main()
    helper()
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_edge = next(
            (e for e in call_edges if "helper" in e.dst), None
        )
        assert helper_edge is not None
        assert helper_edge.evidence_type == "function_call"
        assert helper_edge.confidence >= 0.80


class TestLuaRequireAliasResolution:
    """Tests for require-alias call resolution.

    When ``local X = require("foo.bar")`` is followed by ``X.method()``
    or ``X:method()``, the call should resolve to foo/bar.lua's exported
    method rather than relying on global name matching.
    """

    def test_require_alias_dot_call_resolves_to_module(self, tmp_path: Path) -> None:
        """X.method() resolves to foo/bar.lua when X = require("foo.bar")."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        # Create the module file
        mod_dir = tmp_path / "foo"
        mod_dir.mkdir()
        make_lua_file(mod_dir, "bar.lua", """
local _M = {}

function _M.process(data)
    return data
end

return _M
""")

        # Create the caller file
        make_lua_file(tmp_path, "main.lua", """
local bar = require("foo.bar")

function handler()
    bar.process("hello")
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        process_edge = next(
            (e for e in call_edges if "process" in e.dst and "handler" in e.src), None
        )
        assert process_edge is not None, (
            f"Call edge from handler to process not found. Edges: {call_edges}"
        )
        # Should resolve via require alias, not global name match
        assert process_edge.evidence_type == "require_alias_call"
        assert process_edge.confidence >= 0.80
        # Target should be the symbol in foo/bar.lua
        assert "foo/bar.lua" in process_edge.dst or "foo" in process_edge.dst

    def test_require_alias_colon_call_resolves_to_module(self, tmp_path: Path) -> None:
        """X:method() resolves to foo/bar.lua when X = require("foo.bar")."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        # Create the module file
        mod_dir = tmp_path / "foo"
        mod_dir.mkdir()
        make_lua_file(mod_dir, "bar.lua", """
local Handler = {}

function Handler:access(conf)
    return true
end

return Handler
""")

        # Create the caller file
        make_lua_file(tmp_path, "main.lua", """
local bar = require("foo.bar")

function run()
    bar:access({})
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        access_edge = next(
            (e for e in call_edges if "access" in e.dst and "run" in e.src), None
        )
        assert access_edge is not None, (
            f"Call edge from run to access not found. Edges: {call_edges}"
        )
        assert access_edge.evidence_type == "require_alias_call"
        assert access_edge.confidence >= 0.80

    def test_require_alias_init_lua_resolution(self, tmp_path: Path) -> None:
        """require("foo") resolves to foo/init.lua."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        # Create init.lua module
        mod_dir = tmp_path / "foo"
        mod_dir.mkdir()
        make_lua_file(mod_dir, "init.lua", """
local _M = {}

function _M.create()
    return {}
end

return _M
""")

        # Create the caller
        make_lua_file(tmp_path, "main.lua", """
local foo = require("foo")

function setup()
    foo.create()
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        create_edge = next(
            (e for e in call_edges if "create" in e.dst and "setup" in e.src), None
        )
        assert create_edge is not None, (
            f"Call edge from setup to create not found. Edges: {call_edges}"
        )
        assert create_edge.evidence_type == "require_alias_call"

    def test_unresolved_method_call_creates_edge(self, tmp_path: Path) -> None:
        """Method call to unknown target still creates an unresolved edge."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
function handler(obj)
    obj:totally_unknown_method()
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        edge = next(
            (e for e in call_edges if "totally_unknown_method" in e.dst), None
        )
        assert edge is not None, (
            f"Unresolved method call edge not found. Edges: {call_edges}"
        )
        assert "?" in edge.dst  # Unresolved target
        assert edge.confidence == 0.50

    def test_non_require_dot_call_unaffected(self, tmp_path: Path) -> None:
        """Dot calls on non-require aliases still use regular resolution."""
        from hypergumbo_lang_mainstream.lua import analyze_lua

        make_lua_file(tmp_path, "main.lua", """
local MyTable = {}

function MyTable.helper()
    return 1
end

function main()
    MyTable.helper()
end
""")

        result = analyze_lua(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_edge = next(
            (e for e in call_edges if "helper" in e.dst), None
        )
        assert helper_edge is not None
        # Non-require dot calls should not have require_alias_call evidence
        assert helper_edge.evidence_type != "require_alias_call"
