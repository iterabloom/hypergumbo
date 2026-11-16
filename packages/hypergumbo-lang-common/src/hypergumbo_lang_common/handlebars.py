# SPDX-License-Identifier: AGPL-3.0-or-later
"""Handlebars template analyzer using regex patterns.

Handlebars is a popular templating language used in Ember.js, Express.js, and
standalone. Templates (.hbs, .handlebars) use {{}} syntax for expressions,
helpers, partials, and block helpers.

How It Works
------------
No tree-sitter grammar is available on PyPI, so :func:`_tags` splits each
file into its mustache tags itself, over the whole text rather than line by
line (an opener's ``}}`` may sit on a later line):

1. Find all .hbs and .handlebars files
2. Tokenize each into tags, skipping what Handlebars does not compile:
   comments (``{{!-- --}}``, ``{{! }}``), escaped mustaches (``\\{{``) and
   the body of a raw block (``{{{{raw}}}} ... {{{{/raw}}}}``)
3. Strip the whitespace-control tildes (``{{~#if a~}}``) from every tag
4. Walk the tags in source order, keeping the stack of open blocks, and
   emit partial references and block helpers (non-block ``{{helper args}}``
   calls are not extracted)

Symbols Extracted
-----------------
- **partial**: Partial template references (``{{> header}}``, ``{{~> header}}``)
  and partial blocks (``{{#> layout}}``)
- **block**: Built-in block helper invocations (``{{#each}}``, ``{{#if}}``,
  ``{{#with}}``, ``{{^if}}``) and the built-in ``{{#*inline "name"}}``
  decorator block
- **helper**: Custom block helper invocations (``{{#formatDate date}}``),
  their inverse sections (``{{^items}}``) and raw blocks (``{{{{raw}}}}``)

Why This Design
---------------
- Handlebars is widely used in Node.js web applications
- Partial references reveal template composition
- Block helpers show control flow and iteration patterns
- Custom helpers indicate reusable template logic

Identity (ADR-0035, WI-tusad)
-----------------------------
Every symbol mints ``overload_arity.scoped_stable_id`` with the blocks open
around it as the scope chain, each labelled by its opening tag's content with
whitespace collapsed and whitespace-control tildes removed (``if user``,
``each items``); a block's own label is the last link of its chain. An opener
other than ``{{#name}}`` keeps its sigil in the label, so it never shares a
label with a ``{{#name}}`` block: ``^items`` (inverse section), ``> layout``
(partial block), ``*inline "sidebar"`` (decorator block). ``{{> header}}``
inside ``{{#if user}}`` and again inside ``{{#if admin}}`` are two symbols that
the floor key ``(kind, path, name)`` nulled. The label is the tag's text, never
its position: two sibling ``{{#if user}}`` blocks share a label, and a symbol
repeated in them (or in the two arms of one ``{{#if}}``: ``{{else}}`` and
``{{else if}}`` continue their block and open no scope, as Twig's ``else``)
is a same-scope tie that the per-file split pass orders (ADR-0035 §1).

Block rules (:func:`_walk`): an opener (``{{#name}}``, ``{{^name}}``,
``{{#> partial}}``, ``{{#*decorator}}``) pushes a block; ``{{/name}}`` pops
back to the innermost open block of that name and is ignored when none is
open (Handlebars rejects such a template; the analyzer keeps going). A block
never closed stays open to the end of the file, so a symbol's chain depends
only on the text above it. A tag whose ``}}`` never comes (the next ``{{``
or the end of the file comes first) still yields its symbol, but opens no
block. An unterminated ``{{!--`` comment ends at the next ``}}``, and a raw
block with no ``{{{{/name}}}}`` holds no text back.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import scoped_stable_id
from hypergumbo_core.paths import to_relative_path

PASS_ID = make_pass_id("handlebars")

# Built-in block helpers to exclude from custom helper detection
_BUILTIN_BLOCKS = frozenset({"if", "unless", "each", "with", "else"})
# Built-in decorator blocks ({{#*inline "name"}})
_BUILTIN_DECORATORS = frozenset({"inline"})

# The partial name after ``>``: {{> partialName}} or {{> "path/to/partial"}}
_PARTIAL_NAME_RE = re.compile(r""">\s*['"]?([^\s'"}\)]+)['"]?""")
# A block helper's symbol name: the word that opens its path
_HELPER_NAME_RE = re.compile(r"\s*(\w+)")
# A block's path as its closer names it: {{#each-in}} ... {{/each-in}}
_PATH_RE = re.compile(r"\s*([^\s}()]+)")
# {{{{raw args}}}}: the body of a raw block is text up to {{{{/raw}}}}
_RAW_OPEN_RE = re.compile(r"\{\{\{\{(?!/)\s*([^}]*?)\s*\}\}\}\}")
# The end of a long comment: --}} or --~}}
_LONG_COMMENT_END_RE = re.compile(r"--~?\}\}")


@dataclass(frozen=True)
class _Tag:
    """One mustache tag: ``body`` is its text between the delimiters, with
    the whitespace-control tildes removed; ``body_at`` is the offset of
    ``body[0]`` in the file. ``closed`` is False for a tag whose ``}}``
    never came. ``raw`` marks a raw block's opener."""

    start: int
    body: str
    body_at: int
    closed: bool = True
    raw: bool = False


