# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: GraphQL SDL embedded in host-language source.

Why this pass exists
--------------------
Both GraphQL linkers take their *schema* side — the destination of every edge
they can emit — from symbols with ``language == "graphql"``, and the only
producer of those is the GraphQL **analyzer**, which reads ``*.graphql`` /
``*.gql`` files and nothing else. In the 794 archived surveys that carry a
GraphQL linker run, that analyzer ran in **3**. The dominant idiom in the
JS/TS ecosystem is not a schema file; it is a schema written inline in a
``gql`` tagged template literal — including in apollo-server itself, which has
**zero** ``.graphql`` files and 22 source files containing ``type Query``.

The result (WI-dinum) was two linkers minting nodes and emitting no edges:
``graphql-linker`` has emitted **0 edges in 14 runs, corpus-wide**, and
``graphql-resolver-linker`` emitted edges in **2 of 794**. Worse, one whole
branch was unreachable by construction rather than by circumstance: the
resolver linker's ``implements`` edge keys on ``kind="field"`` carrying
``meta["parent_type"]``, and **no producer anywhere in the tree emits a
GraphQL field symbol**. The analyzer emits type / input / interface / enum /
scalar / union / directive / fragment / operation — never ``field``. Its 100%
test coverage came from tests that hand-construct the symbol, so the branch was
green in CI and dead in production.

This pass gives the schema side a producer that does not depend on the
repository keeping its schema in a separate file.

What it emits, and what it deliberately does not
------------------------------------------------
Only the three kinds a consumer can actually use: ``type``, ``interface`` and
``field``. Minting ``input`` / ``enum`` / ``union`` / ``scalar`` nodes that
nothing can link to would be the exact defect this row is about, one level
down — so they are parsed past, not minted.

Class A, not Class B (ADR-0031). A ``type User { ... }`` inside a ``gql``
template is a real declaration in a real language with a real span; it is not
a synthetic stand-in for one. The ADR's own precedents are ``vue_component``
("vue IS a real template language", Class A) and ``grpc.py``'s proto-file scan
(Class A, ``language="proto"`` kept) — both linkers minting Class-A symbols for
a language other than the host file's. So ``language="graphql"`` with the host
file's path, which also means the two consumers read these symbols through
their existing filters with no change.

Identity is stamped here rather than left to the backstop.
``populate_kind_stable_ids`` runs in the analyzer orchestrator, *before*
linkers, so a symbol minted at this layer never reaches it; and ``field`` has
no factory entry there in any case. Each kind is stamped with the factory the
backstop would have used (``make_type_stable_id`` /
``make_interface_stable_id``), so an embedded ``type User`` and a
``.graphql``-file ``type User`` carry the same *shape* of identity. Fields are
parent-qualified (``User.email``) because a bare field name repeats across
every type in a schema.

