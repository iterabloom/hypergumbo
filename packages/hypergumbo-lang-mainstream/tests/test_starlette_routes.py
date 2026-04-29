# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Starlette Route / WebSocketRoute extractor (WI-sulaz)."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.py import (
    _collect_module_constants,
    _extract_starlette_usage_contexts,
)


def _imports_for(py_file: Path) -> dict[str, tuple[str, str]]:
    tree = ast.parse(py_file.read_text())
    _, imports = _collect_module_constants(tree, py_file.parent, py_file)
    return imports


def test_route_call_emits_usage_context(tmp_path: Path) -> None:
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Route\n"
        "\n"
        "def hello(request):\n"
        "    return None\n"
        "\n"
        "routes = [Route('/hello', hello, methods=['GET', 'POST'])]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    assert len(contexts) == 1
    ctx = contexts[0]
    assert ctx.metadata["route_path"] == "/hello"
    assert ctx.metadata["methods"] == ["GET", "POST"]
    assert ctx.metadata["view_name"] == "hello"
    assert ctx.metadata["receiver"] == "Route"
    assert ctx.position == "view_func"


def test_websocket_route_uses_synthetic_ws_method(tmp_path: Path) -> None:
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import WebSocketRoute\n"
        "\n"
        "async def ws(websocket):\n"
        "    pass\n"
        "\n"
        "routes = [WebSocketRoute('/ws', ws)]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    assert len(contexts) == 1
    ctx = contexts[0]
    assert ctx.metadata["methods"] == ["WS"]
    assert ctx.metadata["receiver"] == "WebSocketRoute"


def test_route_class_from_other_module_is_not_matched(tmp_path: Path) -> None:
    """Import-scoped: a `Route` class imported from elsewhere must not match."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from some_other_pkg import Route\n"
        "\n"
        "def handler(request):\n"
        "    pass\n"
        "\n"
        "routes = [Route('/whatever', handler)]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    assert contexts == []


def test_route_aliased_import_is_matched(tmp_path: Path) -> None:
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Route as R\n"
        "\n"
        "def handler(request):\n"
        "    pass\n"
        "\n"
        "routes = [R('/x', handler, methods=['GET'])]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    assert len(contexts) == 1
    assert contexts[0].metadata["route_path"] == "/x"
    # The receiver is the original Starlette class name, not the alias.
    assert contexts[0].metadata["receiver"] == "Route"


def test_route_without_imports_returns_empty(tmp_path: Path) -> None:
    py_file = tmp_path / "app.py"
    py_file.write_text("Route('/x', handler)\n")
    tree = ast.parse(py_file.read_text())
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=None)
    assert contexts == []


def test_route_with_dynamic_path_is_skipped(tmp_path: Path) -> None:
    """Non-literal route paths can't be statically resolved."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Route\n"
        "PREFIX = '/api'\n"
        "def h(r): pass\n"
        "routes = [Route(PREFIX + '/x', h)]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    # Concatenation isn't a Constant; we skip it.
    assert contexts == []


def test_route_path_normalized_with_leading_slash(tmp_path: Path) -> None:
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Route\n"
        "def h(r): pass\n"
        "routes = [Route('plain', h)]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    assert contexts[0].metadata["route_path"] == "/plain"


def test_other_starlette_routing_imports_are_ignored(tmp_path: Path) -> None:
    """Mount() and other starlette.routing names that aren't Route/WebSocketRoute
    must not produce route UsageContexts (covers the original-name filter)."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Mount\n"
        "def h(r): pass\n"
        "routes = [Mount('/sub', routes=[])]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    assert contexts == []


def test_attribute_call_is_not_matched(tmp_path: Path) -> None:
    """``mod.Route(...)`` is not a bare-name call and must not match."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Route\n"
        "import some_mod\n"
        "def h(r): pass\n"
        # The Route import is bound, but this call is `some_mod.Route(...)` —
        # an Attribute call, not a Name call. Should be skipped.
        "routes = [some_mod.Route('/x', h)]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    assert contexts == []


def test_unrelated_calls_are_skipped(tmp_path: Path) -> None:
    """Calls to names not in the Starlette routing set are skipped."""
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Route\n"
        "def h(r): pass\n"
        # `print` is a bare-name call but not in starlette_names.
        "print('hello')\n"
        "routes = [Route('/x', h)]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    assert len(contexts) == 1
    assert contexts[0].metadata["route_path"] == "/x"


def test_route_with_attribute_handler(tmp_path: Path) -> None:
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Route\n"
        "import handlers\n"
        "routes = [Route('/x', handlers.h)]\n"
    )
    tree = ast.parse(py_file.read_text())
    imports = _imports_for(py_file)
    contexts = _extract_starlette_usage_contexts(tree, str(py_file), {}, imports=imports)
    assert contexts[0].metadata["view_name"] == "h"


# --- End-to-end: route symbols actually appear in the behavior map ---

def test_starlette_routes_appear_as_route_kind_nodes(tmp_path: Path) -> None:
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Route, WebSocketRoute\n"
        "\n"
        "def health(request):\n"
        "    return None\n"
        "\n"
        "def list_items(request):\n"
        "    return None\n"
        "\n"
        "async def ws_handler(websocket):\n"
        "    pass\n"
        "\n"
        "routes = [\n"
        "    Route('/health', health, methods=['GET']),\n"
        "    Route('/items', list_items, methods=['GET', 'POST']),\n"
        "    WebSocketRoute('/ws', ws_handler),\n"
        "]\n"
    )
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    paths_methods = sorted(
        (n["meta"]["route_path"], n["meta"]["http_method"]) for n in routes
    )
    # /items has two methods → two route symbols
    assert paths_methods == [
        ("/health", "GET"),
        ("/items", "GET"),
        ("/items", "POST"),
        ("/ws", "WS"),
    ]
    # Framework metadata propagated
    for r in routes:
        assert r["meta"]["framework"] == "starlette"


def test_starlette_yaml_attaches_concept_route_to_handler(tmp_path: Path) -> None:
    # Framework detection (profile.py) reads manifest files to decide which
    # frameworks are in use; without one, starlette.yaml is not loaded.
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "demo"\n'
        'dependencies = ["starlette"]\n'
    )
    py_file = tmp_path / "app.py"
    py_file.write_text(
        "from starlette.routing import Route\n"
        "\n"
        "def hello(request):\n"
        "    return None\n"
        "\n"
        "routes = [Route('/hello', hello, methods=['GET'])]\n"
    )
    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    data = json.loads(out_path.read_text())

    handler = next(n for n in data["nodes"] if n["kind"] == "function" and n["name"] == "hello")
    concepts = handler.get("meta", {}).get("concepts", [])
    concept_names = {c["concept"] for c in concepts}
    assert "route" in concept_names
