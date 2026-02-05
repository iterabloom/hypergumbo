"""Branch coverage tests for groovy.py analyzer.

Tests specific branch paths in the Groovy analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Class/interface/enum extraction
- Method and function extraction
- Base class extraction
- Signature extraction
- Call edges and import edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.groovy import (
    _make_symbol_id,
    _make_file_id,
    analyze_groovy,
    find_groovy_files,
)


def make_groovy_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Groovy file with given content."""
    (tmp_path / name).write_text(content)


class TestGroovyHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("src/Main.groovy", 1, 10, "Main", "class")
        assert symbol_id == "groovy:src/Main.groovy:1-10:Main:class"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = _make_file_id("src/Main.groovy")
        assert file_id == "groovy:src/Main.groovy:1-1:file:file"


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_simple_class(self, tmp_path: Path) -> None:
        """Test simple class extraction."""
        make_groovy_file(tmp_path, "Person.groovy", """
class Person {
    String name
    int age
}
""")
        result = analyze_groovy(tmp_path)
        assert not result.skipped

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) >= 1
        assert any(c.name == "Person" for c in classes)

    def test_class_with_extends(self, tmp_path: Path) -> None:
        """Test class with extends clause."""
        make_groovy_file(tmp_path, "Employee.groovy", """
class Employee extends Person {
    String department
}
""")
        result = analyze_groovy(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]

        assert len(classes) >= 1
        employee = next((c for c in classes if c.name == "Employee"), None)
        assert employee is not None
        assert "Person" in (employee.meta or {}).get("base_classes", [])

    def test_class_with_implements(self, tmp_path: Path) -> None:
        """Test class with implements clause."""
        make_groovy_file(tmp_path, "Worker.groovy", """
interface Workable {
    void work()
}

class Worker implements Workable {
    void work() {
        println "Working"
    }
}
""")
        result = analyze_groovy(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]

        worker = next((c for c in classes if c.name == "Worker"), None)
        assert worker is not None
        base_classes = (worker.meta or {}).get("base_classes", [])
        assert "Workable" in base_classes

    def test_class_with_extends_and_implements(self, tmp_path: Path) -> None:
        """Test class with both extends and implements."""
        make_groovy_file(tmp_path, "Manager.groovy", """
class Person {}
interface Manageable {}

class Manager extends Person implements Manageable {
    void manage() {}
}
""")
        result = analyze_groovy(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]

        manager = next((c for c in classes if c.name == "Manager"), None)
        assert manager is not None
        base_classes = (manager.meta or {}).get("base_classes", [])
        assert len(base_classes) >= 2

    def test_class_with_generic_type(self, tmp_path: Path) -> None:
        """Test class extending generic type."""
        make_groovy_file(tmp_path, "ListWrapper.groovy", """
class ListWrapper extends ArrayList<String> {
    void addItem(String item) {
        add(item)
    }
}
""")
        result = analyze_groovy(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]

        wrapper = next((c for c in classes if c.name == "ListWrapper"), None)
        assert wrapper is not None
        base_classes = (wrapper.meta or {}).get("base_classes", [])
        assert "ArrayList" in base_classes


class TestInterfaceExtraction:
    """Branch coverage for interface extraction."""

    def test_simple_interface(self, tmp_path: Path) -> None:
        """Test simple interface extraction."""
        make_groovy_file(tmp_path, "Printable.groovy", """
interface Printable {
    void print()
}
""")
        result = analyze_groovy(tmp_path)
        interfaces = [s for s in result.symbols if s.kind == "interface"]

        assert len(interfaces) >= 1
        assert any(i.name == "Printable" for i in interfaces)


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_simple_enum(self, tmp_path: Path) -> None:
        """Test simple enum extraction."""
        make_groovy_file(tmp_path, "Status.groovy", """
enum Status {
    ACTIVE,
    INACTIVE,
    PENDING
}
""")
        result = analyze_groovy(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]

        assert len(enums) >= 1
        assert any(e.name == "Status" for e in enums)


