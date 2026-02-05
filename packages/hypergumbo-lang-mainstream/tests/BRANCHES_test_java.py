"""Branch coverage tests for Java analyzer.

These tests specifically target uncovered branches in java.py.
They are in a separate file to allow easy management if they impact CI speed.

Strategy:
- Truly unreachable defensive code is marked with `# pragma: no cover` in the source
- Reachable edge cases are tested here
- Focus on branches that affect correctness, not obscure paths
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


# ============================================================================
# Tests for method signature extraction branch coverage
# ============================================================================


def test_java_method_with_array_param(tmp_path: Path) -> None:
    """Cover branch: array parameter with dimensions (line 179->171).

    Java method with array parameter: String[] args.
    """
    java_file = tmp_path / "Main.java"
    java_file.write_text(
        "public class Main {\n"
        "    public static void main(String[] args) {\n"
        "        System.out.println(\"Hello\");\n"
        "    }\n"
        "    \n"
        "    public void process(int[] numbers, String[] names) {\n"
        "        // process arrays\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert "Main.main" in method_names
    assert "Main.process" in method_names


def test_java_method_with_varargs(tmp_path: Path) -> None:
    """Cover branch: varargs parameter (line 183->196).

    Java method with varargs: String... args.
    """
    java_file = tmp_path / "Logger.java"
    java_file.write_text(
        "public class Logger {\n"
        "    public void log(String format, Object... args) {\n"
        "        System.out.printf(format, args);\n"
        "    }\n"
        "    \n"
        "    public String concat(String... parts) {\n"
        "        return String.join(\"\", parts);\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert "Logger.log" in method_names
    assert "Logger.concat" in method_names


def test_java_method_with_generic_param(tmp_path: Path) -> None:
    """Cover branch: generic type parameter.

    Java method with generic parameter: List<String> items.
    """
    java_file = tmp_path / "Service.java"
    java_file.write_text(
        "import java.util.List;\n"
        "import java.util.Map;\n"
        "\n"
        "public class Service {\n"
        "    public void processList(List<String> items) {\n"
        "        items.forEach(System.out::println);\n"
        "    }\n"
        "    \n"
        "    public Map<String, Integer> getStats() {\n"
        "        return null;\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert "Service.processList" in method_names
    assert "Service.getStats" in method_names


# ============================================================================
# Tests for class inheritance branch coverage
# ============================================================================


def test_java_class_extends(tmp_path: Path) -> None:
    """Cover branch: class extends another class.

    Java class inheritance.
    """
    java_file = tmp_path / "Animal.java"
    java_file.write_text(
        "public class Animal {\n"
        "    public void speak() {\n"
        "        System.out.println(\"...\");\n"
        "    }\n"
        "}\n"
    )

    dog_file = tmp_path / "Dog.java"
    dog_file.write_text(
        "public class Dog extends Animal {\n"
        "    @Override\n"
        "    public void speak() {\n"
        "        System.out.println(\"Woof\");\n"
        "    }\n"
        "    \n"
        "    public void fetch() {\n"
        "        System.out.println(\"Fetching\");\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    class_names = [c["name"] for c in classes]
    assert "Animal" in class_names
    assert "Dog" in class_names


def test_java_class_implements_interface(tmp_path: Path) -> None:
    """Cover branch: class implements interface.

    Java interface implementation.
    """
    java_file = tmp_path / "Handler.java"
    java_file.write_text(
        "public interface Runnable {\n"
        "    void run();\n"
        "}\n"
        "\n"
        "public class Handler implements Runnable {\n"
        "    @Override\n"
        "    public void run() {\n"
        "        System.out.println(\"Running\");\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] in ("class", "interface")]
    class_names = [c["name"] for c in classes]
    assert "Handler" in class_names


# ============================================================================
# Tests for annotation extraction branch coverage
# ============================================================================


def test_java_spring_controller(tmp_path: Path) -> None:
    """Cover branch: Spring @Controller and @RequestMapping.

    Spring MVC controller annotations.
    """
    java_file = tmp_path / "UserController.java"
    java_file.write_text(
        "import org.springframework.web.bind.annotation.*;\n"
        "\n"
        "@RestController\n"
        "@RequestMapping(\"/api/users\")\n"
        "public class UserController {\n"
        "    \n"
        "    @GetMapping\n"
        "    public List<User> getAllUsers() {\n"
        "        return userService.findAll();\n"
        "    }\n"
        "    \n"
        "    @GetMapping(\"/{id}\")\n"
        "    public User getUser(@PathVariable Long id) {\n"
        "        return userService.findById(id);\n"
        "    }\n"
        "    \n"
        "    @PostMapping\n"
        "    public User createUser(@RequestBody User user) {\n"
        "        return userService.save(user);\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert "UserController.getAllUsers" in method_names
    assert "UserController.getUser" in method_names
    assert "UserController.createUser" in method_names


def test_java_junit_test(tmp_path: Path) -> None:
    """Cover branch: JUnit @Test annotation.

    JUnit test methods.
    """
    java_file = tmp_path / "CalculatorTest.java"
    java_file.write_text(
        "import org.junit.Test;\n"
        "import static org.junit.Assert.*;\n"
        "\n"
        "public class CalculatorTest {\n"
        "    \n"
        "    @Test\n"
        "    public void testAdd() {\n"
        "        assertEquals(4, 2 + 2);\n"
        "    }\n"
        "    \n"
        "    @Test\n"
        "    public void testSubtract() {\n"
        "        assertEquals(0, 2 - 2);\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert "CalculatorTest.testAdd" in method_names
    assert "CalculatorTest.testSubtract" in method_names


# ============================================================================
# Tests for constructor extraction branch coverage
# ============================================================================


def test_java_constructor(tmp_path: Path) -> None:
    """Cover branch: constructor declaration.

    Java class constructors.
    """
    java_file = tmp_path / "Person.java"
    java_file.write_text(
        "public class Person {\n"
        "    private String name;\n"
        "    private int age;\n"
        "    \n"
        "    public Person() {\n"
        "        this.name = \"Unknown\";\n"
        "        this.age = 0;\n"
        "    }\n"
        "    \n"
        "    public Person(String name) {\n"
        "        this.name = name;\n"
        "        this.age = 0;\n"
        "    }\n"
        "    \n"
        "    public Person(String name, int age) {\n"
        "        this.name = name;\n"
        "        this.age = age;\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Constructors may be extracted as methods
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert "Person" in [c["name"] for c in classes]


# ============================================================================
# Tests for static/final modifiers branch coverage
# ============================================================================


def test_java_static_methods(tmp_path: Path) -> None:
    """Cover branch: static method declaration.

    Java static methods.
    """
    java_file = tmp_path / "Utils.java"
    java_file.write_text(
        "public class Utils {\n"
        "    public static String format(String s) {\n"
        "        return s.trim();\n"
        "    }\n"
        "    \n"
        "    public static int max(int a, int b) {\n"
        "        return a > b ? a : b;\n"
        "    }\n"
        "    \n"
        "    public static final String VERSION = \"1.0\";\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert "Utils.format" in method_names
    assert "Utils.max" in method_names


def test_java_inner_class(tmp_path: Path) -> None:
    """Cover branch: inner class declaration.

    Java inner/nested class.
    """
    java_file = tmp_path / "Outer.java"
    java_file.write_text(
        "public class Outer {\n"
        "    private int value;\n"
        "    \n"
        "    public class Inner {\n"
        "        public void accessOuter() {\n"
        "            System.out.println(value);\n"
        "        }\n"
        "    }\n"
        "    \n"
        "    public static class StaticInner {\n"
        "        public void doSomething() {\n"
        "            System.out.println(\"Static inner\");\n"
        "        }\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    class_names = [c["name"] for c in classes]
    assert "Outer" in class_names
    # Inner classes may be extracted with qualified names
    assert any("Inner" in name for name in class_names)


# ============================================================================
# Tests for enum extraction branch coverage
# ============================================================================


def test_java_enum(tmp_path: Path) -> None:
    """Cover branch: enum declaration.

    Java enum definition.
    """
    java_file = tmp_path / "Status.java"
    java_file.write_text(
        "public enum Status {\n"
        "    PENDING,\n"
        "    ACTIVE,\n"
        "    COMPLETED,\n"
        "    FAILED;\n"
        "    \n"
        "    public boolean isTerminal() {\n"
        "        return this == COMPLETED || this == FAILED;\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Enums may be extracted as class or enum kind
    classes = [n for n in data["nodes"] if n["kind"] in ("class", "enum")]
    class_names = [c["name"] for c in classes]
    assert "Status" in class_names
