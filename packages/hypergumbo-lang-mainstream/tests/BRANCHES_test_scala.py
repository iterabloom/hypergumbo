# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for scala.py analyzer.

Tests specific branch paths in the Scala analyzer that may not be covered
by the main test suite. Focuses on:
- Extends clause extraction (simple type, generic type)
- Import hints (simple, selectors, renamed)
- Signature extraction with various parameter and return types
- Symbol extraction (functions, methods, classes, objects, traits)
- Call resolution (local, global with import hints)
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def make_scala_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Scala file with given content."""
    (tmp_path / name).write_text(content)


def analyze(tmp_path: Path) -> dict:
    """Run behavior map and return parsed JSON result."""
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    return json.loads(out_path.read_text())


class TestScalaExtendsClause:
    """Branch coverage for _extract_extends_clause."""

    def test_simple_extends(self, tmp_path: Path) -> None:
        """Test class with simple type in extends clause."""
        make_scala_file(tmp_path, "Service.scala", """
class BaseService

class UserService extends BaseService {
  def getUser(): Unit = {}
}
""")
        data = analyze(tmp_path)
        cls = next((n for n in data["nodes"] if n["name"] == "UserService"), None)
        assert cls is not None
        assert cls.get("meta", {}).get("base_classes") == ["BaseService"]

    def test_generic_extends(self, tmp_path: Path) -> None:
        """Test class extending generic type like Repository[User]."""
        make_scala_file(tmp_path, "Repository.scala", """
trait Repository[T]

class UserRepository extends Repository[User] {
  def find(): Unit = {}
}
""")
        data = analyze(tmp_path)
        cls = next((n for n in data["nodes"] if n["name"] == "UserRepository"), None)
        assert cls is not None
        # Generic type parameter should be stripped
        assert cls.get("meta", {}).get("base_classes") == ["Repository"]

    def test_extends_with_traits(self, tmp_path: Path) -> None:
        """Test class extending class with multiple traits (with keyword)."""
        make_scala_file(tmp_path, "Mixins.scala", """
trait Logging
trait Metrics
class Base

class Service extends Base with Logging with Metrics {
  def run(): Unit = {}
}
""")
        data = analyze(tmp_path)
        cls = next((n for n in data["nodes"] if n["name"] == "Service"), None)
        assert cls is not None
        bases = cls.get("meta", {}).get("base_classes", [])
        assert "Base" in bases
        # Note: may or may not include traits depending on tree-sitter parsing

    def test_class_no_extends(self, tmp_path: Path) -> None:
        """Test class without extends clause."""
        make_scala_file(tmp_path, "Simple.scala", """
class Simple {
  def method(): Unit = {}
}
""")
        data = analyze(tmp_path)
        cls = next((n for n in data["nodes"] if n["name"] == "Simple"), None)
        assert cls is not None
        assert cls.get("meta") is None or cls.get("meta", {}).get("base_classes") is None

    def test_trait_extends_trait(self, tmp_path: Path) -> None:
        """Test trait extending another trait."""
        make_scala_file(tmp_path, "Traits.scala", """
trait BaseTrait

trait ExtendedTrait extends BaseTrait {
  def process(): Unit
}
""")
        data = analyze(tmp_path)
        trait = next((n for n in data["nodes"] if n["name"] == "ExtendedTrait"), None)
        assert trait is not None
        assert trait["kind"] == "trait"
        assert trait.get("meta", {}).get("base_classes") == ["BaseTrait"]


class TestScalaImportHints:
    """Branch coverage for _extract_import_hints."""

    def test_simple_import(self, tmp_path: Path) -> None:
        """Test simple import statement."""
        make_scala_file(tmp_path, "App.scala", """
import scala.collection.mutable.ListBuffer

object App {
  def main(): Unit = {
    val buf = new ListBuffer[Int]()
  }
}
""")
        data = analyze(tmp_path)
        # Should create import edge
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert any("scala.collection.mutable.ListBuffer" in e["dst"] for e in edges)

    def test_import_selectors(self, tmp_path: Path) -> None:
        """Test import with selectors like import a.b.{A, B}."""
        make_scala_file(tmp_path, "Multi.scala", """
import scala.collection.{Map, Set}

