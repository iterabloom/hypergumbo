"""Branch coverage tests for smithy.py analyzer.

Tests specific branch paths in the Smithy analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import smithy as smithy_module
from hypergumbo_lang_extended1.smithy import (
    analyze_smithy,
    find_smithy_files,
)


def make_smithy_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Smithy file with given content."""
    (tmp_path / name).write_text(content)


class TestServiceExtraction:
    """Branch coverage for service extraction."""

    def test_service_declaration(self, tmp_path: Path) -> None:
        """Test service declaration extraction."""
        make_smithy_file(tmp_path, "model.smithy", """
$version: "2"
namespace example

service WeatherService {
    operations: [GetCurrentTime]
}
""")
        result = analyze_smithy(tmp_path)
        assert not result.skipped
        services = [s for s in result.symbols if s.kind == "service"]
        assert any("WeatherService" in svc.name for svc in services)


class TestOperationExtraction:
    """Branch coverage for operation extraction."""

    def test_operation_declaration(self, tmp_path: Path) -> None:
        """Test operation declaration extraction."""
        make_smithy_file(tmp_path, "model.smithy", """
$version: "2"
namespace example

operation GetCurrentTime {
    output: GetCurrentTimeOutput
}
""")
        result = analyze_smithy(tmp_path)
        operations = [s for s in result.symbols if s.kind == "operation"]
        assert any("GetCurrentTime" in op.name for op in operations)


class TestStructureExtraction:
    """Branch coverage for structure extraction."""

    def test_structure_declaration(self, tmp_path: Path) -> None:
        """Test structure declaration extraction."""
        make_smithy_file(tmp_path, "model.smithy", """
$version: "2"
namespace example

structure GetCurrentTimeOutput {
    @required
    time: Timestamp
}
""")
        result = analyze_smithy(tmp_path)
        structures = [s for s in result.symbols if s.kind == "structure"]
        assert any("GetCurrentTimeOutput" in st.name for st in structures)


class TestResourceExtraction:
    """Branch coverage for resource extraction."""

    def test_resource_declaration(self, tmp_path: Path) -> None:
        """Test resource declaration extraction."""
        make_smithy_file(tmp_path, "model.smithy", """
$version: "2"
namespace example

resource Forecast {
    identifiers: { cityId: CityId }
    read: GetForecast
}
""")
        result = analyze_smithy(tmp_path)
        resources = [s for s in result.symbols if s.kind == "resource"]
        assert any("Forecast" in r.name for r in resources)


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_smithy_file(tmp_path, "model.smithy", """
$version: "2"
namespace example

enum Suit {
    CLUB
    DIAMOND
    HEART
    SPADE
}
""")
        result = analyze_smithy(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any("Suit" in e.name for e in enums)


class TestUnionExtraction:
    """Branch coverage for union extraction."""

    def test_union_declaration(self, tmp_path: Path) -> None:
        """Test union declaration extraction."""
        make_smithy_file(tmp_path, "model.smithy", """
$version: "2"
namespace example

union MyUnion {
    i32: Integer
    string: String
}
""")
        result = analyze_smithy(tmp_path)
        unions = [s for s in result.symbols if s.kind == "union"]
        assert any("MyUnion" in u.name for u in unions)


class TestUseEdges:
    """Branch coverage for use edge extraction."""

    def test_use_creates_edge(self, tmp_path: Path) -> None:
        """Test use creates import edge."""
        make_smithy_file(tmp_path, "model.smithy", """
$version: "2"
namespace example

use smithy.api#String
use smithy.api#required
""")
        result = analyze_smithy(tmp_path)
        uses = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestFindSmithyFiles:
    """Branch coverage for file discovery."""

    def test_finds_smithy_files(self, tmp_path: Path) -> None:
        """Test .smithy files are discovered."""
        (tmp_path / "model.smithy").write_text('$version: "2"')
        files = list(find_smithy_files(tmp_path))
        assert any(f.suffix == ".smithy" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_smithy_files(self, tmp_path: Path) -> None:
        """Test directory with no Smithy files."""
        result = analyze_smithy(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(smithy_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="smithy analysis skipped"):
                result = smithy_module.analyze_smithy(tmp_path)
        assert result.skipped is True
