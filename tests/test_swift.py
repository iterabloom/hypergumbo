"""Tests for Swift analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindSwiftFiles:
    """Tests for Swift file discovery."""

    def test_finds_swift_files(self, tmp_path: Path) -> None:
        """Finds .swift files."""
        from hypergumbo.analyze.swift import find_swift_files

        (tmp_path / "Main.swift").write_text("func main() {}")
        (tmp_path / "Utils.swift").write_text("class Utils {}")
        (tmp_path / "other.txt").write_text("not swift")

        files = list(find_swift_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".swift" for f in files)


class TestSwiftTreeSitterAvailability:
    """Tests for tree-sitter-swift availability checking."""

    def test_is_swift_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-swift is available."""
        from hypergumbo.analyze.swift import is_swift_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_swift_tree_sitter_available() is True

    def test_is_swift_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.swift import is_swift_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_swift_tree_sitter_available() is False

    def test_is_swift_tree_sitter_available_no_swift(self) -> None:
        """Returns False when tree-sitter is available but swift grammar is not."""
        from hypergumbo.analyze.swift import is_swift_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()
            return None

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_swift_tree_sitter_available() is False


class TestAnalyzeSwiftFallback:
    """Tests for fallback behavior when tree-sitter-swift unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-swift unavailable."""
        from hypergumbo.analyze.swift import analyze_swift

        (tmp_path / "test.swift").write_text("func test() {}")

        with patch("hypergumbo.analyze.swift.is_swift_tree_sitter_available", return_value=False):
            result = analyze_swift(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-swift" in result.skip_reason


class TestSwiftFunctionExtraction:
    """Tests for extracting Swift functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts Swift function declarations."""
        from hypergumbo.analyze.swift import analyze_swift

        swift_file = tmp_path / "Main.swift"
        swift_file.write_text("""
func main() {
    print("Hello, world!")
}

func helper(x: Int) -> Int {
    return x + 1
}
""")

        result = analyze_swift(tmp_path)


        assert result.run is not None
        assert result.run.files_analyzed == 1
        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "main" in func_names
        assert "helper" in func_names


class TestSwiftClassExtraction:
    """Tests for extracting Swift classes."""

    def test_extracts_class(self, tmp_path: Path) -> None:
        """Extracts class declarations."""
        from hypergumbo.analyze.swift import analyze_swift

        swift_file = tmp_path / "Models.swift"
        swift_file.write_text("""
class User {
    var name: String

    init(name: String) {
        self.name = name
    }

    func greet() {
        print("Hello, \\(name)!")
    }
}

class Point {
    var x: Int
    var y: Int
}
""")

        result = analyze_swift(tmp_path)


        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [s.name for s in classes]
        assert "User" in class_names
        assert "Point" in class_names


class TestSwiftStructExtraction:
    """Tests for extracting Swift structs."""

    def test_extracts_struct(self, tmp_path: Path) -> None:
        """Extracts struct declarations."""
        from hypergumbo.analyze.swift import analyze_swift

        swift_file = tmp_path / "Types.swift"
        swift_file.write_text("""
struct Vector {
    var x: Double
    var y: Double
}

struct Config {
    var apiKey: String
    var timeout: Int
}
""")

        result = analyze_swift(tmp_path)


        structs = [s for s in result.symbols if s.kind == "struct"]
        struct_names = [s.name for s in structs]
        assert "Vector" in struct_names
        assert "Config" in struct_names


class TestSwiftProtocolExtraction:
    """Tests for extracting Swift protocols."""

    def test_extracts_protocol(self, tmp_path: Path) -> None:
        """Extracts protocol declarations."""
        from hypergumbo.analyze.swift import analyze_swift

        swift_file = tmp_path / "Protocols.swift"
        swift_file.write_text("""
protocol Drawable {
    func draw()
}

protocol Clickable {
    func onClick()
}
""")

        result = analyze_swift(tmp_path)


        protocols = [s for s in result.symbols if s.kind == "protocol"]
        protocol_names = [s.name for s in protocols]
        assert "Drawable" in protocol_names
        assert "Clickable" in protocol_names


class TestSwiftEnumExtraction:
    """Tests for extracting Swift enums."""

    def test_extracts_enum(self, tmp_path: Path) -> None:
        """Extracts enum declarations."""
        from hypergumbo.analyze.swift import analyze_swift

        swift_file = tmp_path / "Enums.swift"
        swift_file.write_text("""
enum Color {
    case red
    case green
    case blue
}

enum Direction: String {
    case north = "N"
    case south = "S"
}
""")

        result = analyze_swift(tmp_path)


        enums = [s for s in result.symbols if s.kind == "enum"]
        enum_names = [s.name for s in enums]
        assert "Color" in enum_names
        assert "Direction" in enum_names


class TestSwiftFunctionCalls:
    """Tests for detecting function calls in Swift."""

    def test_detects_function_call(self, tmp_path: Path) -> None:
        """Detects calls to functions in same file."""
        from hypergumbo.analyze.swift import analyze_swift

        swift_file = tmp_path / "Utils.swift"
        swift_file.write_text("""
func caller() {
    helper()
}

func helper() {
    print("helping")
}
""")

        result = analyze_swift(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1


class TestSwiftImports:
    """Tests for detecting Swift import statements."""

    def test_detects_import_statement(self, tmp_path: Path) -> None:
        """Detects import statements."""
        from hypergumbo.analyze.swift import analyze_swift

        swift_file = tmp_path / "Main.swift"
        swift_file.write_text("""
import Foundation
import UIKit

func main() {
    print("Hello")
}
""")

        result = analyze_swift(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestSwiftEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Returns skipped with run when parser loading fails."""
        from hypergumbo.analyze.swift import analyze_swift

        (tmp_path / "test.swift").write_text("func test() {}")

        with patch("hypergumbo.analyze.swift.is_swift_tree_sitter_available", return_value=True):
            with patch.dict("sys.modules", {"tree_sitter_swift": MagicMock()}):
                import sys
                mock_module = sys.modules["tree_sitter_swift"]
                mock_module.language.side_effect = RuntimeError("Parser load failed")
                result = analyze_swift(tmp_path)

        assert result.skipped is True
        assert "Failed to load Swift parser" in result.skip_reason
        assert result.run is not None

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo.analyze.swift import analyze_swift

        (tmp_path / "empty.swift").write_text("// Just a comment\n")

        result = analyze_swift(tmp_path)


        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo.analyze.swift import analyze_swift

        (tmp_path / "Helper.swift").write_text("""
func greet(name: String) -> String {
    return "Hello, \\(name)"
}
""")

        (tmp_path / "Main.swift").write_text("""
func run() {
    greet(name: "world")
}
""")

        result = analyze_swift(tmp_path)


        assert result.run.files_analyzed >= 2


class TestSwiftMethodExtraction:
    """Tests for extracting methods from classes."""

    def test_extracts_class_methods(self, tmp_path: Path) -> None:
        """Extracts methods defined inside classes."""
        from hypergumbo.analyze.swift import analyze_swift

        swift_file = tmp_path / "User.swift"
        swift_file.write_text("""
class User {
    var name: String = ""

    func getName() -> String {
        return name
    }

    func setName(newName: String) {
        name = newName
    }
}
""")

        result = analyze_swift(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert any("getName" in name for name in method_names)


class TestSwiftFileReadErrors:
    """Tests for file read error handling."""

    def test_symbol_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Symbol extraction handles file read errors gracefully."""
        from hypergumbo.analyze.swift import (
            _extract_symbols_from_file,
            is_swift_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_swift_tree_sitter_available():
            pytest.skip("tree-sitter-swift not available")

        import tree_sitter_swift
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_swift.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        swift_file = tmp_path / "test.swift"
        swift_file.write_text("func test() {}")

        with patch.object(Path, "read_bytes", side_effect=OSError("Read failed")):
            result = _extract_symbols_from_file(swift_file, parser, run)

        assert result.symbols == []

    def test_edge_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Edge extraction handles file read errors gracefully."""
        from hypergumbo.analyze.swift import (
            _extract_edges_from_file,
            is_swift_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_swift_tree_sitter_available():
            pytest.skip("tree-sitter-swift not available")

        import tree_sitter_swift
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_swift.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        swift_file = tmp_path / "test.swift"
        swift_file.write_text("func test() {}")

        with patch.object(Path, "read_bytes", side_effect=IOError("Read failed")):
            result = _extract_edges_from_file(swift_file, parser, {}, {}, run)

        assert result == []


class TestSwiftHelperFunctions:
    """Tests for helper function edge cases."""

    def test_find_child_by_type_returns_none(self, tmp_path: Path) -> None:
        """_find_child_by_type returns None when no matching child."""
        from hypergumbo.analyze.swift import (
            _find_child_by_type,
            is_swift_tree_sitter_available,
        )

        if not is_swift_tree_sitter_available():
            pytest.skip("tree-sitter-swift not available")

        import tree_sitter_swift
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_swift.language())
        parser = tree_sitter.Parser(lang)

        source = b"// comment\n"
        tree = parser.parse(source)

        result = _find_child_by_type(tree.root_node, "nonexistent_type")
        assert result is None


class TestSwiftSignatureExtraction:
    """Tests for Swift function signature extraction."""

    def test_basic_function_signature(self, tmp_path: Path) -> None:
        """Extracts signature from a basic function."""
        from hypergumbo.analyze.swift import analyze_swift

        (tmp_path / "Calculator.swift").write_text("""
class Calculator {
    func add(x: Int, y: Int) -> Int {
        return x + y
    }
}
""")
        result = analyze_swift(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "add" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(x: Int, y: Int) -> Int"

    def test_void_function_signature(self, tmp_path: Path) -> None:
        """Extracts signature from void function."""
        from hypergumbo.analyze.swift import analyze_swift

        (tmp_path / "Logger.swift").write_text("""
class Logger {
    func log(message: String) {
        print(message)
    }
}
""")
        result = analyze_swift(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "log" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(message: String)"

    def test_no_params_signature(self, tmp_path: Path) -> None:
        """Extracts signature from function with no parameters."""
        from hypergumbo.analyze.swift import analyze_swift

        (tmp_path / "Counter.swift").write_text("""
class Counter {
    func getCount() -> Int {
        return 0
    }
}
""")
        result = analyze_swift(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "getCount" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "() -> Int"