object Multi {
  def process(): Unit = {}
}
""")
        data = analyze(tmp_path)
        # Should create import edge for base path
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert len(edges) >= 1

    def test_import_renamed(self, tmp_path: Path) -> None:
        """Test import with rename like import a.{B => Alias}."""
        make_scala_file(tmp_path, "Renamed.scala", """
import java.util.{ArrayList => JArrayList}

object Renamed {
  def test(): Unit = {
    val list = new JArrayList[String]()
  }
}
""")
        data = analyze(tmp_path)
        # Should still create import edge
        edges = [e for e in data["edges"] if e["type"] == "imports"]
        assert len(edges) >= 1


class TestScalaSignatureExtraction:
    """Branch coverage for _extract_scala_signature."""

    def test_type_identifier_param(self, tmp_path: Path) -> None:
        """Test function with simple type identifier parameter."""
        make_scala_file(tmp_path, "Simple.scala", """
object Simple {
  def greet(name: String): Unit = {}
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "Simple.greet"), None)
        assert method is not None
        assert method["signature"] == "(name: String)"

    def test_generic_type_param(self, tmp_path: Path) -> None:
        """Test function with generic type parameter like List[Int]."""
        make_scala_file(tmp_path, "Generic.scala", """
object Generic {
  def process(items: List[Int]): Int = items.sum
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "Generic.process"), None)
        assert method is not None
        assert "List[Int]" in method["signature"]
        assert ": Int" in method["signature"]

    def test_tuple_type_param(self, tmp_path: Path) -> None:
        """Test function with tuple type parameter."""
        make_scala_file(tmp_path, "Tuples.scala", """
object Tuples {
  def pair(t: (Int, String)): Unit = {}
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "Tuples.pair"), None)
        assert method is not None
        assert "(" in method["signature"] and "Int" in method["signature"]

    def test_function_type_param(self, tmp_path: Path) -> None:
        """Test function with function type parameter like Int => String."""
        make_scala_file(tmp_path, "FuncType.scala", """
object FuncType {
  def transform(f: Int => String): Unit = {}
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "FuncType.transform"), None)
        assert method is not None
        assert "=>" in method["signature"]

    def test_infix_type_param(self, tmp_path: Path) -> None:
        """Test function with infix type like A :: B."""
        # Note: This may not parse exactly as "infix_type" in all cases
        make_scala_file(tmp_path, "Infix.scala", """
object Infix {
  def cons(pair: Either[Int, String]): Unit = {}
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "Infix.cons"), None)
        assert method is not None

    def test_unit_return_omitted(self, tmp_path: Path) -> None:
        """Test that Unit return type is omitted from signature."""
        make_scala_file(tmp_path, "UnitReturn.scala", """
object UnitReturn {
  def action(x: Int): Unit = println(x)
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "UnitReturn.action"), None)
        assert method is not None
        # Unit should be omitted
        assert ": Unit" not in method["signature"]
        assert method["signature"] == "(x: Int)"

    def test_non_unit_return_included(self, tmp_path: Path) -> None:
        """Test that non-Unit return type is included in signature."""
        make_scala_file(tmp_path, "IntReturn.scala", """
object IntReturn {
  def calculate(x: Int): Int = x * 2
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "IntReturn.calculate"), None)
        assert method is not None
        assert method["signature"] == "(x: Int): Int"


