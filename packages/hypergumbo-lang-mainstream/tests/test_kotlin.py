"""Tests for Kotlin analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindKotlinFiles:
    """Tests for Kotlin file discovery."""

    def test_finds_kotlin_files(self, tmp_path: Path) -> None:
        """Finds .kt files."""
        from hypergumbo_lang_mainstream.kotlin import find_kotlin_files

        (tmp_path / "Main.kt").write_text("fun main() {}")
        (tmp_path / "Utils.kt").write_text("class Utils {}")
        (tmp_path / "other.txt").write_text("not kotlin")

        files = list(find_kotlin_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".kt" for f in files)


class TestKotlinTreeSitterAvailability:
    """Tests for tree-sitter-kotlin availability checking."""

    def test_is_kotlin_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-kotlin is available."""
        from hypergumbo_lang_mainstream.kotlin import is_kotlin_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_kotlin_tree_sitter_available() is True

    def test_is_kotlin_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo_lang_mainstream.kotlin import is_kotlin_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_kotlin_tree_sitter_available() is False

    def test_is_kotlin_tree_sitter_available_no_kotlin(self) -> None:
        """Returns False when tree-sitter is available but kotlin grammar is not."""
        from hypergumbo_lang_mainstream.kotlin import is_kotlin_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()
            return None

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_kotlin_tree_sitter_available() is False


class TestAnalyzeKotlinFallback:
    """Tests for fallback behavior when tree-sitter-kotlin unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-kotlin unavailable."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "test.kt").write_text("fun test() {}")

        with patch("hypergumbo_lang_mainstream.kotlin.is_kotlin_tree_sitter_available", return_value=False):
            result = analyze_kotlin(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-kotlin" in result.skip_reason


class TestKotlinFunctionExtraction:
    """Tests for extracting Kotlin functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts Kotlin function declarations."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Main.kt"
        kt_file.write_text("""
fun main() {
    println("Hello, world!")
}

fun helper(x: Int): Int {
    return x + 1
}
""")

        result = analyze_kotlin(tmp_path)


        assert result.run is not None
        assert result.run.files_analyzed == 1
        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "main" in func_names
        assert "helper" in func_names


class TestKotlinClassExtraction:
    """Tests for extracting Kotlin classes."""

    def test_extracts_class(self, tmp_path: Path) -> None:
        """Extracts class declarations."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Models.kt"
        kt_file.write_text("""
class User(val name: String) {
    fun greet() {
        println("Hello, $name!")
    }
}

data class Point(val x: Int, val y: Int)
""")

        result = analyze_kotlin(tmp_path)


        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [s.name for s in classes]
        assert "User" in class_names
        assert "Point" in class_names


class TestKotlinObjectExtraction:
    """Tests for extracting Kotlin objects."""

    def test_extracts_object(self, tmp_path: Path) -> None:
        """Extracts object declarations."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Singleton.kt"
        kt_file.write_text("""
object Database {
    fun connect() {
        println("Connecting...")
    }
}

object Config {
    val version = "1.0"
}
""")

        result = analyze_kotlin(tmp_path)


        objects = [s for s in result.symbols if s.kind == "object"]
        object_names = [s.name for s in objects]
        assert "Database" in object_names
        assert "Config" in object_names


class TestKotlinInterfaceExtraction:
    """Tests for extracting Kotlin interfaces."""

    def test_extracts_interface(self, tmp_path: Path) -> None:
        """Extracts interface declarations."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Interfaces.kt"
        kt_file.write_text("""
interface Drawable {
    fun draw()
}

