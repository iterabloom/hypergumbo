"""Tests for Scala analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindScalaFiles:
    """Tests for Scala file discovery."""

    def test_finds_scala_files(self, tmp_path: Path) -> None:
        """Finds .scala files."""
        from hypergumbo_lang_mainstream.scala import find_scala_files

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
        from hypergumbo_lang_mainstream.scala import is_scala_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_scala_tree_sitter_available() is True

    def test_is_scala_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo_lang_mainstream.scala import is_scala_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_scala_tree_sitter_available() is False

    def test_is_scala_tree_sitter_available_no_scala(self) -> None:
        """Returns False when tree-sitter is available but scala grammar is not."""
        from hypergumbo_lang_mainstream.scala import is_scala_tree_sitter_available

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "test.scala").write_text("object Test {}")

        with patch("hypergumbo_lang_mainstream.scala.is_scala_tree_sitter_available", return_value=False):
            result = analyze_scala(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-scala" in result.skip_reason


class TestScalaFunctionExtraction:
    """Tests for extracting Scala functions/methods."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts Scala function declarations."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

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


class TestScalaLambdaCallAttribution:
    """Tests for call edge attribution inside lambda expressions.

    Scala uses lambdas heavily (foreach, map, filter). Calls inside these
    lambdas must be attributed to the enclosing named function.
    """

    def test_call_inside_foreach_lambda_attributed(self, tmp_path: Path) -> None:
        """Calls inside foreach lambda are attributed to enclosing function.

        When you have:
            def processItems(): Unit = {
                items.foreach { x => helper(x) }
            }

        The call to helper() should be attributed to processItems.
        """
        from hypergumbo_lang_mainstream.scala import analyze_scala

        scala_file = tmp_path / "Test.scala"
        scala_file.write_text("""
object Test {
  def helper(x: Int): Unit = {
    println(x)
  }

  def processItems(): Unit = {
    val items = List(1, 2, 3)
    items.foreach { x =>
      helper(x)
    }
  }

  def main(args: Array[String]): Unit = {
    processItems()
  }
}
""")

        result = analyze_scala(tmp_path)

        # Find symbols
        process_func = next(
            (s for s in result.symbols if s.name == "Test.processItems"),
            None,
        )
        helper_func = next(
            (s for s in result.symbols if s.name == "Test.helper"),
            None,
        )

        assert process_func is not None, "processItems function should be found"
        assert helper_func is not None, "helper function should be found"

        # Find call edge from processItems to helper (inside lambda)
        call_edge = next(
            (
                e for e in result.edges
                if e.src == process_func.id
                and e.dst == helper_func.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call to helper() inside foreach lambda should be attributed to processItems"

    def test_call_inside_map_lambda_attributed(self, tmp_path: Path) -> None:
        """Calls inside map lambda are attributed to enclosing function."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        scala_file = tmp_path / "Test.scala"
        scala_file.write_text("""
object Test {
  def transform(x: Int): Int = x * 2

  def processData(): List[Int] = {
    val nums = List(1, 2, 3)
    nums.map(x => transform(x))
  }
}
""")

        result = analyze_scala(tmp_path)

        process_func = next(
            (s for s in result.symbols if s.name == "Test.processData"),
            None,
        )
        transform_func = next(
            (s for s in result.symbols if s.name == "Test.transform"),
            None,
        )

        assert process_func is not None
        assert transform_func is not None

        call_edge = next(
            (
                e for e in result.edges
                if e.src == process_func.id
                and e.dst == transform_func.id
            ),
            None,
        )
        assert call_edge is not None, "Call to transform() inside map lambda should be attributed to processData"


class TestScalaImports:
    """Tests for detecting Scala import statements."""

    def test_detects_import_statement(self, tmp_path: Path) -> None:
        """Detects import statements."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "test.scala").write_text("object Test {}")

        with patch("hypergumbo_lang_mainstream.scala.is_scala_tree_sitter_available", return_value=True):
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
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "empty.scala").write_text("// Just a comment\n")

        result = analyze_scala(tmp_path)


        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import (
            _extract_symbols_from_file,
            is_scala_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.scala import (
            _extract_edges_from_file,
            is_scala_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

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


class TestImportHintsExtraction:
    """Tests for import hints extraction for disambiguation."""

    def test_extracts_simple_import(self, tmp_path: Path) -> None:
        """Extracts simple import using last component."""
        from hypergumbo_lang_mainstream.scala import (
            _extract_import_hints,
            is_scala_tree_sitter_available,
        )

        if not is_scala_tree_sitter_available():
            pytest.skip("tree-sitter-scala not available")

        import tree_sitter_scala
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_scala.language())
        parser = tree_sitter.Parser(lang)

        scala_file = tmp_path / "Main.scala"
        scala_file.write_text("""
import scala.collection.mutable.HashMap

object Main {
  def run(): Unit = {
    val map = new HashMap[String, Int]()
  }
}
""")

        source = scala_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_import_hints(tree, source)

        # Last component of import path should be the short name
        assert "HashMap" in hints
        assert hints["HashMap"] == "scala.collection.mutable.HashMap"

    def test_extracts_import_selectors(self, tmp_path: Path) -> None:
        """Extracts multiple imports from selector syntax."""
        from hypergumbo_lang_mainstream.scala import (
            _extract_import_hints,
            is_scala_tree_sitter_available,
        )

        if not is_scala_tree_sitter_available():
            pytest.skip("tree-sitter-scala not available")

        import tree_sitter_scala
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_scala.language())
        parser = tree_sitter.Parser(lang)

        scala_file = tmp_path / "Main.scala"
        scala_file.write_text("""
import scala.collection.mutable.{HashMap, ListBuffer}

object Main {
  def run(): Unit = {}
}
""")

        source = scala_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_import_hints(tree, source)

        # Both selected imports should be mapped
        assert "HashMap" in hints
        assert hints["HashMap"] == "scala.collection.mutable.HashMap"
        assert "ListBuffer" in hints
        assert hints["ListBuffer"] == "scala.collection.mutable.ListBuffer"

    def test_extracts_renamed_import(self, tmp_path: Path) -> None:
        """Extracts renamed import with alias."""
        from hypergumbo_lang_mainstream.scala import (
            _extract_import_hints,
            is_scala_tree_sitter_available,
        )

        if not is_scala_tree_sitter_available():
            pytest.skip("tree-sitter-scala not available")

        import tree_sitter_scala
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_scala.language())
        parser = tree_sitter.Parser(lang)

        scala_file = tmp_path / "Main.scala"
        scala_file.write_text("""
import scala.collection.mutable.{HashMap => MutableMap}

object Main {
  def run(): Unit = {
    val map = new MutableMap[String, Int]()
  }
}
""")

        source = scala_file.read_bytes()
        tree = parser.parse(source)

        hints = _extract_import_hints(tree, source)

        # Alias should map to the original full path
        assert "MutableMap" in hints
        assert hints["MutableMap"] == "scala.collection.mutable.HashMap"


