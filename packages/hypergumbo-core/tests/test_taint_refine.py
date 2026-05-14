# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the post-DDG IR refinement pass (WI-dilih).

The pass consumes a function's tree-sitter AST and DDG and rewrites the
``edge.dst`` of unresolved-external method-call edges so the analyzer's
``python:external:0-0:NAME:unresolved`` placeholder is replaced with a
module-resolved form like ``python:os.environ:0-0:NAME:unresolved``.

This lets ``_sink_module_compatible`` reject cross-module short-name
collisions (e.g., ``dict.get`` vs ``multiprocessing.Queue.get``) that
currently fall through its ``external`` exemption.

Scope: applies only to languages with a §1c def/use extractor — Python
today, Rust / TypeScript when those extractors land.
"""
from __future__ import annotations

import pytest

tree_sitter = pytest.importorskip("tree_sitter")
get_language = pytest.importorskip("tree_sitter_language_pack").get_language

# Imports below intentionally follow the importorskip guards so a missing
# tree-sitter install skips the module cleanly instead of erroring at
# collection time.
from hypergumbo_core.cfg import (  # noqa: E402
    build_function_cfg,
    load_cfg_mapping,
    populate_def_use_for_cfg,
    solve_reaching_defs,
)
# Force-register the Python def/use extractor.
import hypergumbo_lang_mainstream.py_def_use  # noqa: E402
from hypergumbo_core.taint_refine import (  # noqa: E402
    extract_python_imports,
    extract_python_receiver_hints,
    refine_external_edges,
)


@pytest.fixture(scope="module")
def py_parser():
    lang = get_language("python")
    return tree_sitter.Parser(lang)


@pytest.fixture(scope="module")
def py_mapping():
    return load_cfg_mapping("python")


def _first_function(tree_root):
    """Return the first ``function_definition`` node in a parsed module."""
    for child in tree_root.children:
        if child.type == "function_definition":
            return child
    raise AssertionError("no function_definition found")  # pragma: no cover


def _build_function_ddg(tree_root, source, mapping, symbol_id):
    """Run the CFG / def-use / reaching-def pipeline for the first function."""
    fn_node = _first_function(tree_root)
    body_node = fn_node.child_by_field_name("body")
    cfg = build_function_cfg(body_node, source, mapping, symbol_id)
    populate_def_use_for_cfg(cfg, body_node, source, "python")
    result = solve_reaching_defs(cfg)
    return fn_node, body_node, cfg, result


# ---------------------------------------------------------------------------
# extract_python_imports
# ---------------------------------------------------------------------------


def test_extract_imports_simple_import(py_parser):
    src = b"import os\n"
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    assert module_imports == {"os": "os"}
    assert imports == {}


def test_extract_imports_dotted_import(py_parser):
    src = b"import os.path\n"
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    # `import os.path` binds the root name `os` (Python semantics).
    assert module_imports == {"os": "os.path"}


def test_extract_imports_aliased_import(py_parser):
    src = b"import os.path as op\n"
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    assert module_imports == {"op": "os.path"}


def test_extract_imports_from_import(py_parser):
    src = b"from os import environ\n"
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    assert module_imports == {}
    assert imports == {"environ": ("os", "environ")}


def test_extract_imports_from_import_aliased(py_parser):
    src = b"from os import environ as env_alias\n"
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    assert imports == {"env_alias": ("os", "environ")}


def test_extract_imports_from_dotted_module(py_parser):
    src = b"from os.path import join, exists as e\n"
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    assert imports == {
        "join": ("os.path", "join"),
        "e": ("os.path", "exists"),
    }


def test_extract_imports_combined(py_parser):
    src = b"""import os
import subprocess as sp
from os import environ
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    assert module_imports == {"os": "os", "sp": "subprocess"}
    assert imports == {"environ": ("os", "environ")}


# ---------------------------------------------------------------------------
# extract_python_receiver_hints
# ---------------------------------------------------------------------------


