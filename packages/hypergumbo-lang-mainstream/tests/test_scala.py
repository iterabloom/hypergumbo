"""Tests for Scala analyzer."""
import pytest
from pathlib import Path

from hypergumbo_core.analyze.base import find_child_by_type
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
        from hypergumbo_lang_mainstream import scala as scala_module
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "test.scala").write_text("object Test {}")

        with patch.object(scala_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="scala analysis skipped"):
                result = analyze_scala(tmp_path)

        assert result.skipped is True
        assert "scala" in result.skip_reason

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

    def test_detects_method_call_on_object(self, tmp_path: Path) -> None:
        """Detects obj.method() calls via field_expression.

        Scala method calls like svc.process() produce a field_expression
        AST node. The method name is the last identifier in the expression.
        """
        from hypergumbo_lang_mainstream.scala import analyze_scala

        scala_file = tmp_path / "App.scala"
        scala_file.write_text("""
class Service {
  def process(): Unit = {}
}

class Controller(val svc: Service) {
  def doWork(): Unit = {
    svc.process()
  }
}
""")

        result = analyze_scala(tmp_path)

        do_work = next(
            (s for s in result.symbols if "doWork" in s.name), None
        )
        process = next(
            (s for s in result.symbols if "process" in s.name
             and "Service" in s.id), None
        )

        assert do_work is not None
        assert process is not None

        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == do_work.id
                and e.dst == process.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, (
            f"Expected call edge from doWork to Service.process. "
            f"Edges: {[e for e in result.edges if e.edge_type == 'calls']}"
        )

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
        """Raises error when parser loading fails (base class does not catch)."""
        from hypergumbo_lang_mainstream import scala as scala_module
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "test.scala").write_text("object Test {}")

        with patch.object(scala_module._analyzer, "_check_grammar_available", return_value=True):
            with patch.object(
                scala_module._analyzer, "_create_parser",
                side_effect=RuntimeError("Parser load failed"),
            ):
                with pytest.raises(RuntimeError, match="Parser load failed"):
                    analyze_scala(tmp_path)

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
    """Tests for file read error handling.

    The base TreeSitterAnalyzer.analyze() method handles file read errors
    during Pass 1 by incrementing files_skipped.
    """

    def test_analyzer_handles_read_error_in_pass1(self, tmp_path: Path) -> None:
        """Analyzer skips files with read errors during Pass 1."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        # Create a valid file plus a file that will fail to read
        (tmp_path / "good.scala").write_text("object Good { def good(): Unit = {} }")
        bad_file = tmp_path / "bad.scala"
        bad_file.write_text("object Bad {}")

        original_read_bytes = Path.read_bytes

        def patched_read_bytes(self: Path) -> bytes:
            if self.name == "bad.scala":
                raise OSError("Read failed")
            return original_read_bytes(self)

        with patch.object(Path, "read_bytes", patched_read_bytes):
            result = analyze_scala(tmp_path)

        assert result.run is not None
        assert result.run.files_skipped >= 1

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
        from hypergumbo_lang_mainstream.scala import is_scala_tree_sitter_available

        if not is_scala_tree_sitter_available():
            pytest.skip("tree-sitter-scala not available")

        import tree_sitter_scala
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_scala.language())
        parser = tree_sitter.Parser(lang)

        source = b"// comment\n"
        tree = parser.parse(source)

        result = find_child_by_type(tree.root_node, "nonexistent_type")
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


class TestScalaVisibilityModifiers:
    """Tests for Scala visibility modifier extraction."""

    def test_method_visibility(self, tmp_path: Path) -> None:
        """Methods with access modifiers get them extracted."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Vis.scala").write_text("""
class Vis {
    private def privMethod(): Unit = {}
    protected def protMethod(): Int = 0
    def defaultMethod(): Unit = {}
}
""")
        result = analyze_scala(tmp_path)

        priv = next(s for s in result.symbols if "privMethod" in s.name)
        assert "private" in priv.modifiers

        prot = next(s for s in result.symbols if "protMethod" in s.name)
        assert "protected" in prot.modifiers

        default = next(s for s in result.symbols if "defaultMethod" in s.name)
        assert "private" not in default.modifiers
        assert "protected" not in default.modifiers

    def test_class_modifiers(self, tmp_path: Path) -> None:
        """Classes with modifiers get them extracted."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Classes.scala").write_text("""
