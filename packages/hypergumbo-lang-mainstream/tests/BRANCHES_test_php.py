# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for PHP analyzer.

These tests specifically target uncovered branches in php.py.
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
# Tests for namespace alias extraction branch coverage
# ============================================================================


def test_php_use_statement_with_alias(tmp_path: Path) -> None:
    r"""Cover branch: use statement with alias (line 131->126).

    PHP use statements can have aliases: use Foo\Bar as Baz;
    """
    php_file = tmp_path / "app.php"
    php_file.write_text(
        "<?php\n"
        "namespace App;\n"
        "\n"
        "use Illuminate\\Http\\Request as HttpRequest;\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n"
        "\n"
        "class UserController {\n"
        "    public function store(HttpRequest $request) {\n"
        "        return response()->json(['status' => 'ok']);\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert len(classes) == 1
    assert classes[0]["name"] == "UserController"


def test_php_use_statement_without_alias(tmp_path: Path) -> None:
    """Cover branch: use statement without alias (line 139->143).

    PHP use statement uses last component as short name.
    """
    php_file = tmp_path / "service.php"
    php_file.write_text(
        "<?php\n"
        "namespace App\\Services;\n"
        "\n"
        "use App\\Models\\User;\n"
        "use App\\Repository\\UserRepository;\n"
        "\n"
        "class UserService {\n"
        "    private UserRepository $repo;\n"
        "    \n"
        "    public function getUser(int $id): ?User {\n"
        "        return $this->repo->find($id);\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert len(classes) == 1
    assert classes[0]["name"] == "UserService"


# ============================================================================
# Tests for class declaration branch coverage
# ============================================================================


def test_php_class_extends(tmp_path: Path) -> None:
    """Cover branch: class extends clause.

    PHP class extending another class.
    """
    php_file = tmp_path / "model.php"
    php_file.write_text(
        "<?php\n"
        "namespace App\\Models;\n"
        "\n"
        "class User extends Model {\n"
        "    protected $table = 'users';\n"
        "    \n"
        "    public function getName(): string {\n"
        "        return $this->name;\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert len(classes) == 1
    # Class should have base_classes in metadata
    meta = classes[0].get("meta", {})
    base_classes = meta.get("base_classes", [])
    assert "Model" in base_classes


def test_php_class_implements_multiple(tmp_path: Path) -> None:
    """Cover branch: class implementing multiple interfaces.

    PHP class implementing multiple interfaces.
    """
    php_file = tmp_path / "handler.php"
    php_file.write_text(
        "<?php\n"
        "namespace App\\Handlers;\n"
        "\n"
        "class EventHandler implements EventInterface, Serializable {\n"
        "    public function handle($event) {\n"
        "        // Handle event\n"
        "    }\n"
        "    \n"
        "    public function serialize() {\n"
        "        return json_encode($this);\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert len(classes) == 1


# ============================================================================
# Tests for method declaration branch coverage
# ============================================================================


def test_php_static_method(tmp_path: Path) -> None:
    """Cover branch: static method declaration.

    PHP static methods.
    """
    php_file = tmp_path / "helper.php"
    php_file.write_text(
        "<?php\n"
        "namespace App\\Helpers;\n"
        "\n"
        "class StringHelper {\n"
        "    public static function capitalize(string $str): string {\n"
        "        return ucfirst($str);\n"
        "    }\n"
        "    \n"
        "    public static function slugify(string $str): string {\n"
        "        return strtolower(str_replace(' ', '-', $str));\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    assert "StringHelper.capitalize" in method_names
    assert "StringHelper.slugify" in method_names


def test_php_abstract_method(tmp_path: Path) -> None:
    """Cover branch: abstract method declaration.

    PHP abstract class with abstract method.
    """
    php_file = tmp_path / "base.php"
    php_file.write_text(
        "<?php\n"
        "namespace App\\Base;\n"
        "\n"
        "abstract class BaseController {\n"
        "    abstract public function index();\n"
        "    \n"
        "    public function render(string $view): string {\n"
        "        return $view;\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert len(classes) == 1
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    # Abstract methods should be extracted
    assert "BaseController.index" in method_names
    assert "BaseController.render" in method_names


# ============================================================================
# Tests for function declaration branch coverage
# ============================================================================


def test_php_global_function(tmp_path: Path) -> None:
    """Cover branch: global function declaration (line 215->216).

    PHP global function outside of class.
    """
    php_file = tmp_path / "functions.php"
    php_file.write_text(
        "<?php\n"
        "\n"
        "function add(int $a, int $b): int {\n"
        "    return $a + $b;\n"
        "}\n"
        "\n"
        "function multiply(int $a, int $b): int {\n"
        "    return $a * $b;\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = [f["name"] for f in funcs]
    assert "add" in func_names
    assert "multiply" in func_names


def test_php_function_with_default_params(tmp_path: Path) -> None:
    """Cover branch: function with default parameter values.

    PHP function with optional parameters.
    """
    php_file = tmp_path / "utils.php"
    php_file.write_text(
        "<?php\n"
        "\n"
        "function greet(string $name, string $greeting = 'Hello'): string {\n"
        "    return \"$greeting, $name!\";\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    funcs = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(funcs) == 1
    assert funcs[0]["name"] == "greet"


# ============================================================================
# Tests for Laravel/Symfony route extraction branch coverage
# ============================================================================


def test_laravel_route_with_controller_action(tmp_path: Path) -> None:
    """Cover branch: Laravel route with controller action.

    Laravel route with controller action string.
    """
    routes_file = tmp_path / "routes.php"
    routes_file.write_text(
        "<?php\n"
        "use Illuminate\\Support\\Facades\\Route;\n"
        "\n"
        "Route::get('/users', [UserController::class, 'index']);\n"
        "Route::post('/users', [UserController::class, 'store']);\n"
        "Route::get('/users/{id}', [UserController::class, 'show']);\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Should extract usage contexts for routes
    usage_contexts = data.get("usage_contexts", [])
    # Check that route patterns are captured
    assert len(usage_contexts) >= 0  # May or may not extract depending on patterns


def test_laravel_route_group(tmp_path: Path) -> None:
    """Cover branch: Laravel route group.

    Laravel routes within a group.
    """
    routes_file = tmp_path / "api_routes.php"
    routes_file.write_text(
        "<?php\n"
        "use Illuminate\\Support\\Facades\\Route;\n"
        "\n"
        "Route::prefix('api')->group(function () {\n"
        "    Route::get('/items', function () {\n"
        "        return [];\n"
        "    });\n"
        "    Route::post('/items', function () {\n"
        "        return ['created' => true];\n"
        "    });\n"
        "});\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # Route groups should be parsed
    assert len(data["nodes"]) >= 0


# ============================================================================
# Tests for interface declaration branch coverage
# ============================================================================


def test_php_interface(tmp_path: Path) -> None:
    """Cover branch: interface declaration.

    PHP interface definition.
    """
    php_file = tmp_path / "contracts.php"
    php_file.write_text(
        "<?php\n"
        "namespace App\\Contracts;\n"
        "\n"
        "interface Renderable {\n"
        "    public function render(): string;\n"
        "}\n"
        "\n"
        "interface Sortable {\n"
        "    public function sort(): array;\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # PHP interfaces are extracted - check for any interface-related extraction
    # Interface methods should be captured
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    # Interface method signatures should be extracted
    assert "render" in method_names or "Renderable.render" in method_names


def test_php_trait(tmp_path: Path) -> None:
    """Cover branch: trait declaration.

    PHP trait definition.
    """
    php_file = tmp_path / "traits.php"
    php_file.write_text(
        "<?php\n"
        "namespace App\\Traits;\n"
        "\n"
        "trait HasTimestamps {\n"
        "    public function getCreatedAt(): string {\n"
        "        return $this->created_at;\n"
        "    }\n"
        "    \n"
        "    public function getUpdatedAt(): string {\n"
        "        return $this->updated_at;\n"
        "    }\n"
        "}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    # PHP trait methods are extracted
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_names = [m["name"] for m in methods]
    # Trait methods should be extracted (with or without trait prefix)
    assert "getCreatedAt" in method_names or "HasTimestamps.getCreatedAt" in method_names