def test_receiver_hint_via_module_attr_chain(py_parser, py_mapping):
    """``x = os.environ`` followed by ``x.get(...)`` → hint ``os.environ``.

    The receiver `x` is bound to the module attribute ``os.environ`` and
    later used as the receiver of `.get()`. The pass walks DDG back from
    the call site's `x` use to its def, inspects the RHS (an attribute
    chain rooted at the import ``os``), and yields ``os.environ`` as
    the hint for the call-site's edge rewrite.
    """
    src = b"""import os
def f():
    x = os.environ
    return x.get("FOO")
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    sym_id = "python:t.py:2-4:f:function"
    fn_node, body_node, cfg, result = _build_function_ddg(
        tree.root_node, src, py_mapping, sym_id,
    )
    hints = extract_python_receiver_hints(
        body_node, src, module_imports, imports, result.ddg_edges,
    )
    # The call site is at line 4 with attr name 'get'.
    assert hints == {(4, "get"): "os.environ"}


def test_receiver_hint_via_from_import_alias(py_parser, py_mapping):
    """``from os import environ as e`` then ``y = e; y.get(...)`` →
    hint ``os.environ`` (e is rebound to environ from os)."""
    src = b"""from os import environ as e
def f():
    y = e
    return y.get("X")
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    sym_id = "python:t.py:2-4:f:function"
    fn_node, body_node, cfg, result = _build_function_ddg(
        tree.root_node, src, py_mapping, sym_id,
    )
    hints = extract_python_receiver_hints(
        body_node, src, module_imports, imports, result.ddg_edges,
    )
    assert hints == {(4, "get"): "os.environ"}


def test_receiver_hint_unresolvable_when_no_binding(py_parser, py_mapping):
    """Parameter receiver — no in-function def → no hint.

    The pass MUST NOT invent a hint; if the receiver's def isn't visible
    in the DDG (it's a parameter, a closure capture, etc.), the edge
    keeps its ``external`` placeholder.
    """
    src = b"""def f(x):
    return x.get("FOO")
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    sym_id = "python:t.py:1-2:f:function"
    fn_node, body_node, cfg, result = _build_function_ddg(
        tree.root_node, src, py_mapping, sym_id,
    )
    hints = extract_python_receiver_hints(
        body_node, src, module_imports, imports, result.ddg_edges,
    )
    assert hints == {}


def test_receiver_hint_skips_call_rhs(py_parser, py_mapping):
    """``x = requests.get(); x.json()`` — RHS is a call, not an attr chain.

    Best-MVP semantics: only handle pure attribute-chain RHS binding.
    Call RHSes can't be resolved without return-type inference; leave
    the edge unresolved (covered by structural fallback).
    """
    src = b"""import requests
def f():
    x = requests.Session()
    return x.get("/foo")
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    sym_id = "python:t.py:2-4:f:function"
    fn_node, body_node, cfg, result = _build_function_ddg(
        tree.root_node, src, py_mapping, sym_id,
    )
    hints = extract_python_receiver_hints(
        body_node, src, module_imports, imports, result.ddg_edges,
    )
    assert hints == {}


def test_receiver_hint_single_identifier_module_alias(py_parser, py_mapping):
    """``import os; x = os; x.environ`` — RHS is a single identifier
    that is itself an ``import``-bound name. Hint is the module path."""
    src = b"""import os
def f():
    x = os
    return x.environ
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    sym_id = "python:t.py:2-4:f:function"
    fn_node, body_node, cfg, result = _build_function_ddg(
        tree.root_node, src, py_mapping, sym_id,
    )
    hints = extract_python_receiver_hints(
        body_node, src, module_imports, imports, result.ddg_edges,
    )
    # ``x.environ`` is an attribute access; the AST shape this test
    # checks is just that ``x``'s module-of-origin is recoverable when
    # the binding RHS is the bare ``os`` identifier. There's no
    # ``.method()`` call here so no call-attr hint emerges — but the
    # branch ``name in module_imports`` is exercised when the helper
    # walks any attribute-style access that the call-finder reaches.
    # To keep this test pure, use a method call form too:
    src2 = b"""import os
def g():
    x = os
    return x.getcwd()
