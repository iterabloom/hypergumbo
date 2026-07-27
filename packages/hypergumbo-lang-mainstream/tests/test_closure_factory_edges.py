# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for closure-factory ``dispatches_to`` edges (dispatch:F8 PR-A).

A function ``F`` that returns one of its own directly-nested functions is a
*closure factory*. The returned inner closure is reachable whenever ``F`` is
(``F`` is reached at its call / decoration sites), but the call-graph BFS used
by ``dead-code-maybe`` only follows ``{calls, dispatches_to, wraps}`` edges, so
the nested closure has ZERO reachability in-edges and is falsely flagged dead.

These tests pin the structural fix: the Python analyzer emits an edge
``F -> nested_closure`` of type ``dispatches_to`` with
``meta["dispatch_kind"] == "closure_factory"`` for ``return <bare-name>``
where the name resolves to a directly-nested ``FunctionDef`` /
``AsyncFunctionDef`` of ``F``. Negative cases (returning a parameter, a call,
an import, or merely *defining* a nested function without returning it) emit
NO such edge — the detection is return-of-nested-name only.
"""
from pathlib import Path

from hypergumbo_lang_mainstream.py import extract_nodes


def _closure_factory_edges(py_file: Path) -> list:
    """Return all closure-factory ``dispatches_to`` edges in *py_file*."""
    result = extract_nodes(py_file)
    return [
        e
        for e in result.edges
        if e.edge_type == "dispatches_to"
        and (e.meta or {}).get("dispatch_kind") == "closure_factory"
    ]


def test_factory_returning_nested_function_emits_dispatch_edge(tmp_path: Path) -> None:
    """``def make(): def inner(): ...; return inner`` → one closure_factory edge."""
    py_file = tmp_path / "factory.py"
    py_file.write_text(
        "def make():\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner\n"
    )

    edges = _closure_factory_edges(py_file)
    assert len(edges) == 1, [
        (e.src, e.dst, e.edge_type, e.meta) for e in extract_nodes(py_file).edges
    ]
    edge = edges[0]
    assert "make" in edge.src
    assert "inner" in edge.dst
    assert edge.edge_type == "dispatches_to"
    assert (edge.meta or {}).get("dispatch_kind") == "closure_factory"


def test_factory_returning_nested_async_function_emits_dispatch_edge(
    tmp_path: Path,
) -> None:
    """A nested ``async def`` returned by its factory also emits the edge."""
    py_file = tmp_path / "async_factory.py"
    py_file.write_text(
        "def make():\n"
        "    async def inner():\n"
        "        return 1\n"
        "    return inner\n"
    )

    edges = _closure_factory_edges(py_file)
    assert len(edges) == 1
    assert "inner" in edges[0].dst


def test_async_factory_returning_nested_function_emits_dispatch_edge(
    tmp_path: Path,
) -> None:
    """An ``async def`` factory returning its nested helper emits the edge."""
    py_file = tmp_path / "async_outer.py"
    py_file.write_text(
        "async def make():\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner\n"
    )

    edges = _closure_factory_edges(py_file)
    assert len(edges) == 1
    assert "inner" in edges[0].dst


def test_return_inside_if_block_emits_dispatch_edge(tmp_path: Path) -> None:
    """A ``return <nested>`` inside a simple ``if`` block still emits the edge."""
    py_file = tmp_path / "if_factory.py"
    py_file.write_text(
        "def make(flag):\n"
        "    def inner():\n"
        "        return 1\n"
        "    if flag:\n"
        "        return inner\n"
        "    return None\n"
    )

    edges = _closure_factory_edges(py_file)
    assert len(edges) == 1
    assert "inner" in edges[0].dst


def test_return_inside_try_block_emits_dispatch_edge(tmp_path: Path) -> None:
    """A ``return <nested>`` inside a simple ``try`` block still emits the edge."""
    py_file = tmp_path / "try_factory.py"
    py_file.write_text(
        "def make():\n"
        "    def inner():\n"
        "        return 1\n"
        "    try:\n"
        "        return inner\n"
        "    except Exception:\n"
        "        return None\n"
    )

    edges = _closure_factory_edges(py_file)
    assert len(edges) == 1
    assert "inner" in edges[0].dst


def test_factory_emits_exactly_one_edge_per_nested_target(tmp_path: Path) -> None:
    """Returning the same nested closure twice de-dupes to one edge_key.

    The canonical ``register_analyzer`` shape returns its decorator once, but a
    factory with two return statements (e.g. an early ``if`` return plus a
    fallthrough ``return``) pointing at the same nested function must not
    double-count. Edge identity collapses on ``(src, dst, type)`` via
    ``edge_key``; we assert at most one distinct closure_factory edge_key.
    """
    py_file = tmp_path / "double_return.py"
    py_file.write_text(
        "def make(flag):\n"
        "    def inner():\n"
        "        return 1\n"
        "    if flag:\n"
        "        return inner\n"
        "    return inner\n"
    )

    edges = _closure_factory_edges(py_file)
    assert len({e.edge_key for e in edges}) == 1


def test_return_parameter_emits_no_dispatch_edge(tmp_path: Path) -> None:
    """``def f(x): return x`` returns a parameter → no closure_factory edge."""
    py_file = tmp_path / "param.py"
    py_file.write_text("def f(x):\n    return x\n")

    assert _closure_factory_edges(py_file) == []


def test_factory_returning_non_nested_name_emits_no_dispatch_edge(
    tmp_path: Path,
) -> None:
    """A factory with a nested def that returns a DIFFERENT (non-nested) name.

    ``F`` does have a nested closure (so ``inner_scope`` is non-empty and the
    detector proceeds past the early-out), but the actual ``return`` targets a
    parameter, not the nested def — so the bare name is absent from
    ``inner_scope`` and no edge is emitted. Pins the present-but-not-nested
    resolution path.
    """
    py_file = tmp_path / "non_nested_return.py"
    py_file.write_text(
        "def make(x):\n"
        "    def inner():\n"
        "        return 1\n"
        "    return x\n"
    )

    assert _closure_factory_edges(py_file) == []


def test_nested_def_inside_if_block_is_not_traversed(tmp_path: Path) -> None:
    """A nested def living inside an ``if`` block does not leak its own returns.

    The factory ``make`` returns its top-level nested ``outer`` (one edge). A
    *second* nested function ``buried`` is defined inside the ``if`` block and
    has its own ``return 99``; the return collector must NOT descend into
    ``buried``'s scope and mis-attribute ``return 99`` to ``make``. We assert
    exactly one closure_factory edge, targeting ``outer`` — not two.
    """
    py_file = tmp_path / "nested_in_if.py"
    py_file.write_text(
        "def make(flag):\n"
        "    def outer():\n"
        "        return 1\n"
        "    if flag:\n"
        "        def buried():\n"
        "            return 99\n"
        "        buried()\n"
        "    return outer\n"
    )

    edges = _closure_factory_edges(py_file)
    assert len(edges) == 1
    assert "outer" in edges[0].dst


def test_return_call_emits_no_dispatch_edge(tmp_path: Path) -> None:
    """``def f(): return helper()`` returns a call result → no edge.

    Even though ``helper`` is a nested function, the return is a *call*
    (``ast.Call``), not a bare reference to the function object, so no
    closure_factory edge is emitted.
    """
    py_file = tmp_path / "call.py"
    py_file.write_text(
        "def f():\n"
        "    def helper():\n"
        "        return 1\n"
        "    return helper()\n"
    )

    assert _closure_factory_edges(py_file) == []


def test_return_import_emits_no_dispatch_edge(tmp_path: Path) -> None:
    """Returning an imported name (not a nested def) emits no edge."""
    py_file = tmp_path / "imp.py"
    py_file.write_text(
        "from os.path import join\n"
        "def f():\n"
        "    return join\n"
    )

    assert _closure_factory_edges(py_file) == []


def test_return_module_level_function_emits_no_dispatch_edge(tmp_path: Path) -> None:
    """Returning a NON-nested (module-level) function emits no edge.

    ``other`` is a sibling top-level function, not nested in ``f``; the
    closure-factory edge is scoped to F's own directly-nested defs only.
    """
    py_file = tmp_path / "sibling.py"
    py_file.write_text(
        "def other():\n"
        "    return 1\n"
        "def f():\n"
        "    return other\n"
    )

    assert _closure_factory_edges(py_file) == []


def test_return_attribute_emits_no_dispatch_edge(tmp_path: Path) -> None:
    """``def f(self): return self.x`` returns an attribute → no edge."""
    py_file = tmp_path / "attr.py"
    py_file.write_text(
        "class C:\n"
        "    def f(self):\n"
        "        return self.x\n"
    )

    assert _closure_factory_edges(py_file) == []


def test_defines_nested_but_does_not_return_it_emits_no_dispatch_edge(
    tmp_path: Path,
) -> None:
    """Defining + calling a nested function (but not returning it) → no edge.

    Pins the contract that detection is return-based: ``inner()`` is invoked
    (a ``calls`` edge), never returned, so no closure_factory edge appears.
    """
    py_file = tmp_path / "noreturn.py"
    py_file.write_text(
        "def make():\n"
        "    def inner():\n"
        "        return 1\n"
        "    inner()\n"
    )

    assert _closure_factory_edges(py_file) == []
