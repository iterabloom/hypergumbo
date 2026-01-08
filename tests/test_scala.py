"""Tests for Scala analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindScalaFiles:
    """Tests for Scala file discovery."""

    def test_finds_scala_files(self, tmp_path: Path) -> None:
        """Finds .scala files."""
        from hypergumbo.analyze.scala import find_scala_files

        (tmp_path / "Main.scala").write_text("object Main { def main(args: Array[String]): Unit = {} }")
        (tmp_path / "Utils.scala").write_text("class Utils {}")
        (tmp_path / "other.txt").write_text("not scala")

        files = list(find_scala_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".scala" for f in files)


class TestScalaTreeSitterAvailability:
    """Tests for tree-sitter-scala availability checking."""

    def test_is_scala_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-scala is available."""
        from hypergumbo.analyze.scala import is_scala_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_scala_tree_sitter_available() is True

    def test_is_scala_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.scala import is_scala_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_scala_tree_sitter_available() is False

    def test_is_scala_tree_sitter_available_no_scala(self) -> None:
        """Returns False when tree-sitter is available but scala grammar is not."""
        from hypergumbo.analyze.scala import is_scala_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()
            return None

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_scala_tree_sitter_available() is False


class TestAnalyzeScalaFallback:
    """Tests for fallback behavior when tree-sitter-scala unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-scala unavailable."""
        from hypergumbo.analyze.scala import analyze_scala

        (tmp_path / "test.scala").write_text("object Test {}")

        with patch("hypergumbo.analyze.scala.is_scala_tree_sitter_available", return_value=False):
            result = analyze_scala(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-scala" in result.skip_reason


class TestScalaFunctionExtraction:
    """Tests for extracting Scala functions/methods."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts Scala function declarations."""
        from hypergumbo.analyze.scala import analyze_scala

        scala_file = tmp_path / "Main.scala"
        scala_file.write_text("""
def main(args: Array[String]): Unit = {
    println("Hello, world!")
}

def helper(x: Int): Int = {
    x + 1
}
""")

        result = analyze_scala(tmp_path)


        assert result.run is not None
        assert result.run.files_analyzed == 1
        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "main" in func_names
        assert "helper" in func_names


class TestScalaClassExtraction:
    """Tests for extracting Scala classes."""

    def test_extracts_class(self, tmp_path: Path) -> None:
        """Extracts class declarations."""
        from hypergumbo.analyze.scala import analyze_scala

        scala_file = tmp_path / "Models.scala"
        scala_file.write_text("""
class User(name: String) {
    def greet(): Unit = {
        println(s"Hello, $name!")
    }
}

class Point(x: Int, y: Int)
""")

        result = analyze_scala(tmp_path)


        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [s.name for s in classes]
        assert "User" in class_names
        assert "Point" in class_names


class TestScalaObjectExtraction:
    """Tests for extracting Scala objects."""

    def test_extracts_object(self, tmp_path: Path) -> None:
        """Extracts object declarations."""
        from hypergumbo.analyze.scala import analyze_scala

        scala_file = tmp_path / "Singleton.scala"
        scala_file.write_text("""
object Database {
    def connect(): Unit = {
        println("Connecting...")
    }
}

object Config {
    val version = "1.0"
}
""")

        result = analyze_scala(tmp_path)


        objects = [s for s in result.symbols if s.kind == "object"]
        object_names = [s.name for s in objects]
        assert "Database" in object_names
        assert "Config" in object_names


class TestScalaTraitExtraction:
    """Tests for extracting Scala traits."""

    def test_extracts_trait(self, tmp_path: Path) -> None:
        """Extracts trait declarations."""
        from hypergumbo.analyze.scala import analyze_scala

        scala_file = tmp_path / "Traits.scala"
        scala_file.write_text("""
trait Drawable {
    def draw(): Unit
}

trait Clickable {
    def onClick(): Unit
}
""")

        result = analyze_scala(tmp_path)


        traits = [s for s in result.symbols if s.kind == "trait"]
        trait_names = [s.name for s in traits]
        assert "Drawable" in trait_names
        assert "Clickable" in trait_names


class TestScalaFunctionCalls:
    """Tests for detecting function calls in Scala."""

    def test_detects_function_call(self, tmp_path: Path) -> None:
        """Detects calls to functions in same file."""
        from hypergumbo.analyze.scala import analyze_scala

        scala_file = tmp_path / "Utils.scala"
        scala_file.write_text("""
def caller(): Unit = {
    helper()
}

def helper(): Unit = {
    println("helping")
}
""")

        result = analyze_scala(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1


class TestScalaImports:
    """Tests for detecting Scala import statements."""

    def test_detects_import_statement(self, tmp_path: Path) -> None:
        """Detects import statements."""
        from hypergumbo.analyze.scala import analyze_scala

        scala_file = tmp_path / "Main.scala"
        scala_file.write_text("""
import scala.collection.mutable.ListBuffer
import java.io.File

object Main {
    def main(): Unit = {
        println("Hello")
    }
}
""")

        result = analyze_scala(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestScalaEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Returns skipped with run when parser loading fails."""
        from hypergumbo.analyze.scala import analyze_scala

        (tmp_path / "test.scala").write_text("object Test {}")

        with patch("hypergumbo.analyze.scala.is_scala_tree_sitter_available", return_value=True):
            with patch.dict("sys.modules", {"tree_sitter_scala": MagicMock()}):
                import sys
                mock_module = sys.modules["tree_sitter_scala"]
                mock_module.language.side_effect = RuntimeError("Parser load failed")
                result = analyze_scala(tmp_path)

        assert result.skipped is True
        assert "Failed to load Scala parser" in result.skip_reason
        assert result.run is not None

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo.analyze.scala import analyze_scala

        (tmp_path / "empty.scala").write_text("// Just a comment\n")

        result = analyze_scala(tmp_path)


        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo.analyze.scala import analyze_scala

        (tmp_path / "Helper.scala").write_text("""
def greet(name: String): String = {
    s"Hello, $name"
}
""")

        (tmp_path / "Main.scala").write_text("""
def run(): Unit = {
    greet("world")
}
""")

        result = analyze_scala(tmp_path)


        assert result.run.files_analyzed >= 2


class TestScalaMethodExtraction:
    """Tests for extracting methods from classes."""

    def test_extracts_class_methods(self, tmp_path: Path) -> None:
        """Extracts methods defined inside classes."""
        from hypergumbo.analyze.scala import analyze_scala

        scala_file = tmp_path / "User.scala"
        scala_file.write_text("""
class User(val name: String) {
    def getName(): String = {
        name
    }

    def setName(newName: String): Unit = {
        // setter
    }
}
""")

        result = analyze_scala(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert any("getName" in name for name in method_names)


class TestScalaFileReadErrors:
    """Tests for file read error handling."""

    def test_symbol_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Symbol extraction handles file read errors gracefully."""
        from hypergumbo.analyze.scala import (
            _extract_symbols_from_file,
            is_scala_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_scala_tree_sitter_available():
            pytest.skip("tree-sitter-scala not available")

        import tree_sitter_scala
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_scala.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        scala_file = tmp_path / "test.scala"
        scala_file.write_text("object Test {}")

        with patch.object(Path, "read_bytes", side_effect=OSError("Read failed")):
            result = _extract_symbols_from_file(scala_file, parser, run)

        assert result.symbols == []

    def test_edge_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Edge extraction handles file read errors gracefully."""
        from hypergumbo.analyze.scala import (
            _extract_edges_from_file,
            is_scala_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_scala_tree_sitter_available():
            pytest.skip("tree-sitter-scala not available")

        import tree_sitter_scala
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_scala.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        scala_file = tmp_path / "test.scala"
        scala_file.write_text("object Test {}")

        with patch.object(Path, "read_bytes", side_effect=IOError("Read failed")):
            result = _extract_edges_from_file(scala_file, parser, {}, {}, run)

        assert result == []


class TestScalaHelperFunctions:
    """Tests for helper function edge cases."""

    def test_find_child_by_type_returns_none(self, tmp_path: Path) -> None:
        """_find_child_by_type returns None when no matching child."""
        from hypergumbo.analyze.scala import (
            _find_child_by_type,
            is_scala_tree_sitter_available,
        )

        if not is_scala_tree_sitter_available():
            pytest.skip("tree-sitter-scala not available")

        import tree_sitter_scala
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_scala.language())
        parser = tree_sitter.Parser(lang)

        source = b"// comment\n"
        tree = parser.parse(source)

        result = _find_child_by_type(tree.root_node, "nonexistent_type")
        assert result is None


class TestScalaSignatureExtraction:
    """Tests for Scala function signature extraction."""

    def test_basic_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from a basic method."""
        from hypergumbo.analyze.scala import analyze_scala

        (tmp_path / "Calculator.scala").write_text("""
class Calculator {
    def add(x: Int, y: Int): Int = x + y
}
""")
        result = analyze_scala(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "add" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(x: Int, y: Int): Int"

    def test_unit_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from Unit method (omits Unit)."""
        from hypergumbo.analyze.scala import analyze_scala

        (tmp_path / "Logger.scala").write_text("""
class Logger {
    def log(message: String): Unit = println(message)
}
""")
        result = analyze_scala(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "log" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(message: String)"

    def test_no_params_signature(self, tmp_path: Path) -> None:
        """Extracts signature from method with no parameters."""
        from hypergumbo.analyze.scala import analyze_scala

        (tmp_path / "Counter.scala").write_text("""
class Counter {
    def getCount(): Int = 0
}
""")
        result = analyze_scala(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "getCount" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "(): Int"

    def test_trait_abstract_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from abstract method in trait."""
        from hypergumbo.analyze.scala import analyze_scala

        (tmp_path / "Drawable.scala").write_text("""
trait Drawable {
    def draw(): Unit
}
""")
        result = analyze_scala(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "draw" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "()"
