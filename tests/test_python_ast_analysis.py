"""Tests for Python AST analysis - detecting functions and classes."""
import json
from pathlib import Path

from hypergumbo.cli import run_behavior_map
from hypergumbo.analyze.py import extract_nodes, _module_name_from_path, _resolve_relative_import


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
    assert func["stable_id"] == "get"
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
    assert func["stable_id"] == "post"
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
    assert func["stable_id"] == "get"
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
    assert func_by_name["do_get"]["stable_id"] == "get"
    assert func_by_name["do_post"]["stable_id"] == "post"
    assert func_by_name["do_put"]["stable_id"] == "put"
    assert func_by_name["do_patch"]["stable_id"] == "patch"
    assert func_by_name["do_delete"]["stable_id"] == "delete"
    assert func_by_name["do_head"]["stable_id"] == "head"
    assert func_by_name["do_options"]["stable_id"] == "options"


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
    # Flask uses @app.route - detect as "route" method
    assert func["stable_id"] == "route"
    assert func.get("meta", {}).get("route_path") == "/hello"


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

    assert func_by_name["get_users"]["stable_id"] == "get"
    assert func_by_name["create_user"]["stable_id"] == "post"


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
    assert func["stable_id"] == "get"


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
    assert func["stable_id"] == "get,post"


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
    assert "get" in func["stable_id"]
    assert "post" in func["stable_id"]
    assert "put" in func["stable_id"]
    assert "patch" in func["stable_id"]
    assert "delete" in func["stable_id"]


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
    assert method_by_name["UserView.get"]["stable_id"] == "get"
    assert method_by_name["UserView.post"]["stable_id"] == "post"


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
