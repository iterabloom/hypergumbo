"""Tests for Python AST analysis - detecting functions and classes."""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.py import (
    extract_nodes,
    _module_name_from_path,
    _resolve_relative_import,
    _compute_cyclomatic_complexity,
    _compute_lines_of_code,
)
import ast


def test_run_detects_python_function(tmp_path: Path) -> None:
    """Running analysis on a Python file should detect function definitions."""
    # Create a Python file with a function
    py_file = tmp_path / "hello.py"
    py_file.write_text("def greet():\n    pass\n")

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Load results
    data = json.loads(out_path.read_text())

    # Expect a node in the output
    assert len(data["nodes"]) == 1
    node = data["nodes"][0]
    assert node["name"] == "greet"
    assert node["kind"] == "function"
    assert node["language"] == "python"
    assert "hello.py" in node["path"]


def test_run_skips_syntax_error_files(tmp_path: Path) -> None:
    """Files with syntax errors should be skipped, not crash analysis."""
    # Create a valid Python file
    good_file = tmp_path / "good.py"
    good_file.write_text("def works():\n    pass\n")

    # Create an invalid Python file
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def broken(\n")  # SyntaxError

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Should still find the good function
    data = json.loads(out_path.read_text())
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["name"] == "works"


def test_run_skips_unicode_error_files(tmp_path: Path) -> None:
    """Files with encoding errors should be skipped, not crash analysis."""
    # Create a valid Python file
    good_file = tmp_path / "good.py"
    good_file.write_text("def works():\n    pass\n")

    # Create a file with invalid UTF-8 bytes
    bad_file = tmp_path / "bad.py"
    bad_file.write_bytes(b"\x80\x81\x82 invalid utf-8")

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Should still find the good function
    data = json.loads(out_path.read_text())
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["name"] == "works"


def test_run_detects_python_class(tmp_path: Path) -> None:
    """Running analysis on a Python file should detect class definitions."""
    # Create a Python file with a class
    py_file = tmp_path / "models.py"
    py_file.write_text("class User:\n    pass\n")

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Load results
    data = json.loads(out_path.read_text())

    # Expect a class node in the output
    assert len(data["nodes"]) == 1
    node = data["nodes"][0]
    assert node["name"] == "User"
    assert node["kind"] == "class"
    assert node["language"] == "python"
    assert "models.py" in node["path"]


def test_run_detects_call_edges(tmp_path: Path) -> None:
    """Running analysis should detect when one function calls another."""
    # Create a Python file with two functions where one calls the other
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "def helper():\n"
        "    pass\n"
        "\n"
        "def main():\n"
        "    helper()\n"
    )

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have two function nodes
    assert len(data["nodes"]) == 2

    # Should have one edge showing main calls helper
    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert edge["type"] == "calls"
    assert "main" in edge["src"]
    assert "helper" in edge["dst"]


def test_run_detects_cross_file_call_edges(tmp_path: Path) -> None:
    """Running analysis should detect calls across files via imports."""
    # Create a utility module with a helper function
    utils_file = tmp_path / "utils.py"
    utils_file.write_text("def helper():\n    pass\n")

    # Create a main module that imports and calls the helper
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from utils import helper\n"
        "\n"
        "def run():\n"
        "    helper()\n"
    )

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have two function nodes (helper in utils, run in main)
    assert len(data["nodes"]) == 2

    # Should have both call and import edges
    call_edges = [e for e in data["edges"] if e["type"] == "calls"]
    import_edges = [e for e in data["edges"] if e["type"] == "imports"]
    assert len(call_edges) == 1
    assert len(import_edges) == 1

    # Verify the call edge: run -> helper
    edge = call_edges[0]
    assert "run" in edge["src"]
    assert "helper" in edge["dst"]
    # The target should reference utils.py, not main.py
    assert "utils.py" in edge["dst"]


def test_run_detects_import_edges(tmp_path: Path) -> None:
    """Running analysis should detect import edges."""
    # Create a utility module with a helper function
    utils_file = tmp_path / "utils.py"
    utils_file.write_text("def helper():\n    pass\n")

    # Create a main module that imports the helper
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from utils import helper\n"
        "\n"
        "def run():\n"
        "    helper()\n"
    )

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have import edges
    import_edges = [e for e in data["edges"] if e["type"] == "imports"]
    assert len(import_edges) >= 1, "Expected at least one import edge"

    # The import edge should reference the imported symbol
    import_edge = import_edges[0]
    assert "main.py" in import_edge["src"]
    assert "helper" in import_edge["dst"]
    assert import_edge["meta"]["evidence_type"] == "ast_import"
    # Static imports should have high confidence
    assert import_edge["confidence"] >= 0.9


def test_run_detects_module_import_edges(tmp_path: Path) -> None:
    """Running analysis should detect 'import X' style imports."""
    # Create a main module with a plain import
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "import os\n"
        "\n"
        "def run():\n"
        "    pass\n"
    )

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have import edge for 'import os'
    import_edges = [e for e in data["edges"] if e["type"] == "imports"]
    assert len(import_edges) >= 1, "Expected at least one import edge for 'import os'"

    # The import edge should reference the module
    import_edge = import_edges[0]
    assert "main.py" in import_edge["src"]
    assert "os" in import_edge["dst"]


def test_run_detects_submodule_import_calls(tmp_path: Path) -> None:
    """Running analysis should detect calls through submodule imports.

    This tests the pattern:
        from app import crud
        crud.create_user()

    Where crud is a submodule (app/crud.py), not a symbol in app/__init__.py.
    """
    # Create package structure: app/crud.py
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("")
    (app_dir / "crud.py").write_text(
        "def create_user():\n"
        "    pass\n"
    )

    # Create main module that imports submodule and calls function
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from app import crud\n"
        "\n"
        "def run():\n"
        "    crud.create_user()\n"
    )

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have call edge from run -> create_user
    call_edges = [e for e in data["edges"] if e["type"] == "calls"]

    # Find the edge from run to create_user
    run_to_create_user = [
        e for e in call_edges
        if "run" in e["src"] and "create_user" in e["dst"]
    ]
    assert len(run_to_create_user) == 1, (
        f"Expected 1 edge from run to create_user, got {len(run_to_create_user)}. "
        f"All call edges: {call_edges}"
    )

    # The target should be the resolved function in crud.py, not unresolved
    edge = run_to_create_user[0]
    assert "crud.py" in edge["dst"], f"Expected resolved target in crud.py, got: {edge['dst']}"
    assert "unresolved" not in edge["dst"], f"Target should be resolved: {edge['dst']}"


def test_extract_nodes_detects_local_calls(tmp_path: Path) -> None:
    """extract_nodes should detect intra-file calls."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "def helper():\n"
        "    pass\n"
        "\n"
        "def main():\n"
        "    helper()\n"
    )

    result = extract_nodes(py_file)

    assert len(result.symbols) == 2
    assert len(result.edges) == 1
    assert "main" in result.edges[0].src
    assert "helper" in result.edges[0].dst


def test_extract_nodes_handles_syntax_error(tmp_path: Path) -> None:
    """extract_nodes should return empty result for syntax errors."""
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def broken(\n")

    result = extract_nodes(bad_file)

    assert result.symbols == []
    assert result.edges == []


def test_module_name_from_path_basic(tmp_path: Path) -> None:
    """_module_name_from_path should convert paths to module names."""
    py_file = tmp_path / "utils.py"
    assert _module_name_from_path(py_file, tmp_path) == "utils"


def test_module_name_from_path_nested(tmp_path: Path) -> None:
    """_module_name_from_path should handle nested packages."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    py_file = pkg / "mod.py"
    assert _module_name_from_path(py_file, tmp_path) == "pkg.mod"


def test_module_name_from_path_outside_repo(tmp_path: Path) -> None:
    """_module_name_from_path should handle files outside repo root."""
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    py_file = other_dir / "external.py"
    # When file is outside repo_root, falls back to using the path as-is
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    result = _module_name_from_path(py_file, repo_root)
    assert "external" in result


def test_resolve_relative_import_too_high() -> None:
    """_resolve_relative_import should handle going up too many levels gracefully."""
    # Trying to go up 5 levels from 'pkg.mod' (only 2 levels) should return module as-is
    result = _resolve_relative_import("utils", 5, "pkg.mod")
    assert result == "utils"

    # With no module part, should return empty string
    result = _resolve_relative_import(None, 5, "pkg.mod")
    assert result == ""


def test_run_detects_relative_import_calls(tmp_path: Path) -> None:
    """Running analysis should detect calls via relative imports (from ..X import Y)."""
    # Create a package structure:
    # pkg/
    #   __init__.py
    #   utils.py      -> def helper(): pass
    #   sub/
    #     __init__.py
    #     main.py     -> from ..utils import helper; def run(): helper()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")

    utils_file = pkg / "utils.py"
    utils_file.write_text("def helper():\n    pass\n")

    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")

    main_file = sub / "main.py"
    main_file.write_text(
        "from ..utils import helper\n"
        "\n"
        "def run():\n"
        "    helper()\n"
    )

    # Run analysis
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have two function nodes (helper in utils, run in main)
    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 2

    # Should have both call and import edges
    call_edges = [e for e in data["edges"] if e["type"] == "calls"]
    import_edges = [e for e in data["edges"] if e["type"] == "imports"]
    assert len(call_edges) == 1
    assert len(import_edges) == 1

    # Verify the call edge: run -> helper
    edge = call_edges[0]
    assert "run" in edge["src"]
    assert "helper" in edge["dst"]
    # The target should reference utils.py, not main.py
    assert "utils.py" in edge["dst"]