def _raw_block(content: str, start: int) -> tuple[_Tag, int] | None:
    """The raw-block opener at ``start`` and the offset after its body, or
    None when no raw opener starts there. A raw block whose closer never
    comes holds nothing back: scanning resumes after the opener."""
    m = _RAW_OPEN_RE.match(content, start)
    if m is None or not m.group(1):
        return None
    tag = _Tag(start, m.group(1), m.start(1), raw=True)
    name = m.group(1).split()[0]
    close = re.compile(r"\{\{\{\{/\s*" + re.escape(name) + r"\s*\}\}\}\}").search(
        content, m.end())
    return tag, close.end() if close else m.end()


def _comment_end(content: str, at: int) -> int:
    """The offset after the comment whose ``!`` is at ``at``: ``{{!-- --}}``
    ends at ``--}}`` (at the next ``}}`` when that never comes), ``{{! }}``
    at the next ``}}``; a comment that never ends runs to the end."""
    if content.startswith("!--", at):
        m = _LONG_COMMENT_END_RE.search(content, at + 3)
        if m:
            return m.end()
    end = content.find("}}", at)
    return end + 2 if end >= 0 else len(content)


def _tags(content: str) -> Iterator[_Tag]:
    """The mustache tags of ``content`` in source order, without comments,
    escaped mustaches and the text of raw blocks."""
    pos = 0
    while True:
        start = content.find("{{", pos)
        if start < 0:
            return
        if content[start - 1:start] == "\\" and content[start - 2:start] != "\\\\":
            pos = start + 2  # \{{ is text; \\{{ is a backslash, then a tag
            continue
        raw = _raw_block(content, start)
        if raw is not None:
            yield raw[0]
            pos = raw[1]
            continue
        at = start + 2
        if content.startswith("~", at):
            at += 1
        if content.startswith("!", at):
            pos = _comment_end(content, at)
            continue
        end = content.find("}}", at)
        following = content.find("{{", at)
        closed = end >= 0 and not 0 <= following < end
        if not closed:
            end = following if following >= 0 else len(content)
        body = content[at:end]
        if body.endswith("~"):
            body = body[:-1]
        yield _Tag(start, body, at, closed)
        pos = end + 2 if closed else end


@dataclass(frozen=True)
class _Found:
    """A symbol a tag yields: its kind and name, the scope chain it hashes,
    and the file offsets its span runs between."""

    kind: str
    name: str
    scope: tuple[str, ...]
    start: int
    end: int


def _helper(path: str) -> str:
    """``block`` for a built-in block helper, else ``helper``."""
    return "block" if path in _BUILTIN_BLOCKS else "helper"


