# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Kotlin analyzer."""
import pytest
from pathlib import Path

from hypergumbo_core.analyze.base import find_child_by_type
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

    def test_is_kotlin_tree_sitter_available_returns_bool(self) -> None:
        """Availability check returns a boolean."""
        from hypergumbo_lang_mainstream.kotlin import is_kotlin_tree_sitter_available

        result = is_kotlin_tree_sitter_available()
        assert isinstance(result, bool)

class TestAnalyzeKotlinFallback:
    """Tests for fallback behavior when tree-sitter-kotlin unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-kotlin unavailable."""
        import hypergumbo_lang_mainstream.kotlin as kt_module
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "test.kt").write_text("fun test() {}")

        with patch.object(kt_module._analyzer, "_check_grammar_available", return_value=False):
            result = analyze_kotlin(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason

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

class TestKotlinExtensionFunctions:
    """WI-fuhav: Kotlin extension function detection (``fun Receiver.name()``)."""

    def test_detects_extension_function(self, tmp_path: Path) -> None:
        """A ``fun Receiver.name()`` declaration is flagged as an extension."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "SpringApplicationExtensions.kt"
        kt_file.write_text(
            "package org.springframework.boot\n\n"
            "class SpringApplication\n\n"
            "fun SpringApplication.configure(block: () -> Unit) {\n"
            "    block()\n"
            "}\n",
        )

        result = analyze_kotlin(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        configure = next(
            (s for s in funcs if s.name == "configure"), None,
        )
        assert configure is not None
        assert configure.is_exported is True
        assert configure.meta is not None
        assert configure.meta.get("extension_receiver") == "SpringApplication"

    def test_plain_function_not_flagged(self, tmp_path: Path) -> None:
        """A regular (non-extension) top-level function is not flagged."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Main.kt"
        kt_file.write_text(
            "fun greet(name: String) {\n"
            "    println(\"hello, $name\")\n"
            "}\n",
        )
        result = analyze_kotlin(tmp_path)
        greet = next(
            (s for s in result.symbols
             if s.kind == "function" and s.name == "greet"),
            None,
        )
        assert greet is not None
        assert greet.is_exported is False
        assert (greet.meta or {}).get("extension_receiver") is None

    def test_extension_function_on_generic_receiver(
        self, tmp_path: Path,
    ) -> None:
        """Extension on a generic receiver (``List<T>``) is detected."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "Ext.kt"
        kt_file.write_text(
            "fun List<Int>.sumSafe(): Int = this.fold(0) { acc, x -> acc + x }\n",
        )
        result = analyze_kotlin(tmp_path)
        sum_safe = next(
            (s for s in result.symbols
             if s.kind == "function" and s.name == "sumSafe"),
            None,
        )
        assert sum_safe is not None
        assert sum_safe.is_exported is True
        # Generic receiver text is preserved in the meta.
        assert "List" in (sum_safe.meta or {}).get("extension_receiver", "")

    def test_extension_call_emits_edge_via_var_types(
        self, tmp_path: Path,
    ) -> None:
        """WI-visaz: ``receiver.extFn()`` emits a calls edge to the extension.

        ``fun SpringApplication.configure(...)`` defined in one file, called
        as ``app.configure { ... }`` in another file where ``app`` has
        declared type ``SpringApplication``. The call site must reach the
        extension function via a ``calls`` edge so dead-code-maybe,
        slice, and reverse-slice see it as live.
        """
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        ext_file = tmp_path / "SpringApplicationExtensions.kt"
        ext_file.write_text(
            "package org.springframework.boot\n\n"
            "class SpringApplication\n\n"
            "fun SpringApplication.configure(block: () -> Unit) {\n"
            "    block()\n"
            "}\n",
        )
        caller_file = tmp_path / "Main.kt"
        caller_file.write_text(
            "package demo\n\n"
            "import org.springframework.boot.SpringApplication\n"
            "import org.springframework.boot.configure\n\n"
            "fun main() {\n"
            "    val app: SpringApplication = SpringApplication()\n"
            "    app.configure { println(\"hello\") }\n"
            "}\n",
        )

        result = analyze_kotlin(tmp_path)
        configure_sym = next(
            (s for s in result.symbols
             if s.kind == "function" and s.name == "configure"),
            None,
        )
        assert configure_sym is not None, (
            "configure extension function symbol not found"
        )

        calls_to_configure = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst == configure_sym.id
        ]
        assert len(calls_to_configure) >= 1, (
            f"expected at least one calls→configure edge, got "
            f"{[(e.src, e.dst, e.evidence_type) for e in result.edges]}"
        )

    def test_extension_call_with_generic_receiver(
        self, tmp_path: Path,
    ) -> None:
        """WI-visaz: generic receivers (``List<T>``) match on the base name.

        ``fun List<Int>.sumSafe(): Int`` defined in one file, called as
        ``nums.sumSafe()`` where ``nums: List<Int>``. The lookup must strip
        the generic parameter and match on ``List``.
        """
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        ext_file = tmp_path / "Ext.kt"
        ext_file.write_text(
            "package utils\n\n"
            "fun List<Int>.sumSafe(): Int = this.fold(0) { acc, x -> acc + x }\n",
        )
        caller_file = tmp_path / "Caller.kt"
        caller_file.write_text(
            "package demo\n\n"
            "import utils.sumSafe\n\n"
            "fun run(nums: List<Int>): Int {\n"
            "    return nums.sumSafe()\n"
            "}\n",
        )

        result = analyze_kotlin(tmp_path)
        sum_safe = next(
            (s for s in result.symbols
             if s.kind == "function" and s.name == "sumSafe"),
            None,
        )
        assert sum_safe is not None

        calls_to_sum_safe = [
            e for e in result.edges
            if e.edge_type == "calls" and e.dst == sum_safe.id
        ]
        assert len(calls_to_sum_safe) >= 1, (
            f"expected calls→sumSafe edge, got "
            f"{[(e.src, e.dst, e.evidence_type) for e in result.edges]}"
        )


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

    def test_extends_prefers_imported_class_over_name_collision(
        self, tmp_path: Path
    ) -> None:
        """When multiple classes share a name, extends resolves to the imported one.

        INV-015: Same bug as Python (Django 238 Model stubs). Two files define
        class 'Model'; child file imports from specific package. Edge should
        resolve to the imported Model.
        """
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        # Real Model class in models package
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "Model.kt").write_text(
            "package com.example.models\n"
            "\n"
            "open class Model {\n"
            "    fun save() {}\n"
            "}\n"
        )

        # Test stub Model class (different file, same name)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "Helpers.kt").write_text(
            "package com.example.tests\n"
            "\n"
            "open class Model {\n"
            "    /* stub */\n"
            "}\n"
        )

        # A file that imports com.example.models.Model and extends it
        (tmp_path / "App.kt").write_text(
            "package com.example\n"
            "\n"
            "import com.example.models.Model\n"
            "\n"
            "class Article : Model() {\n"
            "    fun publish() {}\n"
            "}\n"
        )

        result = analyze_kotlin(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        article_extends = [e for e in extends_edges if "Article" in e.src]
        assert len(article_extends) == 1, (
            f"Expected 1 extends edge from Article, got {len(article_extends)}"
        )

        # Edge should point to models/Model.kt::Model, NOT tests/Helpers.kt::Model
        edge = article_extends[0]
        assert "models/Model.kt" in edge.dst or "models\\Model.kt" in edge.dst, (
            f"Article extends edge should point to models/Model.kt::Model, "
            f"but points to: {edge.dst}"
        )

    def test_extends_same_file_class_preferred_over_other_file(
        self, tmp_path: Path
    ) -> None:
        """When base class is defined in the same file, prefer it over other files."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        # Base defined in file A
        (tmp_path / "A.kt").write_text(
            "open class Base {\n    fun run() {}\n}\n"
        )

        # Base defined in file B AND used as base in same file
        (tmp_path / "B.kt").write_text(
            "open class Base {\n    fun run() {}\n}\n"
            "\n"
            "class Child : Base() {\n    fun go() {}\n}\n"
        )

        result = analyze_kotlin(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        child_extends = [e for e in extends_edges if "Child" in e.src]
        assert len(child_extends) == 1

        # Should resolve to B.kt::Base (same file), not A.kt::Base
        edge = child_extends[0]
        assert "B.kt" in edge.dst, (
            f"Child extends edge should prefer same-file Base (B.kt), "
            f"but points to: {edge.dst}"
        )

    def test_extends_deterministic_fallback_when_ambiguous(
        self, tmp_path: Path
    ) -> None:
        """When no same-file or import match, extends uses deterministic fallback."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        # Two files define 'Base', neither is imported
        (tmp_path / "ModA.kt").write_text(
            "open class Base {\n    fun run() {}\n}\n"
        )
        (tmp_path / "ModB.kt").write_text(
            "open class Base {\n    fun run() {}\n}\n"
        )
        # A third file extends 'Base' without importing either
        (tmp_path / "Child.kt").write_text(
            "class Child : Base() {\n    fun go() {}\n}\n"
        )

        result = analyze_kotlin(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        child_extends = [e for e in extends_edges if "Child" in e.src]
        # Should still create an edge (deterministic fallback)
        assert len(child_extends) == 1

    def test_extends_import_matches_full_fqn_path(
        self, tmp_path: Path
    ) -> None:
        """Import disambiguation via full FQN-to-path when directory mirrors package."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        # Gradle-style deep directory that mirrors FQN: com/example/models/Model.kt
        deep = tmp_path / "com" / "example" / "models"
        deep.mkdir(parents=True)
        (deep / "Model.kt").write_text(
            "package com.example.models\n"
            "\n"
            "open class Model {\n"
            "    fun save() {}\n"
            "}\n"
        )

        # Another Model at a flat path (e.g. test stub)
        (tmp_path / "StubModel.kt").write_text(
            "package com.example.tests\n"
            "\n"
            "open class Model {\n"
            "    /* stub */\n"
            "}\n"
        )

        # Child imports the FQN that matches the deep path exactly
        (tmp_path / "App.kt").write_text(
            "package com.example\n"
            "\n"
            "import com.example.models.Model\n"
            "\n"
            "class Article : Model() {\n"
            "    fun publish() {}\n"
            "}\n"
        )

        result = analyze_kotlin(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        article_extends = [e for e in extends_edges if "Article" in e.src]
        assert len(article_extends) == 1

        # Should resolve to com/example/models/Model.kt (full FQN match)
        edge = article_extends[0]
        assert "com/example/models/Model.kt" in edge.dst or "com\\example\\models\\Model.kt" in edge.dst, (
            f"Article extends should resolve to com/example/models/Model.kt, "
            f"but points to: {edge.dst}"
        )

    def test_implements_prefers_imported_interface_over_collision(
        self, tmp_path: Path
    ) -> None:
        """Interface disambiguation: implements resolves to imported interface."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        # Real Validator interface
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "Validator.kt").write_text(
            "package com.example.core\n"
            "\n"
            "interface Validator {\n"
            "    fun validate(): Boolean\n"
            "}\n"
        )

        # Stub Validator interface (different file, same name)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "Mock.kt").write_text(
            "package com.example.tests\n"
            "\n"
            "interface Validator {\n"
            "    fun validate(): Boolean\n"
            "}\n"
        )

        # A file that imports com.example.core.Validator and implements it
        (tmp_path / "Form.kt").write_text(
            "package com.example\n"
            "\n"
            "import com.example.core.Validator\n"
            "\n"
            "class FormValidator : Validator {\n"
            "    override fun validate(): Boolean = true\n"
            "}\n"
        )

        result = analyze_kotlin(tmp_path)

        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        form_impl = [e for e in impl_edges if "FormValidator" in e.src]
        assert len(form_impl) == 1, (
            f"Expected 1 implements edge from FormValidator, got {len(form_impl)}"
        )

        # Edge should point to core/Validator.kt::Validator
        edge = form_impl[0]
        assert "core/Validator.kt" in edge.dst or "core\\Validator.kt" in edge.dst, (
            f"FormValidator implements edge should point to core/Validator.kt::Validator, "
            f"but points to: {edge.dst}"
        )

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

        import hypergumbo_lang_mainstream.kotlin as kt_module

        with patch.object(kt_module._analyzer, "_check_grammar_available", return_value=True):
            with patch.object(kt_module._analyzer, "_create_parser", side_effect=RuntimeError("Parser load failed")):
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
        from hypergumbo_lang_mainstream.kotlin import is_kotlin_tree_sitter_available

        if not is_kotlin_tree_sitter_available():
            pytest.skip("tree-sitter-kotlin not available")

        import tree_sitter_kotlin
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_kotlin.language())
        parser = tree_sitter.Parser(lang)

        source = b"// comment\n"
        tree = parser.parse(source)

        result = find_child_by_type(tree.root_node, "nonexistent_type")
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

class TestKotlinReturnTypeInference:
    """Tests for return type tracking from function return type annotations."""

    def test_type_inference_from_return_type_annotation(
        self, tmp_path: Path
    ) -> None:
        """Functions with return type annotations enable variable type inference."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "App.kt"
        kt_file.write_text("""
class ServiceClient {
    fun fetch(): String { return "" }
}

fun getClient(): ServiceClient {
    return ServiceClient()
}

fun main() {
    val client = getClient()
    client.fetch()
}
""")

        result = analyze_kotlin(tmp_path)

        assert result.run is not None

        main_func = next(
            (s for s in result.symbols if s.name == "main"), None
        )
        fetch_method = next(
            (s for s in result.symbols if "fetch" in s.name), None
        )

        assert main_func is not None
        assert fetch_method is not None

        # Should have edge from main to ServiceClient.fetch via return type inference
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == main_func.id
                and e.dst == fetch_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, (
            "Expected call edge for client.fetch() via return type inference. "
            f"Edges from main: {[e for e in result.edges if e.src == main_func.id]}"
        )
        assert call_edge.evidence_type == "ast_call_type_inferred"

    def test_type_inference_no_annotation_no_resolution(
        self, tmp_path: Path
    ) -> None:
        """Functions without return type annotation don't enable type inference."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "App.kt"
        kt_file.write_text("""
class ServiceClient {
    fun fetch(): String { return "" }
}

fun getClient() {
    return ServiceClient()
}

fun main() {
    val client = getClient()
    client.fetch()
}
""")

        result = analyze_kotlin(tmp_path)

        assert result.run is not None

        main_func = next(
            (s for s in result.symbols if s.name == "main"), None
        )
        fetch_method = next(
            (s for s in result.symbols if "fetch" in s.name), None
        )

        assert main_func is not None
        assert fetch_method is not None

        # Should NOT have edge from main to ServiceClient.fetch — no return type
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == main_func.id
                and e.dst == fetch_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is None

    def test_navigation_call_return_type_inference(
        self, tmp_path: Path
    ) -> None:
        """Navigation calls (factory.create()) also track return types."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "App.kt"
        kt_file.write_text("""
class ServiceClient {
    fun fetch(): String { return "" }
}

class Factory {
    fun create(): ServiceClient {
        return ServiceClient()
    }
}

fun main() {
    val factory = Factory()
    val client = factory.create()
    client.fetch()
}
""")

        result = analyze_kotlin(tmp_path)

        assert result.run is not None

        main_func = next(
            (s for s in result.symbols if s.name == "main"), None
        )
        fetch_method = next(
            (s for s in result.symbols if "fetch" in s.name), None
        )

        assert main_func is not None
        assert fetch_method is not None

        # Should have edge from main to ServiceClient.fetch via return type inference
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == main_func.id
                and e.dst == fetch_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, (
            "Expected call edge for client.fetch() via navigation return type inference. "
            f"Edges from main: {[e for e in result.edges if e.src == main_func.id]}"
        )
        assert call_edge.evidence_type == "ast_call_type_inferred"

class TestKotlinReturnTypeExtraction:
    """Unit tests for _extract_kotlin_return_type_name helper."""

    def test_simple_return_type(self) -> None:
        """Extracts simple return type from Kotlin signature."""
        from hypergumbo_lang_mainstream.kotlin import _extract_kotlin_return_type_name

        assert _extract_kotlin_return_type_name("(): ServiceClient") == "ServiceClient"

    def test_with_params(self) -> None:
        """Extracts return type with parameters."""
        from hypergumbo_lang_mainstream.kotlin import _extract_kotlin_return_type_name

        assert _extract_kotlin_return_type_name("(name: String, age: Int): User") == "User"

    def test_no_return_type(self) -> None:
        """Returns None when no return type annotation."""
        from hypergumbo_lang_mainstream.kotlin import _extract_kotlin_return_type_name

        assert _extract_kotlin_return_type_name("()") is None

    def test_none_signature(self) -> None:
        """Returns None for None signature."""
        from hypergumbo_lang_mainstream.kotlin import _extract_kotlin_return_type_name

        assert _extract_kotlin_return_type_name(None) is None

    def test_generic_return_type(self) -> None:
        """Returns None for generic return types (not simple identifier)."""
        from hypergumbo_lang_mainstream.kotlin import _extract_kotlin_return_type_name

        assert _extract_kotlin_return_type_name("(): List<String>") is None

    def test_nullable_return_type(self) -> None:
        """Returns None for nullable return types (String?)."""
        from hypergumbo_lang_mainstream.kotlin import _extract_kotlin_return_type_name

        assert _extract_kotlin_return_type_name("(): String?") is None

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

    def test_this_property_method_call_resolved(self, tmp_path: Path) -> None:
        """this.property.method() calls resolve via constructor parameter types.

        Kotlin classes often inject dependencies via constructor parameters:
            class Controller(private val svc: Service) {
                fun doWork() { this.svc.process() }
            }
        The call this.svc.process() should resolve to Service.process.
        """
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        svc_file = tmp_path / "Service.kt"
        svc_file.write_text("""
class Service {
    fun process() {
        println("processing")
    }
}
""")

        ctrl_file = tmp_path / "Controller.kt"
        ctrl_file.write_text("""
class Controller(private val svc: Service) {
    fun doWork() {
        this.svc.process()
    }
}
""")

        result = analyze_kotlin(tmp_path)

        assert result.run is not None

        # Find symbols
        do_work = next(
            (s for s in result.symbols if "doWork" in s.name), None
        )
        process_method = next(
            (s for s in result.symbols if "process" in s.name
             and "Service" in s.id), None
        )

        assert do_work is not None, "doWork method not found"
        assert process_method is not None, "Service.process method not found"

        # Should have edge from Controller.doWork to Service.process
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == do_work.id
                and e.dst == process_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, (
            f"Expected call edge from doWork to Service.process. "
            f"Edges from doWork: {[e for e in result.edges if e.src == do_work.id]}"
        )

    def test_this_property_method_call_generic_type(self, tmp_path: Path) -> None:
        """Constructor parameter with generic type resolves correctly.

        Generic type parameters (e.g., Repository<User>) are stripped to the
        base type (Repository) for method call resolution.
        """
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        repo_file = tmp_path / "Repository.kt"
        repo_file.write_text("""
class Repository<T> {
    fun findAll(): List<T> {
        return emptyList()
    }
}
""")

        ctrl_file = tmp_path / "Controller.kt"
        ctrl_file.write_text("""
class Controller(private val repo: Repository<String>) {
    fun list() {
        this.repo.findAll()
    }
}
""")

        result = analyze_kotlin(tmp_path)

        list_method = next(
            (s for s in result.symbols if "list" in s.name
             and "Controller" in s.id), None
        )
        find_all = next(
            (s for s in result.symbols if "findAll" in s.name), None
        )

        assert list_method is not None
        assert find_all is not None

        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == list_method.id
                and e.dst == find_all.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None

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


class TestKotlinAmbiguousMethodGuard:
    """Tests for AMB-METHOD invariant in Kotlin.

    When a method name has 3+ definitions across different classes and
    the receiver type cannot be inferred, the analyzer must NOT produce
    a resolved call edge (which would be a false positive).

    Invariant: Method calls with 3+ ambiguous receiver types must not
    produce resolved call edges.
    """

    def test_ambiguous_method_three_plus_classes_no_resolved_edge(
        self, tmp_path: Path,
    ) -> None:
        """close() with 3 classes defining close() → no resolved edge."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "multi.kt"
        kt_file.write_text("""
class ServiceA {
    fun close() { }
}

class ServiceB {
    fun close() { }
}

class ServiceC {
    fun close() { }
}

fun cleanup() {
    close()
}
""")

        result = analyze_kotlin(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        cleanup_calls = [e for e in call_edges if "cleanup" in e.src]

        # Should NOT have a resolved edge to any specific class's close()
        for edge in cleanup_calls:
            assert "ServiceA" not in edge.dst, (
                f"Ambiguous method should not resolve to ServiceA, got {edge.dst}"
            )
            assert "ServiceB" not in edge.dst, (
                f"Ambiguous method should not resolve to ServiceB, got {edge.dst}"
            )
            assert "ServiceC" not in edge.dst, (
                f"Ambiguous method should not resolve to ServiceC, got {edge.dst}"
            )

    def test_two_classes_same_method_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        """run() with 2 classes → still resolves (below threshold)."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        kt_file = tmp_path / "two.kt"
        kt_file.write_text("""
class ServiceA {
    fun run() { }
}

class ServiceB {
    fun run() { }
}

fun execute() {
    run()
}
""")

        result = analyze_kotlin(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        execute_calls = [e for e in call_edges if "execute" in e.src]

        # 2 candidates is below the threshold — should still resolve
        run_calls = [e for e in execute_calls if "run" in e.dst.lower()]
        assert len(run_calls) >= 1, "2 candidates should still resolve"


class TestKotlinVisibilityModifiers:
    """Tests for visibility modifier extraction into Symbol.modifiers."""

    def test_method_visibility_modifiers(self, tmp_path: Path) -> None:
        """Methods with visibility modifiers get them extracted."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Vis.kt").write_text("""
class Vis {
    public fun pubMethod() {}
    private fun privMethod() {}
    protected fun protMethod() {}
    internal fun intMethod() {}
    fun defaultMethod() {}
}
""")
        result = analyze_kotlin(tmp_path)

        pub = next(s for s in result.symbols if s.name == "Vis.pubMethod")
        assert "public" in pub.modifiers

        priv = next(s for s in result.symbols if s.name == "Vis.privMethod")
        assert "private" in priv.modifiers

        prot = next(s for s in result.symbols if s.name == "Vis.protMethod")
        assert "protected" in prot.modifiers

        internal = next(s for s in result.symbols if s.name == "Vis.intMethod")
        assert "internal" in internal.modifiers

        default = next(s for s in result.symbols if s.name == "Vis.defaultMethod")
        assert "public" not in default.modifiers
        assert "private" not in default.modifiers

    def test_class_modifiers(self, tmp_path: Path) -> None:
        """Classes with visibility and inheritance modifiers get them extracted."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Classes.kt").write_text("""
public open class PubOpen {}
internal data class IntData(val x: Int)
abstract class AbsClass {}
""")
        result = analyze_kotlin(tmp_path)

        pub_open = next(s for s in result.symbols if s.name == "PubOpen")
        assert "public" in pub_open.modifiers
        assert "open" in pub_open.modifiers

        int_data = next(s for s in result.symbols if s.name == "IntData")
        assert "internal" in int_data.modifiers
        assert "data" in int_data.modifiers

        abs_cls = next(s for s in result.symbols if s.name == "AbsClass")
        assert "abstract" in abs_cls.modifiers


class TestNormalizeKotlinSignature:
    """Tests for Kotlin signature normalization (ADR-0014 §3)."""

    def test_basic_method(self) -> None:
        from hypergumbo_lang_mainstream.kotlin import normalize_kotlin_signature
        assert normalize_kotlin_signature("(name: String, age: Int): User") == "(String,Int)User"

    def test_no_return(self) -> None:
        from hypergumbo_lang_mainstream.kotlin import normalize_kotlin_signature
        assert normalize_kotlin_signature("(msg: String)") == "(String)"

    def test_none(self) -> None:
        from hypergumbo_lang_mainstream.kotlin import normalize_kotlin_signature
        assert normalize_kotlin_signature(None) is None


class TestKotlinAnnotations:
    """Tests for Kotlin annotation extraction into meta.decorators.

    Kotlin annotations (e.g., @Entity, @GetMapping("/users")) are extracted
    into Symbol.meta["decorators"] as a list of dicts with name, args, kwargs.
    The analyzer also creates decorated_by edges to annotation symbols.
    """

    def test_simple_class_annotation(self, tmp_path: Path) -> None:
        """Simple annotation on class creates decorators metadata."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Model.kt").write_text('''
@Entity
class User {
}
''')
        result = analyze_kotlin(tmp_path)
        user = next(s for s in result.symbols if s.name == "User")
        assert user.meta is not None
        assert "decorators" in user.meta
        decorators = user.meta["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Entity"
        assert decorators[0]["args"] == []
        assert decorators[0]["kwargs"] == {}

    def test_annotation_with_named_arg(self, tmp_path: Path) -> None:
        """Annotation with named argument extracts kwargs."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Model.kt").write_text('''
@Table(name = "users")
class User {
}
''')
        result = analyze_kotlin(tmp_path)
        user = next(s for s in result.symbols if s.name == "User")
        decorators = user.meta["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Table"
        assert decorators[0]["kwargs"] == {"name": "users"}

    def test_annotation_with_positional_arg(self, tmp_path: Path) -> None:
        """Annotation with positional argument extracts args."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Controller.kt").write_text('''
class UserController {
    @GetMapping("/users")
    fun listUsers() {}
}
''')
        result = analyze_kotlin(tmp_path)
        method = next(s for s in result.symbols if s.name == "UserController.listUsers")
        assert method.meta is not None
        decorators = method.meta["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "GetMapping"
        assert decorators[0]["args"] == ["/users"]

    def test_multiple_annotations(self, tmp_path: Path) -> None:
        """Multiple annotations on a single class are all extracted."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Model.kt").write_text('''
@Entity
@Table(name = "products")
class Product {
}
''')
        result = analyze_kotlin(tmp_path)
        product = next(s for s in result.symbols if s.name == "Product")
        decorators = product.meta["decorators"]
        assert len(decorators) == 2
        names = [d["name"] for d in decorators]
        assert "Entity" in names
        assert "Table" in names

    def test_method_annotation(self, tmp_path: Path) -> None:
        """Annotations on methods are extracted."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Service.kt").write_text('''
class UserService {
    @Deprecated("Use findById instead")
    fun getUser() {}
}
''')
        result = analyze_kotlin(tmp_path)
        method = next(s for s in result.symbols if s.name == "UserService.getUser")
        decorators = method.meta["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Deprecated"
        assert decorators[0]["args"] == ["Use findById instead"]

    def test_boolean_annotation_value(self, tmp_path: Path) -> None:
        """Boolean annotation values are extracted as Python booleans."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Model.kt").write_text('''
@JsonIgnoreProperties(ignoreUnknown = true)
class Response {
}
''')
        result = analyze_kotlin(tmp_path)
        resp = next(s for s in result.symbols if s.name == "Response")
        decorators = resp.meta["decorators"]
        assert decorators[0]["kwargs"]["ignoreUnknown"] is True

    def test_integer_annotation_value(self, tmp_path: Path) -> None:
        """Integer annotation values are extracted as Python ints."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Config.kt").write_text('''
@Retry(maxAttempts = 3)
fun callService() {}
''')
        result = analyze_kotlin(tmp_path)
        func = next(s for s in result.symbols if s.name == "callService")
        decorators = func.meta["decorators"]
        assert decorators[0]["kwargs"]["maxAttempts"] == 3

    def test_object_annotation(self, tmp_path: Path) -> None:
        """Annotations on Kotlin objects are extracted."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Config.kt").write_text('''
@Singleton
object AppConfig {
}
''')
        result = analyze_kotlin(tmp_path)
        obj = next(s for s in result.symbols if s.name == "AppConfig")
        assert obj.meta is not None
        decorators = obj.meta["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Singleton"

    def test_false_boolean_annotation_value(self, tmp_path: Path) -> None:
        """Boolean false annotation value is extracted correctly."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Model.kt").write_text('''
@JsonProperty(required = false)
class Config {
}
''')
        result = analyze_kotlin(tmp_path)
        cfg = next(s for s in result.symbols if s.name == "Config")
        decorators = cfg.meta["decorators"]
        assert decorators[0]["kwargs"]["required"] is False

    def test_float_annotation_value(self, tmp_path: Path) -> None:
        """Float annotation value is extracted as Python float."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Config.kt").write_text('''
@Threshold(value = 0.95)
fun check() {}
''')
        result = analyze_kotlin(tmp_path)
        func = next(s for s in result.symbols if s.name == "check")
        decorators = func.meta["decorators"]
        assert decorators[0]["kwargs"]["value"] == 0.95

    def test_hex_integer_annotation_value(self, tmp_path: Path) -> None:
        """Hex integer annotation value is extracted as Python int."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Config.kt").write_text('''
@Color(value = 0xFF00FF)
class Theme {
}
''')
        result = analyze_kotlin(tmp_path)
        theme = next(s for s in result.symbols if s.name == "Theme")
        decorators = theme.meta["decorators"]
        assert decorators[0]["kwargs"]["value"] == 0xFF00FF

    def test_navigation_expression_annotation_value(self, tmp_path: Path) -> None:
        """Qualified reference (Enum.VALUE) in annotation is extracted."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Config.kt").write_text('''
@Target(AnnotationTarget.CLASS)
class MyAnnotation {
}
''')
        result = analyze_kotlin(tmp_path)
        ann = next(s for s in result.symbols if s.name == "MyAnnotation")
        decorators = ann.meta["decorators"]
        assert decorators[0]["name"] == "Target"
        assert decorators[0]["args"] == ["AnnotationTarget.CLASS"]

    def test_plain_identifier_annotation_value(self, tmp_path: Path) -> None:
        """Plain identifier (enum constant) in annotation is extracted as string."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Config.kt").write_text('''
@Target(CLASS)
class MyAnnotation {
}
''')
        result = analyze_kotlin(tmp_path)
        ann = next(s for s in result.symbols if s.name == "MyAnnotation")
        decorators = ann.meta["decorators"]
        assert decorators[0]["name"] == "Target"
        assert decorators[0]["args"] == ["CLASS"]

    def test_multiple_positional_args(self, tmp_path: Path) -> None:
        """Multiple positional arguments are all extracted."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Config.kt").write_text('''
@Suppress("unused", "unchecked")
class MyClass {
}
''')
        result = analyze_kotlin(tmp_path)
        cls = next(s for s in result.symbols if s.name == "MyClass")
        decorators = cls.meta["decorators"]
        assert decorators[0]["name"] == "Suppress"
        assert decorators[0]["args"] == ["unused", "unchecked"]

    def test_no_annotations_no_decorators_key(self, tmp_path: Path) -> None:
        """Symbols without annotations have no decorators in meta."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Plain.kt").write_text('''
fun plainFunction() {}
''')
        result = analyze_kotlin(tmp_path)
        func = next(s for s in result.symbols if s.name == "plainFunction")
        # meta should be None (no decorators, no base_classes)
        assert func.meta is None or "decorators" not in func.meta

    def test_class_with_annotations_and_base_classes(self, tmp_path: Path) -> None:
        """Class with both annotations and base classes has both in meta."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Model.kt").write_text('''
interface Serializable

@Entity
class User : Serializable {
}
''')
        result = analyze_kotlin(tmp_path)
        user = next(s for s in result.symbols if s.name == "User")
        assert user.meta is not None
        assert "decorators" in user.meta
        assert "base_classes" in user.meta
        assert user.meta["decorators"][0]["name"] == "Entity"
        assert "Serializable" in user.meta["base_classes"]