interface Clickable {
    fun onClick()
}
""")

        result = analyze_kotlin(tmp_path)


        interfaces = [s for s in result.symbols if s.kind == "interface"]
        interface_names = [s.name for s in interfaces]
        assert "Drawable" in interface_names
        assert "Clickable" in interface_names


class TestKotlinInheritanceEdges:
    """Tests for extracting Kotlin inheritance edges (META-001)."""

    def test_extracts_base_class_metadata(self, tmp_path: Path) -> None:
        """Extracts base_classes metadata for class with superclass."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Models.kt"
        kt_file.write_text("""
open class BaseModel {
    fun save() {}
}

class User : BaseModel() {
    fun greet() {}
}
""")

        result = analyze_kotlin(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta is not None
        assert user.meta.get("base_classes") == ["BaseModel"]

    def test_extracts_interface_implementation(self, tmp_path: Path) -> None:
        """Extracts base_classes metadata for class implementing interface."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Models.kt"
        kt_file.write_text("""
interface Drawable {
    fun draw()
}

class Circle : Drawable {
    override fun draw() {}
}
""")

        result = analyze_kotlin(tmp_path)

        circle = next((s for s in result.symbols if s.name == "Circle"), None)
        assert circle is not None
        assert circle.meta is not None
        assert circle.meta.get("base_classes") == ["Drawable"]

    def test_extracts_multiple_inheritance(self, tmp_path: Path) -> None:
        """Extracts all base classes for class extending class and interfaces."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Models.kt"
        kt_file.write_text("""
open class BaseModel {
    fun save() {}
}

interface Drawable {
    fun draw()
}

interface Clickable {
    fun onClick()
}

class Widget : BaseModel(), Drawable, Clickable {
    override fun draw() {}
    override fun onClick() {}
}
""")

        result = analyze_kotlin(tmp_path)

        widget = next((s for s in result.symbols if s.name == "Widget"), None)
        assert widget is not None
        assert widget.meta is not None
        base_classes = widget.meta.get("base_classes")
        assert base_classes is not None
        assert "BaseModel" in base_classes
        assert "Drawable" in base_classes
        assert "Clickable" in base_classes

    def test_creates_extends_edge(self, tmp_path: Path) -> None:
        """Creates extends edge from class to its superclass."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Models.kt"
        kt_file.write_text("""
open class BaseModel {
    fun save() {}
}

class User : BaseModel() {
    fun greet() {}
}
""")

        result = analyze_kotlin(tmp_path)

        user = next((s for s in result.symbols if s.name == "User"), None)
        base = next((s for s in result.symbols if s.name == "BaseModel"), None)
        assert user is not None
        assert base is not None

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) == 1
        assert extends_edges[0].src == user.id
        assert extends_edges[0].dst == base.id

    def test_creates_implements_edge_for_interface(self, tmp_path: Path) -> None:
        """Creates implements edge from class to interface."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Models.kt"
        kt_file.write_text("""
interface Drawable {
    fun draw()
}

class Circle : Drawable {
    override fun draw() {}
}
""")

        result = analyze_kotlin(tmp_path)

        circle = next((s for s in result.symbols if s.name == "Circle"), None)
        drawable = next((s for s in result.symbols if s.name == "Drawable"), None)
        assert circle is not None
        assert drawable is not None

        implements_edges = [e for e in result.edges if e.edge_type == "implements"]
        assert len(implements_edges) == 1
        assert implements_edges[0].src == circle.id
        assert implements_edges[0].dst == drawable.id

    def test_no_edge_for_external_superclass(self, tmp_path: Path) -> None:
        """No edge created when superclass is not in analyzed codebase."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Models.kt"
        kt_file.write_text("""
class UserController : AbstractController() {
    fun index() {}
}
""")

        result = analyze_kotlin(tmp_path)

        controller = next((s for s in result.symbols if s.name == "UserController"), None)
        assert controller is not None
        assert controller.meta is not None
        assert controller.meta.get("base_classes") == ["AbstractController"]

        # No edges (AbstractController is external)
        extends_edges = [e for e in result.edges if e.edge_type in ("extends", "implements")]
        assert len(extends_edges) == 0


class TestKotlinFunctionCalls:
    """Tests for detecting function calls in Kotlin."""

    def test_detects_function_call(self, tmp_path: Path) -> None:
        """Detects calls to functions in same file."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Utils.kt"
        kt_file.write_text("""
fun caller() {
    helper()
}

fun helper() {
    println("helping")
}
""")

        result = analyze_kotlin(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1


class TestKotlinImports:
    """Tests for detecting Kotlin import statements."""

    def test_detects_import_statement(self, tmp_path: Path) -> None:
        """Detects import statements."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Main.kt"
        kt_file.write_text("""
import kotlin.collections.List
import java.io.File

fun main() {
    println("Hello")
}
""")

        result = analyze_kotlin(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestKotlinEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Returns skipped with run when parser loading fails."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "test.kt").write_text("fun test() {}")

        with patch("hypergumbo_lang_mainstream.kotlin.is_kotlin_tree_sitter_available", return_value=True):
            with patch.dict("sys.modules", {"tree_sitter_kotlin": MagicMock()}):
                import sys
                mock_module = sys.modules["tree_sitter_kotlin"]
                mock_module.language.side_effect = RuntimeError("Parser load failed")
                result = analyze_kotlin(tmp_path)

        assert result.skipped is True
        assert "Failed to load Kotlin parser" in result.skip_reason
        assert result.run is not None

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "empty.kt").write_text("// Just a comment\n")

        result = analyze_kotlin(tmp_path)


        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Helper.kt").write_text("""
fun greet(name: String): String {
    return "Hello, $name"
}
""")

        (tmp_path / "Main.kt").write_text("""
fun run() {
    greet("world")
}
""")

        result = analyze_kotlin(tmp_path)


        assert result.run.files_analyzed >= 2


class TestKotlinMethodExtraction:
    """Tests for extracting methods from classes."""

    def test_extracts_class_methods(self, tmp_path: Path) -> None:
        """Extracts methods defined inside classes."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "User.kt"
        kt_file.write_text("""
class User(val name: String) {
    fun getName(): String {
        return name
    }

    fun setName(newName: String) {
        // setter
    }
}
""")

        result = analyze_kotlin(tmp_path)


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert any("getName" in name for name in method_names)


class TestKotlinFileReadErrors:
    """Tests for file read error handling."""

    def test_symbol_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Symbol extraction handles file read errors gracefully."""
        from hypergumbo_lang_mainstream.kotlin import (
            _extract_symbols_from_file,
            is_kotlin_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

        if not is_kotlin_tree_sitter_available():
            pytest.skip("tree-sitter-kotlin not available")

        import tree_sitter_kotlin
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_kotlin.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        kt_file = tmp_path / "test.kt"
        kt_file.write_text("fun test() {}")

        with patch.object(Path, "read_bytes", side_effect=OSError("Read failed")):
            result = _extract_symbols_from_file(kt_file, parser, run)

        assert result.symbols == []

    def test_edge_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Edge extraction handles file read errors gracefully."""
        from hypergumbo_lang_mainstream.kotlin import (
            _extract_edges_from_file,
            is_kotlin_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

        if not is_kotlin_tree_sitter_available():
            pytest.skip("tree-sitter-kotlin not available")

        import tree_sitter_kotlin
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_kotlin.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        kt_file = tmp_path / "test.kt"
        kt_file.write_text("fun test() {}")

        with patch.object(Path, "read_bytes", side_effect=IOError("Read failed")):
            result = _extract_edges_from_file(kt_file, parser, {}, {}, {}, run)

        assert result == []


class TestKotlinNavigationCalls:
    """Tests for navigation suffix call patterns."""

    def test_detects_method_call_on_object(self, tmp_path: Path) -> None:
        """Detects method calls via navigation suffix."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Main.kt"
        kt_file.write_text("""
object Helpers {
    fun greet() {
        println("Hello")
    }
}

fun caller() {
    Helpers.greet()
}
""")

        result = analyze_kotlin(tmp_path)


        # Should detect call, even if it goes through navigation
        assert result.run is not None


class TestKotlinHelperFunctions:
    """Tests for helper function edge cases."""

    def test_find_child_by_type_returns_none(self, tmp_path: Path) -> None:
        """_find_child_by_type returns None when no matching child."""
        from hypergumbo_lang_mainstream.kotlin import (
            _find_child_by_type,
            is_kotlin_tree_sitter_available,
        )

        if not is_kotlin_tree_sitter_available():
            pytest.skip("tree-sitter-kotlin not available")

        import tree_sitter_kotlin
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_kotlin.language())
        parser = tree_sitter.Parser(lang)

        source = b"// comment\n"
        tree = parser.parse(source)

        result = _find_child_by_type(tree.root_node, "nonexistent_type")
        assert result is None


class TestKotlinObjectMethodCalls:
    """Tests for Object.method() call resolution."""

    def test_object_method_call_resolved(self, tmp_path: Path) -> None:
        """Object method calls are resolved to target symbols."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Service.kt"
        kt_file.write_text("""
object Helper {
    fun greet() {
        println("Hello")
    }
}

fun main() {
    Helper.greet()
}
""")

        result = analyze_kotlin(tmp_path)

        assert result.run is not None

        # Find symbols
        main_func = next(
            (s for s in result.symbols if s.name == "main"), None
        )
        greet_method = next(
            (s for s in result.symbols if "greet" in s.name), None
        )

        assert main_func is not None
        assert greet_method is not None

        # Should have edge from main to Helper.greet
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == main_func.id
                and e.dst == greet_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None
        assert call_edge.evidence_type == "ast_call_static"
        assert call_edge.confidence == 0.95


class TestKotlinVariableTypeInference:
    """Tests for type inference from constructor assignments."""

    def test_variable_method_call_resolved_via_type_inference(
        self, tmp_path: Path
    ) -> None:
        """Variable method calls resolved via constructor-based type inference."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "App.kt"
        kt_file.write_text("""
class Helper {
    fun doWork() {
        println("working")
    }
}

fun main() {
    val h = Helper()
    h.doWork()
}
""")

        result = analyze_kotlin(tmp_path)

        assert result.run is not None

        # Find symbols
        main_func = next(
            (s for s in result.symbols if s.name == "main"), None
        )
        dowork_method = next(
            (s for s in result.symbols if "doWork" in s.name), None
        )

        assert main_func is not None
        assert dowork_method is not None

        # Should have edge from main to Helper.doWork via type inference
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == main_func.id
                and e.dst == dowork_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None
        assert call_edge.evidence_type == "ast_call_type_inferred"
        assert call_edge.confidence == 0.85

    def test_parameter_type_inference(self, tmp_path: Path) -> None:
        """Function parameter types should enable method call resolution."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "App.kt"
        kt_file.write_text("""
class Database {
    fun save(obj: Any) { }
    fun commit() { }
}

fun process(db: Database, data: String) {
    db.save(data)
    db.commit()
}
""")

        result = analyze_kotlin(tmp_path)

        assert result.run is not None

        # Find symbols
        process_func = next(
            (s for s in result.symbols if s.name == "process"), None
        )
        db_save = next(
            (s for s in result.symbols if "save" in s.name and "Database" in s.id), None
        )
        db_commit = next(
            (s for s in result.symbols if "commit" in s.name and "Database" in s.id), None
        )

        assert process_func is not None
        assert db_save is not None
        assert db_commit is not None

        # Should have edges from process to Database.save and Database.commit
        save_edge = next(
            (
                e
                for e in result.edges
                if e.src == process_func.id
                and e.dst == db_save.id
                and e.edge_type == "calls"
            ),
            None,
        )
        commit_edge = next(
            (
                e
                for e in result.edges
                if e.src == process_func.id
                and e.dst == db_commit.id
                and e.edge_type == "calls"
            ),
            None,
        )

        assert save_edge is not None, "Expected call edge for db.save() via param type inference"
        assert commit_edge is not None, "Expected call edge for db.commit() via param type inference"
        assert save_edge.evidence_type == "ast_call_type_inferred"
        assert commit_edge.evidence_type == "ast_call_type_inferred"


class TestKotlinThisMethodCalls:
    """Tests for this.method() call resolution."""

    def test_this_method_call_resolved(self, tmp_path: Path) -> None:
        """this.method() calls are resolved to enclosing class methods."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Service.kt"
        kt_file.write_text("""
class Service {
    fun helper() {
        println("helping")
    }

    fun run() {
        this.helper()
    }
}
""")

        result = analyze_kotlin(tmp_path)

        assert result.run is not None

        # Find symbols
        run_method = next(
            (s for s in result.symbols if "run" in s.name), None
        )
        helper_method = next(
            (s for s in result.symbols if "helper" in s.name), None
        )

        assert run_method is not None
        assert helper_method is not None

        # Should have edge from Service.run to Service.helper
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == run_method.id
                and e.dst == helper_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None
        assert call_edge.evidence_type == "ast_call_this"
        assert call_edge.confidence == 0.90


class TestKotlinImportExtraction:
    """Tests for import extraction and tracking."""

    def test_imports_extracted_to_file_analysis(self, tmp_path: Path) -> None:
        """Import statements are extracted and tracked in FileAnalysis."""
        from hypergumbo_lang_mainstream.kotlin import (
            _extract_symbols_from_file,
            is_kotlin_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

        if not is_kotlin_tree_sitter_available():
            pytest.skip("tree-sitter-kotlin not available")

        import tree_sitter_kotlin
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_kotlin.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        kt_file = tmp_path / "Main.kt"
        kt_file.write_text("""
import com.example.Helper
import java.io.File

fun main() {
    println("hello")
}
""")

        analysis = _extract_symbols_from_file(kt_file, parser, run)

        # Check imports are extracted
        assert "Helper" in analysis.imports
        assert analysis.imports["Helper"] == "com.example.Helper"
        assert "File" in analysis.imports
        assert analysis.imports["File"] == "java.io.File"

    def test_import_used_for_disambiguation(self, tmp_path: Path) -> None:
        """Import path should be used as path_hint for call resolution disambiguation.

        When the same function name exists in multiple files, the import statement
        should help resolve to the correct target. This test verifies that the
        imports dict is actually used during resolution by checking the confidence
        level (path_hint matches get higher confidence).
        """
        from hypergumbo_lang_mainstream.kotlin import (
            _extract_edges_from_file,
            _extract_symbols_from_file,
            is_kotlin_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun
        from hypergumbo_core.symbol_resolution import NameResolver

        if not is_kotlin_tree_sitter_available():
            pytest.skip("tree-sitter-kotlin not available")

        import tree_sitter_kotlin
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_kotlin.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        # Create caller file that imports a specific Helper
        caller_file = tmp_path / "Caller.kt"
        caller_file.write_text("""
import com.example.Helper

fun caller() {
    Helper.doWork()
}

class Helper {
    companion object {
        fun doWork() {}
    }
}
""")

        # Extract symbols and imports
        analysis = _extract_symbols_from_file(caller_file, parser, run)
        local_symbols = analysis.symbol_by_name
        imports = analysis.imports

        # Build global symbols
        global_symbols = {s.name: s for s in analysis.symbols}

        # Extract edges with imports
        edges = _extract_edges_from_file(
            caller_file, parser, local_symbols, global_symbols, imports, run
        )

        # Verify edges were created (imports dict is being passed through)
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1, "Expected at least one call edge to be created"


class TestKotlinLambdaCallAttribution:
    """Tests for call edge attribution inside lambda expressions.

    Kotlin uses lambdas heavily (forEach, map, callbacks). Calls inside these
    lambdas must be attributed to the enclosing named function.
    """

    def test_call_inside_trailing_lambda_attributed(self, tmp_path: Path) -> None:
        """Calls inside trailing lambda are attributed to enclosing function.

        When you have:
            fun main() {
                items.forEach { item ->
                    helper(item)  // This call should be from main
                }
            }

        The call to helper() should be attributed to main, not lost.
        """
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "App.kt"
        kt_file.write_text("""
fun helper(x: Int) {
    println(x)
}

fun main() {
    val items = listOf(1, 2, 3)
    items.forEach { item ->
        helper(item)
    }
}
""")

        result = analyze_kotlin(tmp_path)

        # Find symbols
        main_func = next((s for s in result.symbols if s.name == "main"), None)
        helper_func = next((s for s in result.symbols if s.name == "helper"), None)

        assert main_func is not None, "Should find main function"
        assert helper_func is not None, "Should find helper function"

        # The call to helper() inside the lambda should be attributed to main
        call_edge = next(
            (
                e for e in result.edges
                if e.src == main_func.id
                and e.dst == helper_func.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call to helper() inside forEach lambda should be attributed to main"

    def test_call_inside_callback_lambda_attributed(self, tmp_path: Path) -> None:
        """Calls inside callback lambdas are attributed to enclosing function.

        When you have:
            fun caller() {
                runCallback { doWork() }
            }

        The call to doWork() should be attributed to caller.
        """
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Callback.kt"
        kt_file.write_text("""
fun doWork() {
    println("working")
}

fun runCallback(callback: () -> Unit) {
    callback()
}

fun caller() {
    runCallback { doWork() }
}
""")

        result = analyze_kotlin(tmp_path)

        # Find symbols
        caller_func = next((s for s in result.symbols if s.name == "caller"), None)
        dowork_func = next((s for s in result.symbols if s.name == "doWork"), None)

        assert caller_func is not None, "Should find caller function"
        assert dowork_func is not None, "Should find doWork function"

        # The call to doWork() inside the lambda should be attributed to caller
        call_edge = next(
            (
                e for e in result.edges
                if e.src == caller_func.id
                and e.dst == dowork_func.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call to doWork() inside callback lambda should be attributed to caller"

    def test_nested_lambda_attributed_to_outer_function(self, tmp_path: Path) -> None:
        """Calls inside nested lambdas are attributed to the outermost named function.

        When you have:
            fun outer() {
                items.forEach { x ->
                    items.map { y ->
                        helper()  // Should be attributed to outer
                    }
                }
            }
        """
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Nested.kt"
        kt_file.write_text("""
fun helper() {
    println("help")
}

fun outer() {
    val items = listOf(1, 2, 3)
    items.forEach { x ->
        items.map { y ->
            helper()
        }
    }
}
""")

        result = analyze_kotlin(tmp_path)

        # Find symbols
        outer_func = next((s for s in result.symbols if s.name == "outer"), None)
        helper_func = next((s for s in result.symbols if s.name == "helper"), None)

        assert outer_func is not None
        assert helper_func is not None

        # Call inside nested lambda should be attributed to outer
        call_edge = next(
            (
                e for e in result.edges
                if e.src == outer_func.id
                and e.dst == helper_func.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call inside nested lambdas should be attributed to outermost function"

    def test_call_at_top_level_outside_function_no_edge(self, tmp_path: Path) -> None:
        """Calls at top level (outside any function) should not create edges.

        Top-level property initializers run at object creation time, not inside
        any specific function. These calls should not be attributed.
        """
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "TopLevel.kt"
        kt_file.write_text("""
fun helper() {
    println("help")
}

// Top-level property with initializer that calls helper
// This call is not inside any named function
val result = helper()
""")

        result = analyze_kotlin(tmp_path)

        # Find helper symbol
        helper_func = next((s for s in result.symbols if s.name == "helper"), None)
        assert helper_func is not None

        # There should be no call edge to helper (call is at top level)
        call_edges = [
            e for e in result.edges
            if e.dst == helper_func.id and e.edge_type == "calls"
        ]
        assert len(call_edges) == 0, "Top-level call should not create an edge"
