"""Branch coverage tests for Python AST analysis.

These tests specifically target uncovered branches in py.py.
They are in a separate file to allow easy management if they impact CI speed.
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def test_django_cbv_as_view_with_local_class_attribute_form(tmp_path: Path) -> None:
    """Cover branch: views.LocalView.as_view() where LocalView is defined locally.

    This tests the case where view_name is in symbol_by_name (line 610 in py.py).
    """
    urls_file = tmp_path / "urls.py"
    urls_file.write_text(
        "from django.urls import path\n"
        "from django.views import View\n"
        "\n"
        "class LocalView(View):\n"
        "    def get(self, request):\n"
        "        pass\n"
        "\n"
        "# Use 'views' module pattern to get ast.Attribute form\n"
        "# This simulates: views.LocalView.as_view()\n"
        "# But since LocalView IS local, it should resolve\n"
        "import sys\n"
        "views = sys.modules[__name__]\n"
        "\n"
        "urlpatterns = [\n"
        "    path('local/', views.LocalView.as_view()),\n"
        "]\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    assert len(routes) == 1
    assert routes[0].get("meta", {}).get("view_name") == "LocalView"


def test_django_cbv_as_view_with_local_class_name_form(tmp_path: Path) -> None:
    """Cover branch: LocalView.as_view() where LocalView is defined locally.

    This tests the case where view_name is in symbol_by_name (line 615 in py.py).
    Uses direct Name reference (not attribute).
    """
    urls_file = tmp_path / "urls.py"
    urls_file.write_text(
        "from django.urls import path\n"
        "from django.views import View\n"
        "\n"
        "class MyLocalView(View):\n"
        "    def get(self, request):\n"
        "        pass\n"
        "\n"
        "urlpatterns = [\n"
        "    path('mylocal/', MyLocalView.as_view()),\n"
        "]\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    routes = [n for n in data["nodes"] if n["kind"] == "route"]
    assert len(routes) == 1
    assert routes[0].get("meta", {}).get("view_name") == "MyLocalView"

    # Also check that usage_contexts capture the local reference
    usage_contexts = data.get("usage_contexts", [])
    route_contexts = [uc for uc in usage_contexts if uc.get("context_name") == "path"]
    assert len(route_contexts) == 1
    # The view_name should be set in metadata
    assert route_contexts[0].get("metadata", {}).get("view_name") == "MyLocalView"
