# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py
"""Which line of a comment is a declaration's one-line summary.

``extract_doc_comment`` used to return the first non-empty line of the
comment above a declaration. A comment that opens with a ruled line, a bare
block opener or the file's shebang then documented the function as
``------------------``, ``/*``, ``{-`` or ``!/bin/bash``, and a sketch
printed that as what the function does (113 symbols on twelve repositories,
in Go, JavaScript, TypeScript, Elm, Rust and shell).

The rule is stated on the text left after the comment's own markers are
removed, so one table of cases runs through every comment syntax here. The
analyzers' own end-to-end test is in hypergumbo-lang-mainstream.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from hypergumbo_core.analyze.base import (
    _comment_text_lines,
    _is_usage_line,
    extract_doc_comment,
)


@dataclass
class _Node:
    type: str
    start_byte: int = 0
    end_byte: int = 0
    start_point: tuple[int, int] = (0, 0)
    end_point: tuple[int, int] = (0, 0)
    prev_named_sibling: "_Node | None" = None


def _summary(comment: str, *, first_line: int = 5, name: str | None = None,
             max_len: int = 80) -> str | None:
    """The summary of a declaration under ONE comment node holding ``comment``."""
    source = comment.encode()
    lines = comment.count("\n")
    node = _Node("comment", 0, len(source), (first_line, 0), (first_line + lines, 0))
    declaration = _Node("function_definition", prev_named_sibling=node)
    return extract_doc_comment(declaration, source, max_len, name=name)  # type: ignore[arg-type]


# One comment per syntax, each line ready to take a text: (line prefix,
# opener line or None, closer line or None).
SYNTAXES = {
    "hash": ("# ", None, None),
    "slashes": ("// ", None, None),
    "rustdoc": ("/// ", None, None),
    "rust inner": ("//! ", None, None),
    "dashes": ("-- ", None, None),
    "semicolons": (";; ", None, None),
    "percent": ("%% ", None, None),
    "c block": (" * ", "/*", " */"),
    "javadoc": (" * ", "/**", " */"),
    "c block no stars": ("   ", "/*", "*/"),
    "elm block": ("   ", "{-|", "-}"),
    "ocaml block": ("   ", "(*", "*)"),
    "html block": ("   ", "<!--", "-->"),
}

DESCRIPTION = "Safe field extraction from JSON on stdin."


def _comment(syntax: str, texts: list[str]) -> str:
    prefix, opener, closer = SYNTAXES[syntax]
    body = [(prefix + text).rstrip() for text in texts]
    return "\n".join(([opener] if opener else []) + body + ([closer] if closer else []))


@pytest.mark.parametrize("syntax", SYNTAXES)
@pytest.mark.parametrize("above", [
    [],                                               # the plain case
    ["------------------------------------------"],   # a ruled line
    ["=========================================="],
    ["+--------------------------------------+"],
    ["", ""],                                         # blank comment lines
    ["@since 2.0"],                                   # a tag
    ["SPDX-License-Identifier: MIT"],
    ["Copyright (c) 2024 The Authors."],
    ["-*- coding: utf-8 -*-"],
    ["vim: set ts=4 sw=4:"],
])
def test_the_summary_is_the_first_line_that_documents(syntax: str, above: list[str]) -> None:
    comment = _comment(syntax, [*above, DESCRIPTION, "A second line."])
    assert _summary(comment) == DESCRIPTION, comment


@pytest.mark.parametrize("syntax", SYNTAXES)
def test_a_comment_that_documents_nothing_gives_no_summary(syntax: str) -> None:
    """Reach: the same comment with one describing line has a summary."""
    decoration = ["----------------", "", "Copyright 2024 Someone", "@param x the x"]
    assert _summary(_comment(syntax, decoration)) is None
    assert _summary(_comment(syntax, [*decoration, DESCRIPTION])) == DESCRIPTION


@pytest.mark.parametrize("syntax", SYNTAXES)
def test_a_line_that_restates_the_declaration_is_passed_over(syntax: str) -> None:
    """``json_field DOTPATH`` above a describing line is usage, not summary:
    only when the name is the declaration's, and only when something
    follows."""
    usage = "json_field DOTPATH"
    both = _comment(syntax, ["-----", usage, "  " + DESCRIPTION])
    assert _summary(both, name="json_field") == DESCRIPTION
    assert _summary(both, name="other") == usage
    assert _summary(both) == usage
    alone = _comment(syntax, ["-----", usage, "-----"])
    assert _summary(alone, name="json_field") == usage


def test_a_shebang_is_not_documentation() -> None:
    """The first function of a script has the shebang as the comment above
    it. Only on the file's first line is ``#!`` a shebang."""
    script = "#!/bin/bash\n# Runs the checks."
    assert _summary(script, first_line=0) == "Runs the checks."
    assert _summary("#!/bin/bash", first_line=0) is None
    assert _summary("#! important: read this", first_line=7) == "important: read this"


def test_several_comment_nodes_are_read_top_down_and_a_gap_ends_them() -> None:
    source = b"// unrelated note\n\n// ----\n// Does the thing.\nfn x() {}"
    far = _Node("line_comment", 0, 17, (0, 0), (0, 0))
    rule = _Node("line_comment", 19, 26, (2, 0), (2, 0), prev_named_sibling=far)
    text = _Node("line_comment", 27, 45, (3, 0), (3, 0), prev_named_sibling=rule)
    declaration = _Node("function_item", prev_named_sibling=text)
    assert extract_doc_comment(declaration, source) == "Does the thing."  # type: ignore[arg-type]


def test_truncation_and_no_comment() -> None:
    long = "word " * 40
    cut = _summary("# " + long, max_len=20)
    assert cut is not None and len(cut) == 20 and cut.endswith("…")
    usage = _summary("# f " + "ARG " * 30, name="f", max_len=20)
    assert usage is not None and len(usage) == 20
    assert extract_doc_comment(_Node("function_item"), b"") is None  # type: ignore[arg-type]
    not_a_comment = _Node("function_item", prev_named_sibling=_Node("decorator"))
    assert extract_doc_comment(not_a_comment, b"") is None  # type: ignore[arg-type]


@pytest.mark.parametrize(("raw", "lines"), [
    ("/** Does X.\n * more\n */", ["Does X.", "more", ""]),
    ("/* one line */", ["one line"]),
    ("/*! explicit */", ["explicit"]),
    ("{-\n  text\n-}", ["", "text", ""]),
    ("<!-- Avatar -->", ["Avatar"]),
    ("//////////", [""]),
    ("### Heading", ["Heading"]),
    # The marker is the comment's own: dashes are text in a hash comment.
    ("# --flag does X", ["--flag does X"]),
    ("-- --flag does X", ["--flag does X"]),
    ("plain text\n second", ["plain text", "second"]),
    ("", [""]),
])
def test_comment_text_lines(raw: str, lines: list[str]) -> None:
    assert _comment_text_lines(raw) == lines


@pytest.mark.parametrize(("line", "name", "usage"), [
    ("json_field DOTPATH", "json_field", True),
    ("detect_api_base", "detect_api_base", True),
    ("foo(x, y)", "foo", True),
    ("foo: <file> [-v] $1", "foo", True),
    ("main entry point of the tool", "main", False),
    ("foo(path) reads the file", "foo", False),
    ("json_field DOTPATH", "other", False),
])
def test_is_usage_line(line: str, name: str, usage: bool) -> None:
    assert _is_usage_line(line, name) is usage