class TestMethodExtraction:
    """Branch coverage for method extraction."""

    def test_method_in_class(self, tmp_path: Path) -> None:
        """Test method extraction inside class."""
        make_groovy_file(tmp_path, "Calculator.groovy", """
class Calculator {
    int add(int a, int b) {
        return a + b
    }
}
""")
        result = analyze_groovy(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]

        assert len(methods) >= 1
        add_method = next((m for m in methods if "add" in m.name), None)
        assert add_method is not None
        assert add_method.name == "Calculator.add"

    def test_method_with_signature(self, tmp_path: Path) -> None:
        """Test method signature extraction."""
        make_groovy_file(tmp_path, "Service.groovy", """
class Service {
    String process(String input, int count) {
        return input * count
    }
}
""")
        result = analyze_groovy(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]

        process = next((m for m in methods if "process" in m.name), None)
        assert process is not None
        assert process.signature is not None
        assert "String input" in process.signature

    def test_method_void_return(self, tmp_path: Path) -> None:
        """Test method with void return type."""
        make_groovy_file(tmp_path, "Logger.groovy", """
class Logger {
    void log(String message) {
        println message
    }
}
""")
        result = analyze_groovy(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]

        log_method = next((m for m in methods if "log" in m.name), None)
        assert log_method is not None
        # void return should not appear in signature
        assert "void" not in (log_method.signature or "")


class TestFunctionExtraction:
    """Branch coverage for top-level function extraction."""

    def test_def_function(self, tmp_path: Path) -> None:
        """Test def keyword function extraction."""
        make_groovy_file(tmp_path, "utils.groovy", """
def greet(String name) {
    return "Hello, ${name}!"
}

def calculate(int x, int y) {
    return x + y
}
""")
        result = analyze_groovy(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 2
        assert any(f.name == "greet" for f in functions)
        assert any(f.name == "calculate" for f in functions)


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_simple_import(self, tmp_path: Path) -> None:
        """Test simple import creates edge."""
        make_groovy_file(tmp_path, "App.groovy", """
import java.util.List

class App {
    List<String> items = []
}
""")
        result = analyze_groovy(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1

    def test_multiple_imports(self, tmp_path: Path) -> None:
        """Test multiple imports create edges."""
        make_groovy_file(tmp_path, "DataService.groovy", """
import java.util.List
import java.util.Map
import java.io.File

class DataService {
    void process() {}
}
""")
        result = analyze_groovy(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 3


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_method_call_creates_edge(self, tmp_path: Path) -> None:
        """Test method call creates call edge."""
        make_groovy_file(tmp_path, "Helper.groovy", """
class Helper {
    void setup() {
        println "Setting up"
    }

    void run() {
        setup()
    }
}
""")
        result = analyze_groovy(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        assert len(call_edges) >= 1


class TestFindGroovyFiles:
    """Branch coverage for file discovery."""

    def test_finds_groovy_files(self, tmp_path: Path) -> None:
        """Test .groovy files are discovered."""
        (tmp_path / "Main.groovy").write_text("class Main {}")

        files = list(find_groovy_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".groovy" for f in files)

    def test_finds_gradle_files(self, tmp_path: Path) -> None:
        """Test .gradle files are discovered."""
        (tmp_path / "build.gradle").write_text("""
plugins {
    id 'java'
}
""")

        files = list(find_groovy_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".gradle" for f in files)


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_groovy_files(self, tmp_path: Path) -> None:
        """Test directory with no Groovy files."""
        result = analyze_groovy(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_groovy(self, tmp_path: Path) -> None:
        """Test minimal Groovy file."""
        make_groovy_file(tmp_path, "Min.groovy", """
class Min {}
""")
        result = analyze_groovy(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_groovy_file(tmp_path, "App.groovy", """
class App {
    void main() {}
}
""")
        result = analyze_groovy(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestComplexGroovy:
    """Branch coverage for complex Groovy files."""

    def test_gradle_build_file(self, tmp_path: Path) -> None:
        """Test Gradle build file parsing."""
        make_groovy_file(tmp_path, "build.gradle", """
plugins {
    id 'java'
    id 'application'
}

repositories {
    mavenCentral()
}

dependencies {
    implementation 'org.apache.commons:commons-lang3:3.12.0'
    testImplementation 'junit:junit:4.13.2'
}

application {
    mainClass = 'com.example.App'
}
""")
        result = analyze_groovy(tmp_path)
        # Gradle files should be analyzable
        assert not result.skipped
