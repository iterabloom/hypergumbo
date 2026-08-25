# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Java analyzer."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

from hypergumbo_lang_mainstream import java as java_module


def _run_inherited_calls_pipeline(analyzer_result):
    """Run inheritance + inherited_calls linkers on top of analyzer output.

    WI-dukog (PR-3 of INV-nilud): the Java analyzer's Site-1 walk
    (bare / ``this.method()`` ancestor traversal at ``java.py:1437-1461``)
    was lifted into the ``inherited_calls`` Tier-2 linker
    (``_walk_single_then_interfaces`` walker). Tests that previously
    asserted ``ast_call_inherited`` edges directly from ``analyze_java``
    output now compose the linker stack to recover them. Mirrors the
    Ruby helper added in PR-2 (``test_ruby.py``).
    """
    from hypergumbo_core.linkers.inheritance import link_inheritance
    from hypergumbo_core.linkers.inherited_calls import link_inherited_calls
    from hypergumbo_core.linkers.registry import LinkerContext

    inh_ctx = LinkerContext(
        repo_root=Path("/"),
        symbols=analyzer_result.symbols,
        edges=analyzer_result.edges,
    )
    inh_result = link_inheritance(inh_ctx)
    combined = list(analyzer_result.edges) + list(inh_result.edges)
    call_ctx = LinkerContext(
        repo_root=Path("/"),
        symbols=analyzer_result.symbols,
        edges=combined,
    )
    call_result = link_inherited_calls(call_ctx)
    return combined + list(call_result.edges)


class TestFindJavaFiles:
    """Tests for Java file discovery."""

    def test_finds_java_files(self, tmp_path: Path) -> None:
        """Finds .java files."""
        from hypergumbo_lang_mainstream.java import find_java_files

        (tmp_path / "Main.java").write_text("public class Main {}")
        (tmp_path / "Utils.java").write_text("public class Utils {}")
        (tmp_path / "other.txt").write_text("not java")

        files = list(find_java_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".java" for f in files)


class TestJavaTreeSitterAvailability:
    """Tests for tree-sitter-java availability checking."""

    def test_is_java_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-java is available."""
        result = java_module.is_java_tree_sitter_available()
        assert result is True

    def test_is_java_tree_sitter_available_false(self) -> None:
        """Returns False when grammar is not available."""
        with patch.object(java_module._java_analyzer, "_check_grammar_available", return_value=False):
            assert java_module.is_java_tree_sitter_available() is False

    def test_is_java_tree_sitter_available_via_analyzer(self) -> None:
        """Availability check delegates to TreeSitterAnalyzer._check_grammar_available."""
        assert java_module.is_java_tree_sitter_available() == java_module._java_analyzer._check_grammar_available()


class TestAnalyzeJavaFallback:
    """Tests for fallback behavior when tree-sitter-java unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-java unavailable."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Test.java").write_text("public class Test {}")

        with patch.object(java_module._java_analyzer, "_check_grammar_available", return_value=False):
            result = analyze_java(tmp_path)

        assert result.skipped is True
        assert "not available" in result.skip_reason


class TestJavaClassExtraction:
    """Tests for extracting Java classes."""

    def test_extracts_class(self, tmp_path: Path) -> None:
        """Extracts Java class declarations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Person.java"
        java_file.write_text("""
public class Person {
    private String name;

    public Person(String name) {
        this.name = name;
    }

