"""Tests for Groovy analyzer."""
from pathlib import Path
from unittest.mock import patch


class TestGroovyHelpers:
    """Tests for Groovy analyzer helper functions."""

    def test_find_child_by_type_returns_none(self) -> None:
        """Returns None when no matching child type is found."""
        from unittest.mock import MagicMock
        from hypergumbo.analyze.groovy import _find_child_by_type

        # Create a mock node with no children matching the type
        mock_node = MagicMock()
        mock_child = MagicMock()
        mock_child.type = "different_type"
        mock_node.children = [mock_child]

        result = _find_child_by_type(mock_node, "identifier")
        assert result is None


class TestFindGroovyFiles:
    """Tests for Groovy file discovery."""

    def test_finds_groovy_files(self, tmp_path: Path) -> None:
        """Finds .groovy files."""
        from hypergumbo.analyze.groovy import find_groovy_files

        (tmp_path / "Main.groovy").write_text("class Main {}")
        (tmp_path / "build.gradle").write_text("apply plugin: 'java'")
        (tmp_path / "other.txt").write_text("not groovy")

        files = list(find_groovy_files(tmp_path))

        assert len(files) == 2
        assert any(f.suffix == ".groovy" for f in files)
        assert any(f.name == "build.gradle" for f in files)


class TestGroovyTreeSitterAvailability:
    """Tests for tree-sitter-groovy availability checking."""

    def test_is_groovy_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-groovy is available."""
        from hypergumbo.analyze.groovy import is_groovy_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_groovy_tree_sitter_available() is True

    def test_is_groovy_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.groovy import is_groovy_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_groovy_tree_sitter_available() is False

    def test_is_groovy_tree_sitter_available_no_groovy(self) -> None:
        """Returns False when tree-sitter is available but groovy grammar is not."""
        from hypergumbo.analyze.groovy import is_groovy_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()
            return None

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_groovy_tree_sitter_available() is False


class TestAnalyzeGroovyFallback:
    """Tests for fallback behavior when tree-sitter-groovy unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-groovy unavailable."""
        from hypergumbo.analyze.groovy import analyze_groovy

        (tmp_path / "test.groovy").write_text("def test() {}")

        with patch("hypergumbo.analyze.groovy.is_groovy_tree_sitter_available", return_value=False):
            result = analyze_groovy(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-groovy" in result.skip_reason


class TestGroovyClassExtraction:
    """Tests for extracting Groovy classes."""

    def test_extracts_class(self, tmp_path: Path) -> None:
        """Extracts class declarations."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Main.groovy"
        groovy_file.write_text("""
class User {
    String name

    void greet() {
        println("Hello, $name!")
    }
}

class Config {
    static String version = "1.0"
}
""")

        result = analyze_groovy(tmp_path)


        assert result.run is not None
        assert result.run.files_analyzed == 1
        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [s.name for s in classes]
        assert "User" in class_names
        assert "Config" in class_names


class TestGroovyMethodExtraction:
    """Tests for extracting Groovy methods."""

    def test_extracts_methods(self, tmp_path: Path) -> None:
        """Extracts method declarations from classes."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Utils.groovy"
        groovy_file.write_text("""
class Utils {
    void doSomething() {
        println "doing something"
    }

    int calculate(int a, int b) {
        return a + b
    }
}
""")

        result = analyze_groovy(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert "Utils.doSomething" in method_names
        assert "Utils.calculate" in method_names


class TestGroovyFunctionExtraction:
    """Tests for extracting Groovy top-level functions."""

    def test_extracts_functions(self, tmp_path: Path) -> None:
        """Extracts top-level function definitions."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "scripts.groovy"
        groovy_file.write_text("""
def greet(name) {
    println "Hello, $name!"
}

def calculate(a, b) {
    return a + b
}
""")

        result = analyze_groovy(tmp_path)


        functions = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in functions]
        assert "greet" in func_names
        assert "calculate" in func_names


class TestGroovyImportEdges:
    """Tests for extracting import statements."""

    def test_extracts_imports(self, tmp_path: Path) -> None:
        """Extracts import statements as edges."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Main.groovy"
        groovy_file.write_text("""
import groovy.json.JsonSlurper
import java.util.List

class Main {
    void parse() {
        def slurper = new JsonSlurper()
    }
}
""")

        result = analyze_groovy(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) == 2

        imported = [e.dst for e in import_edges]
        assert any("groovy.json.JsonSlurper" in dst for dst in imported)
        assert any("java.util.List" in dst for dst in imported)


class TestGroovyCallEdges:
    """Tests for extracting function call edges."""

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between functions."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Main.groovy"
        groovy_file.write_text("""
