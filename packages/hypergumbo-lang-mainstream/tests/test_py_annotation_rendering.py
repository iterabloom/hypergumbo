# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
"""A Python type annotation is shown whole, and hashed whole.

``_format_annotation`` has a case for six AST node kinds and, called bare,
returns ``""`` for any other, so ``Callable[[int], None]`` was displayed, and
recorded in ``meta["parameters"]``, as ``Callable[, None]``. The displayed
signature, the parameter metadata and a class attribute's signature come from
``ast.unparse``.

The signature hashed into ``stable_id`` keeps the six-case rendering but has
its holes filled (``fill_holes=True``: a node kind with no case is written by
``ast.unparse``), so two functions that differ only inside such an annotation
no longer share an id. A function whose annotations use only the six kinds
hashes the same text as before; the last tests pin both halves.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.py import (
    _display_annotation,
    _format_annotation,
    _format_function_signature,
    analyze_python,
)

# Annotation source -> what the six-case renderer made of it.
HOLES = {
    "Callable[[int, str], None]": "Callable[, None]",
    "Literal[-1, 1]": "Literal[, 1]",
    "Annotated[int, max(1, 2)]": "Annotated[int, ]",
    "tuple[*tuple[int, ...]]": "tuple[]",
    "Callable[..., int] | None": "Callable[Ellipsis, int] | None",
    "dict[str, list[Callable[[], None]]]": "dict[str, list[Callable[, None]]]",
}
WHOLE = ["int", "str | None", "dict[str, list[int]]", "typing.Optional[int]", "'Forward'",
         "Dict[str, Tuple[int, int]]"]


def _source(annotations: list[str]) -> str:
    params = ", ".join(f"p{i}: {a}" for i, a in enumerate(annotations))
    return (
        "from typing import Annotated, Callable, Literal\nimport typing\n\n"
        f"def f({params}) -> {annotations[0]}:\n    return None\n\n"
        f"class C:\n    attr: {annotations[0]}\n"
    )


def _symbols(tmp_path: Path, annotations: list[str]) -> dict[str, object]:
    (tmp_path / "m.py").write_text(_source(annotations))
    return {s.name: s for s in analyze_python(tmp_path).symbols}


def test_the_six_case_renderer_does_leave_holes() -> None:
    """Reach: every shape in HOLES is one the old text got wrong, so the
    assertions below are about something."""
    for source, holed in HOLES.items():
        node = ast.parse(source, mode="eval").body
        assert _format_annotation(node) == holed != source
    for source in WHOLE:
        node = ast.parse(source, mode="eval").body
        assert _format_annotation(node) == _display_annotation(node) == source


@pytest.mark.parametrize("annotation", [*HOLES, *WHOLE])
def test_an_annotation_is_shown_as_written(tmp_path: Path, annotation: str) -> None:
    """In the displayed signature, in the parameter metadata, in the return
    type and on a class attribute: one text, the source's."""
    symbols = _symbols(tmp_path, [annotation])
    shown = ast.unparse(ast.parse(annotation, mode="eval").body)
    function = symbols["f"]
    assert function.signature == f"(p0: {shown}) -> {shown}"  # type: ignore[attr-defined]
    assert function.meta["parameters"] == [  # type: ignore[attr-defined]
        {"name": "p0", "type": shown, "default": False}]
    assert function.meta["return_type"] == shown  # type: ignore[attr-defined]
    assert symbols["C.attr"].signature == shown  # type: ignore[attr-defined]


def test_star_args_and_keyword_only_parameters_are_whole_too(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "from typing import Callable\n\n"
        "def g(a: Callable[[int], None], *args: Callable[[], int], "
        "k: Callable[[str], None] = print, **kw: Callable[[], None]) -> None:\n    pass\n")
    g = {s.name: s for s in analyze_python(tmp_path).symbols}["g"]
    assert g.signature == (
        "(a: Callable[[int], None], *args, k: Callable[[str], None]=print, **kw) -> None")
    # meta["parameters"] lists positional, *args and **kwargs parameters.
    assert [(p["name"], p["type"]) for p in g.meta["parameters"]] == [
        ("a", "Callable[[int], None]"), ("*args", "Callable[[], int]"),
        ("**kw", "Callable[[], None]")]


def test_the_hashed_text_has_its_holes_filled() -> None:
    """The text hashed into stable_id writes every annotation node: a kind
    with no case of its own is written by ``ast.unparse``; the six-case
    rendering is kept everywhere else (``...`` still reads ``Ellipsis``
    outside a hole)."""
    node = ast.parse("def f(cb: Callable[[int], None]) -> Literal[-1]: ...").body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _format_function_signature(node) == "(cb: Callable[[int], None]) -> Literal[-1]"
    # A filled text is longer, and the 60-character cut still applies: past
    # it the signature does not normalize and the untyped tier is used.
    long = ast.parse(
        "def f(cb: Callable[[int, str], None], lit: Literal[-1, 1]) -> Callable[[int], str]: ...",
    ).body[0]
    assert isinstance(long, ast.FunctionDef)
    assert _format_function_signature(long) == (
        "(cb: Callable[[int, str], None], lit: Literal[-1, 1]) -> Ca…")
    assert _format_function_signature(long, max_len=240, render_defaults=True) == (
        "(cb: Callable[[int, str], None], lit: Literal[-1, 1]) -> Callable[[int], str]")


