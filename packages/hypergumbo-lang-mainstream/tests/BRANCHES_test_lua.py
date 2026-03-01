"""Branch coverage tests for lua.py analyzer.

Tests specific branch paths in the Lua analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Function extraction (global and local)
- Method-style functions
- Signature extraction
- Call edges and import edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_mainstream.lua import analyze_lua, find_lua_files

def make_lua_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Lua file with given content."""
    (tmp_path / name).write_text(content)

class TestLuaHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("lua", "src/main.lua", 1, 10, "init", "function")
        assert symbol_id == "lua:src/main.lua:1-10:init:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("lua", "src/main.lua")
        assert file_id == "lua:src/main.lua:1-1:file:file"

class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_global_function(self, tmp_path: Path) -> None:
        """Test global function extraction."""
        make_lua_file(tmp_path, "main.lua", """
function greet(name)
    print("Hello, " .. name)
end
""")
        result = analyze_lua(tmp_path)
        assert not result.skipped

        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) >= 1
        assert any(f.name == "greet" for f in functions)

    def test_local_function(self, tmp_path: Path) -> None:
        """Test local function extraction."""
        make_lua_file(tmp_path, "main.lua", """
local function helper()
    return 42
end
""")
        result = analyze_lua(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 1
        assert any(f.name == "helper" for f in functions)

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple functions extraction."""
        make_lua_file(tmp_path, "utils.lua", """
function add(a, b)
    return a + b
end

function subtract(a, b)
    return a - b
end

local function multiply(a, b)
    return a * b
end
""")
        result = analyze_lua(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 3

class TestMethodExtraction:
    """Branch coverage for method-style function extraction."""

    def test_method_definition(self, tmp_path: Path) -> None:
        """Test method-style function (Table:method)."""
        make_lua_file(tmp_path, "class.lua", """
local MyClass = {}

function MyClass:new(name)
    local obj = { name = name }
    setmetatable(obj, { __index = self })
    return obj
end

function MyClass:greet()
    print("Hello, " .. self.name)
end
""")
        result = analyze_lua(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]

        assert len(methods) >= 2
        assert any("MyClass" in m.name for m in methods)

class TestSignatureExtraction:
    """Branch coverage for signature extraction."""

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function signature with parameters."""
        make_lua_file(tmp_path, "main.lua", """
function process(input, count, options)
    return input
end
""")
        result = analyze_lua(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        process_func = next((f for f in functions if f.name == "process"), None)
        assert process_func is not None
        assert process_func.signature is not None
        assert "input" in process_func.signature
        assert "count" in process_func.signature

    def test_function_no_params(self, tmp_path: Path) -> None:
        """Test function signature with no parameters."""
        make_lua_file(tmp_path, "main.lua", """
function getVersion()
    return "1.0.0"
end
""")
        result = analyze_lua(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        get_version = next((f for f in functions if f.name == "getVersion"), None)
        assert get_version is not None
        assert get_version.signature == "()"

class TestImportEdges:
    """Branch coverage for require statement edges."""

    def test_require_statement(self, tmp_path: Path) -> None:
        """Test require statement creates import edge."""
        make_lua_file(tmp_path, "main.lua", """
local json = require("json")

function parse(data)
    return json.decode(data)
end
""")
        result = analyze_lua(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1

    def test_multiple_requires(self, tmp_path: Path) -> None:
        """Test multiple require statements."""
        make_lua_file(tmp_path, "main.lua", """
local json = require("json")
local socket = require("socket")
local http = require("socket.http")

function main()
    print("loaded")
end
""")
        result = analyze_lua(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 3

class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call_creates_edge(self, tmp_path: Path) -> None:
        """Test function call creates call edge."""
        make_lua_file(tmp_path, "main.lua", """
function helper()
    return 42
end

function main()
    local result = helper()
    print(result)
end
""")
        result = analyze_lua(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        assert len(call_edges) >= 1

class TestFindLuaFiles:
    """Branch coverage for file discovery."""

    def test_finds_lua_files(self, tmp_path: Path) -> None:
        """Test .lua files are discovered."""
        (tmp_path / "main.lua").write_text("print('hello')")

        files = list(find_lua_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".lua" for f in files)

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_lua_files(self, tmp_path: Path) -> None:
        """Test directory with no Lua files."""
        result = analyze_lua(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_lua(self, tmp_path: Path) -> None:
        """Test minimal Lua file."""
        make_lua_file(tmp_path, "main.lua", """
print("hello")
""")
        result = analyze_lua(tmp_path)
        assert not result.skipped

class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_lua_file(tmp_path, "main.lua", """
function main()
    print("main")
end
""")
        result = analyze_lua(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1

class TestComplexLua:
    """Branch coverage for complex Lua files."""

    def test_comprehensive_module(self, tmp_path: Path) -> None:
        """Test comprehensive Lua module."""
        make_lua_file(tmp_path, "module.lua", """
local M = {}

-- Private helper
local function validate(input)
    return input ~= nil
end

-- Public methods
function M:init(config)
    self.config = config
    return self
end

function M:process(data)
    if not validate(data) then
        return nil
    end
    return data
end

function M.new()
    local obj = {}
    setmetatable(obj, { __index = M })
    return obj
end

return M
""")
        result = analyze_lua(tmp_path)

        # Should have multiple functions/methods
        all_funcs = [s for s in result.symbols if s.kind in ("function", "method")]
        assert len(all_funcs) >= 3
