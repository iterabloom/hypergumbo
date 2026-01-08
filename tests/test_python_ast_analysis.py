"""Tests for Python AST analysis - detecting functions and classes."""
import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map
from hypergumbo.analyze.py import (
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    # Load results
    data = json.loads(out_path.read_text())

    # Should have import edge for 'import os'
    import_edges = [e for e in data["edges"] if e["type"] == "imports"]
    assert len(import_edges) >= 1, "Expected at least one import edge for 'import os'"

    # The import edge should reference the module
    import_edge = import_edges[0]
    assert "main.py" in import_edge["src"]
    assert "os" in import_edge["dst"]


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Find method nodes
    methods = [n for n in data["nodes"] if n["kind"] == "method"]
    assert len(methods) == 2

    # Method names should include class prefix
    method_names = [m["name"] for m in methods]
    assert "UserService.create_user" in method_names
    assert "UserService.delete_user" in method_names


# ============================================================================
# FastAPI Route Detection Tests
# ============================================================================


def test_fastapi_get_route_detected(tmp_path: Path) -> None:
    """FastAPI @app.get decorator should set stable_id to 'get' and store route path."""
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Find the route handler function
    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    assert func["name"] == "get_users"
    # stable_id should be the HTTP method
    assert func["stable_id"] == "GET"
    # Route path should be stored in meta
    assert func.get("meta", {}).get("route_path") == "/users"


def test_fastapi_post_route_detected(tmp_path: Path) -> None:
    """FastAPI @app.post decorator should set stable_id to 'post'."""
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    assert func["stable_id"] == "POST"
    assert func.get("meta", {}).get("route_path") == "/users"


def test_fastapi_router_route_detected(tmp_path: Path) -> None:
    """FastAPI @router.get decorator should also be detected."""
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    assert func["stable_id"] == "GET"
    assert func.get("meta", {}).get("route_path") == "/items/{item_id}"


def test_fastapi_all_http_methods(tmp_path: Path) -> None:
    """All HTTP methods should be detected: get, post, put, patch, delete, head, options."""
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 7

    # Check each function has correct stable_id
    func_by_name = {f["name"]: f for f in functions}
    assert func_by_name["do_get"]["stable_id"] == "GET"
    assert func_by_name["do_post"]["stable_id"] == "POST"
    assert func_by_name["do_put"]["stable_id"] == "PUT"
    assert func_by_name["do_patch"]["stable_id"] == "PATCH"
    assert func_by_name["do_delete"]["stable_id"] == "DELETE"
    assert func_by_name["do_head"]["stable_id"] == "HEAD"
    assert func_by_name["do_options"]["stable_id"] == "OPTIONS"


def test_non_route_function_keeps_hash_stable_id(tmp_path: Path) -> None:
    """Functions without route decorators should still use hash-based stable_id."""
    py_file = tmp_path / "utils.py"
    py_file.write_text(
        "def helper():\n"
        "    pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    # Non-route functions should still have sha256:... stable_id
    assert func["stable_id"].startswith("sha256:")


def test_flask_route_detected(tmp_path: Path) -> None:
    """Flask @app.route decorator should also be detected."""
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    # Flask @app.route with methods=['GET'] extracts the actual HTTP method
    assert func["stable_id"] == "GET"
    assert func.get("meta", {}).get("route_path") == "/hello"
    assert func.get("meta", {}).get("http_method") == "GET"


def test_flask_method_specific_decorators(tmp_path: Path) -> None:
    """Flask @app.get, @app.post etc. (Flask 2.0+) should be detected."""
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    func_by_name = {f["name"]: f for f in functions}

    assert func_by_name["get_users"]["stable_id"] == "GET"
    assert func_by_name["create_user"]["stable_id"] == "POST"


# ============================================================================
# Django Route Detection Tests
# ============================================================================


def test_drf_api_view_decorator_single_method(tmp_path: Path) -> None:
    """DRF @api_view(['GET']) decorator should set stable_id to 'get'."""
    py_file = tmp_path / "views.py"
    py_file.write_text(
        "from rest_framework.decorators import api_view\n"
        "\n"
        "@api_view(['GET'])\n"
        "def user_list(request):\n"
        "    return []\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    assert func["name"] == "user_list"
    assert func["stable_id"] == "GET"


def test_drf_api_view_decorator_multiple_methods(tmp_path: Path) -> None:
    """DRF @api_view(['GET', 'POST']) should set stable_id to 'get,post'."""
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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    # Multiple methods joined with comma
    assert func["stable_id"] == "GET,POST"


def test_drf_api_view_all_methods(tmp_path: Path) -> None:
    """DRF @api_view with all HTTP methods."""
    py_file = tmp_path / "views.py"
    py_file.write_text(
        "from rest_framework.decorators import api_view\n"
        "\n"
        "@api_view(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])\n"
        "def resource(request):\n"
        "    pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    func = functions[0]
    assert "GET" in func["stable_id"]
    assert "POST" in func["stable_id"]
    assert "PUT" in func["stable_id"]
    assert "PATCH" in func["stable_id"]
    assert "DELETE" in func["stable_id"]


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    assert len(routes) == 2

    route_paths = {r.get("meta", {}).get("route_path") for r in routes}
    assert "/users/" in route_paths or "users/" in route_paths
    assert "/users/<int:pk>/" in route_paths or "users/<int:pk>/" in route_paths


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    assert len(routes) == 1
    assert routes[0].get("meta", {}).get("view_name") == "my_view"


def test_fastapi_router_prefix_combined_with_route(tmp_path: Path) -> None:
    """FastAPI APIRouter with prefix should combine prefix with route path."""
    py_file = tmp_path / "routes.py"
    py_file.write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter(prefix='/api/v1')\n"
        "\n"
        "@router.get('/users')\n"
        "def get_users():\n"
        "    return []\n"
        "\n"
        "@router.post('/users')\n"
        "def create_user():\n"
        "    return {}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 2

    func_by_name = {f["name"]: f for f in functions}
    # Route path should include prefix
    assert func_by_name["get_users"].get("meta", {}).get("route_path") == "/api/v1/users"
    assert func_by_name["create_user"].get("meta", {}).get("route_path") == "/api/v1/users"


def test_fastapi_router_prefix_no_leading_slash(tmp_path: Path) -> None:
    """Router prefix without leading slash should be normalized."""
    py_file = tmp_path / "routes.py"
    py_file.write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter(prefix='api')\n"
        "\n"
        "@router.get('/items')\n"
        "def get_items():\n"
        "    return []\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    # Should normalize to /api/items
    assert functions[0].get("meta", {}).get("route_path") == "/api/items"


def test_fastapi_multiple_routers_different_prefixes(tmp_path: Path) -> None:
    """Multiple routers with different prefixes should each apply their own prefix."""
    py_file = tmp_path / "routes.py"
    py_file.write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "users_router = APIRouter(prefix='/users')\n"
        "items_router = APIRouter(prefix='/items')\n"
        "\n"
        "@users_router.get('/')\n"
        "def list_users():\n"
        "    return []\n"
        "\n"
        "@items_router.get('/')\n"
        "def list_items():\n"
        "    return []\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 2

    func_by_name = {f["name"]: f for f in functions}
    assert func_by_name["list_users"].get("meta", {}).get("route_path") == "/users/"
    assert func_by_name["list_items"].get("meta", {}).get("route_path") == "/items/"


def test_fastapi_router_without_prefix(tmp_path: Path) -> None:
    """Router without prefix should not affect route paths."""
    py_file = tmp_path / "routes.py"
    py_file.write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/health')\n"
        "def health_check():\n"
        "    return {'status': 'ok'}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    # Route path unchanged
    assert functions[0].get("meta", {}).get("route_path") == "/health"


def test_fastapi_router_prefix_keyword_arg(tmp_path: Path) -> None:
    """Router prefix can be passed as keyword argument."""
    py_file = tmp_path / "routes.py"
    py_file.write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter(tags=['api'], prefix='/v2/api')\n"
        "\n"
        "@router.get('/data')\n"
        "def get_data():\n"
        "    return {}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1

    assert functions[0].get("meta", {}).get("route_path") == "/v2/api/data"


def test_flask_blueprint_url_prefix(tmp_path: Path) -> None:
    """Flask Blueprint with url_prefix should combine prefix with route path."""
    py_file = tmp_path / "routes.py"
    py_file.write_text(
        "from flask import Blueprint\n"
        "\n"
        "bp = Blueprint('api', __name__, url_prefix='/api/v1')\n"
        "\n"
        "@bp.get('/users')\n"
        "def get_users():\n"
        "    return []\n"
        "\n"
        "@bp.route('/items', methods=['POST'])\n"
        "def create_item():\n"
        "    return {}\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

    data = json.loads(out_path.read_text())

    functions = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(functions) == 2

    func_by_name = {f["name"]: f for f in functions}
    # Route paths should include Blueprint prefix
    assert func_by_name["get_users"].get("meta", {}).get("route_path") == "/api/v1/users"
    assert func_by_name["create_item"].get("meta", {}).get("route_path") == "/api/v1/items"


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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
    run_behavior_map(repo_root=tmp_path, out_path=out_path)

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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
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
        run_behavior_map(repo_root=tmp_path, out_path=out_path)
        data = json.loads(out_path.read_text())

        # Should have no edges (neither instantiates nor calls)
        call_edges = [e for e in data["edges"] if e["type"] == "calls"]
        inst_edges = [e for e in data["edges"] if e["type"] == "instantiates"]
        assert len(call_edges) == 0, "Should not have calls edges for unresolved constructor"
        assert len(inst_edges) == 0, "Should not have instantiates edges for unresolved constructor"