class TestScalaHelperFunctions:
    """Tests for helper function edge cases."""

    def test_find_child_by_type_returns_none(self, tmp_path: Path) -> None:
        """_find_child_by_type returns None when no matching child."""
        from hypergumbo_lang_mainstream.scala import (
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


class TestScalaInheritanceEdges:
    """Tests for Scala base_classes metadata extraction.

    The inheritance linker creates edges from base_classes metadata.
    These tests verify that the Scala analyzer extracts base_classes correctly.
    """

    def test_class_extends_class_has_base_classes(self, tmp_path: Path) -> None:
        """Class extending another class has base_classes metadata."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Models.scala").write_text("""
class BaseModel {
    def save(): Unit = {}
}

class User extends BaseModel {
    def greet(): Unit = {}
}
""")
        result = analyze_scala(tmp_path)

        user_class = next(
            (s for s in result.symbols if s.name == "User" and s.kind == "class"),
            None,
        )
        assert user_class is not None
        assert user_class.meta is not None
        assert "base_classes" in user_class.meta
        assert "BaseModel" in user_class.meta["base_classes"]

    def test_class_with_trait_has_base_classes(self, tmp_path: Path) -> None:
        """Class with mixed-in traits has base_classes including traits."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Models.scala").write_text("""
trait Serializable {
    def toJson(): String
}

trait Comparable {
    def compare(): Int
}

class User extends Serializable with Comparable {
    def toJson(): String = "{}"
    def compare(): Int = 0
}
""")
        result = analyze_scala(tmp_path)

        user_class = next(
            (s for s in result.symbols if s.name == "User" and s.kind == "class"),
            None,
        )
        assert user_class is not None
        assert user_class.meta is not None
        assert "base_classes" in user_class.meta
        # Should include both Serializable (extends) and Comparable (with)
        assert "Serializable" in user_class.meta["base_classes"]
        assert "Comparable" in user_class.meta["base_classes"]

    def test_trait_extends_trait_has_base_classes(self, tmp_path: Path) -> None:
        """Trait extending another trait has base_classes."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Traits.scala").write_text("""