def test_run_detects_method_calls_on_self(tmp_path: Path) -> None:
    """Running analysis should detect method calls via self.method()."""
    py_file = tmp_path / "service.py"
    py_file.write_text(
        "class Service:\n"
        "    def helper(self):\n"
        "        pass\n"
        "\n"
        "    def run(self):\n"
        "        self.helper()\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should have a class and two methods
    assert len(data["nodes"]) == 3

    # Should detect run -> helper via self.helper()
    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert edge["type"] == "calls"
    assert "run" in edge["src"]
    assert "helper" in edge["dst"]


def test_run_detects_class_instantiation(tmp_path: Path) -> None:
    """Running analysis should detect ClassName() instantiation as edges."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "def create_user():\n"
        "    return User('test')\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should have instantiation edge: create_user -> User
    inst_edges = [e for e in data["edges"] if e["type"] == "instantiates"]
    assert len(inst_edges) == 1
    assert "create_user" in inst_edges[0]["src"]
    assert "User" in inst_edges[0]["dst"]
    assert inst_edges[0]["meta"]["evidence_type"] == "ast_new"


def test_run_detects_cross_file_instantiation(tmp_path: Path) -> None:
    """Running analysis should detect ClassName() across files via imports."""
    # Create a models module with a class
    models_file = tmp_path / "models.py"
    models_file.write_text(
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
    )

    # Create a main module that imports and instantiates the class
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from models import User\n"
        "\n"
        "def create_user():\n"
        "    return User('test')\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should have instantiation edge: create_user -> User (in models.py)
    inst_edges = [e for e in data["edges"] if e["type"] == "instantiates"]
    assert len(inst_edges) == 1
    assert "create_user" in inst_edges[0]["src"]
    assert "User" in inst_edges[0]["dst"]
    # Target should reference models.py
    assert "models.py" in inst_edges[0]["dst"]


def test_method_symbols_include_class_prefix(tmp_path: Path) -> None:
    """Method symbols should include class prefix in name (ClassName.methodName)."""
    py_file = tmp_path / "service.py"
    py_file.write_text(
        "class UserService:\n"
        "    def create_user(self):\n"
        "        pass\n"
        "\n"
        "    def delete_user(self):\n"
        "        pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Find method nodes
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    assert len(methods) == 2

    # Method names should include class prefix
    method_names = [m["name"] for m in methods]
    assert "UserService.create_user" in method_names
    assert "UserService.delete_user" in method_names


# ============================================================================
# Decorator Metadata Extraction Tests (Route decorators)
# ============================================================================
# Route detection is now handled by FRAMEWORK_PATTERNS phase.
# These tests verify the analyzer extracts correct decorator metadata.


def test_fastapi_get_decorator_metadata(tmp_path: Path) -> None:
    """FastAPI @app.get decorator metadata should be extracted."""
    py_file = tmp_path / "main.py"
    py_file.write_text(
        "from fastapi import FastAPI\n"
        "\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/users')\n"
        "def get_users():\n"
        "    return []\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Find the route handler function
    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    assert func["name"] == "get_users"
    # stable_id is now always a hash
    assert func["stable_id"].startswith("sha256:")
    # Decorator metadata should be extracted
    decorators = func.get("meta", {}).get("decorators", [])
    assert len(decorators) == 1
    assert decorators[0]["name"] == "app.get"
    assert decorators[0]["args"] == ["/users"]


def test_fastapi_post_decorator_metadata(tmp_path: Path) -> None:
    """FastAPI @app.post decorator metadata should be extracted."""
    py_file = tmp_path / "main.py"
    py_file.write_text(
        "from fastapi import FastAPI\n"
        "\n"
        "app = FastAPI()\n"
        "\n"
        "@app.post('/users')\n"
        "def create_user():\n"
        "    return {'id': 1}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    decorators = func.get("meta", {}).get("decorators", [])
    assert len(decorators) == 1
    assert decorators[0]["name"] == "app.post"
    assert decorators[0]["args"] == ["/users"]


def test_fastapi_router_decorator_metadata(tmp_path: Path) -> None:
    """FastAPI @router.get decorator metadata should be extracted."""
    py_file = tmp_path / "routes.py"
    py_file.write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/items/{item_id}')\n"
        "def get_item(item_id: int):\n"
        "    return {'item_id': item_id}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    decorators = func.get("meta", {}).get("decorators", [])
    assert len(decorators) == 1
    assert decorators[0]["name"] == "router.get"
    assert decorators[0]["args"] == ["/items/{item_id}"]


def test_fastapi_all_http_method_decorators(tmp_path: Path) -> None:
    """All HTTP method decorators should have metadata extracted."""
    py_file = tmp_path / "api.py"
    py_file.write_text(
        "from fastapi import FastAPI\n"
        "\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/get')\n"
        "def do_get(): pass\n"
        "\n"
        "@app.post('/post')\n"
        "def do_post(): pass\n"
        "\n"
        "@app.put('/put')\n"
        "def do_put(): pass\n"
        "\n"
        "@app.patch('/patch')\n"
        "def do_patch(): pass\n"
        "\n"
        "@app.delete('/delete')\n"
        "def do_delete(): pass\n"
        "\n"
        "@app.head('/head')\n"
        "def do_head(): pass\n"
        "\n"
        "@app.options('/options')\n"
        "def do_options(): pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 7

    # Check each function has correct decorator metadata
    func_by_name = {f["name"]: f for f in functions}

    expected = {
        "do_get": ("app.get", "/get"),
        "do_post": ("app.post", "/post"),
        "do_put": ("app.put", "/put"),
        "do_patch": ("app.patch", "/patch"),
        "do_delete": ("app.delete", "/delete"),
        "do_head": ("app.head", "/head"),
        "do_options": ("app.options", "/options"),
    }

    for name, (dec_name, path) in expected.items():
        decorators = func_by_name[name].get("meta", {}).get("decorators", [])
        assert len(decorators) == 1, f"{name} should have 1 decorator"
        assert decorators[0]["name"] == dec_name
        assert decorators[0]["args"] == [path]


def test_non_route_function_keeps_hash_stable_id(tmp_path: Path) -> None:
    """Functions without route decorators should still use hash-based stable_id."""
    py_file = tmp_path / "utils.py"
    py_file.write_text(
        "def helper():\n"
        "    pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    # Non-route functions should still have sha256:... stable_id
    assert func["stable_id"].startswith("sha256:")


def test_flask_route_decorator_metadata(tmp_path: Path) -> None:
    """Flask @app.route decorator metadata should be extracted."""
    py_file = tmp_path / "main.py"
    py_file.write_text(
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.route('/hello', methods=['GET'])\n"
        "def hello():\n"
        "    return 'Hello'\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    # Decorator metadata should be extracted
    decorators = func.get("meta", {}).get("decorators", [])
    assert len(decorators) == 1
    assert decorators[0]["name"] == "app.route"
    assert decorators[0]["args"] == ["/hello"]
    assert decorators[0]["kwargs"] == {"methods": ["GET"]}


def test_flask_method_specific_decorator_metadata(tmp_path: Path) -> None:
    """Flask @app.get, @app.post etc. (Flask 2.0+) decorator metadata should be extracted."""
    py_file = tmp_path / "main.py"
    py_file.write_text(
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.get('/users')\n"
        "def get_users():\n"
        "    return []\n"
        "\n"
        "@app.post('/users')\n"
        "def create_user():\n"
        "    return {}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    func_by_name = {f["name"]: f for f in functions}

    # Check decorator metadata
    get_decorators = func_by_name["get_users"].get("meta", {}).get("decorators", [])
    assert len(get_decorators) == 1
    assert get_decorators[0]["name"] == "app.get"
    assert get_decorators[0]["args"] == ["/users"]

    post_decorators = func_by_name["create_user"].get("meta", {}).get("decorators", [])
    assert len(post_decorators) == 1
    assert post_decorators[0]["name"] == "app.post"
    assert post_decorators[0]["args"] == ["/users"]


# ============================================================================
# Flask add_url_rule UsageContext Tests (v1.1.x)
# ============================================================================


def test_flask_add_url_rule_usage_context(tmp_path: Path) -> None:
    """Flask add_url_rule() should emit UsageContext records."""
    from hypergumbo_lang_mainstream.py import analyze_python

    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "def user_list():\n"
        "    return []\n"
        "\n"
        "app.add_url_rule('/users', 'user_list', user_list)\n"
    )

    result = analyze_python(tmp_path)

    # Should have usage contexts for the add_url_rule call
    assert len(result.usage_contexts) == 1
    ctx = result.usage_contexts[0]
    assert ctx.kind == "call"
    assert ctx.context_name == "app.add_url_rule"
    assert ctx.metadata["route_path"] == "/users"
    assert ctx.metadata["view_name"] == "user_list"
    assert ctx.metadata["methods"] == ["GET"]  # Default


def test_flask_add_url_rule_with_view_func_kwarg(tmp_path: Path) -> None:
    """Flask add_url_rule with view_func keyword argument."""
    from hypergumbo_lang_mainstream.py import analyze_python

    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "def get_item():\n"
        "    return {}\n"
        "\n"
        "app.add_url_rule('/items/<int:id>', view_func=get_item)\n"
    )

    result = analyze_python(tmp_path)

    assert len(result.usage_contexts) == 1
    ctx = result.usage_contexts[0]
    assert ctx.metadata["route_path"] == "/items/<int:id>"
    assert ctx.metadata["view_name"] == "get_item"


def test_flask_add_url_rule_with_methods(tmp_path: Path) -> None:
    """Flask add_url_rule with explicit methods."""
    from hypergumbo_lang_mainstream.py import analyze_python

    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "def create_user():\n"
        "    return {}\n"
        "\n"
        "app.add_url_rule('/users', view_func=create_user, methods=['POST', 'PUT'])\n"
    )

    result = analyze_python(tmp_path)

    assert len(result.usage_contexts) == 1
    ctx = result.usage_contexts[0]
    assert ctx.metadata["methods"] == ["POST", "PUT"]


def test_flask_blueprint_add_url_rule(tmp_path: Path) -> None:
    """Flask Blueprint add_url_rule should also be detected."""
    from hypergumbo_lang_mainstream.py import analyze_python

    py_file = tmp_path / "views.py"
    py_file.write_text(
        "from flask import Blueprint\n"
        "\n"
        "bp = Blueprint('api', __name__)\n"
        "\n"
        "def list_items():\n"
        "    return []\n"
        "\n"
        "bp.add_url_rule('/items', view_func=list_items)\n"
    )

    result = analyze_python(tmp_path)

    assert len(result.usage_contexts) == 1
    ctx = result.usage_contexts[0]
    assert ctx.context_name == "bp.add_url_rule"
    assert ctx.metadata["route_path"] == "/items"


def test_flask_add_url_rule_with_attribute_view_func(tmp_path: Path) -> None:
    """Flask add_url_rule with attribute-based view_func (views.handler)."""
    from hypergumbo_lang_mainstream.py import analyze_python

    py_file = tmp_path / "routes.py"
    py_file.write_text(
        "from flask import Flask\n"
        "import views\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "app.add_url_rule('/api/users', view_func=views.user_handler)\n"
    )

    result = analyze_python(tmp_path)

    assert len(result.usage_contexts) == 1
    ctx = result.usage_contexts[0]
    assert ctx.metadata["view_name"] == "user_handler"
    assert ctx.symbol_ref is None  # Can't resolve cross-module


def test_flask_add_url_rule_positional_attribute_handler(tmp_path: Path) -> None:
    """Flask add_url_rule with attribute as third positional argument."""
    from hypergumbo_lang_mainstream.py import analyze_python

    py_file = tmp_path / "routes.py"
    py_file.write_text(
        "from flask import Flask\n"
        "import views\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "# Third arg as attribute: views.list_users\n"
        "app.add_url_rule('/users', 'list_users', views.list_users)\n"
    )

    result = analyze_python(tmp_path)

    assert len(result.usage_contexts) == 1
    ctx = result.usage_contexts[0]
    assert ctx.metadata["view_name"] == "list_users"
    # args should include the attribute as "views.list_users"
    assert "views.list_users" in ctx.metadata["args"]


# ============================================================================
# Django/DRF Decorator Metadata Tests
# ============================================================================


def test_drf_api_view_decorator_single_method_metadata(tmp_path: Path) -> None:
    """DRF @api_view(['GET']) decorator metadata should be extracted."""
    py_file = tmp_path / "views.py"
    py_file.write_text(
        "from rest_framework.decorators import api_view\n"
        "\n"
        "@api_view(['GET'])\n"
        "def user_list(request):\n"
        "    return []\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    assert func["name"] == "user_list"
    # Check decorator metadata extraction
    decorators = func.get("meta", {}).get("decorators", [])
    assert len(decorators) == 1
    assert decorators[0]["name"] == "api_view"
    assert decorators[0]["args"] == [["GET"]]


def test_drf_api_view_decorator_multiple_methods_metadata(tmp_path: Path) -> None:
    """DRF @api_view(['GET', 'POST']) decorator metadata should be extracted."""
    py_file = tmp_path / "views.py"
    py_file.write_text(
        "from rest_framework.decorators import api_view\n"
        "\n"
        "@api_view(['GET', 'POST'])\n"
        "def user_list(request):\n"
        "    if request.method == 'GET':\n"
        "        return []\n"
        "    return {}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    # Check decorator metadata with multiple methods
    decorators = func.get("meta", {}).get("decorators", [])
    assert len(decorators) == 1
    assert decorators[0]["name"] == "api_view"
    assert decorators[0]["args"] == [["GET", "POST"]]


def test_drf_api_view_all_methods_metadata(tmp_path: Path) -> None:
    """DRF @api_view with all HTTP methods - metadata extraction."""
    py_file = tmp_path / "views.py"
    py_file.write_text(
        "from rest_framework.decorators import api_view\n"
        "\n"
        "@api_view(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])\n"
        "def resource(request):\n"
        "    pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    decorators = func.get("meta", {}).get("decorators", [])
    assert len(decorators) == 1
    assert decorators[0]["name"] == "api_view"
    assert decorators[0]["args"] == [["GET", "POST", "PUT", "PATCH", "DELETE"]]


def test_django_cbv_http_methods(tmp_path: Path) -> None:
    """Django class-based view methods (get, post) should be detected as routes."""
    py_file = tmp_path / "views.py"
    py_file.write_text(
        "from django.views import View\n"
        "\n"
        "class UserView(View):\n"
        "    def get(self, request):\n"
        "        return []\n"
        "\n"
        "    def post(self, request):\n"
        "        return {}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    method_by_name = {m["name"]: m for m in methods}

    # Methods named get/post in a View class should be marked as HTTP handlers
    assert "UserView.get" in method_by_name
    assert "UserView.post" in method_by_name
    assert method_by_name["UserView.get"]["stable_id"] == "GET"
    assert method_by_name["UserView.post"]["stable_id"] == "POST"


def test_drf_api_view_no_args_fallback(tmp_path: Path) -> None:
    """DRF @api_view() without args should not crash and use hash stable_id."""
    py_file = tmp_path / "views.py"
    py_file.write_text(
        "from rest_framework.decorators import api_view\n"
        "\n"
        "@api_view()\n"
        "def no_args_view(request):\n"
        "    return []\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    # Without HTTP methods, should fall back to hash-based stable_id
    assert func["stable_id"].startswith("sha256:")


def test_django_path_urlpattern(tmp_path: Path) -> None:
    """Django path() URL patterns should be detected as routes."""
    urls_file = tmp_path / "urls.py"
    urls_file.write_text(
        "from django.urls import path\n"
        "from . import views\n"
        "\n"
        "urlpatterns = [\n"
        "    path('users/', views.user_list),\n"
        "    path('users/<int:pk>/', views.user_detail),\n"
        "]\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    assert len(routes) == 2

    route_paths = {r.get("meta", {}).get("route_path") for r in routes}
    assert "/users/" in route_paths or "users/" in route_paths
    assert "/users/<int:pk>/" in route_paths or "users/<int:pk>/" in route_paths


def test_django_path_empty_string_root_route(tmp_path: Path) -> None:
    """Django path('') for root URL should be detected as route."""
    urls_file = tmp_path / "urls.py"
    urls_file.write_text(
        "from django.urls import path\n"
        "from . import views\n"
        "\n"
        "urlpatterns = [\n"
        "    path('', views.index, name='index'),\n"
        "    path('about/', views.about),\n"
        "]\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    assert len(routes) == 2, "Both empty string root and /about/ should be detected"

    route_paths = {r.get("meta", {}).get("route_path") for r in routes}
    # Empty path gets normalized to "/"
    assert "/" in route_paths, f"Root route should be detected, got: {route_paths}"
    assert "/about/" in route_paths or "about/" in route_paths


def test_django_re_path_urlpattern(tmp_path: Path) -> None:
    """Django re_path() URL patterns should be detected as routes."""
    urls_file = tmp_path / "urls.py"
    urls_file.write_text(
        "from django.urls import re_path\n"
        "from . import views\n"
        "\n"
        "urlpatterns = [\n"
        "    re_path(r'^articles/(?P<year>[0-9]{4})/$', views.year_archive),\n"
        "]\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    assert len(routes) == 1

    route = routes[0]
    assert "articles" in route.get("meta", {}).get("route_path", "")


def test_django_url_legacy_urlpattern(tmp_path: Path) -> None:
    """Django legacy url() patterns should be detected as routes."""
    urls_file = tmp_path / "urls.py"
    urls_file.write_text(
        "from django.conf.urls import url\n"
        "from . import views\n"
        "\n"
        "urlpatterns = [\n"
        "    url(r'^users/$', views.user_list),\n"
        "]\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    assert len(routes) == 1


def test_django_path_with_direct_function_reference(tmp_path: Path) -> None:
    """Django path() with direct function reference (not views.func) is detected."""
    urls_file = tmp_path / "urls.py"
    urls_file.write_text(
        "from django.urls import path\n"
        "\n"
        "def my_view(request):\n"
        "    pass\n"
        "\n"
        "urlpatterns = [\n"
        "    path('items/', my_view),\n"
        "]\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    assert len(routes) == 1
    assert routes[0].get("meta", {}).get("view_name") == "my_view"


# NOTE: Router prefix combination tests were removed.
# Router prefix functionality is now handled by FRAMEWORK_PATTERNS phase.
# See test_framework_patterns.py for pattern matching tests.


def test_reexport_call_edges_resolved(tmp_path: Path) -> None:
    """Calls to re-exported symbols should create proper call edges.

    When a package __init__.py re-exports symbols from submodules:
        # mypackage/__init__.py
        from .submodule import helper

    And another file imports from the package:
        # main.py
        from mypackage import helper
        def caller():
            helper()

    The call edge from caller -> helper should be created, pointing to the
    real symbol in submodule.py, not a placeholder.
    """
    # Create package structure
    pkg = tmp_path / "mypackage"
    pkg.mkdir()

    # Create the actual implementation in submodule
    submodule = pkg / "submodule.py"
    submodule.write_text(
        "def helper():\n"
        "    '''The actual helper function.'''\n"
        "    return 42\n"
    )

    # Create __init__.py that re-exports helper
    init_file = pkg / "__init__.py"
    init_file.write_text(
        "from .submodule import helper\n"
    )

    # Create main.py that imports from package and calls helper
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from mypackage import helper\n"
        "\n"
        "def caller():\n"
        "    '''Calls the re-exported helper.'''\n"
        "    helper()\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should have both functions
    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = {f["name"] for f in functions}
    assert "helper" in func_names, "helper function should be detected"
    assert "caller" in func_names, "caller function should be detected"

    # Find the actual helper symbol (in submodule.py, not a placeholder)
    helper_nodes = [n for n in functions if n["name"] == "helper"]
    assert len(helper_nodes) == 1
    helper_node = helper_nodes[0]
    assert "submodule.py" in helper_node["path"], \
        f"helper should be from submodule.py, got {helper_node['path']}"

    # Find call edges from caller
    caller_nodes = [n for n in functions if n["name"] == "caller"]
    assert len(caller_nodes) == 1
    caller_id = caller_nodes[0]["id"]

    call_edges = [e for e in data["edges"]
                  if e["type"] == "calls" and e["src"] == caller_id]

    # There should be a call edge to helper
    assert len(call_edges) >= 1, \
        f"Expected call edge from caller to helper, got: {call_edges}"

    # The call edge should point to the real helper, not a placeholder
    helper_id = helper_node["id"]
    call_dsts = {e["dst"] for e in call_edges}
    assert helper_id in call_dsts, \
        f"Call edge should point to real helper {helper_id}, got {call_dsts}"


def test_reexport_with_alias_resolved(tmp_path: Path) -> None:
    """Re-exports with aliases should create proper call edges.

    When __init__.py re-exports with an alias:
        from .submodule import helper as public_helper

    And consumer imports the aliased name:
        from mypackage import public_helper
        public_helper()

    The call edge should point to the real helper function.
    """
    # Create package structure
    pkg = tmp_path / "mypackage"
    pkg.mkdir()

    # Create the actual implementation
    submodule = pkg / "submodule.py"
    submodule.write_text(
        "def helper():\n"
        "    '''Internal helper.'''\n"
        "    return 42\n"
    )

    # Create __init__.py that re-exports with an alias
    init_file = pkg / "__init__.py"
    init_file.write_text(
        "from .submodule import helper as public_helper\n"
    )

    # Create main.py that imports the aliased name
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from mypackage import public_helper\n"
        "\n"
        "def caller():\n"
        "    '''Calls the aliased function.'''\n"
        "    public_helper()\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Find the actual helper symbol (in submodule.py)
    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    helper_nodes = [n for n in functions if n["name"] == "helper"]
    assert len(helper_nodes) == 1
    helper_node = helper_nodes[0]

    # Find call edges from caller
    caller_nodes = [n for n in functions if n["name"] == "caller"]
    assert len(caller_nodes) == 1
    caller_id = caller_nodes[0]["id"]

    call_edges = [e for e in data["edges"]
                  if e["type"] == "calls" and e["src"] == caller_id]

    # The call edge should point to the real helper
    assert len(call_edges) >= 1
    helper_id = helper_node["id"]
    call_dsts = {e["dst"] for e in call_edges}
    assert helper_id in call_dsts, \
        f"Call to public_helper should resolve to helper, got {call_dsts}"


def test_src_layout_reexport_resolution(tmp_path: Path) -> None:
    """Re-exports work correctly with src/ layout projects.

    Many Python projects use the src/ layout (PEP 517/518):
        src/mypackage/__init__.py
        src/mypackage/helper.py

    When main.py does:
        from mypackage import helper
        helper()

    The call should resolve to src/mypackage/helper.py, even though
    the file path includes 'src/' but the import path doesn't.
    """
    # Create src/ layout structure
    src = tmp_path / "src"
    src.mkdir()
    pkg = src / "mypackage"
    pkg.mkdir()

    # Create the actual implementation
    helper_file = pkg / "helper.py"
    helper_file.write_text(
        "def helper():\n"
        "    '''The helper function.'''\n"
        "    return 42\n"
    )

    # Create __init__.py that re-exports
    init_file = pkg / "__init__.py"
    init_file.write_text(
        "from .helper import helper\n"
    )

    # Create main.py at project root that imports from package
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from mypackage import helper\n"
        "\n"
        "def caller():\n"
        "    '''Calls the re-exported helper.'''\n"
        "    helper()\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should have both functions
    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = {f["name"] for f in functions}
    assert "helper" in func_names, "helper function should be detected"
    assert "caller" in func_names, "caller function should be detected"

    # Find the actual helper symbol (in src/mypackage/helper.py)
    helper_nodes = [n for n in functions if n["name"] == "helper"]
    assert len(helper_nodes) == 1
    helper_node = helper_nodes[0]
    assert "helper.py" in helper_node["path"], \
        f"helper should be from helper.py, got {helper_node['path']}"

    # Find call edges from caller
    caller_nodes = [n for n in functions if n["name"] == "caller"]
    assert len(caller_nodes) == 1
    caller_id = caller_nodes[0]["id"]

    call_edges = [e for e in data["edges"]
                  if e["type"] == "calls" and e["src"] == caller_id]

    # There should be a call edge to helper
    assert len(call_edges) >= 1, \
        f"Expected call edge from caller to helper, got: {call_edges}"

    # The call edge should point to the real helper, not a placeholder
    helper_id = helper_node["id"]
    call_dsts = {e["dst"] for e in call_edges}
    assert helper_id in call_dsts, \
        f"Call edge should point to real helper {helper_id}, got {call_dsts}"


def test_src_as_package_not_detected_as_layout(tmp_path: Path) -> None:
    """When src/ has __init__.py, it's a package, not src/ layout.

    If src/ itself has __init__.py, it should be treated as a normal
    package named 'src', not as a source root. Module names should
    include 'src.' prefix.
    """
    # Create src as a package (not src/ layout)
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("# src is a package\n")
    (src / "helper.py").write_text(
        "def helper():\n"
        "    return 42\n"
    )

    # Create main.py that imports from src package
    main_file = tmp_path / "main.py"
    main_file.write_text(
        "from src.helper import helper\n"
        "\n"
        "def caller():\n"
        "    helper()\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Should have both functions
    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    func_names = {f["name"] for f in functions}
    assert "helper" in func_names
    assert "caller" in func_names

    # Find the helper and caller
    helper_nodes = [n for n in functions if n["name"] == "helper"]
    caller_nodes = [n for n in functions if n["name"] == "caller"]
    assert len(helper_nodes) == 1
    assert len(caller_nodes) == 1

    helper_id = helper_nodes[0]["id"]
    caller_id = caller_nodes[0]["id"]

    # Find call edge from caller to helper
    call_edges = [e for e in data["edges"]
                  if e["type"] == "calls" and e["src"] == caller_id]

    # Should resolve correctly - imports use "src.helper"
    call_dsts = {e["dst"] for e in call_edges}
    assert helper_id in call_dsts, \
        f"Call edge should resolve to helper {helper_id}, got {call_dsts}"


# ============================================================================
# Cyclomatic Complexity and Lines of Code Tests
# ============================================================================


def test_cyclomatic_complexity_simple_function() -> None:
    """A function with no branches should have complexity 1."""
    code = """
def simple():
    x = 1
    y = 2
    return x + y
"""
    tree = ast.parse(code)
    func = tree.body[0]
    assert _compute_cyclomatic_complexity(func) == 1


def test_cyclomatic_complexity_with_if() -> None:
    """Each if statement adds 1 to complexity."""
    code = """
def with_if(x):
    if x > 0:
        return 1
    return 0
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 1 if = 2
    assert _compute_cyclomatic_complexity(func) == 2


def test_cyclomatic_complexity_with_if_elif_else() -> None:
    """Each if/elif adds 1; else doesn't add (it's the default path)."""
    code = """
def with_branches(x):
    if x > 0:
        return "positive"
    elif x < 0:
        return "negative"
    else:
        return "zero"
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 1 if + 1 elif = 3
    assert _compute_cyclomatic_complexity(func) == 3


def test_cyclomatic_complexity_with_loops() -> None:
    """for and while loops each add 1."""
    code = """
def with_loops(items):
    result = 0
    for item in items:
        result += item
    while result > 100:
        result -= 10
    return result
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 1 for + 1 while = 3
    assert _compute_cyclomatic_complexity(func) == 3


def test_cyclomatic_complexity_with_exception_handling() -> None:
    """Each except handler adds 1."""
    code = """
def with_exceptions():
    try:
        risky_operation()
    except ValueError:
        handle_value_error()
    except TypeError:
        handle_type_error()
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 2 except handlers = 3
    assert _compute_cyclomatic_complexity(func) == 3


def test_cyclomatic_complexity_with_context_managers() -> None:
    """with statements add 1 each."""
    code = """
def with_context():
    with open('file.txt') as f:
        with lock:
            data = f.read()
    return data
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 2 with statements = 3
    assert _compute_cyclomatic_complexity(func) == 3


def test_cyclomatic_complexity_with_boolean_operators() -> None:
    """and/or operators add (n-1) where n is operand count."""
    code = """
def with_boolean(a, b, c):
    if a and b and c:
        return True
    if a or b:
        return True
    return False
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 2 if + 2 (a and b and c has 3 operands) + 1 (a or b has 2 operands) = 6
    assert _compute_cyclomatic_complexity(func) == 6


def test_cyclomatic_complexity_with_ternary() -> None:
    """Conditional expressions (ternary) add 1."""
    code = """
def with_ternary(x):
    return "yes" if x else "no"
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 1 IfExp = 2
    assert _compute_cyclomatic_complexity(func) == 2


def test_cyclomatic_complexity_with_comprehension_if() -> None:
    """Comprehension if clauses add to complexity."""
    code = """
def with_list_comp(items):
    return [x for x in items if x > 0 if x < 100]
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 2 if clauses in comprehension = 3
    assert _compute_cyclomatic_complexity(func) == 3


def test_cyclomatic_complexity_with_match_case() -> None:
    """match/case (Python 3.10+) adds complexity per case."""
    code = """
def with_match(x):
    match x:
        case 1:
            return "one"
        case 2:
            return "two"
        case _:
            return "other"
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 3 cases = 4
    assert _compute_cyclomatic_complexity(func) == 4


def test_cyclomatic_complexity_complex_function() -> None:
    """Complex function with multiple branch types."""
    code = """
def complex_function(items, flag):
    result = []
    for item in items:
        if item > 0 and flag:
            try:
                result.append(process(item))
            except ValueError:
                continue
        elif item < 0:
            result.append(-item)
    return result if result else None
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Base 1 + 1 for + 1 if + 1 (and) + 1 except + 1 elif + 1 IfExp = 7
    assert _compute_cyclomatic_complexity(func) == 7


def test_lines_of_code_simple() -> None:
    """LOC is end_line - start_line + 1."""
    code = """def simple():
    x = 1
    return x
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Lines 1-3
    assert _compute_lines_of_code(func) == 3


def test_lines_of_code_multiline() -> None:
    """LOC counts all lines in a function."""
    code = """def multiline():
    a = 1
    b = 2
    c = 3
    d = 4
    e = 5
    return a + b + c + d + e
"""
    tree = ast.parse(code)
    func = tree.body[0]
    # Lines 1-7
    assert _compute_lines_of_code(func) == 7


def test_cyclomatic_complexity_in_output(tmp_path: Path) -> None:
    """Cyclomatic complexity should appear in analysis output."""
    py_file = tmp_path / "example.py"
    py_file.write_text("""
def simple():
    return 42

def branchy(x, y):
    if x > 0:
        if y > 0:
            return "both positive"
    return "not both positive"
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    functions = {n["name"]: n for n in data["nodes"] if n["kind"] == "function"}

    # simple() has complexity 1 (no branches)
    assert functions["simple"]["cyclomatic_complexity"] == 1

    # branchy() has complexity 3 (base 1 + 2 ifs)
    assert functions["branchy"]["cyclomatic_complexity"] == 3


def test_lines_of_code_in_output(tmp_path: Path) -> None:
    """Lines of code should appear in analysis output."""
    py_file = tmp_path / "example.py"
    py_file.write_text("""def short():
    return 1

def longer():
    a = 1
    b = 2
    c = 3
    d = 4
    return a + b + c + d
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())
    functions = {n["name"]: n for n in data["nodes"] if n["kind"] == "function"}

    # short() is 2 lines
    assert functions["short"]["lines_of_code"] == 2

    # longer() is 6 lines
    assert functions["longer"]["lines_of_code"] == 6


def test_class_has_complexity_and_loc(tmp_path: Path) -> None:
    """Classes should also have cyclomatic_complexity and lines_of_code."""
    py_file = tmp_path / "example.py"
    py_file.write_text("""class MyClass:
    def __init__(self, x):
        self.x = x

    def process(self):
        if self.x > 0:
            return self.x * 2
        return 0
""")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    # Find the class
    classes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert len(classes) == 1
    cls = classes[0]
    assert cls["lines_of_code"] == 8  # Lines 1-8
    # Class complexity includes all methods' branches
    assert cls["cyclomatic_complexity"] >= 1

    # Find methods
    methods = {n["name"]: n for n in data["nodes"] if n["kind"] == "method"}
    assert methods["MyClass.__init__"]["lines_of_code"] == 2
    assert methods["MyClass.__init__"]["cyclomatic_complexity"] == 1
    assert methods["MyClass.process"]["lines_of_code"] == 4
    assert methods["MyClass.process"]["cyclomatic_complexity"] == 2  # 1 base + 1 if


# ============================================================================
# Function Signature Extraction Tests
# ============================================================================


class TestPythonSignatureExtraction:
    """Tests for Python function signature extraction in the analyzer."""

    def test_simple_function_signature(self, tmp_path: Path) -> None:
        """Extract signature for simple function with typed args and return."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "def add(x: int, y: int) -> int:\n"
            "    return x + y\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["signature"] == "(x: int, y: int) -> int"

    def test_signature_with_defaults(self, tmp_path: Path) -> None:
        """Extract signature for function with default values."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "def greet(name: str, greeting: str = 'hello') -> str:\n"
            "    return f'{greeting}, {name}'\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["signature"] == "(name: str, greeting: str=…) -> str"

    def test_signature_with_varargs(self, tmp_path: Path) -> None:
        """Extract signature for function with *args."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "def many(first: int, *rest) -> list:\n"
            "    return [first] + list(rest)\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["signature"] == "(first: int, *rest) -> list"

    def test_signature_with_kwargs(self, tmp_path: Path) -> None:
        """Extract signature for function with **kwargs."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "def options(**kwargs) -> dict:\n"
            "    return kwargs\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["signature"] == "(**kwargs) -> dict"

    def test_signature_with_kwonly_args(self, tmp_path: Path) -> None:
        """Extract signature for function with keyword-only args."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "def kw_only(*, key: str, value: int = 0) -> None:\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        # Note: the bare * is not captured as vararg, but kwonly args follow
        assert funcs[0]["signature"] == "(key: str, value: int=…) -> None"

    def test_signature_with_posonly_args(self, tmp_path: Path) -> None:
        """Extract signature for function with positional-only args (PEP 570)."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "def pos_only(x: int, y: int, /) -> int:\n"
            "    return x + y\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["signature"] == "(x: int, y: int) -> int"

    def test_signature_subscript_annotation(self, tmp_path: Path) -> None:
        """Extract signature with subscript type (List[int], Dict[str, int])."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "from typing import List, Dict\n"
            "def process(items: List[int]) -> Dict[str, int]:\n"
            "    return {str(x): x for x in items}\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["signature"] == "(items: List[int]) -> Dict[str, int]"

    def test_signature_union_type(self, tmp_path: Path) -> None:
        """Extract signature with union type (X | Y)."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "def maybe(x: int | str) -> int | None:\n"
            "    return int(x) if isinstance(x, str) else x\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["signature"] == "(x: int | str) -> int | None"

    def test_signature_attribute_annotation(self, tmp_path: Path) -> None:
        """Extract signature with attribute type (typing.Optional)."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "import typing\n"
            "def opt(x: typing.Optional[int]) -> typing.Optional[str]:\n"
            "    return str(x) if x is not None else None\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["signature"] == "(x: typing.Optional[int]) -> typing.Optional[str]"

    def test_signature_truncated_for_long_signature(self, tmp_path: Path) -> None:
        """Long signatures should be truncated."""
        py_file = tmp_path / "test.py"
        # Create a function with many parameters
        py_file.write_text(
            "def long_func(param_one: str, param_two: str, param_three: str, "
            "param_four: str, param_five: str, param_six: str, param_seven: str) -> str:\n"
            "    return 'x'\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        sig = funcs[0]["signature"]
        # Signature should be truncated to max_len (60 by default) + ellipsis
        assert len(sig) <= 60
        assert sig.endswith("…")

    def test_signature_constant_annotation(self, tmp_path: Path) -> None:
        """Extract signature with constant type like Literal['a', 'b']."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "from typing import Literal\n"
            "def mode(m: Literal['read', 'write']) -> None:\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        # Literal is subscript with tuple of constants
        assert "Literal" in funcs[0]["signature"]

    def test_signature_tuple_annotation(self, tmp_path: Path) -> None:
        """Extract signature with tuple type Dict[str, int] uses tuple for key types."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "from typing import Tuple\n"
            "def coords() -> Tuple[int, int]:\n"
            "    return (0, 0)\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["signature"] == "() -> Tuple[int, int]"


class TestModulePseudoNode:
    """Tests for <module> pseudo-node creation for script-only files."""

    def test_module_node_created_for_script_with_calls(self, tmp_path: Path) -> None:
        """Script files with function calls get a module pseudo-node."""
        py_file = tmp_path / "script.py"
        py_file.write_text(
            "import os\n"
            "print('hello')\n"
            "x = os.getcwd()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1
        assert modules[0]["name"] == "<module:script.py>"
        assert modules[0]["span"]["start_line"] == 1
        assert modules[0]["span"]["end_line"] == 3

    def test_module_node_created_for_script_with_if_main(self, tmp_path: Path) -> None:
        """Scripts with if __name__ == '__main__' get a module pseudo-node."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "import sys\n"
            "\n"
            "def main():\n"
            "    print('hello')\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1
        assert modules[0]["name"] == "<module:main.py>"

    def test_no_module_node_for_pure_definitions(self, tmp_path: Path) -> None:
        """Files with only imports/defs don't get a module pseudo-node."""
        py_file = tmp_path / "lib.py"
        py_file.write_text(
            "import os\n"
            "\n"
            "def helper():\n"
            "    return os.getcwd()\n"
            "\n"
            "class MyClass:\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 0

    def test_module_node_created_for_assignment(self, tmp_path: Path) -> None:
        """Files with module-level assignments get a module pseudo-node."""
        py_file = tmp_path / "config.py"
        py_file.write_text(
            "import os\n"
            "CONFIG = {'debug': True}\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1
        assert modules[0]["name"] == "<module:config.py>"

    def test_no_module_node_for_docstring_only(self, tmp_path: Path) -> None:
        """Files with only docstring don't get a module pseudo-node."""
        py_file = tmp_path / "empty.py"
        py_file.write_text(
            '"""This module does nothing."""\n'
            "\n"
            "import os\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 0

    def test_no_module_node_for_pass_only(self, tmp_path: Path) -> None:
        """Files with only pass statements don't get a module pseudo-node."""
        py_file = tmp_path / "stub.py"
        py_file.write_text(
            '"""Stub module."""\n'
            "pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 0

    def test_no_module_node_for_type_annotation_only(self, tmp_path: Path) -> None:
        """Files with only type annotations don't get a module pseudo-node."""
        py_file = tmp_path / "types.py"
        py_file.write_text(
            '"""Type stubs."""\n'
            "x: int\n"
            "y: str\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 0


class TestModuleQualifiedCalls:
    """Tests for module-qualified call resolution (module.func(), module.Class())."""

    def test_module_qualified_function_call(self, tmp_path: Path) -> None:
        """Module-qualified function calls (module.func()) should emit calls edges."""
        # Create a utility module
        utils_file = tmp_path / "utils.py"
        utils_file.write_text(
            "def helper():\n"
            "    pass\n"
        )

        # Create main module that uses 'import utils' and calls utils.helper()
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "import utils\n"
            "\n"
            "def run():\n"
            "    utils.helper()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Find call edges
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert len(call_edges) >= 1, "Expected at least one call edge"

        # Verify the call edge: run -> helper
        run_helper_edge = next(
            (e for e in call_edges if "run" in e["src"] and "helper" in e["dst"]),
            None
        )
        assert run_helper_edge is not None, "Expected call edge from run to helper"
        assert "utils.py" in run_helper_edge["dst"]

    def test_module_qualified_class_instantiation(self, tmp_path: Path) -> None:
        """Module-qualified instantiation (module.Class()) should emit instantiates edges."""
        # Create a module with a class
        models_file = tmp_path / "models.py"
        models_file.write_text(
            "class User:\n"
            "    def __init__(self):\n"
            "        pass\n"
        )

        # Create main module that uses 'import models' and instantiates models.User()
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "import models\n"
            "\n"
            "def create_user():\n"
            "    user = models.User()\n"
            "    return user\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Find instantiates edges
        inst_edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        assert len(inst_edges) >= 1, "Expected at least one instantiates edge"

        # Verify the instantiates edge: create_user -> User
        create_user_edge = next(
            (e for e in inst_edges if "create_user" in e["src"] and "User" in e["dst"]),
            None
        )
        assert create_user_edge is not None, "Expected instantiates edge from create_user to User"
        assert "models.py" in create_user_edge["dst"]
        assert create_user_edge["meta"]["evidence_type"] == "ast_new"

    def test_aliased_module_import(self, tmp_path: Path) -> None:
        """Aliased module imports (import X as Y) should resolve calls correctly."""
        # Create a utility module
        utils_file = tmp_path / "utils.py"
        utils_file.write_text(
            "def helper():\n"
            "    pass\n"
        )

        # Create main module with aliased import
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "import utils as u\n"
            "\n"
            "def run():\n"
            "    u.helper()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Find call edges
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        run_helper_edge = next(
            (e for e in call_edges if "run" in e["src"] and "helper" in e["dst"]),
            None
        )
        assert run_helper_edge is not None, "Expected call edge from run to helper via alias"


class TestVariableMethodCalls:
    """Tests for variable method call resolution with type inference."""

    def test_variable_method_call_with_type_inference(self, tmp_path: Path) -> None:
        """Variable method calls (stub.method()) should resolve using constructor type."""
        # Create a client class with a method
        client_file = tmp_path / "client.py"
        client_file.write_text(
            "class ServiceClient:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "\n"
            "    def send_request(self):\n"
            "        pass\n"
        )

        # Create main module that instantiates and calls method
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "from client import ServiceClient\n"
            "\n"
            "def make_request():\n"
            "    stub = ServiceClient()\n"
            "    stub.send_request()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Should have both instantiates and calls edges
        inst_edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]

        # Verify instantiates edge
        inst_edge = next(
            (e for e in inst_edges if "make_request" in e["src"] and "ServiceClient" in e["dst"]),
            None
        )
        assert inst_edge is not None, "Expected instantiates edge for ServiceClient"

        # Verify calls edge from stub.send_request() to ServiceClient.send_request
        method_edge = next(
            (e for e in call_edges if "make_request" in e["src"] and "send_request" in e["dst"]),
            None
        )
        assert method_edge is not None, "Expected call edge for send_request method"
        assert "client.py" in method_edge["dst"]

    def test_module_qualified_instantiation_with_method_call(self, tmp_path: Path) -> None:
        """Module.Class() instantiation followed by method call should resolve."""
        # Create a service module with a stub class
        service_file = tmp_path / "service.py"
        service_file.write_text(
            "class EmailServiceStub:\n"
            "    def __init__(self, channel):\n"
            "        pass\n"
            "\n"
            "    def SendEmail(self, request):\n"
            "        pass\n"
        )

        # Create main module using import service pattern
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "import service\n"
            "\n"
            "def send_confirmation():\n"
            "    stub = service.EmailServiceStub(None)\n"
            "    stub.SendEmail({})\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Verify instantiates edge
        inst_edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        inst_edge = next(
            (e for e in inst_edges if "send_confirmation" in e["src"] and "EmailServiceStub" in e["dst"]),
            None
        )
        assert inst_edge is not None, "Expected instantiates edge for EmailServiceStub"

        # Verify calls edge for SendEmail
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        method_edge = next(
            (e for e in call_edges if "send_confirmation" in e["src"] and "SendEmail" in e["dst"]),
            None
        )
        assert method_edge is not None, "Expected call edge for SendEmail method"

    def test_type_inference_limited_to_constructors(self, tmp_path: Path) -> None:
        """Type inference only works for constructor assignments, not function returns."""
        # Create a client class
        client_file = tmp_path / "client.py"
        client_file.write_text(
            "class ServiceClient:\n"
            "    def send_request(self):\n"
            "        pass\n"
            "\n"
            "def get_client():\n"
            "    return ServiceClient()\n"
        )

        # Create main module using function return (NOT tracked)
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "from client import get_client\n"
            "\n"
            "def make_request():\n"
            "    stub = get_client()  # NOT tracked - function return\n"
            "    stub.send_request()  # Should NOT be resolved\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Should have a call edge for get_client()
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        get_client_edge = next(
            (e for e in call_edges if "make_request" in e["src"] and "get_client" in e["dst"]),
            None
        )
        assert get_client_edge is not None, "Expected call edge for get_client"

        # Should NOT have a call edge for send_request (type not tracked from function return)
        send_request_edge = next(
            (e for e in call_edges if "send_request" in e["dst"]),
            None
        )
        assert send_request_edge is None, "Should NOT resolve stub.send_request() from function return"

    def test_module_level_module_qualified_call(self, tmp_path: Path) -> None:
        """Module-level code with module.func() calls should emit edges from <module> node."""
        # Create a utility module
        utils_file = tmp_path / "utils.py"
        utils_file.write_text(
            "def configure():\n"
            "    pass\n"
        )

        # Create a script that calls utils.configure() at module level
        script_file = tmp_path / "script.py"
        script_file.write_text(
            "import utils\n"
            "\n"
            "utils.configure()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Should have a <module> pseudo-node
        module_nodes = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(module_nodes) == 1
        assert module_nodes[0]["name"] == "<module:script.py>"

        # Should have a call edge from <module:script.py> to utils.configure
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        module_call_edge = next(
            (e for e in call_edges if "<module:script.py>" in e["src"] and "configure" in e["dst"]),
            None
        )
        assert module_call_edge is not None, "Expected call edge from <module> to configure"

    def test_local_class_instantiation_with_method_call(self, tmp_path: Path) -> None:
        """Local class instantiation followed by method call should resolve."""
        # Single file with class and usage in same file
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "class LocalClient:\n"
            "    def process(self):\n"
            "        pass\n"
            "\n"
            "def run():\n"
            "    client = LocalClient()\n"
            "    client.process()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Should have instantiates edge
        inst_edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        inst_edge = next(
            (e for e in inst_edges if "run" in e["src"] and "LocalClient" in e["dst"]),
            None
        )
        assert inst_edge is not None, "Expected instantiates edge for LocalClient"

        # Should have calls edge for process method
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        method_edge = next(
            (e for e in call_edges if "run" in e["src"] and "process" in e["dst"]),
            None
        )
        assert method_edge is not None, "Expected call edge for process method"

    def test_unresolved_variable_method_call(self, tmp_path: Path) -> None:
        """Unresolved variable method calls should not emit edges."""
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "def run(external_client):\n"
            "    # external_client type is unknown, can't be resolved\n"
            "    external_client.unknown_method()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Should have no calls edges (method call can't be resolved)
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        assert len(call_edges) == 0, "Should not have calls edges for unresolved variable"

    def test_unresolved_constructor_no_type_tracking(self, tmp_path: Path) -> None:
        """Unresolved constructor calls should not track variable type."""
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "def run():\n"
            "    # unknown_factory is not defined - type can't be tracked\n"
            "    stub = unknown_factory()\n"
            "    stub.do_something()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Should have no edges (neither instantiates nor calls)
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        inst_edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        assert len(call_edges) == 0, "Should not have calls edges for unresolved constructor"
        assert len(inst_edges) == 0, "Should not have instantiates edges for unresolved constructor"

    def test_imported_class_method_call(self, tmp_path: Path) -> None:
        """Imported class method calls like Item.model_validate() should resolve."""
        # Create a model class with a classmethod
        models_file = tmp_path / "models.py"
        models_file.write_text(
            "class Item:\n"
            "    @classmethod\n"
            "    def model_validate(cls, data):\n"
            "        return cls()\n"
            "\n"
            "    def save(self):\n"
            "        pass\n"
        )

        # Create a route that uses the classmethod
        routes_file = tmp_path / "routes.py"
        routes_file.write_text(
            "from models import Item\n"
            "\n"
            "def create_item(data: dict):\n"
            "    # Item.model_validate() is a classmethod call\n"
            "    item = Item.model_validate(data)\n"
            "    return item\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Should have a call edge from create_item to Item.model_validate
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        classmethod_edge = next(
            (e for e in call_edges if "create_item" in e["src"] and "model_validate" in e["dst"]),
            None
        )
        assert classmethod_edge is not None, "Expected call edge for Item.model_validate() classmethod"
        assert "models.py" in classmethod_edge["dst"]

    def test_parameter_type_annotation_inference(self, tmp_path: Path) -> None:
        """Parameter type annotations should enable method call resolution."""
        # Create a service class with methods
        service_file = tmp_path / "service.py"
        service_file.write_text(
            "class Database:\n"
            "    def add(self, obj):\n"
            "        pass\n"
            "\n"
            "    def commit(self):\n"
            "        pass\n"
        )

        # Create a function with typed parameters
        handler_file = tmp_path / "handler.py"
        handler_file.write_text(
            "from service import Database\n"
            "\n"
            "def save_item(db: Database, item: dict):\n"
            "    # db.add() and db.commit() should resolve via param type annotation\n"
            "    db.add(item)\n"
            "    db.commit()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Should have call edges for both db.add() and db.commit()
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]

        add_edge = next(
            (e for e in call_edges if "save_item" in e["src"] and "add" in e["dst"]),
            None
        )
        assert add_edge is not None, "Expected call edge for db.add() via param type annotation"
        assert "service.py" in add_edge["dst"]

        commit_edge = next(
            (e for e in call_edges if "save_item" in e["src"] and "commit" in e["dst"]),
            None
        )
        assert commit_edge is not None, "Expected call edge for db.commit() via param type annotation"
        assert "service.py" in commit_edge["dst"]

    def test_module_qualified_param_type_annotation(self, tmp_path: Path) -> None:
        """module.ClassName type annotations should enable method resolution."""
        # Create a service module
        service_file = tmp_path / "service.py"
        service_file.write_text(
            "class Client:\n"
            "    def send(self, data):\n"
            "        pass\n"
        )

        # Create a handler using module.ClassName annotation
        handler_file = tmp_path / "handler.py"
        handler_file.write_text(
            "import service\n"
            "\n"
            "def send_message(client: service.Client, msg: str):\n"
            "    client.send(msg)\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        send_edge = next(
            (e for e in call_edges if "send_message" in e["src"] and "send" in e["dst"]),
            None
        )
        assert send_edge is not None, "Expected call edge for client.send() via module.ClassName annotation"

    def test_local_class_param_type_annotation(self, tmp_path: Path) -> None:
        """Local class type annotations should enable method resolution."""
        # Single file with class and function that uses it as param type
        main_file = tmp_path / "main.py"
        main_file.write_text(
            "class LocalService:\n"
            "    def process(self, data):\n"
            "        pass\n"
            "\n"
            "def handle(svc: LocalService, data: dict):\n"
            "    svc.process(data)\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        process_edge = next(
            (e for e in call_edges if "handle" in e["src"] and "process" in e["dst"]),
            None
        )
        assert process_edge is not None, "Expected call edge for svc.process() via local class annotation"


# ============================================================================
# Rich Metadata Extraction Tests (ADR-0003)
# ============================================================================


class TestDecoratorMetadata:
    """Tests for rich decorator metadata extraction per ADR-0003."""

    def test_simple_decorator_no_args(self, tmp_path: Path) -> None:
        """Simple decorator like @dataclass should capture name with empty args."""
        py_file = tmp_path / "models.py"
        py_file.write_text(
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class User:\n"
            "    name: str\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        classes = [n for n in data["nodes"] if n["kind"] == "class"]
        assert len(classes) == 1
        user_class = classes[0]

        # Should have decorators in meta
        assert "meta" in user_class
        assert "decorators" in user_class["meta"]
        decorators = user_class["meta"]["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "dataclass"
        assert decorators[0]["args"] == []
        assert decorators[0]["kwargs"] == {}

    def test_decorator_with_positional_arg(self, tmp_path: Path) -> None:
        """Decorator with positional arg like @app.get('/users')."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "from fastapi import FastAPI\n"
            "\n"
            "app = FastAPI()\n"
            "\n"
            "@app.get('/users')\n"
            "def get_users():\n"
            "    return []\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = next(f for f in functions if f["name"] == "get_users")

        assert "meta" in func
        assert "decorators" in func["meta"]
        decorators = func["meta"]["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "app.get"
        assert decorators[0]["args"] == ["/users"]
        assert decorators[0]["kwargs"] == {}

    def test_decorator_with_kwargs(self, tmp_path: Path) -> None:
        """Decorator with keyword args like @app.get('/users', tags=['api'])."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "from fastapi import FastAPI\n"
            "\n"
            "app = FastAPI()\n"
            "\n"
            "@app.get('/users', tags=['api'], summary='Get users')\n"
            "def get_users():\n"
            "    return []\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = next(f for f in functions if f["name"] == "get_users")

        decorators = func["meta"]["decorators"]
        assert len(decorators) == 1
        assert decorators[0]["name"] == "app.get"
        assert decorators[0]["args"] == ["/users"]
        assert decorators[0]["kwargs"] == {"tags": ["api"], "summary": "Get users"}

    def test_decorator_kwargs_only(self, tmp_path: Path) -> None:
        """Decorator with only kwargs like @router.post(response_model=User)."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "from fastapi import APIRouter\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "@router.post(response_model=dict)\n"
            "def create_user():\n"
            "    return {}\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = next(f for f in functions if f["name"] == "create_user")

        decorators = func["meta"]["decorators"]
        assert decorators[0]["args"] == []
        assert decorators[0]["kwargs"] == {"response_model": "dict"}

    def test_multiple_decorators(self, tmp_path: Path) -> None:
        """Multiple decorators should all be captured in order."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "@require_auth\n"
            "@validate_json\n"
            "@cache(timeout=300)\n"
            "def protected_endpoint():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = functions[0]

        decorators = func["meta"]["decorators"]
        assert len(decorators) == 3
        # Decorators should be in source order (top to bottom)
        assert decorators[0]["name"] == "require_auth"
        assert decorators[1]["name"] == "validate_json"
        assert decorators[2]["name"] == "cache"
        assert decorators[2]["kwargs"] == {"timeout": 300}

    def test_decorator_with_variable_arg(self, tmp_path: Path) -> None:
        """Decorator with variable as arg should capture variable name."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "TIMEOUT = 300\n"
            "\n"
            "@cache(TIMEOUT)\n"
            "def cached_func():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = next(f for f in functions if f["name"] == "cached_func")

        decorators = func["meta"]["decorators"]
        # Variable names captured as strings
        assert decorators[0]["args"] == ["TIMEOUT"]

    def test_decorator_with_dict_arg(self, tmp_path: Path) -> None:
        """Decorator with dict argument should capture full dict."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "@config({'key': 'value', 'num': 42})\n"
            "def configured_func():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = functions[0]

        decorators = func["meta"]["decorators"]
        assert decorators[0]["args"] == [{"key": "value", "num": 42}]

    def test_decorator_with_tuple_arg(self, tmp_path: Path) -> None:
        """Decorator with tuple argument should capture as list."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "@accepts(('str', 'int'))\n"
            "def typed_func():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = functions[0]

        decorators = func["meta"]["decorators"]
        assert decorators[0]["args"] == [["str", "int"]]

    def test_decorator_with_attribute_value(self, tmp_path: Path) -> None:
        """Decorator with attribute value like SomeClass.field."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "class Config:\n"
            "    DEBUG = True\n"
            "\n"
            "@setting(Config.DEBUG)\n"
            "def debug_func():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = next(f for f in functions if f["name"] == "debug_func")

        decorators = func["meta"]["decorators"]
        assert decorators[0]["args"] == ["Config.DEBUG"]

    def test_decorator_with_negative_number(self, tmp_path: Path) -> None:
        """Decorator with negative number argument."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "@offset(-10)\n"
            "def adjusted_func():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = functions[0]

        decorators = func["meta"]["decorators"]
        assert decorators[0]["args"] == [-10]

    def test_method_decorators(self, tmp_path: Path) -> None:
        """Method decorators should be captured."""
        py_file = tmp_path / "service.py"
        py_file.write_text(
            "class UserService:\n"
            "    @staticmethod\n"
            "    def utility():\n"
            "        pass\n"
            "\n"
            "    @classmethod\n"
            "    def factory(cls):\n"
            "        return cls()\n"
            "\n"
            "    @property\n"
            "    def name(self):\n"
            "        return 'service'\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        methods = [n for n in data["nodes"] if n["kind"] == "method"]
        method_by_name = {m["name"].split(".")[-1]: m for m in methods}

        # Each method should have its decorator
        assert method_by_name["utility"]["meta"]["decorators"][0]["name"] == "staticmethod"
        assert method_by_name["factory"]["meta"]["decorators"][0]["name"] == "classmethod"
        assert method_by_name["name"]["meta"]["decorators"][0]["name"] == "property"

    def test_decorator_with_ellipsis_arg(self, tmp_path: Path) -> None:
        """Decorator with ellipsis (...) should serialize as string."""
        py_file = tmp_path / "models.py"
        py_file.write_text(
            "def field(default): pass\n"
            "\n"
            "@field(...)\n"
            "def required_field():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = next(f for f in functions if f["name"] == "required_field")

        decorators = func["meta"]["decorators"]
        # Ellipsis should be serialized as "..." string (JSON-safe)
        assert decorators[0]["args"] == ["..."]

    def test_decorator_with_complex_number_arg(self, tmp_path: Path) -> None:
        """Decorator with complex number should serialize as string."""
        py_file = tmp_path / "math_ops.py"
        py_file.write_text(
            "def default_value(val): pass\n"
            "\n"
            "@default_value(1+2j)\n"
            "def get_complex():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = next(f for f in functions if f["name"] == "get_complex")

        decorators = func["meta"]["decorators"]
        # Complex number should be serialized as "(1+2j)" string (JSON-safe)
        assert decorators[0]["args"] == ["(1+2j)"]

    def test_decorator_with_bytes_arg(self, tmp_path: Path) -> None:
        """Decorator with bytes literal should serialize as string."""
        py_file = tmp_path / "binary_ops.py"
        py_file.write_text(
            "def marker(val): pass\n"
            "\n"
            "@marker(b'hello')\n"
            "def process_bytes():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func = next(f for f in functions if f["name"] == "process_bytes")

        decorators = func["meta"]["decorators"]
        # Bytes should be serialized as "b'hello'" string (JSON-safe)
        assert decorators[0]["args"] == ["b'hello'"]


class TestBaseClassMetadata:
    """Tests for base class metadata extraction per ADR-0003."""

    def test_single_base_class(self, tmp_path: Path) -> None:
        """Class with single base class should capture it."""
        py_file = tmp_path / "models.py"
        py_file.write_text(
            "from pydantic import BaseModel\n"
            "\n"
            "class User(BaseModel):\n"
            "    name: str\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        classes = [n for n in data["nodes"] if n["kind"] == "class"]
        user_class = next(c for c in classes if c["name"] == "User")

        assert "meta" in user_class
        assert "base_classes" in user_class["meta"]
        assert user_class["meta"]["base_classes"] == ["BaseModel"]

    def test_multiple_base_classes(self, tmp_path: Path) -> None:
        """Class with multiple base classes (mixins)."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            "class LoginMixin:\n"
            "    pass\n"
            "\n"
            "class APIView:\n"
            "    pass\n"
            "\n"
            "class UserView(LoginMixin, APIView):\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        classes = [n for n in data["nodes"] if n["kind"] == "class"]
        user_view = next(c for c in classes if c["name"] == "UserView")

        # Base classes in declaration order
        assert user_view["meta"]["base_classes"] == ["LoginMixin", "APIView"]

    def test_generic_base_class(self, tmp_path: Path) -> None:
        """Class with Generic[T] base class."""
        py_file = tmp_path / "repo.py"
        py_file.write_text(
            "from typing import Generic, TypeVar\n"
            "\n"
            "T = TypeVar('T')\n"
            "\n"
            "class Repository(Generic[T]):\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        classes = [n for n in data["nodes"] if n["kind"] == "class"]
        repo_class = next(c for c in classes if c["name"] == "Repository")

        assert repo_class["meta"]["base_classes"] == ["Generic[T]"]

    def test_qualified_base_class(self, tmp_path: Path) -> None:
        """Class with qualified base class like QtWidgets.QWidget."""
        py_file = tmp_path / "widget.py"
        py_file.write_text(
            "from PyQt5 import QtWidgets\n"
            "\n"
            "class MyWidget(QtWidgets.QWidget):\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        classes = [n for n in data["nodes"] if n["kind"] == "class"]
        widget = next(c for c in classes if c["name"] == "MyWidget")

        assert widget["meta"]["base_classes"] == ["QtWidgets.QWidget"]

    def test_no_base_class(self, tmp_path: Path) -> None:
        """Class with no base class should not have base_classes in meta."""
        py_file = tmp_path / "models.py"
        py_file.write_text(
            "class SimpleClass:\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        classes = [n for n in data["nodes"] if n["kind"] == "class"]
        simple = classes[0]

        # No base_classes key if empty (meta may be None or missing)
        meta = simple.get("meta") or {}
        base_classes = meta.get("base_classes", [])
        assert base_classes == []


class TestParameterMetadata:
    """Tests for structured parameter metadata extraction per ADR-0003."""

    def test_simple_parameters(self, tmp_path: Path) -> None:
        """Function with simple parameters."""
        py_file = tmp_path / "funcs.py"
        py_file.write_text(
            "def greet(name):\n"
            "    return f'Hello, {name}'\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        greet = funcs[0]

        assert "meta" in greet
        assert "parameters" in greet["meta"]
        params = greet["meta"]["parameters"]
        assert len(params) == 1
        assert params[0]["name"] == "name"
        assert params[0]["type"] is None
        assert params[0]["default"] is False

    def test_typed_parameters(self, tmp_path: Path) -> None:
        """Function with type annotations."""
        py_file = tmp_path / "funcs.py"
        py_file.write_text(
            "def add(x: int, y: int) -> int:\n"
            "    return x + y\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        add = funcs[0]

        params = add["meta"]["parameters"]
        assert len(params) == 2
        assert params[0] == {"name": "x", "type": "int", "default": False}
        assert params[1] == {"name": "y", "type": "int", "default": False}

    def test_parameters_with_defaults(self, tmp_path: Path) -> None:
        """Function with default parameter values."""
        py_file = tmp_path / "funcs.py"
        py_file.write_text(
            "def greet(name: str, greeting: str = 'Hello') -> str:\n"
            "    return f'{greeting}, {name}'\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        greet = funcs[0]

        params = greet["meta"]["parameters"]
        assert params[0] == {"name": "name", "type": "str", "default": False}
        assert params[1] == {"name": "greeting", "type": "str", "default": True}

    def test_varargs_and_kwargs(self, tmp_path: Path) -> None:
        """Function with *args and **kwargs."""
        py_file = tmp_path / "funcs.py"
        py_file.write_text(
            "def flexible(required, *args, **kwargs):\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        func = funcs[0]

        params = func["meta"]["parameters"]
        assert len(params) == 3
        assert params[0]["name"] == "required"
        assert params[1]["name"] == "*args"
        assert params[2]["name"] == "**kwargs"

    def test_method_parameters_exclude_self(self, tmp_path: Path) -> None:
        """Method parameters should exclude self."""
        py_file = tmp_path / "service.py"
        py_file.write_text(
            "class Service:\n"
            "    def process(self, data: dict) -> bool:\n"
            "        return True\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        methods = [n for n in data["nodes"] if n["kind"] == "method"]
        process = methods[0]

        params = process["meta"]["parameters"]
        # self should be excluded or clearly marked
        param_names = [p["name"] for p in params]
        assert "self" not in param_names or params[0].get("is_self", False)

    def test_no_parameters(self, tmp_path: Path) -> None:
        """Function with no parameters."""
        py_file = tmp_path / "funcs.py"
        py_file.write_text(
            "def noop():\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        funcs = [n for n in data["nodes"] if n["kind"] == "function"]
        noop = funcs[0]

        # meta may be None or missing if function has no decorators/params
        meta = noop.get("meta") or {}
        params = meta.get("parameters", [])
        assert params == []


class TestSuffixBasedModuleMatching:
    """Tests for suffix-based module name matching.

    This handles the case where imports use shorter module names than the
    actual file paths. For example, 'from app.crud import X' should resolve
    when the file is registered as 'backend.app.crud'.
    """

    def test_suffix_matching_resolves_nested_imports(self, tmp_path: Path) -> None:
        """Import with shorter module name resolves via suffix matching."""
        # Create a nested directory structure like FastAPI template:
        # backend/app/crud.py contains create_item()
        # backend/app/api/routes/items.py imports from app.crud
        backend = tmp_path / "backend"
        app = backend / "app"
        app_api = app / "api"
        app_routes = app_api / "routes"
        app_routes.mkdir(parents=True)

        # Create __init__.py files
        (backend / "__init__.py").write_text("")
        (app / "__init__.py").write_text("")
        (app_api / "__init__.py").write_text("")
        (app_routes / "__init__.py").write_text("")

        # Create crud.py with a function
        (app / "crud.py").write_text(
            "def create_item(data):\n"
            "    return data\n"
        )

        # Create items.py that imports from app.crud (shorter path)
        (app_routes / "items.py").write_text(
            "from app.crud import create_item\n"
            "\n"
            "def handler():\n"
            "    return create_item({'name': 'test'})\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Find the import edge
        import_edges = [e for e in data["edges"] if e["type"] == "imports"]

        # The import edge should point to the actual symbol, not a placeholder
        # Placeholder format: python:app.crud:0-0:create_item:symbol
        # Resolved format: python:.../crud.py:1-2:create_item:function
        import_dsts = [e["dst"] for e in import_edges]
        # At least one should be resolved (not contain :0-0: placeholder)
        resolved = [d for d in import_dsts if ":0-0:" not in d and "create_item" in d]
        assert len(resolved) >= 1, f"Expected resolved import edge, got: {import_dsts}"


    def test_nested_module_import_with_method_call(self, tmp_path: Path) -> None:
        """Module import with method call resolves in nested structure.

        This tests the exact FastAPI pattern:
            from app import crud  # Module import
            crud.create_item()    # Method call on imported module

        Where the directory structure is backend/app/crud.py.
        This is Case 2e in py.py's _process_call().
        """
        # Create a nested directory structure like FastAPI template
        backend = tmp_path / "backend"
        app = backend / "app"
        app.mkdir(parents=True)

        # Create __init__.py files
        (backend / "__init__.py").write_text("")
        (app / "__init__.py").write_text("")

        # Create crud.py with functions
        (app / "crud.py").write_text(
            "def create_item(session, item_in):\n"
            "    '''Create an item in the database.'''\n"
            "    return {'name': item_in}\n"
            "\n"
            "def delete_item(session, item_id):\n"
            "    '''Delete an item.'''\n"
            "    pass\n"
        )

        # Create routes/items.py that imports the module and calls its functions
        routes = app / "routes"
        routes.mkdir()
        (routes / "__init__.py").write_text("")
        (routes / "items.py").write_text(
            "from app import crud\n"
            "\n"
            "def create_item_handler(session, item_in):\n"
            "    '''Route handler that calls crud.create_item().'''\n"
            "    return crud.create_item(session=session, item_in=item_in)\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Find call edges from the handler
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]

        # Find the handler symbol
        nodes = {n["id"]: n for n in data["nodes"]}
        handler_edges = [e for e in call_edges if "create_item_handler" in e["src"]]

        # Should have at least one call edge from the handler
        assert len(handler_edges) >= 1, (
            f"Expected call edge from handler, got none. "
            f"All call edges: {call_edges}"
        )

        # The target should be resolved to crud.py, not unresolved
        crud_call = [e for e in handler_edges if "crud" in e["dst"].lower() or "create_item" in e["dst"]]
        assert len(crud_call) >= 1, (
            f"Expected edge to crud.create_item, got: {handler_edges}"
        )

        # Should NOT be unresolved
        for edge in crud_call:
            assert "unresolved" not in edge["dst"], (
                f"Expected resolved target, got unresolved: {edge['dst']}"
            )


class TestUnresolvedEdgeEmission:
    """Tests for unresolved edge emission.

    When Python calls can't be resolved, we emit edges to unresolved
    placeholders (like Go does) to enable cross-language linking.
    """

    def test_unresolved_edge_for_external_module_call(self, tmp_path: Path) -> None:
        """Calls to external modules emit unresolved edges."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "import external_lib\n"
            "\n"
            "def handler():\n"
            "    return external_lib.process()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Find call edges
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]

        # Should have an unresolved edge to external_lib.process
        unresolved = [
            e for e in call_edges
            if ":unresolved" in e["dst"] and "process" in e["dst"]
        ]
        assert len(unresolved) == 1, f"Expected unresolved edge, got: {call_edges}"
        edge = unresolved[0]

        # Verify the edge properties
        assert edge["meta"]["evidence_type"] == "unresolved_method_call"
        assert edge["confidence"] == 0.50

    def test_unresolved_edge_for_imported_class_method(self, tmp_path: Path) -> None:
        """Calls to methods on imported classes emit unresolved edges."""
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "from external_lib import Client\n"
            "\n"
            "def handler():\n"
            "    return Client.create()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Find call edges
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]

        # Should have an unresolved edge to Client.create
        unresolved = [
            e for e in call_edges
            if ":unresolved" in e["dst"] and "Client.create" in e["dst"]
        ]
        assert len(unresolved) == 1, f"Expected unresolved edge, got: {call_edges}"


class TestNestedFunctionExtraction:
    """Tests for extracting nested functions (closures) with decorators.

    This addresses the FastAPI router factory pattern where route handlers
    are defined as nested functions inside a factory function:

        def get_entity_router():
            router = APIRouter()

            @router.get("/entities")
            def list_entities():  # <-- This should be extracted
                ...

            return router
    """

    def test_nested_function_with_route_decorator_is_extracted(self, tmp_path: Path) -> None:
        """Nested functions with route decorators should be detected.

        The FastAPI APIRouter factory pattern uses nested functions as route handlers.
        These should be extracted so framework patterns can detect routes.
        """
        py_file = tmp_path / "routes.py"
        py_file.write_text(
            "from fastapi import APIRouter\n"
            "\n"
            "def get_entity_router():\n"
            "    router = APIRouter()\n"
            "\n"
            "    @router.get('/entities')\n"
            "    def list_entities():\n"
            "        return []\n"
            "\n"
            "    @router.get('/entities/{id}')\n"
            "    def get_entity(id: int):\n"
            "        return {'id': id}\n"
            "\n"
            "    @router.post('/entities')\n"
            "    def create_entity():\n"
            "        return {'created': True}\n"
            "\n"
            "    return router\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func_names = [f["name"] for f in functions]

        # Should include the factory function AND the nested route handlers
        assert "get_entity_router" in func_names, "Factory function should be extracted"
        assert "list_entities" in func_names, "Nested route handler should be extracted"
        assert "get_entity" in func_names, "Nested route handler should be extracted"
        assert "create_entity" in func_names, "Nested route handler should be extracted"

        # Verify the nested functions have their decorator metadata
        nested_funcs = {f["name"]: f for f in functions if f["name"] in ["list_entities", "get_entity", "create_entity"]}

        for name, func in nested_funcs.items():
            decorators = func.get("meta", {}).get("decorators", [])
            assert len(decorators) >= 1, f"{name} should have decorator metadata"
            assert decorators[0]["name"].startswith("router."), f"{name} decorator should be router.*"

    def test_nested_function_without_decorator_not_extracted(self, tmp_path: Path) -> None:
        """Nested helper functions without decorators should NOT be extracted.

        We don't want to clutter the symbol list with private helper closures.
        Only nested functions with decorators (indicating they serve as handlers) should be extracted.
        """
        py_file = tmp_path / "utils.py"
        py_file.write_text(
            "def outer():\n"
            "    def inner_helper():\n"
            "        return 42\n"
            "\n"
            "    return inner_helper()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func_names = [f["name"] for f in functions]

        # Should only have the outer function, not the inner helper
        assert "outer" in func_names
        assert "inner_helper" not in func_names, "Undecorated nested function should not be extracted"

    def test_nested_async_function_with_decorator_is_extracted(self, tmp_path: Path) -> None:
        """Async nested functions with decorators should also be extracted."""
        py_file = tmp_path / "async_routes.py"
        py_file.write_text(
            "from fastapi import APIRouter\n"
            "\n"
            "def get_async_router():\n"
            "    router = APIRouter()\n"
            "\n"
            "    @router.get('/async')\n"
            "    async def async_handler():\n"
            "        return {'async': True}\n"
            "\n"
            "    return router\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func_names = [f["name"] for f in functions]

        assert "get_async_router" in func_names
        assert "async_handler" in func_names, "Async nested handler should be extracted"

    def test_deeply_nested_decorated_function_is_extracted(self, tmp_path: Path) -> None:
        """Decorated functions nested more than one level deep should be extracted."""
        py_file = tmp_path / "deep.py"
        py_file.write_text(
            "def level1():\n"
            "    def level2():\n"
            "        @some_decorator\n"
            "        def level3_decorated():\n"
            "            pass\n"
            "        return level3_decorated\n"
            "    return level2()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        functions = [n for n in data["nodes"] if n["kind"] == "function"]
        func_names = [f["name"] for f in functions]

        assert "level1" in func_names
        assert "level3_decorated" in func_names, "Deeply nested decorated function should be extracted"
        # level2 has no decorator, so should not be extracted
        assert "level2" not in func_names


class TestIfNameMainDetection:
    """Tests for detecting Python's if __name__ == "__main__" pattern.

    This structural pattern indicates a file is designed to be run as a script.
    Detection enables entrypoint identification for Python modules that serve
    as executable scripts.
    """

    def test_module_with_main_guard_has_concept(self, tmp_path: Path) -> None:
        """Module with if __name__ == "__main__" should have main_guard concept."""
        py_file = tmp_path / "script.py"
        py_file.write_text(
            "def main():\n"
            "    print('Hello')\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Find the module symbol
        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1, "Should have exactly one module symbol"

        module = modules[0]
        meta = module.get("meta") or {}
        concepts = meta.get("concepts", [])
        # Concept is now a dict: {"concept": "main_guard", "framework": "python"}
        concept_names = [c.get("concept") if isinstance(c, dict) else c for c in concepts]
        assert "main_guard" in concept_names, "Module with main guard should have main_guard concept"

    def test_module_without_main_guard_has_no_concept(self, tmp_path: Path) -> None:
        """Module without if __name__ == "__main__" should not have main_guard concept."""
        # A file with module-level code but no main guard
        py_file = tmp_path / "library.py"
        py_file.write_text(
            "def helper():\n"
            "    return 42\n"
            "\n"
            "# Module-level code to ensure module symbol is created\n"
            "x = helper()\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        # Find the module symbol
        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1

        module = modules[0]
        meta = module.get("meta") or {}
        concepts = meta.get("concepts", [])
        # Concept is now a dict: {"concept": "main_guard", "framework": "python"}
        concept_names = [c.get("concept") if isinstance(c, dict) else c for c in concepts]
        assert "main_guard" not in concept_names, "Module without main guard should not have main_guard concept"

    def test_main_guard_with_double_quotes(self, tmp_path: Path) -> None:
        """Main guard with double quotes should also be detected."""
        py_file = tmp_path / "script.py"
        py_file.write_text(
            'if __name__ == "__main__":\n'
            '    print("Hello")\n'
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1

        meta = modules[0].get("meta") or {}
        concepts = meta.get("concepts", [])
        concept_names = [c.get("concept") if isinstance(c, dict) else c for c in concepts]
        assert "main_guard" in concept_names

    def test_main_guard_with_reversed_comparison(self, tmp_path: Path) -> None:
        """Main guard with reversed comparison should also be detected."""
        py_file = tmp_path / "script.py"
        py_file.write_text(
            "if '__main__' == __name__:\n"
            "    pass\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1

        meta = modules[0].get("meta") or {}
        concepts = meta.get("concepts", [])
        concept_names = [c.get("concept") if isinstance(c, dict) else c for c in concepts]
        assert "main_guard" in concept_names

    def test_if_with_non_compare_test_not_detected(self, tmp_path: Path) -> None:
        """if statement with non-comparison test should not be detected as main guard."""
        py_file = tmp_path / "script.py"
        py_file.write_text(
            "# Module with if using non-Compare test\n"
            "if True:\n"
            "    x = 1\n"
            "\n"
            "y = x + 1  # Module-level code to ensure module symbol exists\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1

        meta = modules[0].get("meta") or {}
        concepts = meta.get("concepts", [])
        concept_names = [c.get("concept") if isinstance(c, dict) else c for c in concepts]
        assert "main_guard" not in concept_names

    def test_if_with_not_equal_comparison_not_detected(self, tmp_path: Path) -> None:
        """if __name__ != "__main__" should not be detected as main guard."""
        py_file = tmp_path / "script.py"
        py_file.write_text(
            "# Module with != comparison instead of ==\n"
            "if __name__ != '__main__':\n"
            "    x = 1\n"
            "\n"
            "y = 2  # Module-level code to ensure module symbol exists\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1

        meta = modules[0].get("meta") or {}
        concepts = meta.get("concepts", [])
        concept_names = [c.get("concept") if isinstance(c, dict) else c for c in concepts]
        assert "main_guard" not in concept_names

    def test_if_with_chained_comparison_not_detected(self, tmp_path: Path) -> None:
        """Chained comparison like 'if a == b == c' should not be detected as main guard."""
        py_file = tmp_path / "script.py"
        py_file.write_text(
            "# Module with chained comparison\n"
            "x = 1\n"
            "if 0 < x < 10:\n"
            "    y = x\n"
            "\n"
            "z = y + 1  # Module-level code to ensure module symbol exists\n"
        )

        out_path = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
        data = json.loads(out_path.read_text())

        modules = [n for n in data["nodes"] if n["kind"] == "module"]
        assert len(modules) == 1

        meta = modules[0].get("meta") or {}
        concepts = meta.get("concepts", [])
        concept_names = [c.get("concept") if isinstance(c, dict) else c for c in concepts]
        assert "main_guard" not in concept_names


# ============================================================================
# Python Inheritance Edge Tests (META-001)
# ============================================================================


class TestPythonInheritanceEdges:
    """Tests for Python inheritance edge detection.

    META-001 requires that base_classes metadata becomes extends edges so that
    the type hierarchy linker can create dispatches_to edges for polymorphic dispatch.
    """

    def test_extracts_extends_edge_same_file(self, tmp_path: Path) -> None:
        """Extracts extends relationship edges for classes in the same file."""
        from hypergumbo_lang_mainstream.py import analyze_python

        py_file = tmp_path / "models.py"
        py_file.write_text(
            "class Animal:\n"
            "    def speak(self):\n"
            "        pass\n"
            "\n"
            "class Dog(Animal):\n"
            "    def speak(self):\n"
            "        return 'Woof'\n"
        )

        result = analyze_python(tmp_path)

        assert result.run is not None
        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) >= 1

        # Edge should be from Dog to Animal (child extends parent)
        edge = extends_edges[0]
        assert "Dog" in edge.src
        assert "Animal" in edge.dst

    def test_extracts_extends_edge_cross_file(self, tmp_path: Path) -> None:
        """Extracts extends relationship edges for classes in different files."""
        from hypergumbo_lang_mainstream.py import analyze_python

        (tmp_path / "base.py").write_text(
            "class BaseModel:\n"
            "    def save(self):\n"
            "        pass\n"
        )
        (tmp_path / "models.py").write_text(
            "from base import BaseModel\n"
            "\n"
            "class User(BaseModel):\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
        )

        result = analyze_python(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) >= 1

        # Edge should be from User to BaseModel
        edge = extends_edges[0]
        assert "User" in edge.src
        assert "BaseModel" in edge.dst

    def test_extracts_multiple_inheritance_edges(self, tmp_path: Path) -> None:
        """Extracts extends edges for multiple inheritance."""
        from hypergumbo_lang_mainstream.py import analyze_python

        py_file = tmp_path / "mixins.py"
        py_file.write_text(
            "class LogMixin:\n"
            "    def log(self):\n"
            "        pass\n"
            "\n"
            "class SaveMixin:\n"
            "    def save(self):\n"
            "        pass\n"
            "\n"
            "class Model(LogMixin, SaveMixin):\n"
            "    pass\n"
        )

        result = analyze_python(tmp_path)

        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        # Should have 2 edges: Model -> LogMixin, Model -> SaveMixin
        assert len(extends_edges) >= 2

        src_names = {e.src for e in extends_edges}
        dst_names = {e.dst for e in extends_edges}
        # All edges should be from Model
        assert all("Model" in src for src in src_names)
        # Destinations should include both mixins
        assert any("LogMixin" in dst for dst in dst_names)
        assert any("SaveMixin" in dst for dst in dst_names)

    def test_no_extends_edge_for_external_base_class(self, tmp_path: Path) -> None:
        """No extends edge created when base class is external (not in repo)."""
        from hypergumbo_lang_mainstream.py import analyze_python

        py_file = tmp_path / "models.py"
        py_file.write_text(
            "from pydantic import BaseModel\n"
            "\n"
            "class User(BaseModel):\n"
            "    name: str\n"
        )

        result = analyze_python(tmp_path)

        # base_classes metadata should still be set
        user_class = next((s for s in result.symbols if s.name == "User"), None)
        assert user_class is not None
        assert "base_classes" in (user_class.meta or {})
        assert "BaseModel" in user_class.meta["base_classes"]

        # But no extends edge since BaseModel is external
        extends_edges = [e for e in result.edges if e.edge_type == "extends"]
        assert len(extends_edges) == 0


