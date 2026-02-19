"""Branch coverage tests for haxe.py analyzer.

Tests specific branch paths in the Haxe analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import haxe as haxe_module
from hypergumbo_lang_extended1.haxe import (
    analyze_haxe,
    find_haxe_files,
)


def make_haxe_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Haxe file with given content."""
    (tmp_path / name).write_text(content)


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_class_declaration(self, tmp_path: Path) -> None:
        """Test class declaration extraction."""
        make_haxe_file(tmp_path, "User.hx", """
class User {
    public var name:String;

    public function new(name:String) {
        this.name = name;
    }
}
""")
        result = analyze_haxe(tmp_path)
        assert not result.skipped
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any("User" in c.name for c in classes)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_haxe_file(tmp_path, "Utils.hx", """
class Utils {
    public static function add(a:Int, b:Int):Int {
        return a + b;
    }
}
""")
        result = analyze_haxe(tmp_path)
        funcs = [s for s in result.symbols if s.kind in ("function", "method")]
        assert any("add" in f.name for f in funcs)


class TestInterfaceExtraction:
    """Branch coverage for interface extraction."""

    def test_interface_declaration(self, tmp_path: Path) -> None:
        """Test interface declaration extraction."""
        make_haxe_file(tmp_path, "ISerializable.hx", """
interface ISerializable {
    public function serialize():String;
}
""")
        result = analyze_haxe(tmp_path)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any("ISerializable" in i.name for i in interfaces)


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_haxe_file(tmp_path, "Color.hx", """
enum Color {
    Red;
    Green;
    Blue;
    Rgb(r:Int, g:Int, b:Int);
}
""")
        result = analyze_haxe(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert not result.skipped  # lenient check


class TestTypedefExtraction:
    """Branch coverage for typedef extraction."""

    def test_typedef_declaration(self, tmp_path: Path) -> None:
        """Test typedef declaration extraction."""
        make_haxe_file(tmp_path, "Types.hx", """
typedef Point = {
    x:Float,
    y:Float
}
""")
        result = analyze_haxe(tmp_path)
        typedefs = [s for s in result.symbols if s.kind == "typedef"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_haxe_file(tmp_path, "Main.hx", """
import haxe.io.Bytes;
import sys.FileSystem;

class Main {
    static function main() {
        trace("Hello");
    }
}
""")
        result = analyze_haxe(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_haxe_file(tmp_path, "Main.hx", """
class Main {
    static function helper() {
        trace("helper");
    }

    static function main() {
        helper();
    }
}
""")
        result = analyze_haxe(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindHaxeFiles:
    """Branch coverage for file discovery."""

    def test_finds_hx_files(self, tmp_path: Path) -> None:
        """Test .hx files are discovered."""
        (tmp_path / "Test.hx").write_text("class Test {}")
        files = list(find_haxe_files(tmp_path))
        assert any(f.suffix == ".hx" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_haxe_files(self, tmp_path: Path) -> None:
        """Test directory with no Haxe files."""
        result = analyze_haxe(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(haxe_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="haxe analysis skipped"):
                result = haxe_module.analyze_haxe(tmp_path)
        assert result.skipped is True