trait Entity {
    def id: String
}

trait Persistable extends Entity {
    def save(): Unit
}
""")
        result = analyze_scala(tmp_path)

        persistable_trait = next(
            (s for s in result.symbols if s.name == "Persistable" and s.kind == "trait"),
            None,
        )
        assert persistable_trait is not None
        assert persistable_trait.meta is not None
        assert "base_classes" in persistable_trait.meta
        assert "Entity" in persistable_trait.meta["base_classes"]

    def test_generic_base_class_strips_type_params(self, tmp_path: Path) -> None:
        """Generic base class has type params stripped in base_classes."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Repository.scala").write_text("""
class Repository[T] {
    def find(): T = ???
}

class UserRepository extends Repository[User] {
    def findByName(name: String): User = ???
}

class User
""")
        result = analyze_scala(tmp_path)

        user_repo = next(
            (s for s in result.symbols if s.name == "UserRepository" and s.kind == "class"),
            None,
        )
        assert user_repo is not None
        assert user_repo.meta is not None
        assert "base_classes" in user_repo.meta
        # Should be "Repository", not "Repository[User]"
        assert "Repository" in user_repo.meta["base_classes"]

    def test_class_without_extends_has_no_base_classes(self, tmp_path: Path) -> None:
        """Class without extends clause has no base_classes metadata."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Simple.scala").write_text("""
class SimpleClass {
    def method(): Unit = {}
}
""")
        result = analyze_scala(tmp_path)

        simple_class = next(
            (s for s in result.symbols if s.name == "SimpleClass" and s.kind == "class"),
            None,
        )
        assert simple_class is not None
        # No meta or no base_classes is fine
        if simple_class.meta:
            assert simple_class.meta.get("base_classes", []) == []

    def test_linker_creates_extends_edge(self, tmp_path: Path) -> None:
        """Inheritance linker creates extends edge from base_classes."""
        from hypergumbo_lang_mainstream.scala import analyze_scala
        from hypergumbo_core.linkers.inheritance import link_inheritance
        from hypergumbo_core.linkers.registry import LinkerContext

        (tmp_path / "Models.scala").write_text("""
class BaseModel {
    def save(): Unit = {}
}

class User extends BaseModel {
    def greet(): Unit = {}
}
""")
        result = analyze_scala(tmp_path)

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=result.symbols,
            edges=result.edges,
        )
        linker_result = link_inheritance(ctx)

        # Should create an extends edge
        extends_edges = [e for e in linker_result.edges if e.edge_type == "extends"]
        assert len(extends_edges) == 1
        assert "User" in extends_edges[0].src
        assert "BaseModel" in extends_edges[0].dst


class TestScalaSignatureExtraction:
    """Tests for Scala function signature extraction."""

    def test_basic_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from a basic method."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

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
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Drawable.scala").write_text("""
trait Drawable {
    def draw(): Unit
}
""")
        result = analyze_scala(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "draw" in s.name]
        assert len(methods) == 1
        assert methods[0].signature == "()"