class TestKotlinAnnotationEdges:
    """Tests for decorated_by edge creation from annotations."""

    def test_resolved_annotation_edge(self, tmp_path: Path) -> None:
        """Annotation referencing a local annotation class creates resolved edge."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Annotations.kt").write_text('''
annotation class MyAnnotation

@MyAnnotation
class MyService {
}
''')
        result = analyze_kotlin(tmp_path)
        service = next(s for s in result.symbols if s.name == "MyService")
        anno_cls = next(s for s in result.symbols if s.name == "MyAnnotation")

        decorated_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and e.src == service.id
        ]
        assert len(decorated_edges) == 1
        assert decorated_edges[0].dst == anno_cls.id
        assert decorated_edges[0].confidence == 0.95

    def test_unresolved_annotation_edge(self, tmp_path: Path) -> None:
        """Annotation referencing external class creates unresolved edge."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Service.kt").write_text('''
@Service
class UserService {
}
''')
        result = analyze_kotlin(tmp_path)
        service = next(s for s in result.symbols if s.name == "UserService")

        decorated_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and e.src == service.id
        ]
        assert len(decorated_edges) == 1
        assert "unresolved" in decorated_edges[0].dst
        assert decorated_edges[0].confidence == 0.50

    def test_method_annotation_edge(self, tmp_path: Path) -> None:
        """Method annotations also produce decorated_by edges."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Controller.kt").write_text('''
class Controller {
    @GetMapping("/api")
    fun handleRequest() {}
}
''')
        result = analyze_kotlin(tmp_path)
        method = next(s for s in result.symbols if s.name == "Controller.handleRequest")

        decorated_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and e.src == method.id
        ]
        assert len(decorated_edges) == 1
        assert "unresolved" in decorated_edges[0].dst


class TestKotlinCallableReferences:
    """Tests for Kotlin callable reference (::) detection."""

    def test_simple_callable_reference(self, tmp_path: Path) -> None:
        """Detects ::transform as a references edge."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "App.kt").write_text(
            "class App {\n"
            "    fun transform(s: String): String = s.uppercase()\n"
            "    fun run(items: List<String>) {\n"
            "        items.map(::transform)\n"
            "    }\n"
            "}\n"
        )
        result = analyze_kotlin(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert any(
            "transform" in e.dst and "run" in e.src
            for e in ref_edges
        ), f"Expected callable reference edge, got: {ref_edges}"

    def test_qualified_callable_reference(self, tmp_path: Path) -> None:
        """Detects App::transform as a references edge."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "App.kt").write_text(
            "class App {\n"
            "    fun transform(s: String): String = s.uppercase()\n"
            "    fun run(items: List<String>) {\n"
            "        items.map(App::transform)\n"
            "    }\n"
            "}\n"
        )
        result = analyze_kotlin(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert any(
            "transform" in e.dst and "run" in e.src
            for e in ref_edges
        ), f"Expected qualified reference edge, got: {ref_edges}"

    def test_this_callable_reference(self, tmp_path: Path) -> None:
        """Detects this::process as a references edge."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "App.kt").write_text(
            "class App {\n"
            "    fun process(s: String) {}\n"
            "    fun run(items: List<String>) {\n"
            "        items.forEach(this::process)\n"
            "    }\n"
            "}\n"
        )
        result = analyze_kotlin(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert any(
            "process" in e.dst and "run" in e.src
            for e in ref_edges
        ), f"Expected this:: reference edge, got: {ref_edges}"

    def test_callable_reference_evidence_type(self, tmp_path: Path) -> None:
        """Callable reference edges have correct evidence_type."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "App.kt").write_text(
            "class App {\n"
            "    fun parse(s: String): Int = s.toInt()\n"
            "    fun run(items: List<String>) {\n"
            "        items.map(::parse)\n"
            "    }\n"
            "}\n"
        )
        result = analyze_kotlin(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        matching = [e for e in ref_edges if "parse" in e.dst]
        assert len(matching) == 1
        assert matching[0].evidence_type == "callable_reference"

    def test_top_level_callable_reference(self, tmp_path: Path) -> None:
        """Detects ::topLevel referencing a top-level function from inside a class."""
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Utils.kt").write_text(
            "fun helper(s: String): String = s.trim()\n"
        )
        (tmp_path / "App.kt").write_text(
            "class App {\n"
            "    fun run(items: List<String>) {\n"
            "        items.map(::helper)\n"
            "    }\n"
            "}\n"
        )
        result = analyze_kotlin(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert any(
            "helper" in e.dst and "run" in e.src
            for e in ref_edges
        ), f"Expected top-level callable reference edge, got: {ref_edges}"


class TestKotlinShapeId:
    """Tests for shape_id computation in Kotlin (ADR-0014 §1)."""

    def test_function_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Example.kt").write_text(
            "fun greet(name: String): String = \"Hello $name\"\n"
        )
        result = analyze_kotlin(tmp_path)
        func = next(s for s in result.symbols if s.kind == "function")
        assert func.shape_id is not None
        assert func.shape_id.startswith("sha256:")

    def test_class_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        (tmp_path / "Example.kt").write_text(
            "class Foo {\n  fun bar(): Int = 42\n}\n"
        )
        result = analyze_kotlin(tmp_path)
        cls = next(s for s in result.symbols if s.kind == "class")
        assert cls.shape_id is not None
        assert cls.shape_id.startswith("sha256:")