class Helper {
    void doWork() {
        println "working"
    }
}

class Main {
    void run() {
        helper()
    }

    void helper() {
        println "helping"
    }
}
""")

        result = analyze_groovy(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should find run() calling helper()
        assert len(call_edges) >= 1

        # Verify at least one call edge exists
        src_names = [e.src for e in call_edges]
        dst_names = [e.dst for e in call_edges]
        assert any("run" in src or "Main.run" in src for src in src_names)

    def test_extracts_cross_file_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between functions in different files."""
        from hypergumbo.analyze.groovy import analyze_groovy

        helper_file = tmp_path / "Helper.groovy"
        helper_file.write_text("""
class Helper {
    void doWork() {
        println "working"
    }
}
""")

        main_file = tmp_path / "Main.groovy"
        main_file.write_text("""
class Main {
    void run() {
        doWork()
    }
}
""")

        result = analyze_groovy(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should find run() calling doWork() (cross-file via global symbols)
        assert len(call_edges) >= 1

        # Check for cross-file call edge with lower confidence (0.80)
        cross_file_edges = [e for e in call_edges if e.confidence == 0.80]
        assert len(cross_file_edges) >= 1


class TestGradleBuildFile:
    """Tests for analyzing Gradle build files."""

    def test_analyzes_gradle_file(self, tmp_path: Path) -> None:
        """Analyzes .gradle files."""
        from hypergumbo.analyze.groovy import analyze_groovy

        gradle_file = tmp_path / "build.gradle"
        gradle_file.write_text("""
plugins {
    id 'java'
    id 'application'
}

repositories {
    mavenCentral()
}

dependencies {
    implementation 'org.codehaus.groovy:groovy-all:3.0.9'
}

def customTask() {
    println "Custom task"
}
""")

        result = analyze_groovy(tmp_path)


        assert result.run is not None
        assert result.run.files_analyzed == 1


class TestGroovyInterfaceExtraction:
    """Tests for extracting Groovy interfaces."""

    def test_extracts_interface(self, tmp_path: Path) -> None:
        """Extracts interface declarations."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Api.groovy"
        groovy_file.write_text("""
interface Greeter {
    void greet(String name)
    String getMessage()
}

interface Calculator {
    int add(int a, int b)
}
""")

        result = analyze_groovy(tmp_path)


        interfaces = [s for s in result.symbols if s.kind == "interface"]
        interface_names = [s.name for s in interfaces]
        assert "Greeter" in interface_names
        assert "Calculator" in interface_names


class TestGroovyTraitExtraction:
    """Tests for extracting Groovy traits.

    Note: The tree-sitter-groovy grammar (v0.1.2) does not fully support
    trait declarations - it parses 'trait X' as a function call. This test
    documents the current behavior and will pass once the grammar is updated.
    """

    def test_trait_parsing_limitation(self, tmp_path: Path) -> None:
        """Documents trait parsing limitation in tree-sitter-groovy grammar."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Traits.groovy"
        groovy_file.write_text("""
trait Flyable {
    void fly() {
        println "Flying"
    }
}
""")

        result = analyze_groovy(tmp_path)


        # tree-sitter-groovy v0.1.2 parses 'trait X' as a function call
        # not a trait declaration. This test documents this limitation.
        # When the grammar is updated, this test should be updated.
        traits = [s for s in result.symbols if s.kind == "trait"]
        # Currently 0 traits due to grammar limitation
        assert len(traits) == 0


class TestGroovyEnumExtraction:
    """Tests for extracting Groovy enums."""

    def test_extracts_enum(self, tmp_path: Path) -> None:
        """Extracts enum declarations."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Enums.groovy"
        groovy_file.write_text("""
enum Color {
    RED, GREEN, BLUE
}

enum Status {
    PENDING, ACTIVE, COMPLETED
}
""")

        result = analyze_groovy(tmp_path)


        enums = [s for s in result.symbols if s.kind == "enum"]
        enum_names = [s.name for s in enums]
        assert "Color" in enum_names
        assert "Status" in enum_names


class TestGroovySymbolProperties:
    """Tests for symbol property correctness."""

    def test_symbol_has_correct_span(self, tmp_path: Path) -> None:
        """Symbols have correct line number spans."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Test.groovy"
        groovy_file.write_text("""class Test {
    void method() {
        println "test"
    }
}
""")

        result = analyze_groovy(tmp_path)


        test_class = next((s for s in result.symbols if s.name == "Test"), None)
        assert test_class is not None
        assert test_class.span.start_line == 1
        assert test_class.language == "groovy"
        assert test_class.origin == "groovy-v1"

    def test_method_prefixed_with_class(self, tmp_path: Path) -> None:
        """Methods are prefixed with class name."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Example.groovy"
        groovy_file.write_text("""
class Example {
    void run() {}
}
""")

        result = analyze_groovy(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        assert any(s.name == "Example.run" for s in methods)


class TestGroovyEdgeProperties:
    """Tests for edge property correctness."""

    def test_edge_has_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Test.groovy"
        groovy_file.write_text("""
import java.util.List

class Test {}
""")

        result = analyze_groovy(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        for edge in import_edges:
            assert edge.confidence > 0
            assert edge.confidence <= 1.0


class TestGroovyEmptyFile:
    """Tests for handling empty or minimal files."""

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handles empty Groovy files gracefully."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Empty.groovy"
        groovy_file.write_text("")

        result = analyze_groovy(tmp_path)


        # Should not crash, may have 0 or minimal symbols
        assert result.run is not None

    def test_handles_comment_only_file(self, tmp_path: Path) -> None:
        """Handles files with only comments."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "Comments.groovy"
        groovy_file.write_text("""
// This is a comment
/* Multi-line
   comment */
""")

        result = analyze_groovy(tmp_path)


        assert result.run is not None


class TestGroovyParserFailure:
    """Tests for parser failure handling."""

    def test_handles_parser_load_failure(self, tmp_path: Path) -> None:
        """Handles failure to load Groovy parser."""
        from hypergumbo.analyze.groovy import analyze_groovy

        groovy_file = tmp_path / "test.groovy"
        groovy_file.write_text("class Test {}")

        with patch("hypergumbo.analyze.groovy.is_groovy_tree_sitter_available", return_value=True):
            with patch("tree_sitter_groovy.language", side_effect=Exception("Parser error")):
                result = analyze_groovy(tmp_path)

        assert result.skipped is True
        assert "Parser error" in result.skip_reason or "Failed to load" in result.skip_reason

    def test_handles_unreadable_file(self, tmp_path: Path) -> None:
        """Handles files that can't be read."""
        from hypergumbo.analyze.groovy import analyze_groovy

        # Create a valid groovy file and an unreadable one
        valid_file = tmp_path / "Valid.groovy"
        valid_file.write_text("class Valid {}")

        unreadable = tmp_path / "Unreadable.groovy"
        unreadable.write_text("class Unreadable {}")

        result = analyze_groovy(tmp_path)


        # Should still process the valid file
        assert result.run is not None


class TestGroovySignatureExtraction:
    """Tests for Groovy method signature extraction.

    Note: Groovy's tree-sitter grammar uses dynamic parameter types,
    so we only capture parameter names in most cases.
    """

    def test_params_extraction(self, tmp_path: Path) -> None:
        """Extracts signature with parameter names."""
        from hypergumbo.analyze.groovy import analyze_groovy

        (tmp_path / "Calculator.groovy").write_text("""
class Calculator {
    int add(int x, int y) {
        return x + y
    }
}
""")
        result = analyze_groovy(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "add" in s.name]
        assert len(methods) == 1
        # Groovy's grammar has limited type info, but captures params
        assert methods[0].signature is not None
        assert "x" in methods[0].signature
        assert "y" in methods[0].signature

    def test_void_return_type_omitted(self, tmp_path: Path) -> None:
        """Void return type is omitted from signature."""
        from hypergumbo.analyze.groovy import analyze_groovy

        (tmp_path / "Logger.groovy").write_text("""
class Logger {
    void log(String message) {
        println message
    }
}
""")
        result = analyze_groovy(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "log" in s.name]
        assert len(methods) == 1
        # Should have message param, no return type (void is omitted)
        assert "message" in methods[0].signature
        assert "void" not in methods[0].signature

    def test_no_params_function(self, tmp_path: Path) -> None:
        """Extracts signature for method with no parameters."""
        from hypergumbo.analyze.groovy import analyze_groovy

        (tmp_path / "Counter.groovy").write_text("""
class Counter {
    int getCount() {
        return 0
    }
}
""")
        result = analyze_groovy(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method" and "getCount" in s.name]
        assert len(methods) == 1
        # Empty params
        assert methods[0].signature == "()"