Extraction, and why it is bounded to tagged templates
-----------------------------------------------------
Only the body of a ``gql`` / ``graphql`` tagged template literal (JS/TS) or a
``gql()`` call wrapping a triple-quoted string (Python) is treated as SDL. Scanning source text
directly for ``type X {`` was tried and is wrong: on apollo-server it matches
every TypeScript ``interface ITrace {`` and prose inside comments, yielding
"types" named ``to``, ``is`` and ``for``. The tag is the author's own
declaration that the enclosed text is GraphQL, which is the only reliable
signal available without a parser for the host language.

Inside a block, ``#`` comments are blanked (preserving line structure, so spans
stay honest) before definitions are read — a ``gql`` template is a *string* to
the host language, so the shared doc-region masking in ``read_masked_source``
does not reach into it, and a commented-out ``type Foo {`` would otherwise mint
a symbol.

Bodies are matched by brace balance rather than by ``[^}]*``, so a field
carrying a directive argument with braces does not truncate the type.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Iterator

from ..analyze.base import (
    make_declaration_stable_id,
    make_interface_stable_id,
    make_symbol_id,
    make_type_stable_id,
)
from ..discovery import find_non_test_files
from ..ir import AnalysisRun, PASS_VERSION, Span, Symbol, make_pass_id
from ..pass_silence import silence_reason_for_candidates
from ._text_filters import read_masked_source
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

PASS_ID = make_pass_id("graphql-sdl-linker")

# A tagged template literal whose tag is `gql` or `graphql`. The lookbehind
# keeps `mygql` and `foo.gql` from matching; the tag has to stand alone.
_JS_TAGGED_SDL = re.compile(r"(?<![\w$.])(?:gql|graphql)\s*`([^`]*)`", re.DOTALL)

# Python: gql("""...""") / gql('''...'''). The single-quoted single-line form
# that graphql.py accepts for client operations is deliberately excluded — a
# schema definition does not fit on one line without a newline in it.
_PY_TAGGED_SDL = re.compile(
    r"(?<![\w.])gql\s*\(\s*(?:'''|\x22\x22\x22)(.*?)(?:'''|\x22\x22\x22)",
    re.DOTALL,
)

# A `#` comment inside SDL, to end of line.
_SDL_COMMENT = re.compile(r"#[^\n]*")

# The head of a brace-bodied definition: `type User implements Node {`.
# `extend type Query {` is accepted — it declares fields on Query just as a
# bare definition does, and a resolver implements them identically.
_SDL_HEAD = re.compile(
    r"(?:^|\n)\s*(?:extend\s+)?(type|interface)\s+([A-Za-z_]\w*)[^{\n]*\{",
)

# One field line inside a definition body: `email: String!`, `posts(n: Int): [Post]`.
# Multi-line argument lists are not matched, and are left unclaimed rather than
# guessed at.
_SDL_FIELD = re.compile(r"^[ \t]*([A-Za-z_]\w*)[ \t]*(?:\([^)\n]*\))?[ \t]*:", re.M)

_SOURCE_PATTERNS = ["**/*.py", "**/*.js", "**/*.ts", "**/*.jsx", "**/*.tsx"]


@dataclass
class SdlDefinition:
    """One ``type`` / ``interface`` definition found inside a tagged block."""

    kind: str
    name: str
    start_line: int
    end_line: int
    fields: list[tuple[str, int]] = dataclass_field(default_factory=list)


def _find_source_files(root: Path) -> Iterator[Path]:
    """Host files that might carry an embedded schema."""
    yield from find_non_test_files(root, _SOURCE_PATTERNS)


def _blank_comments(block: str) -> str:
    """Replace ``#`` comment text with spaces, preserving every line break."""
    return _SDL_COMMENT.sub(lambda m: " " * len(m.group(0)), block)


def _tagged_blocks(content: str, is_python: bool) -> Iterator[tuple[str, int]]:
    """Yield ``(block_text, line_of_block_start)`` for each tagged SDL literal."""
    pattern = _PY_TAGGED_SDL if is_python else _JS_TAGGED_SDL
    for match in pattern.finditer(content):
        start = match.start(1)
        yield match.group(1), content[:start].count("\n") + 1


def _matching_brace(text: str, open_index: int) -> int:
    """Index of the ``}`` closing the ``{`` at ``open_index``, or -1.

    Brace *balance* rather than ``[^}]*``: a field whose directive carries an
    object argument (``@constraint(shape: {min: 1})``) contains a ``}`` that
    would otherwise end the type early and drop every field after it.
    """
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _definitions_in_block(block: str, block_line: int) -> list[SdlDefinition]:
    """Parse the ``type`` / ``interface`` definitions out of one SDL block."""
    cleaned = _blank_comments(block)
    found: list[SdlDefinition] = []
    position = 0
    while True:
        head = _SDL_HEAD.search(cleaned, position)
        if head is None:
            return found
        open_index = cleaned.index("{", head.start())
        close_index = _matching_brace(cleaned, open_index)
        if close_index < 0:
            # An unterminated block: the literal was cut off, or the text is
            # not SDL at all. Claim nothing rather than guess where it ends.
            return found
        body = cleaned[open_index + 1:close_index]
        start_line = block_line + cleaned[:head.start(1)].count("\n")
        definition = SdlDefinition(
            kind=head.group(1),
            name=head.group(2),
            start_line=start_line,
            end_line=block_line + cleaned[:close_index].count("\n"),
        )
        for field_match in _SDL_FIELD.finditer(body):
            line = block_line + cleaned[:open_index + 1 + field_match.start(1)].count("\n")
            definition.fields.append((field_match.group(1), line))
        found.append(definition)
        position = close_index + 1


def _type_symbol(definition: SdlDefinition, rel_path: str, run_id: str) -> Symbol:
    stable = (
        make_interface_stable_id("graphql", rel_path, definition.name)
        if definition.kind == "interface"
        else make_type_stable_id("graphql", rel_path, definition.name)
    )
    return Symbol(
        id=make_symbol_id(
            "graphql", rel_path, definition.start_line, definition.end_line,
            definition.name, definition.kind,
        ),
        stable_id=stable,
        kind=definition.kind,
        name=definition.name,
        path=rel_path,
        language="graphql",
        span=Span(
            start_line=definition.start_line,
            end_line=definition.end_line,
            start_col=0,
            end_col=0,
        ),
        origin=[PASS_ID],
        origin_run_id=run_id,
        meta={"embedded_in": "tagged_template"},
    )


def _field_symbol(
    definition: SdlDefinition, field_name: str, line: int, rel_path: str, run_id: str,
) -> Symbol:
    qualified = f"{definition.name}.{field_name}"
    return Symbol(
        id=make_symbol_id("graphql", rel_path, line, line, qualified, "field"),
        stable_id=make_declaration_stable_id("field", "graphql", rel_path, qualified),
        kind="field",
        name=field_name,
        qualified_name=qualified,
        path=rel_path,
        language="graphql",
        span=Span(start_line=line, end_line=line, start_col=0, end_col=0),
        origin=[PASS_ID],
        origin_run_id=run_id,
        # The key graphql_resolver.py reads to build its `type.field` lookup.
        meta={"parent_type": definition.name, "embedded_in": "tagged_template"},
    )


def extract_embedded_sdl(root: Path) -> tuple[list[Symbol], int, int]:
    """Mint schema symbols for every embedded SDL definition under ``root``.

    Returns ``(symbols, files_scanned, definitions_found)``. The definition
    count is the pass's own candidate set — what its scan FOUND, which is what
    ``silence_reason_for_candidates`` must be handed, rather than what it
    emitted.
    """
    symbols: list[Symbol] = []
    files_scanned = 0
    definitions_found = 0
    run_id = ""
    for file_path in _find_source_files(root):
        try:
            content = read_masked_source(file_path, encoding="utf-8", errors="ignore")
        except (OSError, IOError):  # pragma: no cover
            continue
        files_scanned += 1
        if "gql" not in content and "graphql" not in content:
            continue
        # Every path here came from ``_find_source_files(root)``, so it is
        # under ``root`` by construction; no truthiness test on a Path.
        rel_path = str(file_path.relative_to(root))
        is_python = file_path.suffix == ".py"
        for block, block_line in _tagged_blocks(content, is_python):
            for definition in _definitions_in_block(block, block_line):
                definitions_found += 1
                symbols.append(_type_symbol(definition, rel_path, run_id))
                for field_name, line in definition.fields:
                    symbols.append(
                        _field_symbol(definition, field_name, line, rel_path, run_id)
                    )
    return symbols, files_scanned, definitions_found


@register_linker(
    "graphql-sdl-linker",
    # Before graphql-linker and graphql-resolver-linker (both 60). Linkers in
    # one priority group are snapshotted together and cannot see each other's
    # output, so a producer for both of them has to sit in an earlier group.
    priority=55,
    description=(
        "Mints GraphQL schema type/interface/field symbols from SDL embedded "
        "in gql tagged template literals, for the two GraphQL linkers whose "
        "schema side otherwise exists only in .graphql files"
    ),
    # Considered and chosen, not defaulted: NOT gated on the `graphql`
    # framework. A gate here would hide the evidence of its own effect —
    # ADR-0054's sibling analysis (a linker must be demonstrated to work
    # before gating it) applies with force to a pass built because two gated
    # linkers were silently inert.
    activation=LinkerActivation(always=True),
    # No pass dependency: this linker reads source files itself and consumes
    # no upstream pass output. Same honest empty declaration as containment.
    depends_on=[],
)
def graphql_sdl_linker(ctx: LinkerContext) -> LinkerResult:
    """Entry point: embedded SDL in, schema symbols out."""
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    symbols, files_scanned, definitions_found = extract_embedded_sdl(ctx.repo_root)
    for symbol in symbols:
        symbol.origin_run_id = run.execution_id
    run.silence_reason = silence_reason_for_candidates(range(definitions_found))
    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned
    return LinkerResult(symbols=symbols, edges=[], run=run)