def _opener(tag: _Tag) -> tuple[str, str, str, str, int] | None:
    """For a block opener, ``(kind, symbol name, closer name, label, name
    end)``; the symbol name is empty when the tag names no symbol. None for
    any other tag."""
    body = tag.body
    if tag.raw:
        name = body.split()[0]
        word = _HELPER_NAME_RE.match(name)
        return (_helper(word.group(1)) if word else "helper",
                word.group(1) if word else "", name, " ".join(body.split()),
                tag.body_at + len(name))
    if body.startswith("#>"):
        m = _PARTIAL_NAME_RE.match(body, 1)
        name = m.group(1) if m else ""
        return ("partial", name, name, " ".join(body[1:].split()),
                tag.body_at + (m.end() if m else 2))
    if body.startswith("#*"):
        sigil, kinds = 2, _BUILTIN_DECORATORS
    elif body.startswith("#"):
        sigil, kinds = 1, _BUILTIN_BLOCKS
    elif body.startswith("^") and body[1:].strip():
        sigil, kinds = 1, _BUILTIN_BLOCKS
    else:
        return None
    path = _PATH_RE.match(body, sigil)
    word = _HELPER_NAME_RE.match(body, sigil)
    label = " ".join(body[sigil:].split())
    if body[0] != "#" or sigil == 2:
        label = body[sigil - 1] + label
    name = word.group(1) if word else ""
    kind = "block" if name in kinds else "helper"
    return (kind, name, path.group(1) if path else "", label,
            tag.body_at + (word.end() if word else sigil))


def _walk(content: str) -> Iterator[_Found]:
    """The symbols of one file, each with the chain of blocks open at it.

    The stack holds ``(closer name, label)`` for every open block, outermost
    first; see the module docstring for the block rules."""
    stack: list[tuple[str, str]] = []
    for tag in _tags(content):
        opener = _opener(tag)
        if opener is not None:
            kind, name, closer, label, name_end = opener
            if name:
                chain = tuple(lab for _, lab in stack) + (label,)
                yield _Found(kind, name, chain, tag.start, name_end)
            if tag.closed and not tag.raw:
                stack.append((closer, label))
            continue
        if tag.body.startswith("/"):
            path = _PATH_RE.match(tag.body, 1)
            names = [n for n, _ in stack]
            if path and path.group(1) in names:
                del stack[len(names) - 1 - names[::-1].index(path.group(1)):]
            continue
        partial = _PARTIAL_NAME_RE.match(tag.body)
        if partial:
            yield _Found("partial", partial.group(1),
                         tuple(lab for _, lab in stack),
                         tag.start, tag.body_at + partial.end())


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    return f"handlebars:{path}:{line}-{line}:{name}:{kind}"


def find_handlebars_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Handlebars template files."""
    yield from find_files(
        repo_root, ["*.hbs", "*.handlebars"], max_files=max_files
    )


@register_analyzer("handlebars", supports_max_files=True)
def analyze_handlebars(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """Analyze Handlebars templates for partials, blocks, and helpers."""
    symbols: list[Symbol] = []

    for hbs_file in find_handlebars_files(repo_root, max_files=max_files):
        try:
            content = hbs_file.read_text(errors="ignore")
        except OSError:  # pragma: no cover
            continue
        path_str = str(hbs_file)
        rel_path = to_relative_path(hbs_file, repo_root)
        # Line starts as str.splitlines counts lines, so lines number as before
        lines = content.splitlines(keepends=True)
        starts = [0]
        for text in lines:
            starts.append(starts[-1] + len(text))

        for found in _walk(content):
            index = bisect.bisect_right(starts, found.start) - 1
            line_num = index + 1
            start_col = found.start - starts[index]
            # A span stays on its opening line (the node id names one line)
            end_col = min(found.end, starts[index] + len(lines[index].rstrip("\r\n"))) \
                - starts[index]
            symbols.append(Symbol(
                id=_make_symbol_id(path_str, line_num, found.name, found.kind),
                stable_id=scoped_stable_id(
                    kind=found.kind, lang="handlebars", path=rel_path,
                    name=found.name, scope=found.scope,
                ),
                name=found.name,
                kind=found.kind,
                language="handlebars",
                path=path_str,
                span=Span(start_line=line_num, end_line=line_num,
                          start_col=start_col, end_col=end_col),
                origin=PASS_ID,
                origin_run_id="",
            ))

    return AnalysisResult(symbols=symbols, edges=[], usage_contexts=[])
