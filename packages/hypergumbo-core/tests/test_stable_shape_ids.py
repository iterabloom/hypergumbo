# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for stable_id and shape_id computation."""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def test_stable_id_computed_for_functions(tmp_path: Path) -> None:
    """Functions should have a stable_id computed from their signature."""
    (tmp_path / "app.py").write_text("def greet(name): pass\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(func_nodes) == 1
    assert func_nodes[0]["stable_id"] is not None
    assert func_nodes[0]["stable_id"].startswith("sha256:")


def test_stable_id_computed_for_classes(tmp_path: Path) -> None:
    """Classes should have a stable_id computed."""
    (tmp_path / "app.py").write_text("class User: pass\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    class_nodes = [n for n in data["nodes"] if n["kind"] == "class"]
    assert len(class_nodes) == 1
    assert class_nodes[0]["stable_id"] is not None
    assert class_nodes[0]["stable_id"].startswith("sha256:")


def test_stable_id_survives_body_edits(tmp_path: Path) -> None:
    """stable_id survives BODY edits (Phase 6 PR3 / INV-bazij).

    Per ADR-0014's Phase-6 amendment: stable_id is "structural identity
    within a (qualified_name, module_path) scope" — it survives BODY
    edits but NOT rename or move. Two functions with the same name,
    same module path, same signature, but different body content must
    share stable_id. This pins the surviving half of the original
    "survives renames/moves" promise.

    (The untyped-fallback path produces a stable_id from a 7-tuple
    that excludes body content: kind, param_count, arity_flags,
    decorators, containing_stable_id, name, qualified_name.)
    """
    out_path = tmp_path / "out.json"
    src = tmp_path / "mod.py"

    src.write_text("def foo(x): pass\n")
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    sid_before = next(
        n["stable_id"]
        for n in json.loads(out_path.read_text())["nodes"]
        if n["kind"] == "function" and n["name"] == "foo"
    )

    # Same name, same signature, different body.
    src.write_text("def foo(x):\n    return x + 1\n")
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)
    sid_after = next(
        n["stable_id"]
        for n in json.loads(out_path.read_text())["nodes"]
        if n["kind"] == "function" and n["name"] == "foo"
    )

    # Body edits do not change stable_id.
    assert sid_before == sid_after


def test_stable_id_changes_with_signature(tmp_path: Path) -> None:
    """stable_id should change when signature changes."""
    # Two functions with different signatures
    (tmp_path / "a.py").write_text("def func(x): pass\n")
    (tmp_path / "b.py").write_text("def func(x, y): pass\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(func_nodes) == 2

    # Different signatures -> different stable_ids
    assert func_nodes[0]["stable_id"] != func_nodes[1]["stable_id"]


def test_stable_id_changes_with_decorators(tmp_path: Path) -> None:
    """stable_id should change when decorators change."""
    (tmp_path / "app.py").write_text(
        "def plain(x): pass\n"
        "\n"
        "@staticmethod\n"
        "def decorated(x): pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(func_nodes) == 2

    plain = next(n for n in func_nodes if n["name"] == "plain")
    decorated = next(n for n in func_nodes if n["name"] == "decorated")

    # Different decorators -> different stable_ids
    assert plain["stable_id"] != decorated["stable_id"]


def test_shape_id_computed_for_functions(tmp_path: Path) -> None:
    """Functions should have a shape_id computed from their AST structure."""
    (tmp_path / "app.py").write_text("def greet(name): pass\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(func_nodes) == 1
    assert func_nodes[0]["shape_id"] is not None
    assert func_nodes[0]["shape_id"].startswith("sha256:")


def test_shape_id_same_for_same_structure(tmp_path: Path) -> None:
    """Functions with same structure should have same shape_id."""
    # Two functions with same structure (pass statement)
    (tmp_path / "a.py").write_text("def foo(x): pass\n")
    (tmp_path / "b.py").write_text("def bar(y): pass\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(func_nodes) == 2

    # Same structure -> same shape_id
    assert func_nodes[0]["shape_id"] == func_nodes[1]["shape_id"]


def test_shape_id_different_for_different_structure(tmp_path: Path) -> None:
    """Functions with different structure should have different shape_id."""
    (tmp_path / "app.py").write_text(
        "def simple(): pass\n"
        "\n"
        "def complex():\n"
        "    if True:\n"
        "        return 1\n"
        "    return 2\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(func_nodes) == 2

    simple = next(n for n in func_nodes if n["name"] == "simple")
    complex_fn = next(n for n in func_nodes if n["name"] == "complex")

    # Different structure -> different shape_ids
    assert simple["shape_id"] != complex_fn["shape_id"]


def test_shape_id_ignores_variable_names(tmp_path: Path) -> None:
    """shape_id should be same for functions that differ only in variable names."""
    (tmp_path / "a.py").write_text(
        "def add(x, y):\n"
        "    result = x + y\n"
        "    return result\n"
    )
    (tmp_path / "b.py").write_text(
        "def sum(a, b):\n"
        "    total = a + b\n"
        "    return total\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(func_nodes) == 2

    # Same structure (assign, binop, return) -> same shape_id
    assert func_nodes[0]["shape_id"] == func_nodes[1]["shape_id"]


def test_output_includes_scheme_identifiers(tmp_path: Path) -> None:
    """Output should include stable_id_scheme and shape_id_scheme."""
    (tmp_path / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    assert "stable_id_scheme" in data
    assert "shape_id_scheme" in data
    assert data["stable_id_scheme"].startswith("hypergumbo-stableid-")
    assert data["shape_id_scheme"].startswith("hypergumbo-shapeid-")


def test_stable_id_different_param_defaults(tmp_path: Path) -> None:
    """stable_id should differ based on presence of defaults."""
    (tmp_path / "app.py").write_text(
        "def no_default(x): pass\n"
        "\n"
        "def with_default(x=1): pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(func_nodes) == 2

    no_default = next(n for n in func_nodes if n["name"] == "no_default")
    with_default = next(n for n in func_nodes if n["name"] == "with_default")

    # Different arity (defaults) -> different stable_ids
    assert no_default["stable_id"] != with_default["stable_id"]


def test_stable_id_varargs_kwargs(tmp_path: Path) -> None:
    """stable_id should account for *args and **kwargs."""
    (tmp_path / "app.py").write_text(
        "def plain(x): pass\n"
        "\n"
        "def with_args(*args): pass\n"
        "\n"
        "def with_kwargs(**kwargs): pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    stable_ids = {n["name"]: n["stable_id"] for n in func_nodes}

    # All three should have different stable_ids
    assert len(set(stable_ids.values())) == 3


def test_stable_id_decorator_attribute(tmp_path: Path) -> None:
    """stable_id should handle module.decorator style decorators."""
    (tmp_path / "app.py").write_text(
        "import functools\n"
        "\n"
        "@functools.lru_cache\n"
        "def cached(): pass\n"
        "\n"
        "def uncached(): pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    cached = next(n for n in func_nodes if n["name"] == "cached")
    uncached = next(n for n in func_nodes if n["name"] == "uncached")

    # Different decorators -> different stable_ids
    assert cached["stable_id"] != uncached["stable_id"]


def test_stable_id_decorator_call(tmp_path: Path) -> None:
    """stable_id should handle decorator(args) style decorators.

    Phase 6 PR3 (INV-bazij) reframed this test: two functions with the
    same decorator name but DIFFERENT function names now split by name
    (the disambiguator that closes the 60% same-file collision rate).
    The narrow invariant this test still pins is that both functions
    have a well-formed sha256-prefixed stable_id derived from the
    decorator extraction path (i.e. neither stable_id is None).
    """
    (tmp_path / "app.py").write_text(
        "@decorator(1, 2)\n"
        "def with_call(): pass\n"
        "\n"
        "@decorator\n"
        "def without_call(): pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    assert len(func_nodes) == 2
    # Phase 6 PR3 (INV-bazij): name is now a disambiguator — distinct
    # function names split stable_id even when decorators match.
    assert func_nodes[0]["stable_id"] != func_nodes[1]["stable_id"]
    assert func_nodes[0]["stable_id"].startswith("sha256:")
    assert func_nodes[1]["stable_id"].startswith("sha256:")


def test_stable_id_decorator_attribute_call(tmp_path: Path) -> None:
    """stable_id should handle module.decorator(args) style decorators."""
    (tmp_path / "app.py").write_text(
        "import functools\n"
        "\n"
        "@functools.lru_cache(maxsize=128)\n"
        "def with_args(): pass\n"
        "\n"
        "def no_decorator(): pass\n"
    )

    out_path = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False)

    data = json.loads(out_path.read_text())

    func_nodes = [n for n in data["nodes"] if n["kind"] == "function"]
    with_args = next(n for n in func_nodes if n["name"] == "with_args")
    no_decorator = next(n for n in func_nodes if n["name"] == "no_decorator")

    # Different decorators -> different stable_ids
    assert with_args["stable_id"] != no_decorator["stable_id"]
