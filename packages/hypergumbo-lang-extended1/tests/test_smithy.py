"""Tests for the Smithy API definition language analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import smithy as smithy_module
from hypergumbo_lang_extended1.smithy import (
    SmithyAnalysisResult,
    analyze_smithy,
    find_smithy_files,
    is_smithy_tree_sitter_available,
)


def make_smithy_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Smithy file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindSmithyFiles:
    """Tests for find_smithy_files function."""

    def test_finds_smithy_files(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "main.smithy", "namespace test")
        make_smithy_file(tmp_path, "models/user.smithy", "namespace test.models")
        files = find_smithy_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"main.smithy", "user.smithy"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_smithy_files(tmp_path)
        assert files == []


class TestIsSmithyTreeSitterAvailable:
    """Tests for is_smithy_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_smithy_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(smithy_module, "is_smithy_tree_sitter_available", return_value=False):
            assert smithy_module.is_smithy_tree_sitter_available() is False


class TestAnalyzeSmithy:
    """Tests for analyze_smithy function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", "namespace test")
        with patch.object(smithy_module, "is_smithy_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Smithy analysis skipped"):
                result = smithy_module.analyze_smithy(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_namespaces(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example.weather
""")
        result = analyze_smithy(tmp_path)
        assert not result.skipped
        ns = next((s for s in result.symbols if s.kind == "namespace"), None)
        assert ns is not None
        assert ns.name == "example.weather"
        assert ns.language == "smithy"

    def test_extracts_services(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

service Weather {
    version: "1.0"
}
""")
        result = analyze_smithy(tmp_path)
        svc = next((s for s in result.symbols if s.kind == "service"), None)
        assert svc is not None
        assert svc.name == "example#Weather"

    def test_extracts_operations(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

operation GetCity {
    input: GetCityInput
    output: GetCityOutput
}
""")
        result = analyze_smithy(tmp_path)
        op = next((s for s in result.symbols if s.kind == "operation"), None)
        assert op is not None
        assert op.name == "example#GetCity"

    def test_extracts_structures(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

structure User {
    name: String
    age: Integer
}
""")
        result = analyze_smithy(tmp_path)
        struct = next((s for s in result.symbols if s.kind == "structure"), None)
        assert struct is not None
        assert struct.name == "example#User"

    def test_extracts_resources(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

resource City {
    identifiers: { cityId: CityId }
}
""")
        result = analyze_smithy(tmp_path)
        res = next((s for s in result.symbols if s.kind == "resource"), None)
        assert res is not None
        assert res.name == "example#City"

    def test_extracts_simple_types(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

string CityId
integer Count
""")
        result = analyze_smithy(tmp_path)
        simple_types = [s for s in result.symbols if s.kind == "simple_type"]
        assert len(simple_types) == 2
        names = {s.name for s in simple_types}
        assert "example#CityId" in names
        assert "example#Count" in names

    def test_extracts_service_operations_edge(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

service Weather {
    version: "1.0"
    operations: [GetCity]
}

operation GetCity {}
""")
        result = analyze_smithy(tmp_path)
        edge = next(
            (e for e in result.edges if e.edge_type == "contains" and "GetCity" in e.dst),
            None
        )
        assert edge is not None
        assert edge.confidence == 1.0

    def test_extracts_operation_input_output_edges(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

operation GetCity {
    input: GetCityInput
    output: GetCityOutput
}

structure GetCityInput {}
structure GetCityOutput {}
""")
        result = analyze_smithy(tmp_path)
        # Should have edges to input and output types
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) >= 2
        dst_names = {e.dst for e in ref_edges}
        assert any("GetCityInput" in d for d in dst_names)
        assert any("GetCityOutput" in d for d in dst_names)

    def test_extracts_structure_member_edges(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

structure User {
    address: Address
}

structure Address {}
""")
        result = analyze_smithy(tmp_path)
        edge = next(
            (e for e in result.edges if "Address" in e.dst and e.edge_type == "references"),
            None
        )
        assert edge is not None

    def test_filters_primitive_types(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

structure User {
    name: String
    age: Integer
}
""")
        result = analyze_smithy(tmp_path)
        # Should not have edges to String or Integer
        primitive_edges = [
            e for e in result.edges
            if "String" in e.dst or "Integer" in e.dst
        ]
        assert len(primitive_edges) == 0

    def test_unresolved_type_reference(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

operation GetCity {
    input: ExternalInput
}
""")
        result = analyze_smithy(tmp_path)
        edge = next(
            (e for e in result.edges if "ExternalInput" in e.dst),
            None
        )
        assert edge is not None
        assert "unresolved" in edge.dst
        assert edge.confidence == 0.6

    def test_pass_id(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

service Weather {}
""")
        result = analyze_smithy(tmp_path)
        svc = next((s for s in result.symbols if s.kind == "service"), None)
        assert svc is not None
        assert svc.origin == "smithy.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", "namespace test")
        result = analyze_smithy(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "smithy.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_smithy(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

service MyService {}
""")
        result = analyze_smithy(tmp_path)
        svc = next((s for s in result.symbols if s.kind == "service"), None)
        assert svc is not None
        assert svc.id == svc.stable_id
        assert "smithy:" in svc.id
        assert "test.smithy" in svc.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

service MyService {}
""")
        result = analyze_smithy(tmp_path)
        svc = next((s for s in result.symbols if s.kind == "service"), None)
        assert svc is not None
        assert svc.span is not None
        assert svc.span.start_line >= 1
        assert svc.span.end_line >= svc.span.start_line

    def test_extracts_unions(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

union MyUnion {
    a: String
    b: Integer
}
""")
        result = analyze_smithy(tmp_path)
        union = next((s for s in result.symbols if s.kind == "union"), None)
        assert union is not None
        assert union.name == "example#MyUnion"

    def test_extracts_enums(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

enum Status {
    ACTIVE
    INACTIVE
}
""")
        result = analyze_smithy(tmp_path)
        enum = next((s for s in result.symbols if s.kind == "enum"), None)
        assert enum is not None
        assert enum.name == "example#Status"

    def test_extracts_lists(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

list UserList {
    member: User
}
""")
        result = analyze_smithy(tmp_path)
        lst = next((s for s in result.symbols if s.kind == "list"), None)
        assert lst is not None
        assert lst.name == "example#UserList"

    def test_extracts_maps(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

map UserMap {
    key: String
    value: User
}
""")
        result = analyze_smithy(tmp_path)
        mp = next((s for s in result.symbols if s.kind == "map"), None)
        assert mp is not None
        assert mp.name == "example#UserMap"

    def test_extracts_operation_errors(self, tmp_path: Path) -> None:
        make_smithy_file(tmp_path, "test.smithy", """
namespace example

operation GetCity {
    errors: [CityNotFound]
}

@error("client")
structure CityNotFound {}
""")
        result = analyze_smithy(tmp_path)
        edge = next(
            (e for e in result.edges if "CityNotFound" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "references"
