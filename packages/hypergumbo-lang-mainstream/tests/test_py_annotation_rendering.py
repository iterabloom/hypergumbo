# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
"""A Python type annotation is shown whole, and identity does not move.

``_format_annotation`` has a case for six AST node kinds and returns ``""``
for any other, so ``Callable[[int], None]`` was displayed, and recorded in
``meta["parameters"]``, as ``Callable[, None]``. The displayed signature,
the parameter metadata and a class attribute's signature now come from
``ast.unparse``.

The same function still renders the signature that is hashed into
``stable_id``, holes included: correcting THAT text changes the id of every
function with such an annotation, which is an identity-scheme decision. The
last test pins the hashed text so the decision cannot be taken by accident.
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


def test_the_text_hashed_into_stable_id_is_unchanged() -> None:
    """The hashed signature keeps the old rendering, holes and all. If this
    fails, a function's stable_id has changed: that needs a scheme-version
    decision, not an edit to this expectation."""
    node = ast.parse(
        "def f(cb: Callable[[int, str], None], lit: Literal[-1, 1]) -> Callable[[int], str]: ...",
    ).body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _format_function_signature(node) == (
        "(cb: Callable[, None], lit: Literal[, 1]) -> Callable[, str]")
    assert _format_function_signature(node, max_len=240, render_defaults=True) == (
        "(cb: Callable[[int, str], None], lit: Literal[-1, 1]) -> Callable[[int], str]")