"""
    tree2 = py_parser.parse(src2)
    module_imports2, imports2 = extract_python_imports(tree2.root_node, src2)
    sym_id2 = "python:t.py:2-4:g:function"
    fn_node2, body_node2, cfg2, result2 = _build_function_ddg(
        tree2.root_node, src2, py_mapping, sym_id2,
    )
    hints2 = extract_python_receiver_hints(
        body_node2, src2, module_imports2, imports2, result2.ddg_edges,
    )
    assert hints2 == {(4, "getcwd"): "os"}


def test_receiver_hint_from_import_attr_chain(py_parser, py_mapping):
    """``from os import path; x = path.something; x.method()``.

    Covers the ``root in imports`` branch of attribute-chain RHS: the
    root of the chain (``path``) is a ``from``-imported name, so the
    hint extends from the import's module path with the chain tail.
    """
    src = b"""from os import path
def f():
    x = path.sep
    return x.encode("utf-8")
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    sym_id = "python:t.py:2-4:f:function"
    fn_node, body_node, cfg, result = _build_function_ddg(
        tree.root_node, src, py_mapping, sym_id,
    )
    hints = extract_python_receiver_hints(
        body_node, src, module_imports, imports, result.ddg_edges,
    )
    assert hints == {(4, "encode"): "os.path.sep"}


def test_receiver_hint_identifier_rhs_not_an_import(py_parser, py_mapping):
    """``x = some_local; x.method()`` — RHS is an identifier we don't
    recognise as an import. The DDG resolves to the local binding, but
    the binding tells us nothing about module-of-origin, so no hint."""
    src = b"""def f(some_local):
    x = some_local
    return x.run()
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    sym_id = "python:t.py:1-3:f:function"
    fn_node, body_node, cfg, result = _build_function_ddg(
        tree.root_node, src, py_mapping, sym_id,
    )
    hints = extract_python_receiver_hints(
        body_node, src, module_imports, imports, result.ddg_edges,
    )
    assert hints == {}


def test_receiver_hint_call_rooted_attribute_chain(py_parser, py_mapping):
    """``x = foo().bar; x.method()`` — chain root is a call, not an
    identifier. ``_unwind_attribute_chain`` returns None; no hint."""
    src = b"""def f():
    x = foo().bar
    return x.run()
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    sym_id = "python:t.py:1-3:f:function"
    fn_node, body_node, cfg, result = _build_function_ddg(
        tree.root_node, src, py_mapping, sym_id,
    )
    hints = extract_python_receiver_hints(
        body_node, src, module_imports, imports, result.ddg_edges,
    )
    assert hints == {}


def test_receiver_hint_root_not_in_imports(py_parser, py_mapping):
    """``x = foo.bar`` where ``foo`` is not imported — no hint.

    The receiver chain's root must be a known import; otherwise the
    binding tells us nothing about module-of-origin.
    """
    src = b"""def f():
    x = foo.bar  # foo undefined at this scope
    return x.get("X")
"""
    tree = py_parser.parse(src)
    module_imports, imports = extract_python_imports(tree.root_node, src)
    sym_id = "python:t.py:1-3:f:function"
    fn_node, body_node, cfg, result = _build_function_ddg(
        tree.root_node, src, py_mapping, sym_id,
    )
    hints = extract_python_receiver_hints(
        body_node, src, module_imports, imports, result.ddg_edges,
    )
    assert hints == {}


# ---------------------------------------------------------------------------
# refine_external_edges
# ---------------------------------------------------------------------------


def test_refine_external_edges_rewrites_module_segment():
    """``python:external:0-0:get:unresolved`` → ``python:os.environ:...``
    when a hint is present for (src, line, attr)."""
    caller = "python:t.py:2-4:f:function"
    edges = [
        {
            "src": caller,
            "dst": "python:external:0-0:get:unresolved",
            "type": "calls",
            "line": 4,
        },
    ]
    hints_by_caller = {caller: {(4, "get"): "os.environ"}}
    refined = refine_external_edges(edges, hints_by_caller)
    assert refined == [
        {
            "src": caller,
            "dst": "python:os.environ:0-0:get:unresolved",
            "type": "calls",
            "line": 4,
        },
    ]


def test_refine_external_edges_no_hint_passes_through():
    """No hint for this (caller, line, attr) → edge unchanged."""
    caller = "python:t.py:2-4:f:function"
    edges = [
        {
            "src": caller,
            "dst": "python:external:0-0:get:unresolved",
            "type": "calls",
            "line": 4,
        },
    ]
    refined = refine_external_edges(edges, {})
    assert refined == edges