    public String getName() {
        return name;
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1
        names = [s.name for s in result.symbols]
        assert "Person" in names

    def test_extracts_interface(self, tmp_path: Path) -> None:
        """Extracts Java interface declarations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Runnable.java"
        java_file.write_text("""
public interface Runnable {
    void run();
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Runnable" in names
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert len(interfaces) >= 1

    def test_extracts_enum(self, tmp_path: Path) -> None:
        """Extracts Java enum declarations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Color.java"
        java_file.write_text("""
public enum Color {
    RED, GREEN, BLUE
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Color" in names
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert len(enums) >= 1

    def test_extracts_methods(self, tmp_path: Path) -> None:
        """Extracts Java method declarations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Calculator.java"
        java_file.write_text("""
public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }

    public int subtract(int a, int b) {
        return a - b;
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Calculator" in names
        # Methods should be named with class prefix
        assert "Calculator.add" in names or "add" in names
        assert "Calculator.subtract" in names or "subtract" in names

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handles Java file with no classes."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Empty.java"
        java_file.write_text("// Just a comment")

        result = analyze_java(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 1
        assert result.skipped is False


class TestJavaCallEdges:
    """Tests for Java method call detection."""

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between Java methods."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Service.java"
        java_file.write_text("""
public class Service {
    public int helper() {
        return 42;
    }

    public int process() {
        return helper();
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        # Should have a call edge from process to helper
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_extracts_this_method_calls(self, tmp_path: Path) -> None:
        """Extracts this.method() calls."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Service.java"
        java_file.write_text("""
public class Service {
    public int helper() {
        return 42;
    }

    public int process() {
        return this.helper();
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_extracts_field_access_method_calls(self, tmp_path: Path) -> None:
        """Extracts method calls on field access receivers (e.g., this.field.method()).

        Tree-sitter represents `this.owners.findById()` as a method_invocation
        where the object child is a `field_access` node, not an `identifier`.
        The analyzer must use child_by_field_name("name") to extract the method
        name and child_by_field_name("object") for the receiver.

        Without this fix, the identifier scanning loop misidentifies the method
        name: it finds only one identifier (the method name) from child[2] and
        treats it as the receiver, then sets method_name=receiver_name with no
        actual method name to resolve.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Controller.java"
        java_file.write_text("""
public class Controller {
    private Service svc;

    public void doWork() {
        this.svc.process();
    }
}

class Service {
    public void process() {}
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        assert result.run is not None
        call_edges = [
            e for e in all_edges
            if e.edge_type == "calls" and e.is_resolved
        ]
        do_work = next(
            (s for s in result.symbols if s.name == "Controller.doWork"), None
        )
        assert do_work is not None

        # this.svc.process() should produce a call edge from doWork to
        # Service.process. The receiver is a field_access node ("this.svc"),
        # and the method name is "process". WI-puvil PR-5: resolved by the
        # inherited_calls linker via Site-2 (receiver_type_hint=Service).
        do_work_calls = [e for e in call_edges if e.src == do_work.id]
        assert len(do_work_calls) >= 1, (
            f"Expected call edges from doWork for this.svc.process(), "
            f"got {len(do_work_calls)}. "
            "field_access receiver likely not handled."
        )
        # The edge should target Service.process
        svc_process = next(
            (s for s in result.symbols if s.name == "Service.process"), None
        )
        assert svc_process is not None
        targets = {e.dst for e in do_work_calls}
        assert svc_process.id in targets, (
            f"Expected call to Service.process, got targets: {targets}"
        )

    def test_extracts_variable_dot_method_calls(self, tmp_path: Path) -> None:
        """Extracts calls like `variable.method()` where variable is a local var.

        This is the most common Java call pattern: `mav.addObject(owner)`.
        The tree-sitter node has an `identifier` as the `object` field and
        another `identifier` as the `name` field.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Service.java"
        java_file.write_text("""
public class Service {
    public void process() {
        Helper h = new Helper();
        h.doWork();
    }
}

class Helper {
    public void doWork() {}
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        calls = [e for e in result.edges if e.edge_type == "calls"]
        process_sym = next(
            (s for s in result.symbols if s.name == "Service.process"), None
        )
        assert process_sym is not None
        process_calls = [e for e in calls if e.src == process_sym.id]
        # h.doWork() should resolve via type inference (h is Helper)
        assert len(process_calls) >= 1, (
            f"Expected call edge from process to doWork via type inference, "
            f"got {len(process_calls)}."
        )


class TestJavaMonorepoDuplicateNames:
    """Tests for monorepo handling where multiple files define same class names."""

    def test_enclosing_method_found_for_all_duplicate_named_classes(self, tmp_path: Path) -> None:
        """All instances of a duplicate-named method must produce call edges.

        In monorepos where multiple modules define the same class name (e.g.,
        UserService in module_a and module_b), each service's methods must
        produce their own call edges.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        for d in ["module_a", "module_b", "module_c"]:
            (tmp_path / d).mkdir()
            (tmp_path / d / "UserService.java").write_text(
                "public class UserService {\n"
                "    public void save(String name) {}\n"
                "}\n"
            )
            (tmp_path / d / "UserController.java").write_text(
                "public class UserController {\n"
                "    private UserService svc;\n"
                "    public void create() {\n"
                "        svc.save(\"test\");\n"
                "    }\n"
                "}\n"
            )

        result = analyze_java(tmp_path)

        ctrl_creates = [s for s in result.symbols if s.name == "UserController.create"]
        assert len(ctrl_creates) == 3, (
            f"Expected 3 UserController.create, got {len(ctrl_creates)}"
        )

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        controllers_with_edges = set()
        for ctrl in ctrl_creates:
            ctrl_edges = [e for e in call_edges if e.src == ctrl.id]
            if ctrl_edges:
                controllers_with_edges.add(ctrl.path)

        assert len(controllers_with_edges) == 3, (
            f"Expected ALL 3 controllers to have call edges, "
            f"but only {len(controllers_with_edges)} do"
        )


class TestJavaInheritanceEdges:
    """Tests for Java inheritance edge detection."""

    def test_extends_prefers_imported_class_over_name_collision(
        self, tmp_path: Path
    ) -> None:
        """When multiple classes share a name, extends resolves to the imported one.

        INV-015: Same structural bug as Python/Kotlin. Two files define
        class 'Model'; child file imports the one from models package.
        Edge should resolve to the imported Model, not the test stub.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        # Real Model class in models package
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "Model.java").write_text(
            "package com.example.models;\n"
            "\n"
            "public class Model {\n"
            "    public void save() {}\n"
            "}\n"
        )

        # Test stub Model class (different file, same name)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "TestModel.java").write_text(
            "package com.example.tests;\n"
            "\n"
            "public class Model {\n"
            "    /* stub */\n"
            "}\n"
        )

        # A file that imports com.example.models.Model and extends it
        (tmp_path / "Article.java").write_text(
            "package com.example;\n"
            "\n"
            "import com.example.models.Model;\n"
            "\n"
            "public class Article extends Model {\n"
            "    public void publish() {}\n"
            "}\n"
        )

        result = analyze_java(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        article_extends = [e for e in extends_edges if "Article" in e.src]
        assert len(article_extends) == 1, (
            f"Expected 1 extends edge from Article, got {len(article_extends)}"
        )

        # Edge should point to models/Model.java::Model, NOT tests/TestModel.java::Model
        edge = article_extends[0]
        assert "models/Model.java" in edge.dst or "models\\Model.java" in edge.dst, (
            f"Article extends edge should point to models/Model.java::Model, "
            f"but points to: {edge.dst}"
        )

    def test_extends_same_file_class_preferred_over_other_file(
        self, tmp_path: Path
    ) -> None:
        """When base class is defined in the same file, prefer it over other files."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Base defined in file A
        (tmp_path / "A.java").write_text(
            "public class Base {\n    public void run() {}\n}\n"
        )

        # Base defined in file B AND used as base in same file
        (tmp_path / "B.java").write_text(
            "class Base {\n    public void run() {}\n}\n"
            "\n"
            "class Child extends Base {\n    public void go() {}\n}\n"
        )

        result = analyze_java(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        child_extends = [e for e in extends_edges if "Child" in e.src]
        assert len(child_extends) == 1

        # Should resolve to B.java::Base (same file), not A.java::Base
        edge = child_extends[0]
        assert "B.java" in edge.dst, (
            f"Child extends edge should prefer same-file Base (B.java), "
            f"but points to: {edge.dst}"
        )

    def test_extends_deterministic_fallback_when_ambiguous(
        self, tmp_path: Path
    ) -> None:
        """When no same-file or import match, extends uses deterministic fallback."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Two files define 'Base', neither is imported
        (tmp_path / "ModA.java").write_text(
            "public class Base {\n    public void run() {}\n}\n"
        )
        (tmp_path / "ModB.java").write_text(
            "public class Base {\n    public void run() {}\n}\n"
        )
        # A third file extends 'Base' without importing either
        (tmp_path / "Child.java").write_text(
            "public class Child extends Base {\n    public void go() {}\n}\n"
        )

        result = analyze_java(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        child_extends = [e for e in extends_edges if "Child" in e.src]
        # Should still create an edge (deterministic fallback)
        assert len(child_extends) == 1

    def test_implements_prefers_imported_interface_over_collision(
        self, tmp_path: Path
    ) -> None:
        """When multiple interfaces share a name, implements resolves to the imported one."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Real Serializable interface
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "Serializable.java").write_text(
            "package com.example.core;\n"
            "\n"
            "public interface Serializable {\n"
            "    String serialize();\n"
            "}\n"
        )

        # Test stub Serializable interface (different file, same name)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "TestInterfaces.java").write_text(
            "package com.example.tests;\n"
            "\n"
            "public interface Serializable {\n"
            "    /* stub */\n"
            "}\n"
        )

        # A file that imports com.example.core.Serializable and implements it
        (tmp_path / "User.java").write_text(
            "package com.example;\n"
            "\n"
            "import com.example.core.Serializable;\n"
            "\n"
            "public class User implements Serializable {\n"
            "    public String serialize() { return \"\"; }\n"
            "}\n"
        )

        result = analyze_java(tmp_path)

        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        user_impl = [e for e in impl_edges if "User" in e.src]
        assert len(user_impl) == 1, (
            f"Expected 1 implements edge from User, got {len(user_impl)}"
        )

        # Edge should point to core/Serializable.java, NOT tests/TestInterfaces.java
        edge = user_impl[0]
        assert "core/Serializable.java" in edge.dst or "core\\Serializable.java" in edge.dst, (
            f"User implements edge should point to core/Serializable.java, "
            f"but points to: {edge.dst}"
        )

    def test_extends_import_matches_full_fqn_path(
        self, tmp_path: Path
    ) -> None:
        """Import disambiguation via full FQN-to-path when directory mirrors package."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Directory mirrors Java package: com/example/models/Model.java
        (tmp_path / "com" / "example" / "models").mkdir(parents=True)
        (tmp_path / "com" / "example" / "models" / "Model.java").write_text(
            "package com.example.models;\n"
            "\n"
            "public class Model {\n"
            "    public void save() {}\n"
            "}\n"
        )

        # Test stub Model in a different package path
        (tmp_path / "com" / "example" / "tests").mkdir(parents=True)
        (tmp_path / "com" / "example" / "tests" / "Model.java").write_text(
            "package com.example.tests;\n"
            "\n"
            "public class Model {\n"
            "    /* stub */\n"
            "}\n"
        )

        # Child imports the real Model via FQN
        (tmp_path / "App.java").write_text(
            "import com.example.models.Model;\n"
            "\n"
            "public class App extends Model {\n"
            "    public void run() {}\n"
            "}\n"
        )

        result = analyze_java(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        app_extends = [e for e in extends_edges if "App" in e.src]
        assert len(app_extends) == 1

        # Should resolve to com/example/models/Model.java
        edge = app_extends[0]
        assert "models/Model.java" in edge.dst or "models\\Model.java" in edge.dst, (
            f"App extends edge should point to models/Model.java, "
            f"but points to: {edge.dst}"
        )

    def test_single_file_extends_fallback(self, tmp_path: Path) -> None:
        """Legacy _analyze_java_file path creates extends edges without multi-value lookup."""
        from hypergumbo_lang_mainstream.java import _analyze_java_file
        from hypergumbo_core.ir import AnalysisRun

        java_file = tmp_path / "Child.java"
        java_file.write_text(
            "class Base {\n    void run() {}\n}\n"
            "\n"
            "class Child extends Base {\n    void go() {}\n}\n"
        )

        run = AnalysisRun.create(pass_id="java", version="1.0")
        symbols, edges, success = _analyze_java_file(java_file, run)
        assert success

        extends_edges = [e for e in edges if e.edge_type == "extends"]
        assert len(extends_edges) == 1
        assert "Base" in extends_edges[0].dst

    def test_single_file_implements_fallback(self, tmp_path: Path) -> None:
        """Legacy _analyze_java_file path creates implements edges without multi-value lookup."""
        from hypergumbo_lang_mainstream.java import _analyze_java_file
        from hypergumbo_core.ir import AnalysisRun

        java_file = tmp_path / "Impl.java"
        java_file.write_text(
            "interface Runnable {\n    void run();\n}\n"
            "\n"
            "class Worker implements Runnable {\n"
            "    public void run() {}\n"
            "}\n"
        )

        run = AnalysisRun.create(pass_id="java", version="1.0")
        symbols, edges, success = _analyze_java_file(java_file, run)
        assert success

        impl_edges = [e for e in edges if e.edge_type == "implements"]
        assert len(impl_edges) == 1
        assert "Runnable" in impl_edges[0].dst

    def test_extracts_extends_edge(self, tmp_path: Path) -> None:
        """Extracts extends relationship edges."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Animal.java").write_text("""
public class Animal {
    public void speak() {}
}
""")
        (tmp_path / "Dog.java").write_text("""
public class Dog extends Animal {
    @Override
    public void speak() {
        System.out.println("Woof!");
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) >= 1

    def test_extracts_implements_edge(self, tmp_path: Path) -> None:
        """Extracts implements relationship edges."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Runnable.java").write_text("""
public interface Runnable {
    void run();
}
""")
        (tmp_path / "Task.java").write_text("""
public class Task implements Runnable {
    @Override
    public void run() {
        System.out.println("Running");
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        implements_edges = [e for e in result.edges if e.edge_type == "implements"]
        assert len(implements_edges) >= 1


class TestJavaInstantiationEdges:
    """Tests for Java instantiation edge detection."""

    def test_extracts_instantiation_edges(self, tmp_path: Path) -> None:
        """Extracts new ClassName() instantiation edges."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Person.java").write_text("""
public class Person {
    private String name;
    public Person(String name) { this.name = name; }
}
""")
        (tmp_path / "Main.java").write_text("""
public class Main {
    public static void main(String[] args) {
        Person p = new Person("Alice");
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        instantiate_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert len(instantiate_edges) >= 1


class TestJavaCrossFileResolution:
    """Tests for cross-file symbol resolution."""

    def test_cross_file_method_call(self, tmp_path: Path) -> None:
        """Resolves method calls across files."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Helper.java").write_text("""
public class Helper {
    public static int getValue() {
        return 42;
    }
}
""")
        (tmp_path / "Main.java").write_text("""
public class Main {
    public static void main(String[] args) {
        int x = Helper.getValue();
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Should have symbols from both files
        names = [s.name for s in result.symbols]
        assert "Helper" in names
        assert "Main" in names


class TestJavaJNIPatterns:
    """Tests for JNI native method detection."""

    def test_detects_native_methods(self, tmp_path: Path) -> None:
        """Detects native method declarations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Native.java"
        java_file.write_text("""
package com.example;

public class Native {
    static {
        System.loadLibrary("native");
    }

    public native void processData(byte[] data);
    public native int getValue();
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        # Native methods should be detected
        methods = [s for s in result.symbols if s.kind == "method"]
        native_methods = [m for m in methods if "native" in m.name.lower() or "processData" in m.name or "getValue" in m.name]
        # At least verify no crash; native detection is a nice-to-have


class TestJavaAnalysisRun:
    """Tests for Java analysis run tracking."""

    def test_tracks_files_analyzed(self, tmp_path: Path) -> None:
        """Tracks number of files analyzed."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "A.java").write_text("public class A {}")
        (tmp_path / "B.java").write_text("public class B {}")
        (tmp_path / "C.java").write_text("public class C {}")

        result = analyze_java(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 3
        assert result.run.pass_id == "java"

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Handles repo with no Java files."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "app.py").write_text("print('hello')")

        result = analyze_java(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 0
        assert len(result.symbols) == 0


class TestJavaEdgeCases:
    """Tests for Java edge cases and error handling."""

    def test_find_name_in_children_no_name(self) -> None:
        """Returns None when node has no identifier child."""
        from hypergumbo_lang_mainstream.java import _find_identifier_in_children

        mock_child = MagicMock()
        mock_child.type = "other"

        mock_node = MagicMock()
        mock_node.children = [mock_child]

        result = _find_identifier_in_children(mock_node, b"source")
        assert result is None

    def test_get_java_parser_import_error(self) -> None:
        """Returns None when tree-sitter-java is not available."""
        from hypergumbo_lang_mainstream.java import _get_java_parser

        with patch.dict(sys.modules, {
            "tree_sitter": None,
            "tree_sitter_java": None,
        }):
            result = _get_java_parser()
            assert result is None

    def test_analyze_java_file_parser_unavailable(self, tmp_path: Path) -> None:
        """Returns failure when parser is unavailable."""
        from hypergumbo_lang_mainstream.java import _analyze_java_file
        from hypergumbo_core.ir import AnalysisRun

        java_file = tmp_path / "Test.java"
        java_file.write_text("public class Test {}")

        run = AnalysisRun.create(pass_id="test", version="test")

        with patch("hypergumbo_lang_mainstream.java._get_java_parser", return_value=None):
            symbols, edges, success = _analyze_java_file(java_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_analyze_java_file_read_error(self, tmp_path: Path) -> None:
        """Returns failure when file cannot be read."""
        from hypergumbo_lang_mainstream.java import _analyze_java_file
        from hypergumbo_core.ir import AnalysisRun

        java_file = tmp_path / "missing.java"
        # Don't create the file

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_java_file(java_file, run)

        assert success is False
        assert len(symbols) == 0

    def test_java_file_skipped_increments_counter(self, tmp_path: Path) -> None:
        """Java files that fail to read increment skipped counter."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Test.java"
        java_file.write_text("public class Test {}")

        original_read_bytes = Path.read_bytes

        def mock_read_bytes(self: Path) -> bytes:
            if self.name == "Test.java":
                raise IOError("Mock read error")
            return original_read_bytes(self)

        with patch.object(Path, "read_bytes", mock_read_bytes):
            result = analyze_java(tmp_path)

        assert result.run is not None
        assert result.run.files_skipped == 1

    def test_analyze_java_parser_none_after_check(self, tmp_path: Path) -> None:
        """analyze_java handles case where parser is None after availability check."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Test.java"
        java_file.write_text("public class Test {}")

        with patch.object(
            java_module._java_analyzer, "_check_grammar_available",
            return_value=True,
        ), patch(
            "hypergumbo_lang_mainstream.java._get_java_parser",
            return_value=None,
        ):
            result = analyze_java(tmp_path)

        assert result.run is not None
        assert result.skipped is True
        assert "not available" in result.skip_reason


class TestJavaConstructors:
    """Tests for Java constructor detection."""

    def test_extracts_constructors(self, tmp_path: Path) -> None:
        """Extracts Java constructor declarations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Person.java"
        java_file.write_text("""
public class Person {
    private String name;

    public Person() {
        this.name = "Unknown";
    }

    public Person(String name) {
        this.name = name;
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        # Constructors should be detected as methods or constructors
        names = [s.name for s in result.symbols]
        assert "Person" in names


class TestJavaStaticMembers:
    """Tests for Java static member detection."""

    def test_extracts_static_methods(self, tmp_path: Path) -> None:
        """Extracts static method declarations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Utils.java"
        java_file.write_text("""
public class Utils {
    public static int max(int a, int b) {
        return a > b ? a : b;
    }

    public static void log(String msg) {
        System.out.println(msg);
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Utils" in names


class TestJavaInnerClasses:
    """Tests for Java inner class detection."""

    def test_extracts_inner_classes(self, tmp_path: Path) -> None:
        """Extracts inner class declarations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Outer.java"
        java_file.write_text("""
public class Outer {
    public class Inner {
        public void innerMethod() {}
    }

    public static class StaticInner {
        public void staticInnerMethod() {}
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Outer" in names
        # Inner classes might be named Outer.Inner or just Inner
        assert any("Inner" in name for name in names)


class TestJavaAnnotations:
    """Tests for Java annotation handling."""

    def test_handles_annotated_classes(self, tmp_path: Path) -> None:
        """Handles classes with annotations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Service.java"
        java_file.write_text("""
@Deprecated
public class Service {
    @Override
    public String toString() {
        return "Service";
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Service" in names


class TestJavaGenerics:
    """Tests for Java generics handling."""

    def test_handles_generic_classes(self, tmp_path: Path) -> None:
        """Handles classes with generic type parameters."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Container.java"
        java_file.write_text("""
public class Container<T> {
    private T value;

    public T getValue() {
        return value;
    }

    public void setValue(T value) {
        this.value = value;
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Container" in names


class TestJavaAnalyzeFileSuccess:
    """Tests for successful file analysis."""

    def test_analyze_java_file_success(self, tmp_path: Path) -> None:
        """_analyze_java_file returns symbols and edges on success."""
        from hypergumbo_lang_mainstream.java import _analyze_java_file
        from hypergumbo_core.ir import AnalysisRun

        java_file = tmp_path / "Test.java"
        java_file.write_text("""
public class Test {
    public int helper() {
        return 42;
    }

    public int process() {
        return helper();
    }
}
""")

        run = AnalysisRun.create(pass_id="test", version="test")
        symbols, edges, success = _analyze_java_file(java_file, run)

        assert success is True
        assert len(symbols) >= 1  # At least the class


class TestJavaMultipleInterfaces:
    """Tests for multiple interface implementation."""

    def test_multiple_implements(self, tmp_path: Path) -> None:
        """Handles class implementing multiple interfaces."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Readable.java").write_text("public interface Readable { void read(); }")
        (tmp_path / "Writable.java").write_text("public interface Writable { void write(); }")
        (tmp_path / "File.java").write_text("""
public class File implements Readable, Writable {
    public void read() {}
    public void write() {}
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        implements_edges = [e for e in result.edges if e.edge_type == "implements"]
        # Should have at least 2 implements edges (File -> Readable, File -> Writable)
        assert len(implements_edges) >= 2


class TestJavaAbstractClasses:
    """Tests for abstract class handling."""

    def test_extracts_abstract_class(self, tmp_path: Path) -> None:
        """Extracts abstract class declarations."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Shape.java"
        java_file.write_text("""
public abstract class Shape {
    public abstract double area();

    public void describe() {
        System.out.println("I am a shape");
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        names = [s.name for s in result.symbols]
        assert "Shape" in names


class TestSpringBootRouteDetection:
    """Tests for Spring Boot route detection via YAML patterns (ADR-3aaa v1.0.x).

    These tests verify that Spring Boot routes are detected through the YAML
    pattern system rather than deprecated analyzer-level detection.
    """

    def test_get_mapping_detection(self, tmp_path: Path) -> None:
        """Detects @GetMapping annotation via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "UserController.java"
        java_file.write_text("""
import org.springframework.web.bind.annotation.*;

@RestController
public class UserController {
    @GetMapping("/users")
    public List<User> getUsers() {
        return userService.findAll();
    }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"spring-boot"})

        # Find the getUsers method
        methods = [s for s in enriched if s.kind == "method" and "getUsers" in s.name]
        assert len(methods) == 1
        method = methods[0]

        # Should have route concept in meta (from YAML patterns)
        assert method.meta is not None
        route_concepts = [c for c in method.meta.get("concepts", []) if c.get("concept") == "route"]
        assert len(route_concepts) == 1
        assert route_concepts[0]["path"] == "/users"
        assert route_concepts[0]["method"] == "GET"

    def test_post_mapping_detection(self, tmp_path: Path) -> None:
        """Detects @PostMapping annotation via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "UserController.java"
        java_file.write_text("""
@RestController
public class UserController {
    @PostMapping("/users")
    public User createUser(@RequestBody User user) {
        return userService.save(user);
    }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"spring-boot"})

        methods = [s for s in enriched if s.kind == "method" and "createUser" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        route_concepts = [c for c in method.meta.get("concepts", []) if c.get("concept") == "route"]
        assert len(route_concepts) == 1
        assert route_concepts[0]["path"] == "/users"
        assert route_concepts[0]["method"] == "POST"

    def test_all_http_method_mappings(self, tmp_path: Path) -> None:
        """Detects all Spring Boot HTTP method annotations via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "ResourceController.java"
        java_file.write_text("""
@RestController
public class ResourceController {
    @GetMapping("/items")
    public List<Item> getAll() { return null; }

    @PostMapping("/items")
    public Item create() { return null; }

    @PutMapping("/items/{id}")
    public Item update() { return null; }

    @DeleteMapping("/items/{id}")
    public void delete() {}

    @PatchMapping("/items/{id}")
    public Item patch() { return null; }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"spring-boot"})

        # Find methods with route concepts
        methods_with_routes = []
        http_methods = set()
        for s in enriched:
            if s.kind == "method" and s.meta:
                route_concepts = [c for c in s.meta.get("concepts", []) if c.get("concept") == "route"]
                if route_concepts:
                    methods_with_routes.append(s)
                    http_methods.add(route_concepts[0]["method"])

        assert len(methods_with_routes) == 5
        assert http_methods == {"GET", "POST", "PUT", "DELETE", "PATCH"}

    def test_request_mapping_with_method(self, tmp_path: Path) -> None:
        """Detects @RequestMapping with method attribute via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "LegacyController.java"
        java_file.write_text("""
@RestController
public class LegacyController {
    @RequestMapping(value = "/legacy", method = RequestMethod.GET)
    public String getLegacy() { return "legacy"; }

    @RequestMapping(value = "/legacy", method = RequestMethod.POST)
    public String postLegacy() { return "created"; }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"spring-boot"})

        methods = [s for s in enriched if s.kind == "method"]
        route_methods = []
        for m in methods:
            if m.meta:
                route_concepts = [c for c in m.meta.get("concepts", []) if c.get("concept") == "route"]
                if route_concepts:
                    route_methods.append((m, route_concepts[0]))

        assert len(route_methods) == 2
        assert any(rc["method"] == "GET" for m, rc in route_methods)
        assert any(rc["method"] == "POST" for m, rc in route_methods)

    def test_mapping_with_path_variable(self, tmp_path: Path) -> None:
        """Detects routes with path variables like {id} via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "ItemController.java"
        java_file.write_text("""
@RestController
public class ItemController {
    @GetMapping("/items/{id}")
    public Item getById(@PathVariable Long id) {
        return itemService.findById(id);
    }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"spring-boot"})

        methods = [s for s in enriched if s.kind == "method" and "getById" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        route_concepts = [c for c in method.meta.get("concepts", []) if c.get("concept") == "route"]
        assert len(route_concepts) == 1
        assert route_concepts[0]["path"] == "/items/{id}"
        assert route_concepts[0]["method"] == "GET"

    def test_get_mapping_with_value_attribute(self, tmp_path: Path) -> None:
        """Detects @GetMapping with explicit value attribute via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "Controller.java"
        java_file.write_text("""
@RestController
public class Controller {
    @GetMapping(value = "/explicit")
    public String getExplicit() { return "explicit"; }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"spring-boot"})

        methods = [s for s in enriched if s.kind == "method" and "getExplicit" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        route_concepts = [c for c in method.meta.get("concepts", []) if c.get("concept") == "route"]
        assert len(route_concepts) == 1
        assert route_concepts[0]["path"] == "/explicit"
        assert route_concepts[0]["method"] == "GET"

    def test_request_mapping_without_qualified_method(self, tmp_path: Path) -> None:
        """Detects @RequestMapping with unqualified method via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "Controller.java"
        # This is an unusual but valid form
        java_file.write_text("""
@RestController
public class Controller {
    @RequestMapping(value = "/test", method = GET)
    public String test() { return "test"; }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"spring-boot"})

        methods = [s for s in enriched if s.kind == "method" and "test" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        route_concepts = [c for c in method.meta.get("concepts", []) if c.get("concept") == "route"]
        assert len(route_concepts) == 1
        assert route_concepts[0]["path"] == "/test"
        assert route_concepts[0]["method"] == "GET"


class TestJaxRsRouteDetection:
    """Tests for JAX-RS route detection via YAML patterns (ADR-3aaa v1.0.x).

    These tests verify that JAX-RS routes are detected through the YAML
    pattern system rather than deprecated analyzer-level detection.
    """

    def test_jaxrs_get_with_path(self, tmp_path: Path) -> None:
        """Detects JAX-RS @GET via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "UserResource.java"
        java_file.write_text("""
import javax.ws.rs.*;

@Path("/users")
public class UserResource {
    @GET
    public List<User> getUsers() {
        return userService.findAll();
    }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"jax-rs"})

        methods = [s for s in enriched if s.kind == "method" and "getUsers" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        route_concepts = [c for c in method.meta.get("concepts", []) if c.get("concept") == "route"]
        assert len(route_concepts) == 1
        assert route_concepts[0]["method"] == "GET"

    def test_jaxrs_post_with_path(self, tmp_path: Path) -> None:
        """Detects JAX-RS @POST via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "UserResource.java"
        java_file.write_text("""
@Path("/users")
public class UserResource {
    @POST
    @Consumes(MediaType.APPLICATION_JSON)
    public User createUser(User user) {
        return userService.save(user);
    }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"jax-rs"})

        methods = [s for s in enriched if s.kind == "method" and "createUser" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        route_concepts = [c for c in method.meta.get("concepts", []) if c.get("concept") == "route"]
        assert len(route_concepts) == 1
        assert route_concepts[0]["method"] == "POST"

    def test_jaxrs_method_level_path(self, tmp_path: Path) -> None:
        """Detects JAX-RS @GET and @Path via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "UserResource.java"
        java_file.write_text("""
@Path("/users")
public class UserResource {
    @GET
    @Path("/{id}")
    public User getById(@PathParam("id") Long id) {
        return userService.findById(id);
    }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"jax-rs"})

        methods = [s for s in enriched if s.kind == "method" and "getById" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.meta is not None
        route_concepts = [c for c in method.meta.get("concepts", []) if c.get("concept") == "route"]
        assert len(route_concepts) == 1
        assert route_concepts[0]["method"] == "GET"
        # Route path combines class @Path("/users") + method @Path("/{id}")
        assert route_concepts[0].get("path") == "/users/{id}"
        # Method's own resource_path concept retains just the method-level path
        path_concept = next(
            (c for c in method.meta["concepts"] if c.get("concept") == "resource_path"),
            None
        )
        assert path_concept is not None
        assert path_concept.get("path") == "/{id}"

    def test_jaxrs_all_http_methods(self, tmp_path: Path) -> None:
        """Detects all JAX-RS HTTP method annotations via YAML patterns."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "ResourceController.java"
        java_file.write_text("""
@Path("/items")
public class ResourceController {
    @GET
    public List<Item> getAll() { return null; }

    @POST
    public Item create() { return null; }

    @PUT
    public Item update() { return null; }

    @DELETE
    public void delete() {}

    @PATCH
    public Item patch() { return null; }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"jax-rs"})

        # Find methods with route concepts
        methods_with_routes = []
        http_methods = set()
        for s in enriched:
            if s.kind == "method" and s.meta:
                route_concepts = [c for c in s.meta.get("concepts", []) if c.get("concept") == "route"]
                if route_concepts:
                    methods_with_routes.append(s)
                    http_methods.add(route_concepts[0]["method"])

        assert len(methods_with_routes) == 5
        assert http_methods == {"GET", "POST", "PUT", "DELETE", "PATCH"}

    def test_jaxrs_path_with_constant_reference(self, tmp_path: Path) -> None:
        """JAX-RS @Path with constant reference extracts the reference as path."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "AccountResource.java"
        java_file.write_text("""
@Path(JaxrsResource.ACCOUNTS_PATH)
public class AccountResource {
    @GET
    @Path("/{id}")
    public void getById() {}
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"jax-rs"})

        # Class should have resource_path with constant reference
        cls = next((s for s in enriched if s.kind == "class"), None)
        assert cls is not None
        path_concept = next(
            (c for c in cls.meta.get("concepts", []) if c.get("concept") == "resource_path"),
            None,
        )
        assert path_concept is not None
        assert path_concept.get("path") == "JaxrsResource.ACCOUNTS_PATH"

        # Method should have resource_path with /{id}
        method = next((s for s in enriched if s.kind == "method"), None)
        assert method is not None
        method_path = next(
            (c for c in method.meta.get("concepts", []) if c.get("concept") == "resource_path"),
            None,
        )
        assert method_path is not None
        assert method_path.get("path") == "/{id}"

        # Route concept combines class + method paths
        route_concept = next(
            (c for c in method.meta.get("concepts", []) if c.get("concept") == "route"),
            None,
        )
        assert route_concept is not None
        assert route_concept.get("path") == "/JaxrsResource.ACCOUNTS_PATH/{id}"


    def test_jaxrs_path_with_named_value_kwarg(self, tmp_path: Path) -> None:
        """JAX-RS @Path(value="/users") should extract path from kwargs.value."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "UserResource.java"
        java_file.write_text("""
@Path(value = "/users")
public class UserResource {
    @GET
    public List<User> getUsers() {
        return userService.findAll();
    }

    @GET
    @Path(value = "/{id}")
    public User getById(@PathParam("id") Long id) {
        return userService.findById(id);
    }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"jax-rs"})

        # Class should have resource_path with /users from kwargs.value
        cls = next((s for s in enriched if s.kind == "class"), None)
        assert cls is not None
        path_concept = next(
            (c for c in cls.meta.get("concepts", []) if c.get("concept") == "resource_path"),
            None,
        )
        assert path_concept is not None
        assert path_concept.get("path") == "/users"

        # getUsers() should inherit class path
        get_method = next(
            (s for s in enriched if s.kind == "method" and "getUsers" in s.name), None
        )
        assert get_method is not None
        route_concept = next(
            (c for c in get_method.meta.get("concepts", []) if c.get("concept") == "route"),
            None,
        )
        assert route_concept is not None
        assert route_concept.get("path") == "/users"

        # getById() should combine class + method paths from kwargs.value
        get_by_id = next(
            (s for s in enriched if s.kind == "method" and "getById" in s.name), None
        )
        assert get_by_id is not None
        route_concept = next(
            (c for c in get_by_id.meta.get("concepts", []) if c.get("concept") == "route"),
            None,
        )
        assert route_concept is not None
        assert route_concept.get("path") == "/users/{id}"

    def test_jaxrs_subresource_locator_with_generic_return_type(self, tmp_path: Path) -> None:
        """Subresource locator returning a generic type should chain paths.

        Methods like `Class<TokenEndpoint> token()` should have return_type
        extracted so subresource locator path chaining works.
        """
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import enrich_symbols, clear_pattern_cache

        clear_pattern_cache()

        java_file = tmp_path / "ProtocolService.java"
        java_file.write_text("""
@Path("/protocol")
public class ProtocolService {
    @Path("token")
    public TokenEndpoint token() {
        return new TokenEndpoint();
    }
}
""")
        target_file = tmp_path / "TokenEndpoint.java"
        target_file.write_text("""
public class TokenEndpoint {
    @GET
    @Path("access")
    public String getAccessToken() { return null; }
}
""")

        result = analyze_java(tmp_path)
        enriched = enrich_symbols(result.symbols, {"jax-rs"})

        # The target method should have the full chained path
        target_method = next(
            (s for s in enriched if s.kind == "method" and "getAccessToken" in s.name), None
        )
        assert target_method is not None
        route_concept = next(
            (c for c in target_method.meta.get("concepts", []) if c.get("concept") == "route"),
            None,
        )
        assert route_concept is not None
        assert route_concept.get("path") == "/protocol/token/access"


class TestJavaGenericReturnTypeExtraction:
    """Tests for extracting return_type from methods with generic return types.

    Methods returning generic types like Response<T>, CompletionStage<Response>,
    etc. should have the outer type name stored in return_type metadata.
    """

    def test_generic_return_type_extracts_outer_name(self, tmp_path: Path) -> None:
        """Response<User> should store return_type='Response'."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Service.java"
        java_file.write_text("""
public class Service {
    public Response<User> getUser() { return null; }
    public CompletionStage<Response> asyncGet() { return null; }
    public Optional<String> findName() { return null; }
}
""")

        result = analyze_java(tmp_path)

        user_method = next(
            (s for s in result.symbols if "getUser" in s.name and s.kind == "method"), None
        )
        assert user_method is not None
        assert user_method.meta is not None
        assert user_method.meta.get("return_type") == "Response"

        async_method = next(
            (s for s in result.symbols if "asyncGet" in s.name and s.kind == "method"), None
        )
        assert async_method is not None
        assert async_method.meta is not None
        assert async_method.meta.get("return_type") == "CompletionStage"

        find_method = next(
            (s for s in result.symbols if "findName" in s.name and s.kind == "method"), None
        )
        assert find_method is not None
        assert find_method.meta is not None
        assert find_method.meta.get("return_type") == "Optional"


class TestJavaModifiersCapture:
    """Tests for Java method modifier capture in the modifiers field."""

    def test_native_modifier_captured(self, tmp_path: Path) -> None:
        """Native methods should have 'native' in modifiers list."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Native.java"
        java_file.write_text("""
public class Native {
    public native void processData(byte[] data);
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method" and "processData" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert "native" in method.modifiers

    def test_public_static_modifiers_captured(self, tmp_path: Path) -> None:
        """Public static methods should have both modifiers in list."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Utils.java"
        java_file.write_text("""
public class Utils {
    public static int max(int a, int b) {
        return a > b ? a : b;
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method" and "max" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert "public" in method.modifiers
        assert "static" in method.modifiers

    def test_all_modifiers_captured(self, tmp_path: Path) -> None:
        """All method modifiers should be captured."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "JNIBridge.java"
        java_file.write_text("""
public class JNIBridge {
    public static native void nativeCall();
    private synchronized void syncMethod() {}
    protected final void finalMethod() {}
}
""")

        result = analyze_java(tmp_path)

        # Native static method
        native_methods = [s for s in result.symbols if s.kind == "method" and "nativeCall" in s.name]
        assert len(native_methods) == 1
        assert "native" in native_methods[0].modifiers
        assert "static" in native_methods[0].modifiers
        assert "public" in native_methods[0].modifiers

        # Synchronized method
        sync_methods = [s for s in result.symbols if s.kind == "method" and "syncMethod" in s.name]
        assert len(sync_methods) == 1
        assert "synchronized" in sync_methods[0].modifiers
        assert "private" in sync_methods[0].modifiers

        # Final method
        final_methods = [s for s in result.symbols if s.kind == "method" and "finalMethod" in s.name]
        assert len(final_methods) == 1
        assert "final" in final_methods[0].modifiers
        assert "protected" in final_methods[0].modifiers


class TestJavaIsExported:
    """Tests for is_exported on Java symbols (only ``public`` is exported)."""

    def test_public_vs_default_class(self, tmp_path: Path) -> None:
        """A public class is exported; package-private is not."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Mixed.java"
        java_file.write_text("""
public class PublicClass {
    public int publicMethod() { return 1; }
    private int privateMethod() { return 2; }
}

class PackagePrivateClass {
    void packagePrivateMethod() {}
}
""")

        result = analyze_java(tmp_path)
        by_name = {s.name: s for s in result.symbols}
        assert by_name["PublicClass"].is_exported is True
        assert by_name["PackagePrivateClass"].is_exported is False
        assert by_name["PublicClass.publicMethod"].is_exported is True
        assert by_name["PublicClass.privateMethod"].is_exported is False
        assert by_name[
            "PackagePrivateClass.packagePrivateMethod"
        ].is_exported is False


class TestJavaClassInterfaceModifiers:
    """Tests for visibility modifier extraction on classes and interfaces.

    Java classes and interfaces have access modifiers (public, abstract, etc.)
    that must be captured in the symbol's modifiers list to support YAML
    pattern matching (e.g., library export detection for public interfaces).
    """

    def test_public_class_has_public_modifier(self, tmp_path: Path) -> None:
        """A public class captures 'public' in its modifiers."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "MyClass.java"
        java_file.write_text("""
public class MyClass {
    public void doSomething() {}
}
""")

        result = analyze_java(tmp_path)

        cls_sym = next(
            (s for s in result.symbols if s.kind == "class" and s.name == "MyClass"),
            None,
        )
        assert cls_sym is not None
        assert cls_sym.modifiers is not None
        assert "public" in cls_sym.modifiers, (
            f"Public class should have 'public' modifier, got: {cls_sym.modifiers}"
        )

    def test_public_interface_has_public_modifier(self, tmp_path: Path) -> None:
        """A public interface captures 'public' in its modifiers."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "MyInterface.java"
        java_file.write_text("""
public interface MyInterface {
    void doSomething();
}
""")

        result = analyze_java(tmp_path)

        iface_sym = next(
            (s for s in result.symbols if s.kind == "interface" and s.name == "MyInterface"),
            None,
        )
        assert iface_sym is not None
        assert iface_sym.modifiers is not None
        assert "public" in iface_sym.modifiers, (
            f"Public interface should have 'public' modifier, got: {iface_sym.modifiers}"
        )

    def test_abstract_class_has_abstract_modifier(self, tmp_path: Path) -> None:
        """An abstract class captures 'abstract' in its modifiers."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Base.java"
        java_file.write_text("""
public abstract class Base {
    abstract void process();
}
""")

        result = analyze_java(tmp_path)

        cls_sym = next(
            (s for s in result.symbols if s.kind == "class" and s.name == "Base"),
            None,
        )
        assert cls_sym is not None
        assert cls_sym.modifiers is not None
        assert "public" in cls_sym.modifiers
        assert "abstract" in cls_sym.modifiers

    def test_package_private_class_no_public_modifier(self, tmp_path: Path) -> None:
        """A package-private class does NOT have 'public' in modifiers."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Internal.java"
        java_file.write_text("""
class Internal {
    void doSomething() {}
}
""")

        result = analyze_java(tmp_path)

        cls_sym = next(
            (s for s in result.symbols if s.kind == "class" and s.name == "Internal"),
            None,
        )
        assert cls_sym is not None
        # modifiers may be None or empty list - either way, no "public"
        if cls_sym.modifiers:
            assert "public" not in cls_sym.modifiers


class TestJavaSignatureExtraction:
    """Tests for Java function signature extraction."""

    def test_basic_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from a basic method."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Calculator.java"
        java_file.write_text("""
public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method" and "add" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.signature == "(int a, int b) int"

    def test_void_method_signature(self, tmp_path: Path) -> None:
        """Extracts signature from void method."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Logger.java"
        java_file.write_text("""
public class Logger {
    public void log(String message) {
        System.out.println(message);
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method" and "log" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.signature == "(String message)"

    def test_no_params_signature(self, tmp_path: Path) -> None:
        """Extracts signature from method with no parameters."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Counter.java"
        java_file.write_text("""
public class Counter {
    public int getCount() {
        return 0;
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method" and "getCount" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.signature == "() int"

    def test_generic_type_signature(self, tmp_path: Path) -> None:
        """Extracts signature with generic types."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Container.java"
        java_file.write_text("""
public class Container {
    public List<String> getItems(Map<String, Integer> config) {
        return null;
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method" and "getItems" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.signature == "(Map<String, Integer> config) List<String>"

    def test_constructor_signature(self, tmp_path: Path) -> None:
        """Extracts signature from constructor."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Person.java"
        java_file.write_text("""
public class Person {
    private String name;
    private int age;

    public Person(String name, int age) {
        this.name = name;
        this.age = age;
    }
}
""")

        result = analyze_java(tmp_path)

        constructors = [s for s in result.symbols if s.kind == "constructor"]
        assert len(constructors) == 1
        constructor = constructors[0]

        # Constructors have no return type
        assert constructor.signature == "(String name, int age)"

    def test_array_type_signature(self, tmp_path: Path) -> None:
        """Extracts signature with array types."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Processor.java"
        java_file.write_text("""
public class Processor {
    public byte[] process(String[] inputs) {
        return null;
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method" and "process" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.signature == "(String[] inputs) byte[]"

    def test_varargs_signature(self, tmp_path: Path) -> None:
        """Extracts signature with varargs parameters."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Formatter.java"
        java_file.write_text("""
public class Formatter {
    public String format(String pattern, Object... args) {
        return null;
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method" and "format" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.signature == "(String pattern, Object... args) String"

    def test_array_notation_after_name(self, tmp_path: Path) -> None:
        """Extracts signature with array notation after variable name (C-style)."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Legacy.java"
        # C-style array declaration: String args[]
        java_file.write_text("""
public class Legacy {
    public void process(String args[]) {
        return;
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method" and "process" in s.name]
        assert len(methods) == 1
        method = methods[0]

        assert method.signature == "(String[] args)"


class TestJavaReturnTypeExtraction:
    """Unit tests for _extract_java_return_type_name helper."""

    def test_simple_return_type(self) -> None:
        from hypergumbo_lang_mainstream.java import _extract_java_return_type_name

        assert _extract_java_return_type_name("(int a) Client") == "Client"

    def test_void_method(self) -> None:
        from hypergumbo_lang_mainstream.java import _extract_java_return_type_name

        assert _extract_java_return_type_name("(String msg)") is None

    def test_primitive_return(self) -> None:
        from hypergumbo_lang_mainstream.java import _extract_java_return_type_name

        # Primitive types start with lowercase, not tracked
        assert _extract_java_return_type_name("(int a, int b) int") is None

    def test_none_signature(self) -> None:
        from hypergumbo_lang_mainstream.java import _extract_java_return_type_name

        assert _extract_java_return_type_name(None) is None

    def test_empty_signature(self) -> None:
        from hypergumbo_lang_mainstream.java import _extract_java_return_type_name

        assert _extract_java_return_type_name("") is None

    def test_no_paren(self) -> None:
        from hypergumbo_lang_mainstream.java import _extract_java_return_type_name

        assert _extract_java_return_type_name("no parens") is None


class TestJavaReturnTypeMetadata:
    """Tests for return_type in method symbol metadata."""

    def test_method_return_type_in_meta(self, tmp_path: Path) -> None:
        """Non-void, non-primitive return type is stored in meta.return_type."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Foo.java"
        java_file.write_text(
            "public class Foo {\n"
            "    public UserResource getUser() {\n"
            "        return new UserResource();\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        method = next(s for s in result.symbols if s.name == "Foo.getUser")
        assert method.meta is not None
        assert method.meta.get("return_type") == "UserResource"

    def test_void_method_no_return_type(self, tmp_path: Path) -> None:
        """Void methods do not have return_type in metadata."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Foo.java"
        java_file.write_text(
            "public class Foo {\n"
            "    public void doWork() {}\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        method = next(s for s in result.symbols if s.name == "Foo.doWork")
        assert method.meta is None or "return_type" not in (method.meta or {})

    def test_primitive_return_type_excluded(self, tmp_path: Path) -> None:
        """Primitive return types (int, boolean, etc.) are not stored."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Foo.java"
        java_file.write_text(
            "public class Foo {\n"
            "    public int getCount() { return 0; }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        method = next(s for s in result.symbols if s.name == "Foo.getCount")
        assert method.meta is None or "return_type" not in (method.meta or {})

    def test_constructor_no_return_type(self, tmp_path: Path) -> None:
        """Constructors do not have return_type metadata."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Foo.java"
        java_file.write_text(
            "public class Foo {\n"
            "    public Foo(String name) {}\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        constructor = next(
            (s for s in result.symbols if s.kind == "method" and "Foo.Foo" in s.name),
            None,
        )
        if constructor is not None:
            assert constructor.meta is None or "return_type" not in (constructor.meta or {})


class TestJavaStaticImportSkip:
    """Tests for static import handling."""

    def test_static_imports_skipped(self, tmp_path: Path) -> None:
        """Static imports are skipped (we only track class imports)."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Consumer.java"
        java_file.write_text("""
import static java.lang.System.out;
import static com.example.Utils.helper;

public class Consumer {
    public void test() {
        out.println("hello");
    }
}
""")

        result = analyze_java(tmp_path)

        # Analysis should succeed without crashing on static imports
        assert result.run is not None
        assert result.run.files_analyzed == 1
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(c.name == "Consumer" for c in classes)


class TestJavaWildcardImports:
    """WI-tuhok: ``import java.util.*`` resolves bare class usages.

    Surfaced by the WI-mafik audit extension (2026-05-21). Pre-fix, a
    bare type usage like ``Arrays.asList(...)`` after ``import java.util.*``
    emitted an unresolved edge with dst ``java:external:0-0:Arrays.asList:unresolved``
    instead of attributing ``Arrays`` to its source package ``java.util``.
    """

    def test_extract_wildcard_imports_returns_package(self, tmp_path: Path) -> None:
        """``_extract_wildcard_imports`` returns the wildcard package list."""
        import tree_sitter
        import tree_sitter_java
        from hypergumbo_lang_mainstream.java import _extract_wildcard_imports

        parser = tree_sitter.Parser()
        parser.language = tree_sitter.Language(tree_sitter_java.language())
        src = (
            b"import java.util.*;\n"
            b"import java.io.*;\n"
            b"import java.util.Arrays;\n"
        )
        tree = parser.parse(src)
        wildcards = _extract_wildcard_imports(tree, src)
        assert wildcards == ["java.util", "java.io"]

    def test_extract_wildcard_imports_no_wildcards(self, tmp_path: Path) -> None:
        """Pure-explicit-imports file yields an empty list."""
        import tree_sitter
        import tree_sitter_java
        from hypergumbo_lang_mainstream.java import _extract_wildcard_imports

        parser = tree_sitter.Parser()
        parser.language = tree_sitter.Language(tree_sitter_java.language())
        src = b"import java.util.Arrays;\nimport java.io.File;\n"
        tree = parser.parse(src)
        wildcards = _extract_wildcard_imports(tree, src)
        assert wildcards == []

    def test_extract_wildcard_imports_static_wildcard_excluded(
        self, tmp_path: Path,
    ) -> None:
        """``import static java.util.Arrays.*`` is NOT a regular wildcard.

        Static wildcard imports bring **methods** into scope, not class
        names; they belong to the static-import resolver. Treating them
        as regular wildcards would attribute bare method names to the
        Arrays package, which is the static-import resolver's job
        (WI-hudud).
        """
        import tree_sitter
        import tree_sitter_java
        from hypergumbo_lang_mainstream.java import _extract_wildcard_imports

        parser = tree_sitter.Parser()
        parser.language = tree_sitter.Language(tree_sitter_java.language())
        src = b"import static java.util.Arrays.*;\n"
        tree = parser.parse(src)
        wildcards = _extract_wildcard_imports(tree, src)
        assert wildcards == []

    def test_bare_class_use_attributed_to_wildcard_package(
        self, tmp_path: Path,
    ) -> None:
        """End-to-end: ``Arrays.asList(...)`` after ``import java.util.*`` resolves to ``java.util``."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "WildcardFixture.java"
        java_file.write_text(
            "import java.util.*;\n"
            "\n"
            "public class WildcardFixture {\n"
            "    public static void exerciseWildcard() {\n"
            "        Arrays.asList(1, 2, 3);\n"
            "    }\n"
            "}\n"
        )

        result = analyze_java(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # An edge to Arrays.asList must exist, and its dst must reference
        # java.util as the source package (not "external").
        arrays_calls = [
            e for e in call_edges
            if "asList" in e.dst and "Arrays" in e.dst
        ]
        assert arrays_calls, f"no calls edges to Arrays.asList found; got {[e.dst for e in call_edges]}"
        # The dst's module_hint segment should be ``java.util``, not ``external``.
        assert any(
            "java.util" in e.dst for e in arrays_calls
        ), f"expected java.util in dst, got {[e.dst for e in arrays_calls]}"

    def test_explicit_import_takes_precedence_over_wildcard(
        self, tmp_path: Path,
    ) -> None:
        """``import java.util.Arrays;`` + ``import java.io.*;`` → Arrays is in java.util."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "MixedFixture.java"
        java_file.write_text(
            "import java.util.Arrays;\n"
            "import java.io.*;\n"
            "\n"
            "public class MixedFixture {\n"
            "    public static void run() {\n"
            "        Arrays.asList(1, 2);\n"
            "    }\n"
            "}\n"
        )

        result = analyze_java(tmp_path)
        arrays_calls = [
            e for e in result.edges
            if e.edge_type == "calls" and "Arrays" in e.dst and "asList" in e.dst
        ]
        assert arrays_calls
        # Explicit import wins: java.util.Arrays must be the module.
        assert any("java.util.Arrays" in e.dst for e in arrays_calls)
        # And nobody attributes Arrays to java.io.
        assert not any("java.io" in e.dst for e in arrays_calls)

    def test_lowercase_bare_name_not_attributed_to_wildcard(
        self, tmp_path: Path,
    ) -> None:
        """A bare lowercase name is not a class — wildcard fallback shouldn't fire.

        Distinguishes the wildcard-class case from misc bare identifiers
        (which could be local variables, parameters, or static-imported
        methods handled by the WI-hudud path).
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "LowercaseFixture.java"
        java_file.write_text(
            "import java.util.*;\n"
            "\n"
            "public class LowercaseFixture {\n"
            "    public static void run() {\n"
            "        // ``localVar`` is not a class — must NOT be attributed\n"
            "        // to java.util just because the wildcard is present.\n"
            "        int localVar = 1;\n"
            "        System.out.println(localVar);\n"
            "    }\n"
            "}\n"
        )

        result = analyze_java(tmp_path)
        # No call edge should have dst attributing ``localVar`` to java.util.
        for e in result.edges:
            if e.edge_type == "calls":
                assert not (
                    "localVar" in e.dst and "java.util" in e.dst
                ), f"localVar attributed to java.util: {e.dst}"


class TestJavaVariableTypeInference:
    """Tests for type inference from constructor assignments."""

    def test_variable_method_call_resolved_via_type_inference(
        self, tmp_path: Path
    ) -> None:
        """Variable method calls resolved via constructor-based type inference."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Define a helper class with a method
        (tmp_path / "Helper.java").write_text("""
public class Helper {
    public void doWork() {
        System.out.println("working");
    }
}
""")
        # Caller creates Helper instance and calls method on it
        (tmp_path / "Caller.java").write_text("""
public class Caller {
    public void run() {
        Helper h = new Helper();
        h.doWork();
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Find the Caller.run -> Helper.doWork edge
        caller_run = next(
            (s for s in result.symbols if "run" in s.name and "Caller" in s.id), None
        )
        helper_dowork = next(
            (s for s in result.symbols if "doWork" in s.name), None
        )

        assert caller_run is not None
        assert helper_dowork is not None

        # WI-puvil (PR-5): the inherited_calls linker emits this edge via
        # Site-2 (receiver_type_hint=Helper, direct method match).
        call_edge = next(
            (
                e
                for e in all_edges
                if e.src == caller_run.id
                and e.dst == helper_dowork.id
                and e.edge_type == "calls"
                and e.is_resolved
            ),
            None,
        )
        assert call_edge is not None
        assert call_edge.evidence_type == "ast_call_type_inferred"
        assert call_edge.confidence == 0.85


class TestJavaReturnTypeInference:
    """Tests for return type tracking from method signatures."""

    def test_return_type_enables_method_resolution(self, tmp_path: Path) -> None:
        """Factory method return type enables resolution of subsequent method calls."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Client.java").write_text("""
public class Client {
    public void send() {}
}
""")
        (tmp_path / "Factory.java").write_text("""
public class Factory {
    public Client createClient() {
        return new Client();
    }
}
""")
        (tmp_path / "App.java").write_text("""
public class App {
    public void run() {
        Factory f = new Factory();
        Client c = f.createClient();
        c.send();
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        # WI-puvil (PR-5): linker resolves c.send() via Site-2; the
        # return-type-inference cascade in the analyzer populates
        # var_types[c]=Client from the f.createClient() return type, and
        # the unresolved emit threads receiver_type_hint=Client.
        call_edges = [
            e for e in all_edges
            if e.edge_type == "calls" and e.is_resolved
        ]
        send_edge = next(
            (e for e in call_edges if "run" in e.src and "send" in e.dst),
            None,
        )
        assert send_edge is not None, "Expected call edge for c.send() via return type inference"
        assert send_edge.evidence_type == "ast_call_type_inferred"

    def test_no_return_type_no_resolution(self, tmp_path: Path) -> None:
        """Void methods don't enable return type inference."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Client.java").write_text("""
public class Client {
    public void send() {}
}
""")
        (tmp_path / "Service.java").write_text("""
public class Service {
    public void process() {}
}
""")
        (tmp_path / "App.java").write_text("""
public class App {
    public void run() {
        Service s = new Service();
        s.process();
    }
}
""")

        result = analyze_java(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # process() is void — no return type inference needed (resolved via constructor type)
        process_edge = next(
            (e for e in call_edges if "run" in e.src and "process" in e.dst),
            None,
        )
        assert process_edge is not None

    def test_generic_return_type_not_tracked(self, tmp_path: Path) -> None:
        """Generic return types like List<Client> are not tracked for inference."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Client.java").write_text("""
public class Client {
    public void send() {}
}
""")
        (tmp_path / "Factory.java").write_text("""
import java.util.List;
import java.util.ArrayList;

public class Factory {
    public List<Client> getClients() {
        return new ArrayList<>();
    }
}
""")
        (tmp_path / "App.java").write_text("""
public class App {
    public void run() {
        Factory f = new Factory();
        Object result = f.getClients();
    }
}
""")

        result = analyze_java(tmp_path)
        # Just verify it doesn't crash - generic returns aren't tracked
        assert result.run is not None


class TestJpaInheritedMethodFallback:
    """Tests for JPA repository inherited method resolution.

    Spring Data JPA repositories extend framework interfaces like JpaRepository
    which provide methods (save, findById, findAll, deleteById) not defined in
    user code. When a controller calls repo.save(entity), the analyzer can
    infer the type (OwnerRepository) but can't find OwnerRepository.save as a
    symbol. The analyzer should fall back to linking to the repository
    interface/class itself.
    """

    def test_jpa_inherited_method_falls_back_to_type(self, tmp_path: Path) -> None:
        """repo.save() on JPA repository creates edge to the repository interface."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "OwnerRepository.java").write_text("""
public interface OwnerRepository {
    Owner findByLastName(String lastName);
}
""")
        (tmp_path / "OwnerController.java").write_text("""
public class OwnerController {
    private OwnerRepository owners;

    public void processCreation(Owner owner) {
        this.owners.save(owner);
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        controller_method = next(
            (s for s in result.symbols if "processCreation" in s.name), None
        )
        repo_iface = next(
            (s for s in result.symbols if s.name == "OwnerRepository" and s.kind == "interface"), None
        )

        assert controller_method is not None, "Should find processCreation"
        assert repo_iface is not None, "Should find OwnerRepository interface"

        # WI-puvil (PR-5): linker's Site-2 resolver falls back to the
        # type symbol when the method isn't on the type or any ancestor.
        call_edges = [
            e for e in all_edges
            if e.src == controller_method.id
            and e.dst == repo_iface.id
            and e.edge_type == "calls"
            and e.is_resolved
        ]
        assert len(call_edges) == 1, (
            f"Expected fallback edge to OwnerRepository, got {len(call_edges)}. "
            f"All edges from controller: "
            f"{[(e.edge_type, e.dst) for e in all_edges if e.src == controller_method.id]}"
        )
        assert call_edges[0].evidence_type == "ast_call_inherited_method"
        assert call_edges[0].confidence < 0.85  # Lower than direct resolution

    def test_jpa_declared_method_resolves_normally(self, tmp_path: Path) -> None:
        """Methods defined in the repository resolve normally, no fallback."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "OwnerRepository.java").write_text("""
public interface OwnerRepository {
    Owner findByLastName(String lastName);
}
""")
        (tmp_path / "OwnerController.java").write_text("""
public class OwnerController {
    private OwnerRepository owners;

    public void search() {
        this.owners.findByLastName("Smith");
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        controller_method = next(
            (s for s in result.symbols if "search" in s.name), None
        )
        find_method = next(
            (s for s in result.symbols if "findByLastName" in s.name), None
        )

        assert controller_method is not None
        assert find_method is not None

        # WI-puvil (PR-5): linker's Site-2 direct lookup resolves to the
        # method on the interface (ast_call_type_inferred at 0.85).
        call_edges = [
            e for e in all_edges
            if e.src == controller_method.id
            and e.dst == find_method.id
            and e.edge_type == "calls"
            and e.is_resolved
        ]
        assert len(call_edges) == 1
        assert call_edges[0].evidence_type == "ast_call_type_inferred"


class TestJavaImportResolution:
    """Tests for import-based method call resolution."""

    def test_imported_class_static_method_resolution(self, tmp_path: Path) -> None:
        """Method calls resolved via import mapping."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Define utils in a package directory matching the declaration so that
        # WI-fuhaj's import-class-mismatch guard recognises the resolved symbol
        # as the one pointed at by ``import com.example.Utils``.
        utils_dir = tmp_path / "com" / "example"
        utils_dir.mkdir(parents=True)
        (utils_dir / "Utils.java").write_text("""
package com.example;

public class Utils {
    public static int compute(int x) {
        return x * 2;
    }
}
""")
        # Caller imports Utils and calls static method
        (tmp_path / "Main.java").write_text("""
import com.example.Utils;

public class Main {
    public static void main(String[] args) {
        int result = Utils.compute(42);
    }
}
""")

        result = analyze_java(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Find symbols
        main_method = next(
            (s for s in result.symbols if "Main.main" in s.name), None
        )
        compute_method = next(
            (s for s in result.symbols if "compute" in s.name), None
        )

        assert main_method is not None
        assert compute_method is not None

        # Should have edge from Main.main to Utils.compute
        call_edge = next(
            (
                e
                for e in result.edges
                if e.src == main_method.id
                and e.dst == compute_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None


class TestParameterTypeInference:
    """Tests for parameter type inference in Java."""

    def test_parameter_type_inference_basic(self, tmp_path: Path) -> None:
        """Method parameter types should enable method call resolution."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Service class with methods
        (tmp_path / "Database.java").write_text("""
public class Database {
    public void save(Object obj) { }
    public void commit() { }
}
""")
        # Handler receives Database as parameter
        (tmp_path / "Handler.java").write_text("""
public class Handler {
    public void process(Database db, String data) {
        db.save(data);
        db.commit();
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Find symbols
        handler_process = next(
            (s for s in result.symbols if "process" in s.name and "Handler" in s.id), None
        )
        db_save = next(
            (s for s in result.symbols if "save" in s.name and "Database" in s.id), None
        )
        db_commit = next(
            (s for s in result.symbols if "commit" in s.name and "Database" in s.id), None
        )

        assert handler_process is not None
        assert db_save is not None
        assert db_commit is not None

        # WI-puvil (PR-5): linker resolves db.save()/db.commit() via
        # Site-2 (receiver_type_hint=Database from param-type capture).
        save_edge = next(
            (
                e
                for e in all_edges
                if e.src == handler_process.id
                and e.dst == db_save.id
                and e.edge_type == "calls"
                and e.is_resolved
            ),
            None,
        )
        commit_edge = next(
            (
                e
                for e in all_edges
                if e.src == handler_process.id
                and e.dst == db_commit.id
                and e.edge_type == "calls"
                and e.is_resolved
            ),
            None,
        )

        assert save_edge is not None, "Expected call edge for db.save() via param type inference"
        assert commit_edge is not None, "Expected call edge for db.commit() via param type inference"
        # Both should use type inference evidence
        assert save_edge.evidence_type == "ast_call_type_inferred"
        assert commit_edge.evidence_type == "ast_call_type_inferred"


# ============================================================================
# Java Annotation Metadata Tests (Phase 5)
# ============================================================================


class TestAnnotationMetadata:
    """Tests for extracting annotation metadata from Java classes and methods."""

    def test_class_annotation_simple(self, tmp_path: Path) -> None:
        """Extracts simple class annotation without arguments."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "User.java").write_text("""
@Entity
public class User {
    private String name;
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "User"
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Entity"
        assert decorators[0]["args"] == []
        assert decorators[0]["kwargs"] == {}

    def test_class_annotation_with_string_arg(self, tmp_path: Path) -> None:
        """Extracts class annotation with string argument."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "User.java").write_text("""
@Table(name = "users")
public class User {
    private String name;
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Table"
        assert decorators[0]["kwargs"].get("name") == "users"

    def test_method_annotation_simple(self, tmp_path: Path) -> None:
        """Extracts simple method annotation."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "UserService.java").write_text("""
public class UserService {
    @Autowired
    public void setRepository() {}
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Autowired"
        assert decorators[0]["args"] == []

    def test_method_annotation_with_args(self, tmp_path: Path) -> None:
        """Extracts method annotation with arguments."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Controller.java").write_text("""
public class Controller {
    @GetMapping("/users")
    public void getUsers() {}
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        # Should have at least one decorator
        assert len(decorators) >= 1
        get_mapping = next((d for d in decorators if d["name"] == "GetMapping"), None)
        assert get_mapping is not None
        assert get_mapping["args"] == ["/users"]

    def test_multiple_annotations_on_method(self, tmp_path: Path) -> None:
        """Extracts multiple annotations from a method."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Controller.java").write_text("""
public class Controller {
    @Override
    @GetMapping("/items")
    public void getItems() {}
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) >= 2
        decorator_names = [d["name"] for d in decorators]
        assert "Override" in decorator_names
        assert "GetMapping" in decorator_names

    def test_multiple_annotations_on_class(self, tmp_path: Path) -> None:
        """Extracts multiple annotations from a class."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "User.java").write_text("""
@Entity
@Table(name = "users")
public class User {
    private Long id;
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 2
        decorator_names = [d["name"] for d in decorators]
        assert "Entity" in decorator_names
        assert "Table" in decorator_names

    def test_annotation_with_boolean_value(self, tmp_path: Path) -> None:
        """Extracts annotation with boolean value."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "User.java").write_text("""
@JsonIgnoreProperties(ignoreUnknown = true)
public class User {
    private String name;
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "JsonIgnoreProperties"
        # Check kwargs has ignoreUnknown
        assert "ignoreUnknown" in decorators[0]["kwargs"]

    def test_annotation_with_constant_ref_arg(self, tmp_path: Path) -> None:
        """Extracts annotation with constant reference as positional argument."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "AccountResource.java").write_text("""
@Path(JaxrsResource.ACCOUNTS_PATH)
public class AccountResource {
    @GET
    @Path("/{id}")
    public void getById() {}
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        path_dec = next((d for d in decorators if d["name"] == "Path"), None)
        assert path_dec is not None
        # Constant reference captured as positional arg
        assert path_dec["args"] == ["JaxrsResource.ACCOUNTS_PATH"]

    def test_annotation_with_concatenation_arg(self, tmp_path: Path) -> None:
        """Extracts annotation with string concatenation as positional argument."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Resource.java").write_text("""
public class Resource {
    @Path("/{accountId:" + UUID_PATTERN + "}")
    public void getAccount() {}
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        decorators = meta.get("decorators", [])
        path_dec = next((d for d in decorators if d["name"] == "Path"), None)
        assert path_dec is not None
        # Concatenation captured as raw text
        assert len(path_dec["args"]) == 1
        assert "accountId" in str(path_dec["args"][0])

    def test_interface_annotation(self, tmp_path: Path) -> None:
        """Extracts annotation from interface."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "UserRepo.java").write_text("""
@Repository
public interface UserRepo {
    void save();
}
""")

        result = analyze_java(tmp_path)

        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert len(interfaces) == 1
        meta = interfaces[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Repository"


class TestJavaBaseClassMetadata:
    """Tests for extracting base class information from Java classes."""

    def test_class_extends_single(self, tmp_path: Path) -> None:
        """Extracts single base class from extends clause."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Admin.java").write_text("""
public class Admin extends User {
    private boolean isSuper;
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert "User" in base_classes

    def test_class_implements_single(self, tmp_path: Path) -> None:
        """Extracts single interface from implements clause."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "UserService.java").write_text("""
public class UserService implements IUserService {
    public void findAll() {}
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert "IUserService" in base_classes

    def test_class_implements_multiple(self, tmp_path: Path) -> None:
        """Extracts multiple interfaces from implements clause."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "UserService.java").write_text("""
public class UserService implements IUserService, Serializable, Comparable<User> {
    public void findAll() {}
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert len(base_classes) >= 3
        assert "IUserService" in base_classes
        assert "Serializable" in base_classes

    def test_class_extends_and_implements(self, tmp_path: Path) -> None:
        """Extracts both extends and implements clauses."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "AdminService.java").write_text("""
public class AdminService extends BaseService implements IAdminService {
    public void manage() {}
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert "BaseService" in base_classes
        assert "IAdminService" in base_classes

    def test_interface_extends(self, tmp_path: Path) -> None:
        """Extracts base interfaces from interface extends clause."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "IUserRepo.java").write_text("""
public interface IUserRepo extends JpaRepository, CrudRepository {
    void findByName(String name);
}
""")

        result = analyze_java(tmp_path)

        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert len(interfaces) == 1
        meta = interfaces[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert "JpaRepository" in base_classes
        assert "CrudRepository" in base_classes

    def test_generic_base_class(self, tmp_path: Path) -> None:
        """Extracts generic base class with type parameters."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "UserRepo.java").write_text("""
public class UserRepo extends Repository<User, Long> {
    public User findById(Long id) { return null; }
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert len(base_classes) >= 1
        # Should include the generic type info
        assert any("Repository" in bc for bc in base_classes)

    def test_class_no_inheritance(self, tmp_path: Path) -> None:
        """Class without extends/implements has empty base_classes."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Simple.java").write_text("""
public class Simple {
    public void doSomething() {}
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        base_classes = meta.get("base_classes", [])
        # Either empty list or key not present
        assert base_classes == [] or "base_classes" not in meta

    def test_interface_extends_generic_type(self, tmp_path: Path) -> None:
        """Extracts generic type from interface extends clause."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "IUserRepo.java").write_text("""
public interface IUserRepo extends Repository<User, Long> {
    void save();
}
""")

        result = analyze_java(tmp_path)

        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert len(interfaces) == 1
        meta = interfaces[0].meta or {}
        base_classes = meta.get("base_classes", [])
        assert len(base_classes) == 1
        # Should include generic type info
        assert "Repository<User, Long>" in base_classes or "Repository" in base_classes[0]


class TestAnnotationValueTypes:
    """Tests for various annotation value types."""

    def test_annotation_with_integer_value(self, tmp_path: Path) -> None:
        """Extracts annotation with integer value."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "RateLimited.java").write_text("""
@RateLimit(maxRequests = 100)
public class RateLimited {
    public void call() {}
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "RateLimit"
        # Integer value should be extracted
        assert decorators[0]["kwargs"].get("maxRequests") == 100

    def test_annotation_with_hex_integer_value(self, tmp_path: Path) -> None:
        """Extracts annotation with hexadecimal integer value."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Permissions.java").write_text("""
@Permission(mask = 0xFF)
public class Permissions {
    public void check() {}
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Permission"
        # Hex value should be converted to int
        assert decorators[0]["kwargs"].get("mask") == 255

    def test_annotation_with_float_value(self, tmp_path: Path) -> None:
        """Extracts annotation with float value."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Timeout.java").write_text("""
@Timeout(seconds = 30.5)
public class Timeout {
    public void wait() {}
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Timeout"
        # Float value should be extracted
        assert decorators[0]["kwargs"].get("seconds") == 30.5

    def test_annotation_with_array_value(self, tmp_path: Path) -> None:
        """Extracts annotation with array value."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Roles.java").write_text("""
@Authorized(roles = {"admin", "user"})
public class Roles {
    public void access() {}
}
""")

        result = analyze_java(tmp_path)

        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        meta = classes[0].meta or {}
        decorators = meta.get("decorators", [])
        assert len(decorators) == 1
        assert decorators[0]["name"] == "Authorized"
        # Array value should be extracted
        roles = decorators[0]["kwargs"].get("roles")
        assert roles is not None
        assert "admin" in roles
        assert "user" in roles


class TestMethodParentBaseClasses:
    """Tests for extracting parent class base_classes in method metadata.

    ADR-3aaa v1.1.x requires methods to include their parent class's base_classes
    to enable YAML pattern matching for lifecycle hooks (e.g., Android Activity.onCreate).
    """

    def test_method_inherits_parent_base_classes(self, tmp_path: Path) -> None:
        """Method has parent_base_classes when parent extends another class."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "MainActivity.java").write_text("""
public class MainActivity extends Activity {
    @Override
    public void onCreate() {
        // App entry point
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        assert methods[0].name == "MainActivity.onCreate"
        meta = methods[0].meta or {}
        parent_bases = meta.get("parent_base_classes", [])
        assert "Activity" in parent_bases

    def test_method_inherits_multiple_parent_base_classes(self, tmp_path: Path) -> None:
        """Method has all parent_base_classes when parent implements multiple interfaces."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "MyService.java").write_text("""
public class MyService extends Service implements Runnable, Comparable<MyService> {
    @Override
    public void onStartCommand() {
        // Service entry
    }
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        on_start = [m for m in methods if "onStartCommand" in m.name]
        assert len(on_start) == 1
        meta = on_start[0].meta or {}
        parent_bases = meta.get("parent_base_classes", [])
        # Should include Service and both interfaces
        assert "Service" in parent_bases
        assert "Runnable" in parent_bases

    def test_method_no_parent_base_classes_when_no_inheritance(
        self, tmp_path: Path
    ) -> None:
        """Method has no parent_base_classes when parent has no extends/implements."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "PlainClass.java").write_text("""
public class PlainClass {
    public void doSomething() {}
}
""")

        result = analyze_java(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 1
        meta = methods[0].meta or {}
        parent_bases = meta.get("parent_base_classes", [])
        assert parent_bases == []

    def test_android_activity_pattern_matching(self, tmp_path: Path) -> None:
        """Android Activity.onCreate matches lifecycle_hook pattern."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import load_framework_patterns

        (tmp_path / "MyActivity.java").write_text("""
public class MyActivity extends AppCompatActivity {
    @Override
    protected void onCreate() {
        super.onCreate();
    }
}
""")

        result = analyze_java(tmp_path)

        # Load Android patterns
        android_patterns = load_framework_patterns("android")
        assert android_patterns is not None

        # Find the onCreate method
        methods = [s for s in result.symbols if s.kind == "method"]
        on_create = [m for m in methods if "onCreate" in m.name]
        assert len(on_create) == 1

        # Check that a pattern matches
        matched = False
        for pattern in android_patterns.patterns:
            match_result = pattern.matches(on_create[0])
            if match_result is not None and match_result.get("concept") == "lifecycle_hook":
                matched = True
                assert match_result.get("matched_parent_base_class") == "AppCompatActivity"
                assert match_result.get("matched_method_name") == "onCreate"
                break

        assert matched, "Android lifecycle_hook pattern should match Activity.onCreate"

    def test_android_application_pattern_matching(self, tmp_path: Path) -> None:
        """Android Application.onCreate matches lifecycle_hook pattern."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.framework_patterns import load_framework_patterns

        (tmp_path / "MyApp.java").write_text("""
public class MyApp extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
    }
}
""")

        result = analyze_java(tmp_path)

        # Load Android patterns
        android_patterns = load_framework_patterns("android")

        # Find the onCreate method
        methods = [s for s in result.symbols if s.kind == "method"]
        on_create = [m for m in methods if "onCreate" in m.name]
        assert len(on_create) == 1

        # Check that a pattern matches
        matched = False
        for pattern in android_patterns.patterns:
            match_result = pattern.matches(on_create[0])
            if match_result is not None and match_result.get("concept") == "lifecycle_hook":
                matched = True
                assert match_result.get("matched_parent_base_class") == "Application"
                break

        assert matched, "Android lifecycle_hook pattern should match Application.onCreate"


class TestJavaLambdaCallAttribution:
    """Tests for call edge attribution inside Java lambda expressions.

    Java uses lambdas heavily in streams (map, filter, forEach). Calls inside these
    lambdas must be attributed to the enclosing method.
    """

    def test_call_inside_lambda_stream_attributed(self, tmp_path: Path) -> None:
        """Calls inside stream lambdas are attributed to enclosing method.

        When you have:
            public void process() {
                list.stream().forEach(item -> helper(item));
            }

        The call to helper() should be attributed to process, not lost.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "App.java"
        java_file.write_text("""
import java.util.List;

public class App {
    public void helper(int x) {
        System.out.println(x);
    }

    public void process() {
        List<Integer> items = List.of(1, 2, 3);
        items.stream().forEach(item -> helper(item));
    }
}
""")

        result = analyze_java(tmp_path)

        # Find symbols
        process_method = next(
            (s for s in result.symbols if "process" in s.name and s.kind == "method"),
            None,
        )
        helper_method = next(
            (s for s in result.symbols if "helper" in s.name and s.kind == "method"),
            None,
        )

        assert process_method is not None, "Should find process method"
        assert helper_method is not None, "Should find helper method"

        # The call to helper() inside the lambda should be attributed to process
        call_edge = next(
            (
                e for e in result.edges
                if e.src == process_method.id
                and e.dst == helper_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call to helper() inside stream lambda should be attributed to process"

    def test_call_inside_callback_lambda_attributed(self, tmp_path: Path) -> None:
        """Calls inside callback lambdas are attributed to enclosing method."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Callback.java"
        java_file.write_text("""
public class Callback {
    public void worker() {
        System.out.println("working");
    }

    public void runCallback(Runnable r) {
        r.run();
    }

    public void caller() {
        runCallback(() -> worker());
    }
}
""")

        result = analyze_java(tmp_path)

        # Find symbols
        caller_method = next(
            (s for s in result.symbols if "caller" in s.name and s.kind == "method"),
            None,
        )
        worker_method = next(
            (s for s in result.symbols if "worker" in s.name and s.kind == "method"),
            None,
        )

        assert caller_method is not None
        assert worker_method is not None

        # The call to worker() inside the lambda should be attributed to caller
        call_edge = next(
            (
                e for e in result.edges
                if e.src == caller_method.id
                and e.dst == worker_method.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call inside callback lambda should be attributed to caller"


class TestNormalizeJavaSignature:
    """Tests for Java signature normalization (ADR-0014 §3)."""

    def test_basic_method(self) -> None:
        from hypergumbo_lang_mainstream.java import normalize_java_signature
        assert normalize_java_signature("(String name, int age) User") == "(String,int)User"

    def test_void_method(self) -> None:
        from hypergumbo_lang_mainstream.java import normalize_java_signature
        assert normalize_java_signature("(String msg)") == "(String)"

    def test_generics(self) -> None:
        from hypergumbo_lang_mainstream.java import normalize_java_signature
        result = normalize_java_signature("(List<T> items) Map<K, V>", ["T", "K", "V"])
        assert result == "(List<$0>)Map<$1, $2>"

    def test_none(self) -> None:
        from hypergumbo_lang_mainstream.java import normalize_java_signature
        assert normalize_java_signature(None) is None


class TestJavaMethodReferences:
    """Tests for Java method reference (::) detection."""

    def test_static_method_reference(self, tmp_path: Path) -> None:
        """Detects App::transform as a references edge."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text(
            "import java.util.List;\n"
            "class App {\n"
            "    static String transform(String s) { return s; }\n"
            "    void run(List<String> items) {\n"
            "        items.stream().map(App::transform);\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert any(
            "transform" in e.dst and "run" in e.src
            for e in ref_edges
        ), f"Expected method reference edge, got: {ref_edges}"

    def test_instance_method_reference(self, tmp_path: Path) -> None:
        """Detects this::process as a references edge."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text(
            "import java.util.List;\n"
            "class App {\n"
            "    void process(String s) {}\n"
            "    void run(List<String> items) {\n"
            "        items.forEach(this::process);\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert any(
            "process" in e.dst and "run" in e.src
            for e in ref_edges
        ), f"Expected instance method reference edge, got: {ref_edges}"

    def test_constructor_reference(self, tmp_path: Path) -> None:
        """Detects StringBuilder::new as an instantiates edge."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text(
            "import java.util.List;\n"
            "class App {\n"
            "    void run(List<String> items) {\n"
            "        items.stream().map(StringBuilder::new);\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        # Constructor references should not produce edges since
        # StringBuilder is an external class, not in the codebase.
        # Just verify no crash occurs.
        assert result.symbols  # analysis completed

    def test_method_reference_evidence_type(self, tmp_path: Path) -> None:
        """Method reference edges have correct evidence_type."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text(
            "import java.util.List;\n"
            "class App {\n"
            "    static int parse(String s) { return 0; }\n"
            "    void run(List<String> items) {\n"
            "        items.stream().map(App::parse);\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        matching = [e for e in ref_edges if "parse" in e.dst]
        assert len(matching) == 1
        assert matching[0].evidence_type == "method_reference"

    def test_constructor_reference_to_local_class(self, tmp_path: Path) -> None:
        """Detects Widget::new as an instantiates edge to local class."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text(
            "import java.util.List;\n"
            "class Widget {}\n"
        )
        (tmp_path / "Runner.java").write_text(
            "import java.util.List;\n"
            "import java.util.stream.Collectors;\n"
            "class Runner {\n"
            "    void run(List<String> items) {\n"
            "        items.stream().map(Widget::new).collect(Collectors.toList());\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        inst_edges = [e for e in result.edges if e.edge_type == "instantiates"]
        assert any(
            "Widget" in e.dst and "run" in e.src
            for e in inst_edges
        ), f"Expected constructor reference edge, got: {inst_edges}"

    def test_method_reference_no_enclosing_method(self, tmp_path: Path) -> None:
        """Method reference at class level (e.g. field initializer) is ignored gracefully."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text(
            "import java.util.function.Function;\n"
            "class App {\n"
            "    static int transform(int x) { return x; }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        # No crash, no spurious edges
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) == 0


class TestClassNameCollision:
    """Tests for INV-finak: Java class name collision dampening.

    When a file imports an external library class (e.g., org.slf4j.Logger)
    and source code contains an inner class with the same simple name
    (e.g., Config.Logger), the NameResolver's suffix matching incorrectly
    resolves the simple name to the inner class. The fix checks file-level
    imports: if the import FQN doesn't match the resolved candidate's path,
    the edge is skipped (the developer intended the external class).
    """

    def test_instantiation_skipped_when_import_mismatches(self, tmp_path: Path) -> None:
        """new Logger() should NOT resolve to Config.Logger when file imports org.slf4j.Logger."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # File with inner class named Logger
        (tmp_path / "Config.java").write_text(
            "package com.example.config;\n"
            "public class Config {\n"
            "    public static class Logger {\n"
            "        public void log(String msg) {}\n"
            "    }\n"
            "}\n"
        )
        # File that imports external Logger and instantiates it
        (tmp_path / "App.java").write_text(
            "package com.example.app;\n"
            "import org.slf4j.Logger;\n"
            "public class App {\n"
            "    public void run() {\n"
            "        Logger log = new Logger();\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)

        # Find the inner class symbol
        config_logger = next(
            (s for s in result.symbols if s.name == "Config.Logger"), None
        )
        assert config_logger is not None, "Config.Logger should be extracted"

        # App.run should NOT have an instantiates edge to Config.Logger
        app_run = next(
            (s for s in result.symbols if s.name == "App.run"), None
        )
        assert app_run is not None, "App.run should be extracted"

        false_edges = [
            e for e in result.edges
            if e.edge_type == "instantiates"
            and e.src == app_run.id
            and e.dst == config_logger.id
        ]
        assert len(false_edges) == 0, (
            f"new Logger() in App.run should NOT resolve to Config.Logger "
            f"when file imports org.slf4j.Logger (got {len(false_edges)} false edges)"
        )

    def test_instantiation_allowed_when_import_matches(self, tmp_path: Path) -> None:
        """new Logger() SHOULD resolve when file imports the matching source-code class."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Use directory structure matching the package declaration
        logging_dir = tmp_path / "com" / "example" / "logging"
        logging_dir.mkdir(parents=True)
        app_dir = tmp_path / "com" / "example" / "app"
        app_dir.mkdir(parents=True)

        # File with top-level Logger class
        (logging_dir / "Logger.java").write_text(
            "package com.example.logging;\n"
            "public class Logger {\n"
            "    public void log(String msg) {}\n"
            "}\n"
        )
        # File that imports the source-code Logger and instantiates it
        (app_dir / "App.java").write_text(
            "package com.example.app;\n"
            "import com.example.logging.Logger;\n"
            "public class App {\n"
            "    public void run() {\n"
            "        Logger log = new Logger();\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)

        logger_sym = next(
            (s for s in result.symbols if s.name == "Logger"), None
        )
        assert logger_sym is not None

        app_run = next(
            (s for s in result.symbols if s.name == "App.run"), None
        )
        assert app_run is not None

        inst_edges = [
            e for e in result.edges
            if e.edge_type == "instantiates"
            and e.src == app_run.id
            and e.dst == logger_sym.id
        ]
        assert len(inst_edges) == 1, (
            "new Logger() should resolve when import matches source-code class"
        )

    def test_instantiation_allowed_when_import_matches_short_path(self, tmp_path: Path) -> None:
        """new Logger() SHOULD resolve when import short-matches source-code class path.

        Covers the short-path match branch in _is_import_class_mismatch: when
        the full FQN path doesn't match but the last 2 segments (package/Class)
        do, the edge is allowed.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        # Directory structure uses abbreviated path that doesn't match full FQN
        # but DOES match short path: "logging/Logger"
        logging_dir = tmp_path / "src" / "logging"
        logging_dir.mkdir(parents=True)
        app_dir = tmp_path / "src" / "app"
        app_dir.mkdir(parents=True)

        (logging_dir / "Logger.java").write_text(
            "package com.example.logging;\n"
            "public class Logger {\n"
            "    public void log(String msg) {}\n"
            "}\n"
        )
        (app_dir / "App.java").write_text(
            "package com.example.app;\n"
            "import com.example.logging.Logger;\n"
            "public class App {\n"
            "    public void run() {\n"
            "        Logger log = new Logger();\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)

        logger_sym = next(
            (s for s in result.symbols if s.name == "Logger"), None
        )
        assert logger_sym is not None

        app_run = next(
            (s for s in result.symbols if s.name == "App.run"), None
        )
        assert app_run is not None

        inst_edges = [
            e for e in result.edges
            if e.edge_type == "instantiates"
            and e.src == app_run.id
            and e.dst == logger_sym.id
        ]
        assert len(inst_edges) == 1, (
            "new Logger() should resolve when import short-path matches"
        )

    def test_type_inferred_call_skipped_when_import_mismatches(self, tmp_path: Path) -> None:
        """logger.log() via type inference should NOT resolve to Config.Logger.log
        when file imports org.slf4j.Logger."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # File with inner class Logger that has a log method
        (tmp_path / "Config.java").write_text(
            "package com.example.config;\n"
            "public class Config {\n"
            "    public static class Logger {\n"
            "        public void log(String msg) {}\n"
            "    }\n"
            "}\n"
        )
        # File that imports external Logger and calls methods on it
        (tmp_path / "Service.java").write_text(
            "package com.example.service;\n"
            "import org.slf4j.Logger;\n"
            "public class Service {\n"
            "    private Logger logger;\n"
            "    public void process() {\n"
            "        logger.log(\"hello\");\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)

        config_logger_log = next(
            (s for s in result.symbols if s.name == "Config.Logger.log"), None
        )
        assert config_logger_log is not None

        service_process = next(
            (s for s in result.symbols if s.name == "Service.process"), None
        )
        assert service_process is not None

        false_edges = [
            e for e in result.edges
            if e.edge_type == "calls"
            and e.src == service_process.id
            and e.dst == config_logger_log.id
        ]
        assert len(false_edges) == 0, (
            f"logger.log() should NOT resolve to Config.Logger.log "
            f"when file imports org.slf4j.Logger (got {len(false_edges)} false edges)"
        )


    def test_nested_class_not_resolved_from_bare_name_without_import(
        self, tmp_path: Path,
    ) -> None:
        """new Properties() should NOT resolve to Log4jConfiguration.Properties.

        When a file uses a bare class name like Properties without any import,
        and the only matching symbol is a nested class (Log4jConfiguration.Properties),
        the edge should be skipped. In Java, you can't use a bare inner class name
        from outside the outer class — you'd need Log4jConfiguration.Properties.
        This is the Kafka bakeoff false positive scenario (WI-bunul).
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        # File with nested class named Properties
        (tmp_path / "Log4jConfiguration.java").write_text(
            "package kafka.docker;\n"
            "public class Log4jConfiguration {\n"
            "    public static class Properties {\n"
            "        public String getProperty(String key) { return null; }\n"
            "    }\n"
            "}\n"
        )
        # File that uses bare Properties (meaning java.util.Properties, not the nested one)
        (tmp_path / "WordCount.java").write_text(
            "package kafka.streams;\n"
            "import java.util.*;\n"
            "public class WordCount {\n"
            "    public static void main(String[] args) {\n"
            "        Properties props = new Properties();\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)

        nested_props = next(
            (s for s in result.symbols
             if s.name == "Log4jConfiguration.Properties"),
            None,
        )
        assert nested_props is not None, "Nested Properties class should be extracted"

        wc_main = next(
            (s for s in result.symbols if s.name == "WordCount.main"), None,
        )
        assert wc_main is not None, "WordCount.main should be extracted"

        false_edges = [
            e for e in result.edges
            if e.edge_type == "instantiates"
            and e.src == wc_main.id
            and e.dst == nested_props.id
        ]
        assert len(false_edges) == 0, (
            f"new Properties() should NOT resolve to Log4jConfiguration.Properties "
            f"— bare name cannot refer to a nested class from outside the outer class "
            f"(got {len(false_edges)} false edges)"
        )

    def test_nested_class_resolved_from_same_file(
        self, tmp_path: Path,
    ) -> None:
        """new Properties() SHOULD resolve to nested Properties when in the same file.

        Inside the outer class's file, bare inner class names are valid Java.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Log4jConfiguration.java").write_text(
            "package kafka.docker;\n"
            "public class Log4jConfiguration {\n"
            "    public static class Properties {\n"
            "        public String getProperty(String key) { return null; }\n"
            "    }\n"
            "    public void configure() {\n"
            "        Properties p = new Properties();\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)

        nested_props = next(
            (s for s in result.symbols
             if s.name == "Log4jConfiguration.Properties"),
            None,
        )
        assert nested_props is not None

        configure = next(
            (s for s in result.symbols
             if s.name == "Log4jConfiguration.configure"),
            None,
        )
        assert configure is not None

        edges = [
            e for e in result.edges
            if e.edge_type == "instantiates"
            and e.src == configure.id
            and e.dst == nested_props.id
        ]
        assert len(edges) == 1, (
            "new Properties() inside Log4jConfiguration should resolve to "
            "the nested Properties class"
        )

    def test_static_call_skipped_when_import_mismatches(
        self, tmp_path: Path,
    ) -> None:
        """WI-fuhaj Case 2: Logger.info() as a static call should NOT resolve
        to a local Logger.info when the file imports org.slf4j.Logger.

        Covers `_process_java_method_calls` Case 2 (``ClassName.method()``
        static call). Before the fix, Case 2 emitted an ``ast_call_static``
        edge without consulting file imports, so any file with a local class
        named ``Logger`` absorbed every ``Logger.xxx(...)`` static call across
        the repo regardless of whether the caller imported slf4j's ``Logger``
        or the local one.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        # Local top-level Logger class with a static info() method.
        (tmp_path / "Logger.java").write_text(
            "package com.local;\n"
            "public class Logger {\n"
            "    public static void info(String msg) {}\n"
            "}\n"
        )
        # App imports org.slf4j.Logger and calls Logger.info(...) statically.
        (tmp_path / "App.java").write_text(
            "package com.app;\n"
            "import org.slf4j.Logger;\n"
            "public class App {\n"
            "    public void run() {\n"
            "        Logger.info(\"hello\");\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)

        local_info = next(
            (s for s in result.symbols if s.name == "Logger.info"), None,
        )
        assert local_info is not None, "Local Logger.info should be extracted"

        app_run = next(
            (s for s in result.symbols if s.name == "App.run"), None,
        )
        assert app_run is not None

        false_edges = [
            e for e in result.edges
            if e.edge_type == "calls"
            and e.src == app_run.id
            and e.dst == local_info.id
        ]
        assert len(false_edges) == 0, (
            f"Logger.info() static call should NOT resolve to com.local.Logger.info "
            f"when App.java imports org.slf4j.Logger "
            f"(got {len(false_edges)} false edges). "
            f"WI-fuhaj: Case 2 needs the _is_import_class_mismatch guard."
        )

    def test_instance_call_fallback_skipped_when_import_mismatches(
        self, tmp_path: Path,
    ) -> None:
        """WI-fuhaj Case 3 fallback: log.trace() on a Logger field should NOT
        fall back to a local Logger class edge when the file imports slf4j.

        Covers the Kafka bakeoff false-positive pattern: a local POJO named
        ``Logger`` (e.g. Jackson config class) absorbs every ``log.trace/
        log.error/log.warn`` call in every file that imports ``org.slf4j.
        Logger``, because ``resolver.lookup("Logger.trace")`` fails (the POJO
        has no trace method), Case 3 falls through to its ``class_symbols.get
        ("Logger")`` fallback, and emits a calls edge to the wrong Logger
        class symbol. On Kafka this produced 2057+ bogus edges to a 50-line
        POJO. Fix: the fallback must also consult ``_is_import_class_mismatch``.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        # Local top-level Logger POJO with setters only — no trace/error/etc.
        (tmp_path / "Logger.java").write_text(
            "package com.local.pojo;\n"
            "public class Logger {\n"
            "    private String name;\n"
            "    public String getName() { return name; }\n"
            "    public void setName(String n) { this.name = n; }\n"
            "}\n"
        )
        # Service imports slf4j Logger and calls log.trace("...").
        (tmp_path / "Service.java").write_text(
            "package com.svc;\n"
            "import org.slf4j.Logger;\n"
            "public class Service {\n"
            "    private Logger log;\n"
            "    public void doWork() {\n"
            "        log.trace(\"hello\");\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)

        local_logger_cls = next(
            (s for s in result.symbols
             if s.name == "Logger" and s.kind == "class"), None,
        )
        assert local_logger_cls is not None, (
            "Local Logger class symbol should be extracted"
        )

        service_dowork = next(
            (s for s in result.symbols if s.name == "Service.doWork"), None,
        )
        assert service_dowork is not None

        false_edges = [
            e for e in result.edges
            if e.edge_type == "calls"
            and e.src == service_dowork.id
            and e.dst == local_logger_cls.id
        ]
        assert len(false_edges) == 0, (
            f"log.trace() should NOT fall back to the local Logger class "
            f"when Service.java imports org.slf4j.Logger "
            f"(got {len(false_edges)} false edges). "
            f"WI-fuhaj: Case 3 fallback needs the _is_import_class_mismatch guard."
        )


class TestJavaAmbiguousMethodGuard:
    """Tests for AMB-METHOD invariant in Java.

    When a method name appears in 3+ classes and the resolver falls back
    to suffix matching, the analyzer must not create false-positive edges
    to an arbitrary class's implementation.
    """

    def test_ambiguous_method_via_suffix_not_resolved(
        self, tmp_path: Path,
    ) -> None:
        """Bare process() with 3 classes defining process() → no false edge.

        In Java, method calls are always class-qualified by the analyzer.
        But when the qualified name doesn't exist (e.g., inherited method),
        the NameResolver falls back to suffix matching. This test verifies
        that when 3+ classes define the same method, the ambiguity guard
        prevents false resolution.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Server.java").write_text("""
public class Server {
    public void process() { }
}
""")
        (tmp_path / "Client.java").write_text("""
public class Client {
    public void process() { }
}
""")
        (tmp_path / "Worker.java").write_text("""
public class Worker {
    public void process() { }
}
""")
        # Orchestrator calls process() which is NOT defined in its own class.
        # Without a receiver, lookup becomes "Orchestrator.process" which
        # doesn't exist → falls to suffix matching → finds "process" in
        # Server/Client/Worker → should NOT pick one.
        (tmp_path / "Orchestrator.java").write_text("""
public class Orchestrator {
    public void run() {
        process();
    }
}
""")

        result = analyze_java(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        run_calls = [
            e for e in call_edges
            if any("Orchestrator.run" in e.src for _ in [1])
        ]

        # Should NOT resolve to Server/Client/Worker.process
        for edge in run_calls:
            if "process" in edge.dst.lower():
                assert "Server.process" not in edge.dst, (
                    f"Should not resolve to Server.process: {edge.dst}"
                )
                assert "Client.process" not in edge.dst, (
                    f"Should not resolve to Client.process: {edge.dst}"
                )
                assert "Worker.process" not in edge.dst, (
                    f"Should not resolve to Worker.process: {edge.dst}"
                )


class TestJavaInferredReturnType:
    """Tests for inferring concrete return types from method bodies.

    When a Java method declares return type Object but the body only contains
    'return new TokenEndpoint(...)', the concrete type 'TokenEndpoint' should
    be inferred and stored as 'inferred_return_type' in symbol metadata.
    This enables JAX-RS subresource locator path chaining for methods that
    return Object (common in keycloak/jersey projects).
    """

    def test_infer_return_type_from_new_expression(self, tmp_path: Path) -> None:
        """When return type is Object, infer concrete type from return new X()."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "ProtocolService.java"
        java_file.write_text("""
import javax.ws.rs.*;

@Path("/protocol")
public class ProtocolService {
    @Path("token")
    public Object token() {
        return new TokenEndpoint(session, tokenManager);
    }

    @Path("auth")
    public Object auth() {
        return new AuthorizationEndpoint(session, event);
    }

    @GET
    @Path("status")
    public Response getStatus() {
        return Response.ok().build();
    }
}
""")

        result = analyze_java(tmp_path)

        # Find the token() method
        token_method = next(
            (s for s in result.symbols if "token" in s.name and s.kind == "method"),
            None,
        )
        assert token_method is not None, "Expected token method"
        assert token_method.meta is not None

        # Should have inferred_return_type = "TokenEndpoint"
        assert token_method.meta.get("inferred_return_type") == "TokenEndpoint", (
            f"Expected inferred_return_type='TokenEndpoint', got "
            f"{token_method.meta.get('inferred_return_type')}"
        )

        # auth() should also have inferred return type
        auth_method = next(
            (s for s in result.symbols if "auth" in s.name and s.kind == "method"),
            None,
        )
        assert auth_method is not None
        assert auth_method.meta.get("inferred_return_type") == "AuthorizationEndpoint"

        # getStatus() returns Response (concrete), should NOT have inferred_return_type
        status_method = next(
            (s for s in result.symbols if "getStatus" in s.name and s.kind == "method"),
            None,
        )
        assert status_method is not None
        assert "inferred_return_type" not in (status_method.meta or {})

    def test_no_infer_for_abstract_method(self, tmp_path: Path) -> None:
        """Abstract methods have no body, so inference returns None."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "AbstractService.java"
        java_file.write_text("""
public abstract class AbstractService {
    @javax.ws.rs.Path("sub")
    public abstract Object getSub();
}
""")

        result = analyze_java(tmp_path)
        method = next(
            (s for s in result.symbols if "getSub" in s.name and s.kind == "method"),
            None,
        )
        assert method is not None
        assert "inferred_return_type" not in (method.meta or {})

    def test_no_infer_for_bare_return(self, tmp_path: Path) -> None:
        """Bare 'return;' in an Object method should not infer a type."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Service.java"
        java_file.write_text("""
public class Service {
    @javax.ws.rs.Path("sub")
    public Object getSub() {
        return;
    }
}
""")

        result = analyze_java(tmp_path)
        method = next(
            (s for s in result.symbols if "getSub" in s.name and s.kind == "method"),
            None,
        )
        assert method is not None
        assert "inferred_return_type" not in (method.meta or {})

    def test_no_infer_for_variable_return(self, tmp_path: Path) -> None:
        """When method returns a variable (not new X()), don't infer type."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Service.java"
        java_file.write_text("""
public class Service {
    @javax.ws.rs.Path("sub")
    public Object getSub() {
        Object result = createEndpoint();
        return result;
    }
}
""")

        result = analyze_java(tmp_path)
        method = next(
            (s for s in result.symbols if "getSub" in s.name and s.kind == "method"),
            None,
        )
        assert method is not None
        assert "inferred_return_type" not in (method.meta or {})

    def test_no_infer_for_multiple_different_types(self, tmp_path: Path) -> None:
        """When method returns different types, don't infer."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Service.java"
        java_file.write_text("""
public class Service {
    @javax.ws.rs.Path("sub")
    public Object getSub(boolean flag) {
        if (flag) {
            return new EndpointA();
        }
        return new EndpointB();
    }
}
""")

        result = analyze_java(tmp_path)
        method = next(
            (s for s in result.symbols if "getSub" in s.name and s.kind == "method"),
            None,
        )
        assert method is not None
        assert "inferred_return_type" not in (method.meta or {})


class TestJavaInheritedMethodResolution:
    """Tests for resolving inherited method calls via this.method() or bare method().

    When a class extends a parent and calls a method defined in the parent
    (not overridden in the child), the call must resolve to the parent's
    method. PR-3 of INV-nilud (WI-dukog) lifted this walk from the Java
    analyzer (formerly ``java.py:1437-1461``) into the Tier-2
    ``inherited_calls`` linker (``_walk_single_then_interfaces``). The
    four Site-1 tests here drive the analyzer + the linker stack via the
    module-level ``_run_inherited_calls_pipeline`` helper. The Site-3
    (inherited field) tests below still call ``analyze_java`` directly —
    they will be migrated when PR-5 lifts Site 3.
    """

    def test_bare_call_to_parent_method_resolves(self, tmp_path: Path) -> None:
        """bare method() call resolves to parent class method via extends chain."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "BaseResource.java").write_text("""
public class BaseResource {
    protected void verifyNonNull(Object obj) {
        if (obj == null) throw new IllegalArgumentException();
    }
}
""")
        (tmp_path / "AccountResource.java").write_text("""
public class AccountResource extends BaseResource {
    public void createAccount(String name) {
        verifyNonNull(name);
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        create_method = next(
            (s for s in result.symbols if "createAccount" in s.name), None
        )
        verify_method = next(
            (s for s in result.symbols if "verifyNonNull" in s.name), None
        )

        assert create_method is not None, "Should find createAccount"
        assert verify_method is not None, "Should find verifyNonNull"

        # Should have a call edge from createAccount to verifyNonNull
        call_edges = [
            e for e in all_edges
            if e.src == create_method.id
            and e.dst == verify_method.id
            and e.edge_type == "calls"
            and e.is_resolved
        ]
        assert len(call_edges) == 1, (
            f"Expected edge from createAccount to verifyNonNull (inherited), "
            f"got {len(call_edges)}. "
            f"All edges from createAccount: "
            f"{[(e.edge_type, e.dst, e.evidence_type) for e in all_edges if e.src == create_method.id]}"
        )
        assert call_edges[0].evidence_type == "ast_call_inherited"
        assert call_edges[0].confidence <= 0.90  # Slightly lower than direct

    def test_this_call_to_parent_method_resolves(self, tmp_path: Path) -> None:
        """this.method() call resolves to parent class method via extends chain."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "BaseController.java").write_text("""
public class BaseController {
    protected String formatResponse(Object data) {
        return data.toString();
    }
}
""")
        (tmp_path / "UserController.java").write_text("""
public class UserController extends BaseController {
    public String getUser(int id) {
        return this.formatResponse(id);
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        get_user = next(
            (s for s in result.symbols if "getUser" in s.name), None
        )
        format_resp = next(
            (s for s in result.symbols if "formatResponse" in s.name), None
        )

        assert get_user is not None
        assert format_resp is not None

        call_edges = [
            e for e in all_edges
            if e.src == get_user.id
            and e.dst == format_resp.id
            and e.edge_type == "calls"
            and e.is_resolved
        ]
        assert len(call_edges) == 1, (
            f"Expected edge from getUser to formatResponse (inherited), "
            f"got {len(call_edges)}. "
            f"All edges: "
            f"{[(e.edge_type, e.dst, e.evidence_type) for e in all_edges if e.src == get_user.id]}"
        )

    def test_grandparent_method_resolves(self, tmp_path: Path) -> None:
        """Method defined in grandparent class resolves through two extends."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Base.java").write_text("""
public class Base {
    protected void validate() {}
}
""")
        (tmp_path / "Middle.java").write_text("""
public class Middle extends Base {
    protected void transform() {}
}
""")
        (tmp_path / "Child.java").write_text("""
public class Child extends Middle {
    public void process() {
        validate();
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        process_method = next(
            (s for s in result.symbols if "process" in s.name), None
        )
        validate_method = next(
            (s for s in result.symbols if "validate" in s.name), None
        )

        assert process_method is not None
        assert validate_method is not None

        call_edges = [
            e for e in all_edges
            if e.src == process_method.id
            and e.dst == validate_method.id
            and e.edge_type == "calls"
            and e.is_resolved
        ]
        assert len(call_edges) == 1, (
            "Expected edge from process to validate (inherited from grandparent)"
        )

    def test_own_method_preferred_over_parent(self, tmp_path: Path) -> None:
        """When method exists in both child and parent, child method is preferred."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Parent.java").write_text("""
public class Parent {
    protected void doWork() {}
}
""")
        (tmp_path / "Child.java").write_text("""
public class Child extends Parent {
    protected void doWork() {}
    public void run() {
        doWork();
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        run_method = next(
            (s for s in result.symbols if "run" in s.name), None
        )
        child_dowork = next(
            (s for s in result.symbols
             if "doWork" in s.name and "Child" in s.name), None
        )

        assert run_method is not None
        assert child_dowork is not None

        call_edges = [
            e for e in all_edges
            if e.src == run_method.id
            and e.dst == child_dowork.id
            and e.edge_type == "calls"
            and e.is_resolved
        ]
        # Should resolve to Child.doWork (direct), not Parent.doWork
        assert len(call_edges) == 1
        assert call_edges[0].evidence_type == "ast_call_direct"

    def test_direct_call_confidence_within_derived_band(
        self, tmp_path: Path
    ) -> None:
        """INV-zatug: a resolved direct call must sit inside the ADR-0039
        band for ``ast_call_direct``.

        The producer scaled a hardcoded 0.95 by the resolver multiplier, so a
        fully-resolved call published 0.95 — above the pathway's 0.85 ceiling.
        The multiplier is meaningful and is kept; only its *base* moves to the
        registry seed, so the value tracks the inference pathway instead of a
        per-emitter literal. Asserted against ``confidence_within_band`` rather
        than a golden number so the test follows the registry if the seed
        changes.
        """
        from hypergumbo_core.confidence import confidence_within_band
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Main.java").write_text(
            "public class Main {\n"
            "    private int helper() { return 1; }\n"
            "    public int process() { return helper(); }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        direct = [
            e for e in result.edges
            if e.edge_type == "calls"
            and e.evidence_type == "ast_call_direct"
            and e.is_resolved
        ]
        assert direct, "expected a resolved direct call edge"
        for edge in direct:
            assert confidence_within_band("ast_call_direct", edge.confidence), (
                f"{edge.confidence} outside the ast_call_direct band"
            )

    def test_inherited_field_method_call(self, tmp_path: Path) -> None:
        """Call on field declared in parent class resolves via inheritance."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "AccountUserApi.java").write_text("""
public interface AccountUserApi {
    void createAccount(String name);
}
""")
        (tmp_path / "BaseResource.java").write_text("""
public class BaseResource {
    protected AccountUserApi accountApi;
}
""")
        (tmp_path / "AccountResource.java").write_text("""
public class AccountResource extends BaseResource {
    public void create(String name) {
        accountApi.createAccount(name);
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        create = next(
            (s for s in result.symbols if "AccountResource" in s.name and "create" in s.name and s.kind == "method"),
            None,
        )
        api_method = next(
            (s for s in result.symbols if "createAccount" in s.name), None
        )

        assert create is not None, "Should find AccountResource.create"
        assert api_method is not None, "Should find AccountUserApi.createAccount"

        # WI-puvil (PR-5): linker resolves the inherited-field call via
        # Site-3 (walks AccountResource's parent BaseResource, reads
        # meta['fields']['accountApi']='AccountUserApi', then looks up
        # createAccount on the interface).
        call_edges = [
            e for e in all_edges
            if e.src == create.id
            and e.dst == api_method.id
            and e.edge_type == "calls"
            and e.is_resolved
        ]
        assert len(call_edges) == 1, (
            f"Expected edge from create to createAccount via inherited field, "
            f"got {len(call_edges)}. "
            f"All edges from create: "
            f"{[(e.edge_type, e.dst, e.evidence_type) for e in all_edges if e.src == create.id]}"
        )
        assert call_edges[0].evidence_type == "ast_call_inherited_field"
        assert call_edges[0].confidence <= 0.85

    def test_inherited_field_from_grandparent(self, tmp_path: Path) -> None:
        """Field declared in grandparent resolves through two extends levels."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Logger.java").write_text("""
public class Logger {
    public void info(String msg) {}
}
""")
        (tmp_path / "GrandBase.java").write_text("""
public class GrandBase {
    protected Logger logger;
}
""")
        (tmp_path / "MiddleBase.java").write_text("""
public class MiddleBase extends GrandBase {
}
""")
        (tmp_path / "Service.java").write_text("""
public class Service extends MiddleBase {
    public void run() {
        logger.info("started");
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        run_method = next(
            (s for s in result.symbols if "Service" in s.name and "run" in s.name),
            None,
        )
        info_method = next(
            (s for s in result.symbols if "Logger.info" in s.name), None
        )

        assert run_method is not None
        assert info_method is not None

        # WI-puvil (PR-5): linker walks Service→MiddleBase→GrandBase
        # via extends edges, finds logger:Logger in GrandBase's
        # meta['fields'], resolves info() on Logger.
        call_edges = [
            e for e in all_edges
            if e.src == run_method.id
            and e.dst == info_method.id
            and e.edge_type == "calls"
            and e.is_resolved
        ]
        assert len(call_edges) == 1
        assert call_edges[0].evidence_type == "ast_call_inherited_field"


class TestJavaTestPathPreference:
    """Tests for preferring non-test symbols over test symbols in resolution.

    When production code calls a method like ``server.startup()``, the
    resolver should prefer ``Server.startup`` over ``LogCleanerTest.startup``
    if both exist. This prevents false positives from test class methods
    polluting call graphs of production code.
    """

    def test_prod_code_prefers_prod_symbol(self, tmp_path: Path) -> None:
        """Production caller resolves to production method, not test method."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Production file with a Server class
        (tmp_path / "Server.java").write_text("""
public class Server {
    public void startup() {}
}
""")
        # Test file with same method name
        test_dir = tmp_path / "src" / "test" / "java"
        test_dir.mkdir(parents=True)
        (test_dir / "ServerTest.java").write_text("""
public class ServerTest {
    public void startup() {}
}
""")
        # Production caller
        (tmp_path / "App.java").write_text("""
public class App {
    public void run() {
        Server server = new Server();
        server.startup();
    }
}
""")

        result = analyze_java(tmp_path)

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and "startup" in e.dst
        ]

        # Should resolve to Server.startup, not ServerTest.startup
        for edge in call_edges:
            if "run" in edge.src:
                assert "ServerTest" not in edge.dst, (
                    f"Production code should not resolve to test class. "
                    f"Got dst={edge.dst}"
                )


class TestJavaShapeId:
    """Tests for shape_id computation in Java (ADR-0014 §1)."""

    def test_method_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Example.java").write_text(
            "public class Example {\n"
            "  public int add(int a, int b) { return a + b; }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        method = next(s for s in result.symbols if s.kind == "method")
        assert method.shape_id is not None
        assert method.shape_id.startswith("sha256:")

    def test_class_has_shape_id(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Example.java").write_text(
            "public class Example {\n"
            "  public void run() {}\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        cls = next(s for s in result.symbols if s.kind == "class")
        assert cls.shape_id is not None
        assert cls.shape_id.startswith("sha256:")


class TestJavaUnresolvedExternalEdges:
    """Tests for unresolved-external call edges (stdlib calls like InputStream.read)."""

    def test_stdlib_method_call_creates_unresolved_edge(self, tmp_path: Path) -> None:
        """Calls to stdlib methods emit unresolved edges."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "IO.java").write_text("""
public class IO {
    public void readFile(String path) throws Exception {
        java.io.FileInputStream fis = new java.io.FileInputStream(path);
        fis.read();
        fis.close();
    }
}
""")

        result = analyze_java(tmp_path)

        unresolved = [
            e for e in result.edges
            if not e.is_resolved
        ]
        callee_names = {e.dst.split(":")[-2] for e in unresolved}
        # fis.read() and fis.close() should appear as unresolved
        assert "fis.read" in callee_names or "read" in callee_names
        for e in unresolved:
            assert e.confidence == 0.50

    def test_typed_local_receiver_puts_the_TYPE_in_the_module_slot(
        self, tmp_path: Path,
    ) -> None:
        """``File f = new File(p); f.createNewFile()`` names ``java.io.File``.

        INV-linub. The analyzer already infers the receiver's type into
        ``var_types`` and threads it out as ``meta["receiver_type_hint"]`` for
        the Tier-2 ``inherited_calls`` / ``receiver_type_dispatch`` linkers,
        which resolve to PROJECT-INTERNAL symbols. Nothing carried that type
        into the dst's module slot, so the edge named the *variable* —
        ``java:external:0-0:f.createNewFile:unresolved``. The type was computed
        and then discarded for every external-surface consumer.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Sites.java").write_text("""
import java.io.File;

public class Sites {
    public static void write(String p) throws Exception {
        File f = new File(p);
        f.createNewFile();
    }
}
""")

        result = analyze_java(tmp_path)
        edge = next(
            e for e in result.edges
            if not e.is_resolved and "createNewFile" in e.dst
        )
        assert edge.dst == (
            "java:java.io.File:0-0:createNewFile:unresolved"
        ), edge.dst
        # The hint the Tier-2 linkers read must survive unchanged — this fix
        # ADDS a consumer for the inferred type, it does not move it.
        assert (edge.meta or {}).get("receiver_type_hint") == "File"

    def test_typed_local_receiver_edge_reaches_the_taint_catalogue(
        self, tmp_path: Path,
    ) -> None:
        """The point of the module slot: production's matcher must accept it.

        Asserted through ``_match_propagation_entry`` rather than by eyeballing
        the dst, because the dst string is not the contract — reaching the
        catalogue entry is. ``gate_named_entry`` refuses an untyped method call
        unconditionally (``if call_construct == "method": return None``), so an
        edge that merely EXISTS matches nothing; only one carrying a real module
        hint can win. Measured 2026-08-06: java emitted for both the two-step
        and chained shapes and matched on neither.
        """
        from hypergumbo_core.taint import (
            _build_callee_index,
            _match_propagation_entry,
            load_builtin_taint_catalog,
        )
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Sites.java").write_text("""
import java.io.File;

public class Sites {
    public static void write(String p) throws Exception {
        File f = new File(p);
        f.createNewFile();
    }
}
""")

        result = analyze_java(tmp_path)
        catalog = load_builtin_taint_catalog()
        index = _build_callee_index(catalog.sinks_for_language("java"))
        ambiguous = catalog.ambiguous_names_for_language("java")

        edge = next(
            e for e in result.edges
            if not e.is_resolved and "createNewFile" in e.dst
        )
        matched = _match_propagation_entry(
            index, edge.dst, ambiguous,
            (edge.meta or {}).get("call_construct"),
            is_resolved=edge.is_resolved, language="java",
        )
        assert matched is not None, (
            f"edge {edge.dst} still does not reach the catalogue"
        )
        assert matched.qualified_name == "java.io.File.createNewFile"

    def test_typed_receiver_does_not_register_a_PHANTOM_sanitizer(
        self, tmp_path: Path,
    ) -> None:
        """A wrong-typed receiver must not bind a catalogued barrier.

        THE REGRESSION THIS EXISTS FOR. Putting the receiver type in the module
        slot also SHORTENS the callee name from ``w.doFinal`` to ``doFinal``.
        ``_register_sanitizer_callers`` matches sanitizers on the SHORT NAME and
        never consults the module hint, so the shortening alone made
        ``com.example.Widget.doFinal`` bind ``javax.crypto.Cipher.doFinal``.
        Before the module-slot change the name was ``w.doFinal``, which is not
        an index key, so the exposure did not exist — the fix would have traded
        a recall win for a PHANTOM BARRIER.

        That trade is strictly bad and the direction is why: a phantom barrier
        earns ``sanitized``, and a sanitized flow is DROPPED from the claim's
        violation set (#214). Missing a barrier costs precision; inventing one
        costs a real finding, silently. ``_register_sanitizer_callers``'s own
        docstring makes the same ruling (INV-finoh).

        The guard is ``call_construct="method"``, which that function already
        honours — and which does NOT cost the sink match, because
        ``_lookup_named_entry`` takes its module-filter branch and returns
        before ever reaching ``gate_named_entry`` when a real module hint is
        present.
        """
        from collections import defaultdict

        from hypergumbo_core.taint import (
            _build_sanitizer_index_multi,
            _register_sanitizer_callers,
            load_builtin_taint_catalog,
        )
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Wrong.java").write_text("""
import com.example.Widget;

public class Wrong {
    public static void go(byte[] pt) throws Exception {
        Widget w = new Widget();
        w.doFinal(pt);
    }
}
""")

        result = analyze_java(tmp_path)
        edge = next(
            e for e in result.edges
            if not e.is_resolved and "doFinal" in e.dst
        )
        assert (edge.meta or {}).get("call_construct") == "method"

        catalog = load_builtin_taint_catalog()
        index = _build_sanitizer_index_multi(
            catalog.sanitizers_for_language("java"),
        )
        edge_dicts = [{
            "src": e.src, "dst": e.dst, "type": e.edge_type,
            "is_resolved": e.is_resolved, "meta": dict(e.meta or {}),
        } for e in result.edges]
        callers: dict = defaultdict(dict)
        _register_sanitizer_callers(
            edge_dicts, index, callers,
            catalog.ambiguous_names_for_language("java"), {},
        )
        assert not callers, (
            f"phantom barrier registered from a non-Cipher receiver: {dict(callers)}"
        )

    def test_untyped_receiver_still_names_the_variable(
        self, tmp_path: Path,
    ) -> None:
        """No inferred type ⇒ unchanged behaviour, NOT an invented module.

        The non-destructiveness half (L12): when the fix cannot do better it
        must do exactly what it did before. A receiver with no entry in
        ``var_types`` has no type evidence, and manufacturing one would be the
        bare-name category error this whole area is trying to stop.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Sites.java").write_text("""
public class Sites {
    public static void go(Object o) {
        o.hashCode();
    }
}
""")

        result = analyze_java(tmp_path)
        edge = next(
            e for e in result.edges
            if not e.is_resolved and "hashCode" in e.dst
        )
        assert edge.dst == "java:external:0-0:o.hashCode:unresolved", edge.dst

    def test_typed_local_whose_type_is_not_imported_is_left_alone(
        self, tmp_path: Path,
    ) -> None:
        """A type we cannot qualify must NOT become a bare simple name.

        ``Helper h = new Helper(); h.run();`` with no import gives a type of
        ``Helper`` and no fully-qualified path for it. Writing ``Helper`` into
        the module slot would assert a module that does not exist and could
        collide with a catalogued entry of the same short name — the failure
        mode INV-fazim documents from the other direction.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Sites.java").write_text("""
public class Sites {
    public static void go() {
        Helper h = new Helper();
        h.run();
    }
}
""")

        result = analyze_java(tmp_path)
        edge = next(
            e for e in result.edges
            if not e.is_resolved and "run" in e.dst
        )
        assert edge.dst == "java:external:0-0:h.run:unresolved", edge.dst

    def test_resolved_call_not_unresolved(self, tmp_path: Path) -> None:
        """When callee IS in project, no unresolved edge."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text("""
public class App {
    public void helper() {}
    public void run() { helper(); }
}
""")

        result = analyze_java(tmp_path)

        call_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and "run" in e.src and "helper" in e.dst
        ]
        assert any(e.evidence_type == "ast_call_direct" for e in call_edges)
        # helper() should NOT produce an unresolved edge
        unresolved_helper = [
            e for e in result.edges
            if not e.is_resolved and "helper" in e.dst
        ]
        assert len(unresolved_helper) == 0


# WI-lozug PR 3: wire emit_module_attribute_refs into _analyze_java_impl so
# that attribute reads on java.lang.System (and imported classes) produce
# ``module_attr_ref`` edges that io_boundary can tag against the
# ``attributes:`` entries in ``io_primitives/java.yaml``.
class TestJavaModuleAttrRefs:
    """Tests for ``module_attr_ref`` emission in Java (WI-lozug PR 3)."""

    def test_emits_module_attr_ref_for_system_out_assignment(
        self, tmp_path: Path,
    ) -> None:
        """``PrintStream ps = System.out;`` emits module_attr_ref for
        System.out.  System is an implicit java.lang import, so the
        helper injects it into the imports map on every Java file."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text("""import java.io.PrintStream;

public class App {
    public void run() {
        PrintStream ps = System.out;
        ps.println("hi");
    }
}
""")

        result = analyze_java(tmp_path)

        attr_edges = [
            e for e in result.edges if e.edge_type == "module_attr_ref"
        ]
        assert any(
            e.dst.endswith(":System.out:attribute") for e in attr_edges
        ), (
            f"Expected System.out to emit module_attr_ref; got: "
            f"{[e.dst for e in attr_edges]}"
        )
        sys_edge = next(
            e for e in attr_edges if e.dst.endswith(":System.out:attribute")
        )
        assert sys_edge.confidence == 0.85
        assert sys_edge.evidence_type == "module_attribute_reference"

    def test_emits_system_out_as_method_invocation_receiver(
        self, tmp_path: Path,
    ) -> None:
        """``System.out.println("hi");`` — System.out is the receiver
        of a method_invocation, and must STILL emit module_attr_ref.
        This is a deliberate deviation from the Go/JS double-count
        rule: io_boundary tags callee names against the primitives
        catalog, and ``println`` is not in the catalog.  If we skipped
        the receiver, the ``attributes: [out]`` entry would never
        match on the dominant usage pattern (every Java program that
        prints to stdout)."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text("""public class App {
    public static void main(String[] args) {
        System.out.println("hi");
    }
}
""")

        result = analyze_java(tmp_path)

        attr_edges = [
            e for e in result.edges if e.edge_type == "module_attr_ref"
        ]
        assert any(
            e.dst.endswith(":System.out:attribute") for e in attr_edges
        ), (
            f"System.out as method receiver must still emit "
            f"module_attr_ref; got: {[e.dst for e in attr_edges]}"
        )

    def test_skips_local_variable_field_access(
        self, tmp_path: Path,
    ) -> None:
        """``cfg.name`` where ``cfg`` is a local variable (not an
        imported class) must NOT emit module_attr_ref — the base is
        not in the imports map."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "App.java").write_text("""public class App {
    static class Cfg {
        public String name = "bar";
    }

    public void run() {
        Cfg cfg = new Cfg();
        String x = cfg.name;
    }
}
""")

        result = analyze_java(tmp_path)

        attr_edges = [
            e for e in result.edges if e.edge_type == "module_attr_ref"
        ]
        assert not any(
            ":cfg.name:attribute" in e.dst for e in attr_edges
        ), (
            f"cfg.name is a local field access, not an imported "
            f"class attribute; got: {[e.dst for e in attr_edges]}"
        )


class TestJavaSite2Site3UnresolvedHints:
    """WI-sivuk (PR-4 of INV-nilud): analyzer threads Site-2/3 hints into
    the final unresolved emit (``java.py:1647-1727``) so the inherited_calls
    linker (PR-5) can resolve typed-receiver and inherited-field calls.

    Also covers the WI-dukog (PR-3) follow-up: Case 1's ``else`` branch at
    ``java.py:1459-1464`` must set ``edge_added = True`` so the final
    fallback emit doesn't fire a second time for the same call site.
    """

    def test_case1_else_emits_single_unresolved_edge(
        self, tmp_path: Path,
    ) -> None:
        """bare ``method()`` whose name isn't in the current class must
        emit exactly ONE unresolved edge (with ``enclosing_class`` hint),
        not two — pre-PR-4 the Case 1 ``else`` branch fell through to the
        final unresolved emit, double-counting every inherited bare call.
        """
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Base.java").write_text("""
public class Base {
    protected void helper() {}
}
""")
        (tmp_path / "Sub.java").write_text("""
public class Sub extends Base {
    public void run() {
        helper();
    }
}
""")

        result = analyze_java(tmp_path)

        helper_unresolved = [
            e for e in result.edges
            if not e.is_resolved and "helper" in e.dst
        ]
        assert len(helper_unresolved) == 1, (
            f"Expected exactly 1 unresolved edge for bare helper() call "
            f"(Case 1 else must set edge_added=True). "
            f"Got {len(helper_unresolved)}: "
            f"{[(e.dst, e.meta) for e in helper_unresolved]}"
        )
        assert helper_unresolved[0].meta == {
            "callee_name": "helper", "enclosing_class": "Sub",
        }

    def test_this_else_emits_single_unresolved_edge(
        self, tmp_path: Path,
    ) -> None:
        """``this.method()`` whose name isn't in the current class must
        emit exactly ONE unresolved edge with ``enclosing_class`` hint."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Base.java").write_text("""
public class Base {
    protected void format(Object o) {}
}
""")
        (tmp_path / "Sub.java").write_text("""
public class Sub extends Base {
    public void run() {
        this.format(null);
    }
}
""")

        result = analyze_java(tmp_path)

        fmt_unresolved = [
            e for e in result.edges
            if not e.is_resolved and "format" in e.dst
        ]
        assert len(fmt_unresolved) == 1, (
            f"Expected exactly 1 unresolved edge for this.format() call. "
            f"Got {len(fmt_unresolved)}: "
            f"{[(e.dst, e.meta) for e in fmt_unresolved]}"
        )
        # ASSERTED KEY BY KEY, not as a whole-dict equality. This test's
        # subject is the Site-1 hint -- ONE unresolved edge carrying
        # ``enclosing_class`` so the inherited_calls linker can recover it --
        # and a dict equality made it also assert the ABSENCE of every key
        # anyone might add later. INV-pirot added one: ``this.format(null)``
        # did not resolve, so the declaring type is not established here, and
        # the two taint gates read ``call_construct`` to know that. The linker
        # reads ``enclosing_class``, never that key, so the recovery this test
        # protects is unchanged.
        meta = fmt_unresolved[0].meta or {}
        assert {k: meta.get(k) for k in ("callee_name", "enclosing_class")} == {
            "callee_name": "format", "enclosing_class": "Sub",
        }
        assert meta["call_construct"] == "method"

    def test_site2_unresolved_carries_receiver_type_hint(
        self, tmp_path: Path,
    ) -> None:
        """``var.method()`` where var's type isn't a project symbol must
        emit an unresolved edge with ``receiver_type_hint`` meta set to
        the inferred type's short name."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # InputStream is not a project symbol — neither in class_symbols
        # nor resolvable as InputStream.read. Parameter type captures
        # the simple name into ``var_types``.
        (tmp_path / "Reader.java").write_text("""
public class Reader {
    public void process(InputStream stream) {
        stream.read();
    }
}
""")

        result = analyze_java(tmp_path)

        read_unresolved = [
            e for e in result.edges
            if not e.is_resolved and "stream.read" in e.dst
        ]
        assert len(read_unresolved) == 1, (
            f"Expected exactly 1 unresolved edge for stream.read(). "
            f"Got {len(read_unresolved)}: "
            f"{[(e.dst, e.meta) for e in read_unresolved]}"
        )
        assert read_unresolved[0].meta is not None
        assert read_unresolved[0].meta.get("receiver_type_hint") == (
            "InputStream"
        ), (
            f"Expected receiver_type_hint=InputStream on unresolved "
            f"stream.read() edge. Got meta={read_unresolved[0].meta}"
        )

    def test_site2_resolved_fallback_through_linker(
        self, tmp_path: Path,
    ) -> None:
        """WI-puvil (PR-5): the ``ast_call_inherited_method`` fallback
        for ``var.method()`` now fires from the inherited_calls linker
        (Site 2, Step 3 — fallback to the type symbol when neither
        direct nor MRO walk resolves)."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "OwnerRepository.java").write_text("""
public interface OwnerRepository {
    Owner findByLastName(String lastName);
}
""")
        (tmp_path / "OwnerController.java").write_text("""
public class OwnerController {
    private OwnerRepository owners;

    public void processCreation(Owner owner) {
        this.owners.save(owner);
    }
}
""")

        result = analyze_java(tmp_path)
        all_edges = _run_inherited_calls_pipeline(result)

        repo_iface = next(
            (s for s in result.symbols
             if s.name == "OwnerRepository" and s.kind == "interface"),
            None,
        )
        assert repo_iface is not None

        inherited_method_edges = [
            e for e in all_edges
            if e.evidence_type == "ast_call_inherited_method"
            and e.dst == repo_iface.id
            and e.is_resolved
        ]
        assert len(inherited_method_edges) == 1

    def test_site3_unresolved_carries_inherited_field_receiver(
        self, tmp_path: Path,
    ) -> None:
        """``field.method()`` where ``field`` is not in var_types nor in
        class_symbols and the analyzer's parent walk doesn't find it on
        any known parent — must still emit an unresolved edge with
        ``inherited_field_receiver`` meta set to the receiver name."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # ``log`` looks like an inherited field, but no parent class
        # declaring it exists in the project. Class_fields contains
        # MyService's empty field set; current_class is set; receiver
        # is not in var_types or class_symbols.
        (tmp_path / "Parent.java").write_text("""
public class Parent {
    protected String otherField;
}
""")
        (tmp_path / "MyService.java").write_text("""
public class MyService extends Parent {
    public void doWork() {
        log.info("hello");
    }
}
""")

        result = analyze_java(tmp_path)

        log_unresolved = [
            e for e in result.edges
            if not e.is_resolved and "log.info" in e.dst
        ]
        assert len(log_unresolved) == 1, (
            f"Expected exactly 1 unresolved edge for log.info(). "
            f"Got {len(log_unresolved)}: "
            f"{[(e.dst, e.meta) for e in log_unresolved]}"
        )
        assert log_unresolved[0].meta is not None
        assert log_unresolved[0].meta.get("inherited_field_receiver") == (
            "log"
        ), (
            f"Expected inherited_field_receiver=log on unresolved "
            f"log.info() edge. Got meta={log_unresolved[0].meta}"
        )

    def test_case4_unresolved_has_no_site_hints(
        self, tmp_path: Path,
    ) -> None:
        """When the call goes through Case 4 (imported receiver, not
        Site 2 nor Site 3), the unresolved edge must NOT carry
        ``receiver_type_hint`` or ``inherited_field_receiver``."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # ``Arrays.asList(...)`` — receiver_name=Arrays is not in
        # var_types (no Arrays variable declared) and not in
        # class_symbols (Arrays isn't a project class). Goes to Case 4.
        (tmp_path / "App.java").write_text("""
import java.util.Arrays;
public class App {
    public void run() {
        Arrays.asList(1, 2, 3);
    }
}
""")

        result = analyze_java(tmp_path)

        arr_unresolved = [
            e for e in result.edges
            if not e.is_resolved and "Arrays.asList" in e.dst
        ]
        assert len(arr_unresolved) == 1
        meta = arr_unresolved[0].meta or {}
        assert "receiver_type_hint" not in meta, (
            f"Case 4 fallback must not carry receiver_type_hint; "
            f"got meta={meta}"
        )
        assert "inherited_field_receiver" not in meta, (
            f"Case 4 fallback must not carry inherited_field_receiver; "
            f"got meta={meta}"
        )


class TestJavaLinesOfCode:
    """Tests for line_span on Java symbols."""

    def test_class_line_span(self, tmp_path: Path) -> None:
        """Class symbols have line_span set from span."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Foo.java").write_text(
            "public class Foo {\n"
            "    public int bar() {\n"
            "        return 1;\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        cls = next(s for s in result.symbols if s.name == "Foo")
        assert cls.line_span == 5


class TestJavaDocstring:
    """INV-jahiv Phase 4 PR3: Symbol.docstring on Java callable symbols."""

    def test_method_with_javadoc(self, tmp_path: Path) -> None:
        """Method symbols populate docstring from preceding Javadoc."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Foo.java"
        java_file.write_text(
            "public class Foo {\n"
            "    /**\n"
            "     * Adds two numbers together.\n"
            "     * @param a first\n"
            "     */\n"
            "    public int add(int a, int b) {\n"
            "        return a + b;\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        add = next(s for s in result.symbols if s.name == "Foo.add")
        assert add.docstring == "Adds two numbers together."

    def test_method_without_javadoc(self, tmp_path: Path) -> None:
        """Method symbols have docstring=None when no preceding Javadoc."""
        from hypergumbo_lang_mainstream.java import analyze_java

        java_file = tmp_path / "Foo.java"
        java_file.write_text(
            "public class Foo {\n"
            "    public int sub(int a, int b) {\n"
            "        return a - b;\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        sub = next(s for s in result.symbols if s.name == "Foo.sub")
        assert sub.docstring is None


class TestJavaQualifiedName:
    """ADR-0032 Phase 4 PR4: Symbol.qualified_name on Java symbols."""

    def test_method_qualified_name_uses_dot_separator(self, tmp_path: Path) -> None:
        """Methods carry package.Class.method qualified_name with '.' separator."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "User.java").write_text(
            "package com.example.app;\n"
            "public class User {\n"
            "    public int getAge() { return 42; }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        method = next(s for s in result.symbols if s.name == "User.getAge")
        assert method.qualified_name == "com.example.app.User.getAge"
        # Separator is '.' for Java (no '::' or '\\').
        assert "::" not in method.qualified_name
        assert "\\" not in method.qualified_name

    def test_class_qualified_name_includes_package(self, tmp_path: Path) -> None:
        """Class symbols carry package.Class qualified_name."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "User.java").write_text(
            "package com.example.app;\n"
            "public class User {}\n"
        )
        result = analyze_java(tmp_path)
        cls = next(s for s in result.symbols if s.name == "User")
        assert cls.qualified_name == "com.example.app.User"

    def test_top_level_class_without_package(self, tmp_path: Path) -> None:
        """A class without a package declaration has just its name as qualified_name."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Bare.java").write_text(
            "public class Bare {}\n"
        )
        result = analyze_java(tmp_path)
        cls = next(s for s in result.symbols if s.name == "Bare")
        assert cls.qualified_name == "Bare"


class TestJavaCyclomaticComplexity:
    """Phase 4 PR5: cyclomatic_complexity populated on Java callables."""

    def test_method_complexity_populated(self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "C.java").write_text(
            "class C {\n"
            "  int branchy(int a) {\n"
            "    if (a > 0 && a < 10) return 1;\n"
            "    for (int i = 0; i < 3; i++) {}\n"
            "    return a > 0 ? 1 : 0;\n"
            "  }\n"
            "  int simple() { return 0; }\n"
            "  public C() {}\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        branchy = next(s for s in result.symbols if s.name == "C.branchy")
        simple = next(s for s in result.symbols if s.name == "C.simple")
        ctor = next(s for s in result.symbols if s.kind == "constructor")
        assert simple.cyclomatic_complexity == 1
        assert ctor.cyclomatic_complexity == 1
        assert branchy.cyclomatic_complexity is not None
        assert branchy.cyclomatic_complexity >= 4


class TestJavaImportEdges:
    """Imports-edge emission (INV-gojit).

    The Java analyzer historically extracted import declarations only into
    name-resolution dicts (``_extract_imports`` / ``_extract_static_imports``
    / ``_extract_wildcard_imports`` build simple-name → FQN maps) and never
    surfaced them as graph ``imports`` edges, leaving Java the lone mainstream
    analyzer whose emission-parity ``(java, edge_imports)`` cell was a strict
    xfail (``edge_types == [calls]``). These tests pin the closure: every
    import declaration — regular, ``import static``, and wildcard — emits one
    file → external-ref ``imports`` edge.
    """

    @staticmethod
    def _imports_edges(result):
        return [e for e in result.edges if e.edge_type == "imports"]

    def test_regular_and_stdlib_imports_emit_edges(self, tmp_path: Path) -> None:
        """INV-gojit filed repro: both a stdlib (``java.util.List``) and a
        non-stdlib (``com.example.helper.Formatter``) import emit an imports
        edge — previously zero were produced (only ``calls`` edges)."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "Main.java").write_text(
            "import java.util.List;\n"
            "import com.example.helper.Formatter;\n"
            "public class Main {\n"
            "    public void run() {\n"
            "        List<String> xs = null;\n"
            "        Formatter f = new Formatter();\n"
            "    }\n"
            "}\n"
        )
        result = analyze_java(tmp_path)
        imports = self._imports_edges(result)
        modules = {e.dst_ref.module_path for e in imports if e.dst_ref}
        names = {e.dst_ref.name for e in imports if e.dst_ref}
        # The module slot is the FULL import specifier (Java imports a type,
        # not a package), at parity with Go/C# — this is what lets
        # refine_frameworks prefix-match framework package patterns.
        assert "java.util.List" in modules
        assert "com.example.helper.Formatter" in modules
        assert {"List", "Formatter"} <= names

    def test_import_edge_src_is_file_node(self, tmp_path: Path) -> None:
        """The imports edge originates at the file node (make_file_id shape)."""
        from hypergumbo_lang_mainstream.java import analyze_java
        from hypergumbo_core.analyze.base import make_file_id

        java_file = tmp_path / "A.java"
        java_file.write_text("import java.util.Map;\npublic class A {}\n")
        result = analyze_java(tmp_path)
        imports = self._imports_edges(result)
        assert imports
        expected_src = make_file_id("java", str(java_file))
        assert all(e.src == expected_src for e in imports)

    def test_import_edge_evidence_and_confidence(self, tmp_path: Path) -> None:
        """Regular imports carry the registered ``import_declaration`` evidence
        type at sibling-parity confidence (0.95)."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "A.java").write_text(
            "import java.util.Set;\npublic class A {}\n"
        )
        result = analyze_java(tmp_path)
        edge = next(iter(self._imports_edges(result)))
        assert edge.evidence_type == "import_declaration"
        assert edge.confidence == 0.95

    def test_static_import_emits_edge(self, tmp_path: Path) -> None:
        """``import static`` emits an imports edge whose module slot is the full
        import specifier and whose name is the member, tagged with the
        registered ``import_static`` evidence type."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "A.java").write_text(
            "import static java.util.Collections.singletonList;\n"
            "public class A {}\n"
        )
        result = analyze_java(tmp_path)
        statics = [
            e for e in self._imports_edges(result)
            if e.evidence_type == "import_static"
        ]
        assert len(statics) == 1
        ref = statics[0].dst_ref
        assert ref is not None
        assert ref.module_path == "java.util.Collections.singletonList"
        assert ref.name == "singletonList"

    def test_wildcard_import_emits_edge(self, tmp_path: Path) -> None:
        """Wildcard ``import java.util.*;`` emits one imports edge naming the
        package, with ``*`` as the member sentinel."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "A.java").write_text(
            "import java.util.*;\npublic class A {}\n"
        )
        result = analyze_java(tmp_path)
        wildcards = [
            e for e in self._imports_edges(result)
            if e.dst_ref and e.dst_ref.name == "*"
        ]
        assert len(wildcards) == 1
        assert wildcards[0].dst_ref.module_path == "java.util"
        assert wildcards[0].evidence_type == "import_declaration"

    def test_static_wildcard_import_emits_edge(self, tmp_path: Path) -> None:
        """Static wildcard ``import static java.util.Arrays.*;`` emits an
        ``import_static`` edge naming the owning type with the ``*`` sentinel."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "A.java").write_text(
            "import static java.util.Arrays.*;\npublic class A {}\n"
        )
        result = analyze_java(tmp_path)
        statics = [
            e for e in self._imports_edges(result)
            if e.evidence_type == "import_static"
            and e.dst_ref and e.dst_ref.name == "*"
        ]
        assert len(statics) == 1
        assert statics[0].dst_ref.module_path == "java.util.Arrays"

    def test_no_imports_no_imports_edges(self, tmp_path: Path) -> None:
        """A file with no import declarations emits zero imports edges."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "A.java").write_text(
            "public class A {\n    public void run() {}\n}\n"
        )
        result = analyze_java(tmp_path)
        assert self._imports_edges(result) == []

    def test_one_edge_per_import_declaration(self, tmp_path: Path) -> None:
        """Edge count matches the import-declaration count exactly (no over-
        or under-emission)."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "A.java").write_text(
            "import java.util.List;\n"
            "import java.util.Map;\n"
            "import static java.util.Collections.emptyList;\n"
            "import java.io.*;\n"
            "public class A {}\n"
        )
        result = analyze_java(tmp_path)
        assert len(self._imports_edges(result)) == 4
