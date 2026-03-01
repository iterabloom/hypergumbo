"""Branch coverage tests for Kotlin analyzer.

These tests specifically target uncovered branches in kotlin.py.
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
# Tests for function signature extraction branch coverage
# ============================================================================


def test_kotlin_function_with_nullable_param(tmp_path: Path) -> None:
    """Cover branch: nullable type parameter extraction (line 172->173).

    Kotlin function with nullable parameter: fun foo(name: String?).
    """
    kt_file = tmp_path / "Service.kt"
    kt_file.write_text(
        "class Service {\n"
        "    fun process(data: String?) {\n"
        "        data?.let { println(it) }\n"
        "    }\n"
        "\n"
        "    fun handle(value: Int?, fallback: String?): String? {\n"
        "        return value?.toString() ?: fallback\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert any("process" in name for name in method_names)
    assert any("handle" in name for name in method_names)


def test_kotlin_function_with_function_type_param(tmp_path: Path) -> None:
    """Cover branch: function type parameter extraction (line 172->173).

    Kotlin function with lambda parameter: fun foo(callback: () -> Unit).
    """
    kt_file = tmp_path / "Handler.kt"
    kt_file.write_text(
        "class Handler {\n"
        "    fun execute(action: () -> Unit) {\n"
        "        action()\n"
        "    }\n"
        "\n"
        "    fun transform(mapper: (Int) -> String): String {\n"
        "        return mapper(42)\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert any("execute" in name for name in method_names)
    assert any("transform" in name for name in method_names)


def test_kotlin_function_with_return_type(tmp_path: Path) -> None:
    """Cover branch: return type extraction (line 177->178).

    Kotlin function with explicit return type.
    """
    kt_file = tmp_path / "Calculator.kt"
    kt_file.write_text(
        "class Calculator {\n"
        "    fun add(a: Int, b: Int): Int {\n"
        "        return a + b\n"
        "    }\n"
        "\n"
        "    fun greeting(): String {\n"
        "        return \"Hello\"\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert any("add" in name for name in method_names)
    assert any("greeting" in name for name in method_names)


# ============================================================================
# Tests for class declaration branch coverage
# ============================================================================


def test_kotlin_interface_declaration(tmp_path: Path) -> None:
    """Cover branch: interface detection (line 405).

    Kotlin interface declaration.
    """
    kt_file = tmp_path / "Repository.kt"
    kt_file.write_text(
        "interface Repository {\n"
        "    fun findById(id: Long): Any?\n"
        "    fun save(entity: Any)\n"
        "}\n"
        "\n"
        "interface Service {\n"
        "    fun process()\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    interfaces = [n for n in data["nodes"] if n["kind"] == "interface"]
    interface_names = [i["name"] for i in interfaces]
    assert "Repository" in interface_names
    assert "Service" in interface_names


def test_kotlin_class_with_inheritance(tmp_path: Path) -> None:
    """Cover branch: delegation specifiers extraction (line 420->422).

    Kotlin class with base class: class Dog : Animal().
    """
    kt_file = tmp_path / "Animals.kt"
    kt_file.write_text(
        "open class Animal {\n"
        "    open fun speak() {\n"
        "        println(\"...\")\n"
        "    }\n"
        "}\n"
        "\n"
        "class Dog : Animal() {\n"
        "    override fun speak() {\n"
        "        println(\"Woof!\")\n"
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


def test_kotlin_class_with_interface(tmp_path: Path) -> None:
    """Cover branch: interface implementation extraction (line 307->311).

    Kotlin class implementing interface: class Handler : RequestHandler.
    """
    kt_file = tmp_path / "Interfaces.kt"
    kt_file.write_text(
        "interface RequestHandler {\n"
        "    fun handle()\n"
        "}\n"
        "\n"
        "class MyHandler : RequestHandler {\n"
        "    override fun handle() {\n"
        "        println(\"Handling\")\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    interfaces = [n for n in data["nodes"] if n["kind"] == "interface"]
    assert any("MyHandler" in c["name"] for c in classes)
    assert any("RequestHandler" in i["name"] for i in interfaces)


def test_kotlin_class_with_multiple_inheritance(tmp_path: Path) -> None:
    """Cover branch: multiple delegation specifiers (line 301->303).

    Kotlin class with class and multiple interfaces.
    """
    kt_file = tmp_path / "Multi.kt"
    kt_file.write_text(
        "interface Runnable {\n"
        "    fun run()\n"
        "}\n"
        "\n"
        "interface Closeable {\n"
        "    fun close()\n"
        "}\n"
        "\n"
        "open class Base\n"
        "\n"
        "class Worker : Base(), Runnable, Closeable {\n"
        "    override fun run() {}\n"
        "    override fun close() {}\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    class_names = [c["name"] for c in classes]
    assert "Worker" in class_names


# ============================================================================
# Tests for object declaration branch coverage
# ============================================================================


def test_kotlin_object_declaration(tmp_path: Path) -> None:
    """Cover branch: object declaration (line 444->470).

    Kotlin object (singleton) declaration.
    """
    kt_file = tmp_path / "Singleton.kt"
    kt_file.write_text(
        "object Logger {\n"
        "    fun log(message: String) {\n"
        "        println(message)\n"
        "    }\n"
        "}\n"
        "\n"
        "object Config {\n"
        "    val version = \"1.0\"\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    objects = [n for n in data["nodes"] if n["kind"] == "object"]
    object_names = [o["name"] for o in objects]
    assert "Logger" in object_names
    assert "Config" in object_names


def test_kotlin_companion_object(tmp_path: Path) -> None:
    """Cover branch: companion object methods.

    Kotlin companion object with static-like methods.
    """
    kt_file = tmp_path / "Factory.kt"
    kt_file.write_text(
        "class User private constructor(val name: String) {\n"
        "    companion object {\n"
        "        fun create(name: String): User {\n"
        "            return User(name)\n"
        "        }\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert any("User" in c["name"] for c in classes)


# ============================================================================
# Tests for import extraction branch coverage
# ============================================================================


def test_kotlin_import_qualified(tmp_path: Path) -> None:
    """Cover branch: qualified import extraction (line 262->263).

    Kotlin import with qualified identifier.
    """
    kt_file = tmp_path / "Client.kt"
    kt_file.write_text(
        "import com.example.service.UserService\n"
        "import kotlin.collections.ArrayList\n"
        "\n"
        "class Client {\n"
        "    fun process() {\n"
        "        // Use imports\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert any("Client" in c["name"] for c in classes)


def test_kotlin_import_simple(tmp_path: Path) -> None:
    """Cover branch: simple import extraction (line 264).

    Kotlin import with simple identifier.
    """
    kt_file = tmp_path / "App.kt"
    kt_file.write_text(
        "import java.util.*\n"
        "\n"
        "class App {\n"
        "    fun run() {\n"
        "        println(\"Running\")\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert any("App" in c["name"] for c in classes)


# ============================================================================
# Tests for call expression branch coverage
# ============================================================================


def test_kotlin_this_method_call(tmp_path: Path) -> None:
    """Cover branch: this.method() call (line 584->601).

    Kotlin method call with explicit this.
    """
    kt_file = tmp_path / "ThisCall.kt"
    kt_file.write_text(
        "class Service {\n"
        "    fun helper() {\n"
        "        println(\"Helper\")\n"
        "    }\n"
        "\n"
        "    fun process() {\n"
        "        this.helper()\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert any("helper" in name for name in method_names)
    assert any("process" in name for name in method_names)


def test_kotlin_static_object_call(tmp_path: Path) -> None:
    """Cover branch: Object.method() call (line 608->624).

    Kotlin call on singleton object.
    """
    kt_file = tmp_path / "ObjectCall.kt"
    kt_file.write_text(
        "object Logger {\n"
        "    fun log(msg: String) {\n"
        "        println(msg)\n"
        "    }\n"
        "}\n"
        "\n"
        "class App {\n"
        "    fun run() {\n"
        "        Logger.log(\"Running\")\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert any("log" in name for name in method_names)
    assert any("run" in name for name in method_names)


def test_kotlin_type_inferred_call(tmp_path: Path) -> None:
    """Cover branch: instance.method() with type inference (line 627->644).

    Kotlin call using type inference from constructor.
    """
    kt_file = tmp_path / "TypeInference.kt"
    kt_file.write_text(
        "class Database {\n"
        "    fun save(data: String) {\n"
        "        println(\"Saving: $data\")\n"
        "    }\n"
        "}\n"
        "\n"
        "class Client {\n"
        "    fun process() {\n"
        "        val db = Database()\n"
        "        db.save(\"test\")\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert any("save" in name for name in method_names)
    assert any("process" in name for name in method_names)


def test_kotlin_simple_function_call(tmp_path: Path) -> None:
    """Cover branch: simple function call (line 664->681).

    Kotlin direct function call without receiver.
    """
    kt_file = tmp_path / "FunctionCall.kt"
    kt_file.write_text(
        "fun helper() {\n"
        "    println(\"Helper\")\n"
        "}\n"
        "\n"
        "fun main() {\n"
        "    helper()\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = [f["name"] for f in funcs]
    assert "helper" in func_names
    assert "main" in func_names


def test_kotlin_top_level_function(tmp_path: Path) -> None:
    """Cover branch: top-level function (line 372->374).

    Kotlin function outside of class.
    """
    kt_file = tmp_path / "Utils.kt"
    kt_file.write_text(
        "fun add(a: Int, b: Int): Int {\n"
        "    return a + b\n"
        "}\n"
        "\n"
        "fun multiply(a: Int, b: Int): Int {\n"
        "    return a * b\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = [f["name"] for f in funcs]
    assert "add" in func_names
    assert "multiply" in func_names


# ============================================================================
# Tests for property declaration and type tracking
# ============================================================================


def test_kotlin_property_type_tracking(tmp_path: Path) -> None:
    """Cover branch: property type tracking from constructor (line 544->545).

    Kotlin val with constructor call for type inference.
    """
    kt_file = tmp_path / "PropertyType.kt"
    kt_file.write_text(
        "class Logger {\n"
        "    fun log(msg: String) {\n"
        "        println(msg)\n"
        "    }\n"
        "}\n"
        "\n"
        "class Service {\n"
        "    val logger = Logger()\n"
        "\n"
        "    fun process() {\n"
        "        logger.log(\"Processing\")\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    class_names = [c["name"] for c in classes]
    assert "Logger" in class_names
    assert "Service" in class_names


def test_kotlin_data_class(tmp_path: Path) -> None:
    """Cover branch: data class declaration.

    Kotlin data class extraction.
    """
    kt_file = tmp_path / "DataClass.kt"
    kt_file.write_text(
        "data class User(\n"
        "    val id: Long,\n"
        "    val name: String,\n"
        "    val email: String\n"
        ")\n"
        "\n"
        "data class Product(\n"
        "    val sku: String,\n"
        "    val price: Double\n"
        ")\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    class_names = [c["name"] for c in classes]
    assert "User" in class_names
    assert "Product" in class_names


def test_kotlin_parameter_type_inference(tmp_path: Path) -> None:
    """Cover branch: parameter type extraction (line 549->552).

    Kotlin function with typed parameters for type inference.
    """
    kt_file = tmp_path / "ParamInference.kt"
    kt_file.write_text(
        "class Client {\n"
        "    fun send(msg: String) {\n"
        "        println(msg)\n"
        "    }\n"
        "}\n"
        "\n"
        "class Service {\n"
        "    fun process(client: Client) {\n"
        "        client.send(\"Hello\")\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert any("send" in name for name in method_names)
    assert any("process" in name for name in method_names)