# Annotation source -> the hashed rendering with the holes filled. Every AST
# node kind an annotation can hold that the six cases do not cover.
FILLED = {
    "Callable[[int, str], None]": "Callable[[int, str], None]",  # List
    "Callable[[], None]": "Callable[[], None]",  # empty List
    "Literal[-1, 1]": "Literal[-1, 1]",  # UnaryOp
    "Literal[1 + 2]": "Literal[1 + 2]",  # BinOp other than |
    "Annotated[int, Field(gt=0)]": "Annotated[int, Field(gt=0)]",  # Call
    "tuple[*Ts]": "tuple[*Ts]",  # Starred in a Tuple slice
    "tuple[*tuple[int, ...]]": "tuple[*tuple[int, ...]]",  # Starred
    "Annotated[int, lambda v: v]": "Annotated[int, lambda v: v]",  # Lambda
    "Annotated[int, {'a': 1}]": "Annotated[int, {'a': 1}]",  # Dict
    "Annotated[int, {1, 2}]": "Annotated[int, {1, 2}]",  # Set
    "Annotated[str, f'x{y}']": "Annotated[str, f'x{y}']",  # JoinedStr
    "Annotated[int, x > 0]": "Annotated[int, x > 0]",  # Compare
    "Annotated[int, a and b]": "Annotated[int, a and b]",  # BoolOp
    "Annotated[int, [i for i in y]]": "Annotated[int, [i for i in y]]",  # ListComp
    "x[1:2]": "x[1:2]",  # Slice
    "a().b": "a().b",  # Call under an Attribute
    "f()[int]": "f()[int]",  # Call under a Subscript
    "int if X else str": "int if X else str",  # IfExp (was dropped whole)
    "Callable[[Callable[..., T]], T]": "Callable[[Callable[..., T]], T]",
}
# Annotations with no hole: the hashed text is the six-case text, unchanged,
# including its deliberate or historical rewrites.
UNCHANGED = {
    "int": "int",
    "typing.Optional[int]": "typing.Optional[int]",
    "dict[str, list[int]]": "dict[str, list[int]]",
    "str | None": "str | None",
    "'Forward'": "'Forward'",
    "None": "None",
    "Literal[1]": "Literal[1]",
    "Callable[..., int]": "Callable[Ellipsis, int]",
    "tuple[()]": "tuple[]",
}


def _hashed(annotation: str) -> str:
    node = ast.parse(f"def f(p: {annotation}) -> {annotation}: ...").body[0]
    assert isinstance(node, ast.FunctionDef)
    return _format_function_signature(node, max_len=10_000)


@pytest.mark.parametrize(("annotation", "filled"), FILLED.items())
def test_every_annotation_node_is_hashed(annotation: str, filled: str) -> None:
    assert _format_annotation(ast.parse(annotation, mode="eval").body) != filled
    assert _hashed(annotation) == f"(p: {filled}) -> {filled}"


@pytest.mark.parametrize(("annotation", "kept"), UNCHANGED.items())
def test_an_annotation_with_no_hole_hashes_as_before(annotation: str, kept: str) -> None:
    assert _format_annotation(ast.parse(annotation, mode="eval").body) == kept
    assert _hashed(annotation) == f"(p: {kept}) -> {kept}"


def test_holes_are_filled_for_the_hash_only(tmp_path: Path) -> None:
    """The other readers of the six-case renderer keep its bare form: a base
    class written as a call is still ``""`` in ``base_classes`` (WI-pusan)."""
    (tmp_path / "m.py").write_text(
        "from collections import namedtuple\n\n"
        "class P(namedtuple('P', 'x y')):\n    pass\n")
    p = {s.name: s for s in analyze_python(tmp_path).symbols}["P"]
    assert p.meta["base_classes"] == [""]  # type: ignore[attr-defined]


def _method_id(tmp_path: Path, annotation: str) -> str:
    (tmp_path / "m.py").write_text(
        "from typing import Callable, Literal\n\n"
        f"class C:\n    def m(self, cb: {annotation}) -> None:\n        pass\n")
    m = {s.name: s for s in analyze_python(tmp_path).symbols}["C.m"]
    return m.stable_id  # type: ignore[attr-defined,no-any-return]


@pytest.mark.parametrize(("left", "right"), [
    ("Callable[[int], None]", "Callable[[bytes, str], None]"),
    ("Literal[-1]", "Literal[-2]"),
])
def test_two_methods_that_differ_inside_a_hole_have_two_ids(
    tmp_path: Path, left: str, right: str,
) -> None:
    assert _method_id(tmp_path, left) != _method_id(tmp_path, right)
