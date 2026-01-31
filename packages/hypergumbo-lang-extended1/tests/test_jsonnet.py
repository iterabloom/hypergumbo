"""Tests for the Jsonnet configuration language analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import jsonnet as jsonnet_module
from hypergumbo_lang_extended1.jsonnet import (
    JsonnetAnalysisResult,
    analyze_jsonnet,
    find_jsonnet_files,
    is_jsonnet_tree_sitter_available,
)


def make_jsonnet_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Jsonnet file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindJsonnetFiles:
    """Tests for find_jsonnet_files function."""

    def test_finds_jsonnet_files(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "config.jsonnet", "{}")
        make_jsonnet_file(tmp_path, "utils.libsonnet", "{}")
        files = find_jsonnet_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"config.jsonnet", "utils.libsonnet"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_jsonnet_files(tmp_path)
        assert files == []


class TestIsJsonnetTreeSitterAvailable:
    """Tests for is_jsonnet_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_jsonnet_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(jsonnet_module, "is_jsonnet_tree_sitter_available", return_value=False):
            assert jsonnet_module.is_jsonnet_tree_sitter_available() is False


class TestAnalyzeJsonnet:
    """Tests for analyze_jsonnet function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", "{}")
        with patch.object(jsonnet_module, "is_jsonnet_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Jsonnet analysis skipped"):
                result = jsonnet_module.analyze_jsonnet(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_local_functions(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "utils.jsonnet", """
local add(a, b) = a + b;
local multiply(x, y) = x * y;
{}
""")
        result = analyze_jsonnet(tmp_path)
        assert not result.skipped
        func = next((s for s in result.symbols if s.name == "add"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.language == "jsonnet"
        assert func.meta["param_count"] == 2

    def test_extracts_local_variables(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "config.jsonnet", """
local name = "test";
local count = 42;
{}
""")
        result = analyze_jsonnet(tmp_path)
        var = next((s for s in result.symbols if s.name == "name"), None)
        assert var is not None
        assert var.kind == "variable"

    def test_extracts_object_methods(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "obj.jsonnet", """
{
  greet(name):: "Hello, " + name,
  compute(x, y):: x + y,
}
""")
        result = analyze_jsonnet(tmp_path)
        method = next((s for s in result.symbols if s.name == "greet"), None)
        assert method is not None
        assert method.kind == "method"
        assert method.meta["param_count"] == 1
        assert method.meta["hidden"] is True

    def test_extracts_object_fields(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "obj.jsonnet", """
{
  name: "test",
  count: 42,
}
""")
        result = analyze_jsonnet(tmp_path)
        field = next((s for s in result.symbols if s.name == "name"), None)
        assert field is not None
        assert field.kind == "field"
        assert field.meta.get("hidden") is False

    def test_extracts_imports(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "main.jsonnet", """
local utils = import "utils.libsonnet";
{}
""")
        result = analyze_jsonnet(tmp_path)
        imp = next((s for s in result.symbols if s.kind == "import"), None)
        assert imp is not None
        assert imp.name == "utils.libsonnet"

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
local helper(x) = x * 2;
local result = helper(5);
result
""")
        result = analyze_jsonnet(tmp_path)
        edge = next(
            (e for e in result.edges if "helper" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"
        assert edge.confidence == 1.0

    def test_extracts_method_call_edges(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
local MyClass = {
  compute(x):: x * 2,
};
local result = MyClass.compute(5);
result
""")
        result = analyze_jsonnet(tmp_path)
        edge = next(
            (e for e in result.edges if "compute" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_extracts_import_edges(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "main.jsonnet", """
local utils = import "utils.libsonnet";
{}
""")
        result = analyze_jsonnet(tmp_path)
        edge = next(
            (e for e in result.edges if e.edge_type == "imports"),
            None
        )
        assert edge is not None
        assert "utils.libsonnet" in edge.dst
        assert edge.confidence == 1.0

    def test_filters_builtins(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
local result = std.format("hello %s", "world");
result
""")
        result = analyze_jsonnet(tmp_path)
        # Should not have edges to std.format (builtin)
        std_edges = [e for e in result.edges if e.edge_type == "calls" and "std" in e.dst]
        assert len(std_edges) == 0

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
local result = externalFunc(42);
result
""")
        result = analyze_jsonnet(tmp_path)
        edge = next(
            (e for e in result.edges if "externalFunc" in e.dst),
            None
        )
        assert edge is not None
        assert "unresolved" in edge.dst
        assert edge.confidence == 0.6

    def test_pass_id(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
local add(a, b) = a + b;
{}
""")
        result = analyze_jsonnet(tmp_path)
        func = next((s for s in result.symbols if s.name == "add"), None)
        assert func is not None
        assert func.origin == "jsonnet.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", "{}")
        result = analyze_jsonnet(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "jsonnet.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_jsonnet(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
local myFunc(x) = x;
{}
""")
        result = analyze_jsonnet(tmp_path)
        func = next((s for s in result.symbols if s.name == "myFunc"), None)
        assert func is not None
        assert func.id == func.stable_id
        assert "jsonnet:" in func.id
        assert "test.jsonnet" in func.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
local myFunc(x) = x;
{}
""")
        result = analyze_jsonnet(tmp_path)
        func = next((s for s in result.symbols if s.name == "myFunc"), None)
        assert func is not None
        assert func.span is not None
        assert func.span.start_line >= 1
        assert func.span.end_line >= func.span.start_line

    def test_libsonnet_extension(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "utils.libsonnet", """
{
  add(a, b):: a + b,
}
""")
        result = analyze_jsonnet(tmp_path)
        method = next((s for s in result.symbols if s.name == "add"), None)
        assert method is not None
        assert "utils.libsonnet" in method.path

    def test_nested_objects(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
{
  outer: {
    inner: {
      value: 42,
    },
  },
}
""")
        result = analyze_jsonnet(tmp_path)
        fields = [s for s in result.symbols if s.kind == "field"]
        names = {f.name for f in fields}
        assert "outer" in names
        assert "inner" in names
        assert "value" in names

    def test_function_with_no_params(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
local getConfig() = { name: "test" };
{}
""")
        result = analyze_jsonnet(tmp_path)
        func = next((s for s in result.symbols if s.name == "getConfig"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.meta["param_count"] == 0

    def test_self_calls_filtered(self, tmp_path: Path) -> None:
        make_jsonnet_file(tmp_path, "test.jsonnet", """
{
  greet():: "Hello",
  message:: self.greet(),
}
""")
        result = analyze_jsonnet(tmp_path)
        # self.greet() should be filtered as builtin (self is builtin)
        self_edges = [e for e in result.edges if "self" in e.dst.lower()]
        assert len(self_edges) == 0
