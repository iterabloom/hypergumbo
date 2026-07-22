# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-linon: Python shape_id must not collide across symbol KINDS.

``_compute_shape_id`` (py.py) hashes the AST body skeleton (spec §337/§342,
"changes if control flow changed"). Two defects made structurally-trivial
symbols of *different* kinds share a shape_id, which would seed false
structural-clone groupings for any consumer (WI-vogij's refactoring-lead
detector, or an LLM agent) that clusters by shape_id:

1. **Kind absent from the hash.** A module-level ``def f(): pass`` and a
   class method ``def m(self): pass`` are both ``ast.FunctionDef`` and hit the
   same body branch; ``self`` is not part of the body skeleton, so they hashed
   identically despite being kind=function vs kind=method.
2. **``async def`` mis-branched as a class.** ``ast.AsyncFunctionDef`` is NOT a
   subclass of ``ast.FunctionDef``, so an async def fell into the ClassDef
   ``else`` branch and a docstring-only ``async def`` hashed identically to a
   docstring-only ``class`` (the observed ``sha256:44283d00…`` cluster: 16
   Exception classes + one async method).

The fix folds the symbol kind and the concrete AST node type into the hashed
structure, so kind and sync/async are discriminated while genuine same-kind
structural clones still share a shape_id (WI-vogij's one non-redundant power).
"""

from pathlib import Path

from hypergumbo_lang_mainstream.py import analyze_python


def _by(result, kind: str, name: str):
    """Return the single symbol with the given kind and name."""
    matches = [s for s in result.symbols if s.kind == kind and s.name.endswith(name)]
    assert len(matches) == 1, f"expected 1 {kind} named ...{name}, got {matches}"
    return matches[0]


def test_function_and_method_same_body_differ(tmp_path: Path) -> None:
    """A plain function and a method with identical bodies get distinct shape_ids."""
    (tmp_path / "m.py").write_text(
        "def plain_func():\n"
        "    pass\n"
        "\n"
        "class Holder:\n"
        "    def plain_method(self):\n"
        "        pass\n"
    )
    result = analyze_python(tmp_path)
    func = _by(result, "function", "plain_func")
    meth = _by(result, "method", "plain_method")
    assert func.shape_id is not None
    assert meth.shape_id is not None
    assert func.shape_id != meth.shape_id


def test_docstring_class_and_async_method_differ(tmp_path: Path) -> None:
    """A docstring-only class and a docstring-only async method get distinct shape_ids.

    Reproduces the observed cluster (Exception classes colliding with an async
    method) directly: before the fix the async method mis-branched to ClassDef.
    """
    (tmp_path / "m.py").write_text(
        'class FooError(Exception):\n'
        '    """just a docstring"""\n'
        '\n'
        'class Handler:\n'
        '    async def on_event(self):\n'
        '        """just a docstring"""\n'
    )
    result = analyze_python(tmp_path)
    cls = _by(result, "class", "FooError")
    meth = _by(result, "method", "on_event")
    assert cls.shape_id is not None
    assert meth.shape_id is not None
    assert cls.shape_id != meth.shape_id


def test_async_and_sync_function_same_body_differ(tmp_path: Path) -> None:
    """An async def and a sync def with identical bodies get distinct shape_ids."""
    (tmp_path / "m.py").write_text(
        "async def do_async():\n"
        "    pass\n"
        "\n"
        "def do_sync():\n"
        "    pass\n"
    )
    result = analyze_python(tmp_path)
    a = _by(result, "function", "do_async")
    s = _by(result, "function", "do_sync")
    assert a.shape_id != s.shape_id


def test_same_kind_same_body_same_shape(tmp_path: Path) -> None:
    """Regression: same-kind, same-structure symbols still share a shape_id.

    shape_id's one non-redundant capability is clustering structural clones;
    the kind-discrimination fix must not break within-kind clone detection.
    """
    (tmp_path / "m.py").write_text(
        "def add(x, y):\n"
        "    result = x + y\n"
        "    return result\n"
        "\n"
        "def combine(a, b):\n"
        "    total = a + b\n"
        "    return total\n"
    )
    result = analyze_python(tmp_path)
    add = _by(result, "function", "add")
    combine = _by(result, "function", "combine")
    assert add.shape_id == combine.shape_id


def test_two_async_functions_same_body_same_shape(tmp_path: Path) -> None:
    """Regression: two async functions with identical bodies share a shape_id.

    The async fix must hash the body by content (not mis-branch to ClassDef),
    so structurally-identical async functions still cluster together.
    """
    (tmp_path / "m.py").write_text(
        "async def first(x):\n"
        "    await x.run()\n"
        "\n"
        "async def second(y):\n"
        "    await y.run()\n"
    )
    result = analyze_python(tmp_path)
    first = _by(result, "function", "first")
    second = _by(result, "function", "second")
    assert first.shape_id == second.shape_id


# WI-luzut: variable- and field-kind symbols must also carry a structural
# shape_id (they were shape_id=None because _compute_shape_id required a
# body-bearing FunctionDef/ClassDef node).


def test_variable_symbols_carry_shape_id(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "CONST = 5\n"
        "COMPUTED = make_thing(1, 2)\n"
    )
    result = analyze_python(tmp_path)
    const = _by(result, "variable", "CONST")
    computed = _by(result, "variable", "COMPUTED")
    assert const.shape_id is not None and const.shape_id.startswith("sha256:")
    assert computed.shape_id is not None and computed.shape_id.startswith("sha256:")
    # Distinct assignment shapes (Constant vs Call) -> distinct shape_ids.
    assert const.shape_id != computed.shape_id


def test_same_shape_variables_share_shape_id(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "A = compute(x)\n"
        "B = compute(y)\n"
    )
    result = analyze_python(tmp_path)
    assert (
        _by(result, "variable", "A").shape_id
        == _by(result, "variable", "B").shape_id
    )


def test_field_symbols_carry_shape_id(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "class C:\n"
        "    x = 5\n"
        "    y: int = 0\n"
    )
    result = analyze_python(tmp_path)
    fx = _by(result, "field", "C.x")
    assert fx.shape_id is not None and fx.shape_id.startswith("sha256:")


def test_variable_and_field_shape_id_differ_by_kind(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "V = 7\n"
        "class C:\n"
        "    V = 7\n"
    )
    result = analyze_python(tmp_path)
    var = _by(result, "variable", "V")
    fld = _by(result, "field", "C.V")
    assert var.shape_id is not None and fld.shape_id is not None
    # kind is folded into the hash (WI-linon), so same assignment shape but
    # different kind -> different shape_id.
    assert var.shape_id != fld.shape_id