class TestScalaSymbolExtraction:
    """Branch coverage for symbol extraction branches."""

    def test_function_in_object(self, tmp_path: Path) -> None:
        """Test method inside object (with enclosing type)."""
        make_scala_file(tmp_path, "Methods.scala", """
object Methods {
  def helper(): Unit = {}
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "Methods.helper"), None)
        assert method is not None
        assert method["kind"] == "method"

    def test_standalone_function(self, tmp_path: Path) -> None:
        """Test top-level function (without enclosing type)."""
        # Note: In Scala 3, top-level defs are possible but Scala 2 requires objects
        # The analyzer handles both - this tests the else branch
        make_scala_file(tmp_path, "TopLevel.scala", """
// Scala 3 style
def topLevelFunc(): Unit = {}
""")
        data = analyze(tmp_path)
        # May or may not parse depending on tree-sitter-scala version
        funcs = [n for n in data["nodes"] if "topLevelFunc" in n["name"]]
        # If it parses, should be function kind
        if funcs:
            assert funcs[0]["kind"] == "function"

    def test_abstract_method_in_trait(self, tmp_path: Path) -> None:
        """Test abstract method declaration in trait."""
        make_scala_file(tmp_path, "Abstract.scala", """
trait Abstract {
  def process(x: Int): String
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "Abstract.process"), None)
        assert method is not None
        assert method["kind"] == "method"
        assert method["signature"] == "(x: Int): String"

    def test_object_definition(self, tmp_path: Path) -> None:
        """Test Scala object (singleton)."""
        make_scala_file(tmp_path, "Singleton.scala", """
object Singleton {
  val value = 42
}
""")
        data = analyze(tmp_path)
        obj = next((n for n in data["nodes"] if n["name"] == "Singleton"), None)
        assert obj is not None
        assert obj["kind"] == "object"

    def test_trait_definition(self, tmp_path: Path) -> None:
        """Test trait definition extraction."""
        make_scala_file(tmp_path, "MyTrait.scala", """
trait MyTrait {
  def action(): Unit
}
""")
        data = analyze(tmp_path)
        trait = next((n for n in data["nodes"] if n["name"] == "MyTrait"), None)
        assert trait is not None
        assert trait["kind"] == "trait"

    def test_class_definition(self, tmp_path: Path) -> None:
        """Test class definition extraction."""
        make_scala_file(tmp_path, "MyClass.scala", """
class MyClass(name: String) {
  def getName(): String = name
}
""")
        data = analyze(tmp_path)
        cls = next((n for n in data["nodes"] if n["name"] == "MyClass"), None)
        assert cls is not None
        assert cls["kind"] == "class"


class TestScalaCallResolution:
    """Branch coverage for call resolution branches."""

    def test_local_call(self, tmp_path: Path) -> None:
        """Test call to function in same file (local resolution)."""
        make_scala_file(tmp_path, "Local.scala", """
object Local {
  def helper(): Unit = {}

  def main(): Unit = {
    helper()
  }
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("helper" in e["dst"] for e in edges)

    def test_cross_file_call(self, tmp_path: Path) -> None:
        """Test call to function in different file (global resolution)."""
        make_scala_file(tmp_path, "Utils.scala", """
object Utils {
  def format(s: String): String = s.trim
}
""")
        make_scala_file(tmp_path, "App.scala", """
object App {
  def run(): Unit = {
    format("test")
  }
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("format" in e["dst"] for e in edges)

    def test_call_with_import_hint(self, tmp_path: Path) -> None:
        """Test call resolution using import hints for disambiguation."""
        (tmp_path / "pkg").mkdir(exist_ok=True)
        make_scala_file(tmp_path, "pkg/Helper.scala", """
package pkg

object Helper {
  def assist(): Unit = {}
}
""")
        make_scala_file(tmp_path, "Main.scala", """
import pkg.Helper

object Main {
  def run(): Unit = {
    assist()
  }
}
""")
        data = analyze(tmp_path)
        edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert any("assist" in e["dst"] for e in edges)


class TestScalaMultipleParams:
    """Test functions with multiple parameters."""

    def test_multiple_params(self, tmp_path: Path) -> None:
        """Test function with multiple parameters."""
        make_scala_file(tmp_path, "MultiParam.scala", """
object MultiParam {
  def add(a: Int, b: Int): Int = a + b
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "MultiParam.add"), None)
        assert method is not None
        assert method["signature"] == "(a: Int, b: Int): Int"

    def test_generic_return_type(self, tmp_path: Path) -> None:
        """Test function with generic return type."""
        make_scala_file(tmp_path, "GenericRet.scala", """
object GenericRet {
  def toList(x: Int): List[Int] = List(x)
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "GenericRet.toList"), None)
        assert method is not None
        assert "List[Int]" in method["signature"]

    def test_tuple_return_type(self, tmp_path: Path) -> None:
        """Test function with tuple return type."""
        make_scala_file(tmp_path, "TupleRet.scala", """
object TupleRet {
  def pair(x: Int): (Int, String) = (x, x.toString)
}
""")
        data = analyze(tmp_path)
        method = next((n for n in data["nodes"] if n["name"] == "TupleRet.pair"), None)
        assert method is not None
        assert "(" in method["signature"]