def test_refine_external_edges_skips_already_resolved():
    """Edges whose dst module is already specific (not 'external') are untouched."""
    caller = "python:t.py:2-4:f:function"
    edges = [
        {
            "src": caller,
            "dst": "python:os.environ:0-0:get:unresolved",
            "type": "calls",
            "line": 4,
        },
        {
            "src": caller,
            "dst": "python:t.py:10-15:helper:function",
            "type": "calls",
            "line": 4,
        },
    ]
    hints_by_caller = {caller: {(4, "get"): "WRONG.MODULE"}}
    refined = refine_external_edges(edges, hints_by_caller)
    assert refined == edges


def test_refine_external_edges_preserves_metadata():
    """Edge metadata (confidence, evidence_type, etc.) is preserved on rewrite."""
    caller = "python:t.py:2-4:f:function"
    edges = [
        {
            "src": caller,
            "dst": "python:external:0-0:get:unresolved",
            "type": "calls",
            "line": 4,
            "confidence": 0.40,
            "evidence_type": "ast_call",
            "meta": {"call_construct": "method"},
        },
    ]
    hints_by_caller = {caller: {(4, "get"): "os.environ"}}
    refined = refine_external_edges(edges, hints_by_caller)
    assert refined[0]["confidence"] == 0.40
    assert refined[0]["evidence_type"] == "ast_call"
    assert refined[0]["meta"] == {"call_construct": "method"}
    assert refined[0]["dst"] == "python:os.environ:0-0:get:unresolved"


def test_refine_external_edges_returns_new_list():
    """Refinement does not mutate the input edges list."""
    caller = "python:t.py:2-4:f:function"
    original = {
        "src": caller,
        "dst": "python:external:0-0:get:unresolved",
        "type": "calls",
        "line": 4,
    }
    edges = [original]
    hints_by_caller = {caller: {(4, "get"): "os.environ"}}
    refined = refine_external_edges(edges, hints_by_caller)
    # Original edge unchanged.
    assert edges[0]["dst"] == "python:external:0-0:get:unresolved"
    # New edge has rewritten dst.
    assert refined[0]["dst"] == "python:os.environ:0-0:get:unresolved"


def test_refine_external_edges_no_caller_match():
    """Hints exist for a different caller — no rewrite."""
    edges = [
        {
            "src": "python:t.py:2-4:f:function",
            "dst": "python:external:0-0:get:unresolved",
            "type": "calls",
            "line": 4,
        },
    ]
    hints_by_caller = {
        "python:t.py:10-12:other:function": {(4, "get"): "os.environ"},
    }
    refined = refine_external_edges(edges, hints_by_caller)
    assert refined == edges


def test_refine_external_edges_caller_has_hints_but_not_for_this_call():
    """Hints exist for the caller but not for the (line, attr) of this
    edge — edge unchanged."""
    caller = "python:t.py:2-6:f:function"
    edges = [
        {
            "src": caller,
            "dst": "python:external:0-0:get:unresolved",
            "type": "calls",
            "line": 5,
        },
    ]
    hints_by_caller = {caller: {(4, "post"): "requests"}}
    refined = refine_external_edges(edges, hints_by_caller)
    assert refined == edges


def test_refine_external_edges_malformed_dst_passes_through():
    """Edge with a dst shorter than the 5-segment symbol_id format —
    refusal is silent; edge passes through unchanged."""
    edges = [
        {
            "src": "python:t.py:2-4:f:function",
            "dst": "not_a_real_symbol_id",
            "type": "calls",
            "line": 4,
        },
    ]
    refined = refine_external_edges(edges, {})
    assert refined == edges


def test_refine_external_edges_non_python_lang_ignored():
    """Non-Python edges are passed through (refinement is per-§1c-extractor)."""
    edges = [
        {
            "src": "go:t.go:2-4:f:function",
            "dst": "go:external:0-0:Get:unresolved",
            "type": "calls",
            "line": 4,
        },
    ]
    hints_by_caller = {}
    refined = refine_external_edges(edges, hints_by_caller)
    assert refined == edges