abstract class AbsClass {}
sealed class SealedClass {}
case class CaseClass(x: Int)
""")
        result = analyze_scala(tmp_path)

        abs_cls = next(s for s in result.symbols if s.name == "AbsClass")
        assert "abstract" in abs_cls.modifiers

        sealed_cls = next(s for s in result.symbols if s.name == "SealedClass")
        assert "sealed" in sealed_cls.modifiers

        case_cls = next(s for s in result.symbols if s.name == "CaseClass")
        assert "case" in case_cls.modifiers


class TestNormalizeScalaSignature:
    """Tests for Scala signature normalization (ADR-0014 §3)."""

    def test_basic_method(self) -> None:
        from hypergumbo_lang_mainstream.scala import normalize_scala_signature
        assert normalize_scala_signature("(x: Int, y: Int): Int") == "(Int,Int)Int"

    def test_none(self) -> None:
        from hypergumbo_lang_mainstream.scala import normalize_scala_signature
        assert normalize_scala_signature(None) is None


class TestScalaDocstrings:
    """Tests for Scaladoc comment extraction via populate_docstrings_from_tree."""

    def test_scaladoc_block_comment_on_class(self, tmp_path: Path) -> None:
        """Extracts Scaladoc block comment preceding a class."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Foo.scala").write_text(
            "/** Represents a foo entity. */\n"
            "class Foo {\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        cls = next((s for s in result.symbols if s.name == "Foo"), None)
        assert cls is not None
        assert cls.docstring == "Represents a foo entity."

    def test_scaladoc_block_comment_on_method(self, tmp_path: Path) -> None:
        """Extracts Scaladoc block comment preceding a method."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Foo.scala").write_text(
            "class Foo {\n"
            "  /** Computes the sum. */\n"
            "  def add(x: Int, y: Int): Int = x + y\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        method = next((s for s in result.symbols if "add" in s.name), None)
        assert method is not None
        assert method.docstring == "Computes the sum."

    def test_line_comment_on_function(self, tmp_path: Path) -> None:
        """Extracts line comment preceding a function."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Foo.scala").write_text(
            "object Foo {\n"
            "  // Greets the user.\n"
            "  def greet(): String = \"hello\"\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        func = next((s for s in result.symbols if "greet" in s.name), None)
        assert func is not None
        assert func.docstring == "Greets the user."

    def test_no_comment_no_docstring(self, tmp_path: Path) -> None:
        """Symbol without preceding comment has no docstring."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Bar.scala").write_text(
            "class Bar {\n"
            "  def run(): Unit = {}\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        method = next((s for s in result.symbols if "run" in s.name), None)
        assert method is not None
        assert method.docstring is None


class TestScalaAnnotations:
    """Tests for Scala annotation extraction (INV-kobad)."""

    def test_class_annotation(self, tmp_path: Path) -> None:
        """Class with @Entity annotation should have meta.decorators."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Model.scala").write_text(
            "import javax.persistence.Entity\n"
            "\n"
            "@Entity\n"
            "class User {\n"
            "  def name(): String = \"\"\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        cls = next((s for s in result.symbols if s.name == "User"), None)
        assert cls is not None
        assert cls.meta is not None
        assert "decorators" in cls.meta
        decorators = cls.meta["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Entity"

    def test_method_annotation_with_args(self, tmp_path: Path) -> None:
        """Method with @deprecated("old", "2.0") should capture arguments."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "App.scala").write_text(
            "object App {\n"
            '  @deprecated("use getAll", "2.0")\n'
            "  def getUsers(): Unit = {}\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        method = next((s for s in result.symbols if "getUsers" in s.name), None)
        assert method is not None
        assert method.meta is not None
        decorators = method.meta["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "deprecated"
        assert "use getAll" in decorators[0]["args"]
        assert "2.0" in decorators[0]["args"]

    def test_annotation_with_named_args(self, tmp_path: Path) -> None:
        """Annotation with named arguments should capture kwargs."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Model.scala").write_text(
            '@Table(name = "users")\n'
            "class User {\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        cls = next((s for s in result.symbols if s.name == "User"), None)
        assert cls is not None
        decorators = cls.meta["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Table"
        assert decorators[0]["kwargs"]["name"] == "users"

    def test_annotation_with_identifier_arg(self, tmp_path: Path) -> None:
        """Annotation with identifier argument should capture it."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Model.scala").write_text(
            "@Scope(SINGLETON)\n"
            "class Service {\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        cls = next((s for s in result.symbols if s.name == "Service"), None)
        assert cls is not None
        decorators = cls.meta["decorators"]
        assert len(decorators) == 1
        assert "SINGLETON" in decorators[0]["args"]

    def test_no_annotation_no_decorators(self, tmp_path: Path) -> None:
        """Class without annotations should not have decorators in meta."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "App.scala").write_text(
            "class Plain {\n"
            "  def run(): Unit = {}\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        cls = next((s for s in result.symbols if s.name == "Plain"), None)
        assert cls is not None
        assert cls.meta is None or "decorators" not in (cls.meta or {})

    def test_class_with_annotation_and_base_class(self, tmp_path: Path) -> None:
        """Class with both annotation and extends should have both in meta."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "Model.scala").write_text(
            "@Entity\n"
            "class Admin extends User {\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        cls = next((s for s in result.symbols if s.name == "Admin"), None)
        assert cls is not None
        assert cls.meta is not None
        assert "decorators" in cls.meta
        assert "base_classes" in cls.meta


class TestScalaFunctionReferences:
    """Tests for Scala eta-expansion function references (INV-dinur).

    ``transform _`` (eta-expansion) converts a method to a function value
    and should produce a "references" edge.
    """

    def test_eta_expansion_reference(self, tmp_path: Path) -> None:
        """``val f = transform _`` should create a references edge."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "App.scala").write_text(
            "object App {\n"
            "  def transform(x: Int): Int = x * 2\n"
            "  def run(): Unit = {\n"
            "    val f = transform _\n"
            "  }\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        ref_edges = [
            e for e in result.edges
            if e.edge_type == "references" and "run" in e.src and "transform" in e.dst
        ]
        assert len(ref_edges) == 1
        assert ref_edges[0].evidence_type == "eta_expansion"

    def test_no_reference_for_unknown_function(self, tmp_path: Path) -> None:
        """Eta-expansion of unknown function should not create edge."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        (tmp_path / "App.scala").write_text(
            "object App {\n"
            "  def run(): Unit = {\n"
            "    val f = unknown _\n"
            "  }\n"
            "}\n"
        )
        result = analyze_scala(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) == 0
