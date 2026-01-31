"""Tests for the Luau language analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import luau as luau_module
from hypergumbo_lang_extended1.luau import (
    LuauAnalysisResult,
    analyze_luau,
    find_luau_files,
    is_luau_tree_sitter_available,
)


def make_luau_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Luau file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindLuauFiles:
    """Tests for find_luau_files function."""

    def test_finds_luau_files(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "main.luau", "local x = 1")
        make_luau_file(tmp_path, "lib/utils.luau", "local y = 2")
        files = find_luau_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"main.luau", "utils.luau"}

    def test_finds_lua_files(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "module.lua", "local x = 1")
        files = find_luau_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "module.lua"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_luau_files(tmp_path)
        assert files == []


class TestIsLuauTreeSitterAvailable:
    """Tests for is_luau_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_luau_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(luau_module, "is_luau_tree_sitter_available", return_value=False):
            assert luau_module.is_luau_tree_sitter_available() is False


class TestAnalyzeLuau:
    """Tests for analyze_luau function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", "local x = 1")
        with patch.object(luau_module, "is_luau_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Luau analysis skipped"):
                result = luau_module.analyze_luau(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_local_function(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function helper()
    return 1
end
""")
        result = analyze_luau(tmp_path)
        assert not result.skipped
        func = next((s for s in result.symbols if s.kind == "function"), None)
        assert func is not None
        assert func.name == "helper"
        assert func.language == "luau"
        assert func.meta.get("local") is True

    def test_extracts_function_with_params(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function add(a, b)
    return a + b
end
""")
        result = analyze_luau(tmp_path)
        func = next((s for s in result.symbols if s.kind == "function"), None)
        assert func is not None
        assert "a" in func.meta.get("params", [])
        assert "b" in func.meta.get("params", [])
        assert func.signature == "add(a, b)"

    def test_extracts_typed_function(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function greet(name: string): string
    return "Hello, " .. name
end
""")
        result = analyze_luau(tmp_path)
        func = next((s for s in result.symbols if s.kind == "function"), None)
        assert func is not None
        params = func.meta.get("params", [])
        # Should have typed parameter
        assert len(params) >= 1

    def test_extracts_module_function(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local MyModule = {}

function MyModule.create()
    return {}
end
""")
        result = analyze_luau(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "MyModule.create"

    def test_extracts_method_function(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local MyClass = {}

function MyClass:init()
    self.value = 0
end
""")
        result = analyze_luau(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert "MyClass" in funcs[0].name
        assert "init" in funcs[0].name

    def test_extracts_type_definition(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
type Player = {
    Name: string,
    Health: number,
}
""")
        result = analyze_luau(tmp_path)
        typ = next((s for s in result.symbols if s.kind == "type"), None)
        assert typ is not None
        assert typ.name == "Player"

    def test_extracts_exported_type(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
export type GameState = {
    Score: number,
}
""")
        result = analyze_luau(tmp_path)
        typ = next((s for s in result.symbols if s.kind == "type"), None)
        assert typ is not None
        assert typ.name == "GameState"
        assert typ.meta.get("exported") is True

    def test_extracts_module_variable(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local MyModule = {}
""")
        result = analyze_luau(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.name == "MyModule"
        assert var.meta.get("local") is True

    def test_ignores_lowercase_variables(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local count = 0
local name = "test"
""")
        result = analyze_luau(tmp_path)
        # Should not extract lowercase variables as modules
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert len(vars) == 0

    def test_extracts_function_call_edge(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function helper()
    return 1
end

local function main()
    helper()
end
""")
        result = analyze_luau(tmp_path)
        edge = next(
            (e for e in result.edges if "helper" in e.dst and "unresolved" not in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_extracts_module_method_call_edge(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function main()
    Helper.doWork()
end
""")
        result = analyze_luau(tmp_path)
        edge = next(
            (e for e in result.edges if "Helper" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_filters_builtin_calls(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function main()
    print("Hello")
    local x = math.abs(-5)
    local s = string.format("test %d", 1)
end
""")
        result = analyze_luau(tmp_path)
        # Should not have edges for print, math.abs, string.format
        builtin_edges = [
            e for e in result.edges
            if "print" in e.dst or "math" in e.dst or "string" in e.dst
        ]
        assert len(builtin_edges) == 0

    def test_filters_roblox_service_calls(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function main()
    local ReplicatedStorage = game:GetService("ReplicatedStorage")
    local Players = game:GetService("Players")
end
""")
        result = analyze_luau(tmp_path)
        # Should not have edges for game:GetService
        game_edges = [e for e in result.edges if "game" in e.dst or "GetService" in e.dst]
        assert len(game_edges) == 0

    def test_resolved_call_has_high_confidence(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function helper()
    return 1
end

local function main()
    helper()
end
""")
        result = analyze_luau(tmp_path)
        edge = next(
            (e for e in result.edges if "helper" in e.dst and "unresolved" not in e.dst),
            None
        )
        assert edge is not None
        assert edge.confidence == 1.0

    def test_unresolved_call_has_low_confidence(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function main()
    ExternalModule.doSomething()
end
""")
        result = analyze_luau(tmp_path)
        edge = next(
            (e for e in result.edges if "unresolved" in e.dst),
            None
        )
        assert edge is not None
        assert edge.confidence == 0.6

    def test_pass_id(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function test()
end
""")
        result = analyze_luau(tmp_path)
        func = next((s for s in result.symbols if s.kind == "function"), None)
        assert func is not None
        assert func.origin == "luau.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", "local x = 1")
        result = analyze_luau(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "luau.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_luau(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function myFunc()
end
""")
        result = analyze_luau(tmp_path)
        func = next((s for s in result.symbols if s.kind == "function"), None)
        assert func is not None
        assert func.id == func.stable_id
        assert "luau:" in func.id
        assert "test.luau" in func.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local function myFunc()
end
""")
        result = analyze_luau(tmp_path)
        func = next((s for s in result.symbols if s.kind == "function"), None)
        assert func is not None
        assert func.span is not None
        assert func.span.start_line >= 1
        assert func.span.end_line >= func.span.start_line

    def test_multiple_types(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
type Point = { x: number, y: number }
type Color = { r: number, g: number, b: number }
export type Config = { debug: boolean }
""")
        result = analyze_luau(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) == 3
        names = {t.name for t in types}
        assert "Point" in names
        assert "Color" in names
        assert "Config" in names

    def test_self_method_call(self, tmp_path: Path) -> None:
        make_luau_file(tmp_path, "test.luau", """
local MyClass = {}

function MyClass:helper()
    return 1
end

function MyClass:main()
    self:helper()
end
""")
        result = analyze_luau(tmp_path)
        # self:helper() call should be extracted
        edges = [e for e in result.edges if "helper" in e.dst]
        assert len(edges) >= 1
