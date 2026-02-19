"""Tests for Java analyzer."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

from hypergumbo_lang_mainstream import java as java_module


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

        assert result.run is not None
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        do_work = next(
            (s for s in result.symbols if s.name == "Controller.doWork"), None
        )
        assert do_work is not None

        # this.svc.process() should produce a call edge from doWork to
        # Service.process. The receiver is a field_access node ("this.svc"),
        # and the method name is "process".
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

        run = AnalysisRun.create(pass_id="java-v1", version="1.0")
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

        run = AnalysisRun.create(pass_id="java-v1", version="1.0")
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
        assert result.run.pass_id == "java-v1"

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
    """Tests for Spring Boot route detection via YAML patterns (ADR-0003 v1.0.x).

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
    """Tests for JAX-RS route detection via YAML patterns (ADR-0003 v1.0.x).

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
        # JAX-RS path is extracted from @Path annotation via resource_path concept
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

        assert result.run is not None
        assert result.run.files_analyzed == 2

        # Find the Caller.run -> Helper.doWork edge
        edges = result.edges
        caller_run = next(
            (s for s in result.symbols if "run" in s.name and "Caller" in s.id), None
        )
        helper_dowork = next(
            (s for s in result.symbols if "doWork" in s.name), None
        )

        assert caller_run is not None
        assert helper_dowork is not None

        # Should have edge from Caller.run to Helper.doWork via type inference
        call_edge = next(
            (
                e
                for e in edges
                if e.src == caller_run.id
                and e.dst == helper_dowork.id
                and e.edge_type == "calls"
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

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # c.send() should resolve via return type of createClient()
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

        controller_method = next(
            (s for s in result.symbols if "processCreation" in s.name), None
        )
        repo_iface = next(
            (s for s in result.symbols if s.name == "OwnerRepository" and s.kind == "interface"), None
        )

        assert controller_method is not None, "Should find processCreation"
        assert repo_iface is not None, "Should find OwnerRepository interface"

        # Should have an edge from processCreation to OwnerRepository
        # (fallback when specific method not found)
        call_edges = [
            e for e in result.edges
            if e.src == controller_method.id
            and e.dst == repo_iface.id
            and e.edge_type == "calls"
        ]
        assert len(call_edges) == 1, (
            f"Expected fallback edge to OwnerRepository, got {len(call_edges)}. "
            f"All edges from controller: "
            f"{[(e.edge_type, e.dst) for e in result.edges if e.src == controller_method.id]}"
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

        controller_method = next(
            (s for s in result.symbols if "search" in s.name), None
        )
        find_method = next(
            (s for s in result.symbols if "findByLastName" in s.name), None
        )

        assert controller_method is not None
        assert find_method is not None

        # Normal resolution to the actual method
        call_edges = [
            e for e in result.edges
            if e.src == controller_method.id
            and e.dst == find_method.id
            and e.edge_type == "calls"
        ]
        assert len(call_edges) == 1
        assert call_edges[0].evidence_type == "ast_call_type_inferred"


class TestJavaImportResolution:
    """Tests for import-based method call resolution."""

    def test_imported_class_static_method_resolution(self, tmp_path: Path) -> None:
        """Method calls resolved via import mapping."""
        from hypergumbo_lang_mainstream.java import analyze_java

        # Define utils in a package
        (tmp_path / "Utils.java").write_text("""
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

        # Should have edges from Handler.process to Database.save and Database.commit
        save_edge = next(
            (
                e
                for e in result.edges
                if e.src == handler_process.id
                and e.dst == db_save.id
                and e.edge_type == "calls"
            ),
            None,
        )
        commit_edge = next(
            (
                e
                for e in result.edges
                if e.src == handler_process.id
                and e.dst == db_commit.id
                and e.edge_type == "calls"
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

    ADR-0003 v1.1.x requires methods to include their parent class's base_classes
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
