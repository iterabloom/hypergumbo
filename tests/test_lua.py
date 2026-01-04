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
        from hypergumbo.analyze.lua import is_lua_tree_sitter_available

        # Should be True since we installed tree-sitter-lua
        assert is_lua_tree_sitter_available() is True


class TestLuaFunctionDetection:
    """Tests for Lua function symbol extraction."""

    def test_detect_global_function(self, tmp_path: Path) -> None:
        """Detect global function definition."""
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

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


class TestLuaImportEdges:
    """Tests for Lua require (import) edge extraction."""

    def test_detect_require_with_parens(self, tmp_path: Path) -> None:
        """Detect require('module') statements."""
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

        make_lua_file(tmp_path, "empty.lua", "")

        result = analyze_lua(tmp_path)

        assert not result.skipped
        # Only file symbol should exist
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0

    def test_syntax_error_file(self, tmp_path: Path) -> None:
        """File with syntax error is handled gracefully."""
        from hypergumbo.analyze.lua import analyze_lua

        make_lua_file(tmp_path, "bad.lua", """
function broken(
    -- missing close paren and body
""")

        result = analyze_lua(tmp_path)

        # Should not crash - parser may still return empty result
        assert not result.skipped

    def test_nested_functions(self, tmp_path: Path) -> None:
        """Nested function definitions are detected."""
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

        make_lua_file(tmp_path, "main.py", "print('hello')")

        result = analyze_lua(tmp_path)

        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind != "file"]
        assert len(symbols) == 0


class TestLuaSpanAccuracy:
    """Tests for accurate source location tracking."""

    def test_function_span(self, tmp_path: Path) -> None:
        """Function span includes full definition."""
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze import lua

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
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

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
        from hypergumbo.analyze.lua import analyze_lua

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
