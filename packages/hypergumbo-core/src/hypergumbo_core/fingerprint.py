# SPDX-License-Identifier: AGPL-3.0-or-later
"""Symbol-level content fingerprinting (WI-fanun; context-aware since WI-falum).

What this module does
---------------------
Compute a structural content fingerprint for the source code inside a
Symbol's span, expressed as a hex SHA-256 prefixed with the scheme tag
``hgfp2:``. The fingerprint reflects the symbol's source modulo
whitespace and comments — two symbols whose code text is identical
except for blank lines, indentation choices, and comments produce the
same fingerprint; renaming an identifier or changing a literal
produces a different one. This is the "Shape + identifiers + literals"
level documented in the WI-fanun design.

Why this exists
---------------
``Symbol.fingerprint`` is declared in the IR schema. Before WI-fanun it
was null on 99.7% of source-code Symbols, which made the schema-level
promise hollow and foreclosed consumer use-cases like symbol-level
change detection, duplicate-code detection, and per-symbol cache
invalidation. This module is the one place that knows how to compute a
fingerprint, and a single orchestrator post-pass stamps every Symbol
that doesn't already carry one. Since ADR-0032 Phase 2 PR2 demolished
the producer-side Format-1 hashes (toml / json / cmake / css / sql /
xml / wasm_bindgen) and this module's WI-falum revision demolished the
wgsl ones, the post-pass solely owns the field.

Why the parse is context-aware (v2)
-----------------------------------
The v1 implementation sliced the span's lines out of the file and
parsed the slice as a standalone document. Two silent failure modes
followed:

- A single-line TOML array element (``"rich~=14.3.2",``) does not parse
  as standalone TOML; tree-sitter error recovery dropped the content and
  every dependency hashed to the same constant (WI-falum — all 76 TOML
  dependency nodes carried ONE fingerprint, a 6.0.0 regression).
- A Python method embedding a column-0 triple-quoted fixture defeats the
  ``textwrap.dedent`` retry (common prefix 0 leaves the ``def``
  indented), so ``ast.parse`` raises and 3,911 test methods got
  ``None`` (WI-lisog facet a).

v2 parses the *whole file* once (cached per file by the post-pass),
locates the parse-tree node covering the span, and hashes that subtree.
The span content is therefore always seen in its real syntactic context.
When the covering node extends beyond the span (the span points at part
of a container, e.g. one element of a TOML array, or two sibling defs),
the fully-contained children are hashed in source order instead — never
the whole container, so the hash still discriminates per-span content.
A located subtree containing parse errors yields ``None``: an honest
null, never a degenerate constant value (the spec validator's
fingerprint-degeneracy umbrella check guards that failure mode at the
output boundary).

How the structural walk works
-----------------------------
Python files are parsed with ``ast.parse``. Each node contributes
``type(node).__name__``; each salient leaf (Name / Constant / Attribute
attr / FunctionDef / etc.) additionally contributes its text
(identifier name, literal value) so renames and literal changes affect
the hash. Comments don't exist in the ``ast`` tree, so the filter is
implicit. If the *file* doesn't parse (genuinely broken source), the
legacy snippet path (parse the slice, retry dedented) is the fallback.

Tree-sitter languages use the language pack's grammar and a pre-order
cursor walk of the located subtree. Internal nodes contribute their
``type``; leaves (``child_count == 0``) contribute ``type:text``.
``comment``-typed nodes (and the language-specific variants) are
filtered before they contribute, mirroring the Python-ast behavior.

Languages whose grammars aren't in the language pack — bash, ansible,
hcl, ini, gitignore, dockerfile-via-text, and the regex-only
analyzers — return ``None``. The orchestrator post-pass leaves those
Symbols' ``fingerprint`` as ``None``, so there's no false precision for
languages we can't honestly fingerprint.

Scheme tag
----------
The returned string is prefixed ``hgfp2:`` so consumers can detect the
version. v1 hashed snippet-rooted walks; v2's subtree-rooted walks
change every emitted value, hence the bump (this is the versioning
convention the v1 docstring promised). A future normalization change
(e.g., a token-canonical walk that ignores renames) would bump to
``hgfp3:``.
"""
from __future__ import annotations

import ast
import hashlib
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    import tree_sitter

    from .ir import Span, Symbol


# Symbol fingerprint scheme tag. Stored in
# ``BehaviorMap.symbol_fingerprint_scheme`` for consumer-side
# version detection. The canonical definition lives in
# ``hypergumbo_core.schema`` (re-exported here for backwards
# compatibility with consumers that imported from this module
# pre-WI-fanun stabilization).
from .schema import SYMBOL_FINGERPRINT_SCHEME  # noqa: F401

_SCHEME_PREFIX = "hgfp2:"


# Tree-sitter comment-like node types we filter from the walk so that
# whitespace-equivalent variants of the same code hash identically.
# Conservative across grammars: most expose ``comment``; a few use the
# more specific variants.
_COMMENT_NODE_TYPES = frozenset({
    "comment",
    "line_comment",
    "block_comment",
    "doc_comment",
    "documentation_comment",
})


# Map Symbol.language to tree-sitter language-pack name when the names
# differ. Most languages match exactly; only a few need translation.
_LANG_PACK_OVERRIDES = {
    "javascript": "javascript",
    "typescript": "typescript",
    "csharp": "csharp",
    "cpp": "cpp",
}

# ASCII whitespace trimmed off the span's byte range before locating the
# covering tree-sitter node. Leading indentation / trailing newlines
# belong to no token, and including them would push the located node up
# to an enclosing container.
_WS_BYTES = b" \t\r\n"


def _slice_source(source: bytes, span: Span) -> bytes:
    """Return the source bytes covered by ``span``.

    Spans are 1-indexed inclusive. A defensive empty span returns
    empty bytes; callers should already have skipped span-less symbols.
    """
    if span.start_line <= 0 or span.end_line < span.start_line:
        return b""
    lines = source.splitlines(keepends=True)
    # Clamp to actual line count.
    start_idx = min(span.start_line - 1, len(lines))
    end_idx = min(span.end_line, len(lines))
    return b"".join(lines[start_idx:end_idx])


# ----------------------------------------------------------------------
# Python (ast) path
# ----------------------------------------------------------------------


def _python_snippet_fingerprint(snippet: bytes) -> str | None:
    """Legacy fallback: fingerprint an out-of-context slice.

    Used only when the *whole file* fails ``ast.parse`` (broken source),
    in which case per-span slices are the best we can do. Class methods
    occupy an indented span, so we retry with ``textwrap.dedent`` before
    giving up.
    """
    text = snippet.decode("utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except (SyntaxError, IndentationError):
        try:
            tree = ast.parse(textwrap.dedent(text))
        except (SyntaxError, IndentationError):
            return None
    parts: list[str] = []
    _walk_python_ast(tree, parts)
    if not parts:  # pragma: no cover - _walk_python_ast always appends Module
        return None
    return _hash(parts)


def _walk_python_ast(node: ast.AST, parts: list[str]) -> None:
    """Pre-order walk of a Python AST contributing structural tokens."""
    parts.append(type(node).__name__)
    # Salient leaf text — names and literals change fingerprints; type
    # comments / line numbers / col offsets don't.
    if isinstance(node, ast.Name):
        parts.append(f"id:{node.id}")
    elif isinstance(node, ast.Attribute):
        parts.append(f"attr:{node.attr}")
    elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
        parts.append(f"def:{node.name}")
    elif isinstance(node, ast.ClassDef):
        parts.append(f"cls:{node.name}")
    elif isinstance(node, ast.arg):
        parts.append(f"arg:{node.arg}")
    elif isinstance(node, ast.Constant):
        parts.append(f"const:{type(node.value).__name__}:{node.value!r}")
    for child in ast.iter_child_nodes(node):
        _walk_python_ast(child, parts)


def _py_effective_lines(node: ast.AST) -> tuple[int, int] | None:
    """Return the (start_line, end_line) a node effectively occupies.

    Producers stamp decorated defs with spans that start at the first
    decorator line, while ``ast`` puts ``lineno`` on the ``def`` / ``class``
    keyword itself — so the effective start extends up to the earliest
    decorator.
    """
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if start is None or end is None:
        return None
    decorators = getattr(node, "decorator_list", None) or []
    for dec in decorators:
        dec_line = getattr(dec, "lineno", None)
        if dec_line is not None and dec_line < start:
            start = dec_line
    return start, end


def _python_context_fingerprint(
    tree: ast.Module, start_line: int, end_line: int,
) -> str | None:
    """Locate the subtree covering [start_line, end_line] and hash it.

    The smallest covering node wins. When even the smallest covering
    node extends beyond the span (the span points at part of a
    container — e.g. two sibling defs, or statements inside a class
    body), its fully-contained children are hashed in source order
    instead. A span covering no parseable content returns None.
    """
    best: ast.AST = tree
    best_extent = float("inf")
    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            continue
        lines = _py_effective_lines(node)
        if lines is None:
            continue
        n_start, n_end = lines
        if n_start <= start_line and n_end >= end_line:
            extent = n_end - n_start
            if extent < best_extent:
                best, best_extent = node, extent
    if not isinstance(best, ast.Module):
        lines = _py_effective_lines(best)
        assert lines is not None  # covering candidates all had lines
        # WI-guhas: _py_effective_lines extends a decorated def/class start up
        # to its earliest decorator, but producers commonly span only the
        # declaration keyword .. body (decorator excluded). For def/class nodes
        # compare the lower bound against the raw declaration-keyword line
        # (best.lineno) rather than the decorator-extended start, so a decorated
        # declaration whose keyword+body lie in the span is hashed WHOLE (name +
        # signature + body) via the subtree path below — not degraded to a
        # name/signature-stripped container fragment that aliases distinct
        # symbols onto one constant.
        decl_start = (
            best.lineno
            if isinstance(best, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            else lines[0]
        )
        if decl_start >= start_line and lines[1] <= end_line:
            # The covering node fits inside the span: it IS the span's
            # content (modulo surrounding blank lines / leading decorators).
            parts: list[str] = []
            _walk_python_ast(best, parts)
            return _hash(parts)
    # Container case: hash the children fully inside the span.
    parts = []
    for child in ast.iter_child_nodes(best):
        lines = _py_effective_lines(child)
        if lines is None:
            continue
        if lines[0] >= start_line and lines[1] <= end_line:
            _walk_python_ast(child, parts)
    if not parts:
        return None
    return _hash(parts)


# ----------------------------------------------------------------------
# Tree-sitter path
# ----------------------------------------------------------------------


def _parse_tree_sitter(grammar_name: str, source: bytes) -> Any | None:
    """Parse the whole file with the language pack's grammar."""
    try:
        import tree_sitter
        from tree_sitter_language_pack import get_language
    except ImportError:  # pragma: no cover - tree_sitter dep is mandatory
        return None
    try:
        language = get_language(grammar_name)
    except Exception:
        return None
    parser = tree_sitter.Parser(language)
    try:
        return parser.parse(source)
    except Exception:  # pragma: no cover - tree-sitter rarely raises here
        return None


def _content_byte_range(
    source: bytes, start_line: int, end_line: int,
) -> tuple[int, int] | None:
    """Map 1-indexed inclusive span lines to a trimmed byte range.

    Returns ``(start_byte, end_byte_exclusive)`` of the span's content
    with surrounding ASCII whitespace stripped, or None when the span
    holds no content (blank lines, lines past EOF).
    """
    lines = source.splitlines(keepends=True)
    if start_line <= 0 or end_line < start_line or start_line > len(lines):
        return None
    start_idx = start_line - 1
    end_idx = min(end_line, len(lines))
    prefix = sum(len(line) for line in lines[:start_idx])
    chunk = b"".join(lines[start_idx:end_idx])
    stripped_lead = len(chunk) - len(chunk.lstrip(_WS_BYTES))
    stripped_chunk = chunk.strip(_WS_BYTES)
    if not stripped_chunk:
        return None
    start_byte = prefix + stripped_lead
    return start_byte, start_byte + len(stripped_chunk)


def _tree_sitter_context_fingerprint(
    tree: Any, source: bytes, start_byte: int, end_byte: int,
) -> str | None:
    """Locate the subtree spanning [start_byte, end_byte) and hash it.

    Mirrors the Python locator: the smallest node spanning the range is
    found via ``descendant_for_byte_range``; if it extends beyond the
    range (the span points at part of a container — e.g. one element of
    a TOML dependency array), the fully-contained children are hashed in
    source order instead. A located subtree containing parse errors
    yields None — an honest null instead of a degenerate constant hash.
    """
    node = tree.root_node.descendant_for_byte_range(start_byte, end_byte)
    if node is None:  # pragma: no cover - root always spans the file
        return None
    parts: list[str] = []
    if node.start_byte >= start_byte and node.end_byte <= end_byte:
        if _node_has_error(node):
            return None
        if not _walk_tree_sitter(node.walk(), source, parts):  # pragma: no cover - defensive
            return None
    else:
        # The container itself being an ERROR node means the range's
        # content was never really parsed — its token children are
        # error-recovery debris, not structure.
        if node.type == "ERROR":
            return None
        contained = [
            child for child in node.children
            if child.start_byte >= start_byte and child.end_byte <= end_byte
        ]
        if not contained:
            return None
        for child in contained:
            if _node_has_error(child):
                return None
            if not _walk_tree_sitter(child.walk(), source, parts):  # pragma: no cover - defensive
                return None
    if not parts:  # pragma: no cover - successful walks always append
        return None
    return _hash(parts)


def _node_has_error(node: Any) -> bool:
    """True when the subtree contains a parse error or missing token."""
    if node.type == "ERROR":
        return True
    return bool(getattr(node, "has_error", False))


def _child_gap_texts(node: Any, source: bytes) -> list[str]:
    """Significant text inside *node* covered by none of its children.

    Whitespace-only gaps return nothing, preserving the
    whitespace-invariance contract.
    """
    gaps: list[str] = []
    pos = node.start_byte
    for child in node.children:
        if child.start_byte > pos:
            gap = source[pos:child.start_byte].strip(_WS_BYTES)
            if gap:
                gaps.append(gap.decode("utf-8", errors="replace"))
        pos = child.end_byte
    if pos < node.end_byte:
        gap = source[pos:node.end_byte].strip(_WS_BYTES)
        if gap:
            gaps.append(gap.decode("utf-8", errors="replace"))
    return gaps


def _walk_tree_sitter(
    cursor: "tree_sitter.TreeCursor", source: bytes, parts: list[str],
) -> bool:
    """Iterative pre-order walk producing tokens for the fingerprint.

    The cursor is rooted at the located subtree; ``goto_parent`` from
    the cursor's root returns False, so the walk never escapes into the
    surrounding file. Returns False if the walk produced no tokens
    (defensive).
    """
    visited = 0
    while True:
        node = cursor.node
        if node is None:
            return False  # pragma: no cover - cursor.walk() always seeded
        node_type = node.type
        if node_type in _COMMENT_NODE_TYPES:
            # Skip the entire comment subtree.
            if not _advance_skip_subtree(cursor):
                break
            continue
        if node.child_count == 0:
            try:
                text = source[node.start_byte:node.end_byte].decode(
                    "utf-8", errors="replace",
                )
            except Exception:  # pragma: no cover - defensive
                text = ""
            parts.append(f"{node_type}:{text}")
        else:
            parts.append(node_type)
            # Some grammars don't materialize all significant text as
            # leaves — tree-sitter-toml's ``string`` node, for example,
            # has only the two quote tokens as children, with the
            # content between them covered by no node at all. Contribute
            # those gaps (whitespace-stripped, so blank-line / indent
            # variants still hash identically); without this the walk
            # is blind to exactly the content the fingerprint exists to
            # discriminate (the WI-falum collapse).
            gap_texts = _child_gap_texts(node, source)
            if gap_texts:
                parts.append("gaps:" + "\x1f".join(gap_texts))
        visited += 1
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return visited > 0
    return visited > 0


def _advance_skip_subtree(cursor: "tree_sitter.TreeCursor") -> bool:
    """Move past the current subtree without descending into it."""
    while not cursor.goto_next_sibling():
        if not cursor.goto_parent():
            return False
    return True


def _hash(parts: list[str]) -> str:
    payload = "|".join(parts).encode("utf-8", errors="replace")
    return _SCHEME_PREFIX + hashlib.sha256(payload).hexdigest()[:16]


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------

# Cache sentinel distinguishing "not parsed yet" from "parse failed".
_PARSE_FAILED = object()


def _compute_fingerprint(
    language: str | None,
    span: Span,
    source: bytes,
    tree_cache: dict[Any, Any],
    cache_key: Any,
) -> str | None:
    """Shared implementation behind the public entry points.

    ``tree_cache`` holds one parsed tree per (path, language family) so
    the orchestrator post-pass parses each file once regardless of how
    many Symbols it contains; the public one-shot API passes a throwaway
    dict.
    """
    if span.start_line <= 0 or span.end_line < span.start_line:
        return None
    if language == "python":
        key = ("py", cache_key)
        tree = tree_cache.get(key)
        if tree is None:
            try:
                tree = ast.parse(source.decode("utf-8", errors="replace"))
            except (SyntaxError, ValueError):
                tree = _PARSE_FAILED
            tree_cache[key] = tree
        if tree is _PARSE_FAILED:
            # Broken file: per-span slices are the best we can do.
            snippet = _slice_source(source, span)
            if not snippet:
                return None
            return _python_snippet_fingerprint(snippet)
        return _python_context_fingerprint(tree, span.start_line, span.end_line)
    grammar = _LANG_PACK_OVERRIDES.get(language, language)
    if not isinstance(grammar, str):
        return None
    key = ("ts", grammar, cache_key)
    tree = tree_cache.get(key)
    if tree is None:
        parsed = _parse_tree_sitter(grammar, source)
        tree = _PARSE_FAILED if parsed is None else parsed
        tree_cache[key] = tree
    if tree is _PARSE_FAILED:
        return None
    byte_range = _content_byte_range(source, span.start_line, span.end_line)
    if byte_range is None:
        return None
    return _tree_sitter_context_fingerprint(tree, source, *byte_range)


def stamp_symbol_fingerprints(
    symbols: Iterable[Symbol],
    repo_root: Path,
) -> None:
    """Orchestrator post-pass: fill Symbol.fingerprint for source-code Symbols.

    Walks *symbols* once, reads and parses each file at most once, and
    stamps the structural fingerprint on every Symbol whose
    ``fingerprint`` is None and whose span / path can be resolved against
    the on-disk source. This post-pass solely owns the field: no analyzer
    emits a producer-side fingerprint anymore (ADR-0032 Phase 2 PR2
    demolished the seven config-analyzer Format-1 hashes; the WI-falum
    revision demolished wgsl's).

    Skips (leaves the existing value):
    - Symbols whose ``fingerprint`` is already canonical (``hgfp2:`` prefix),
      or is an identity-derived synthetic fingerprint on a ``language is None``
      Class-B node (a documented second shape). A NON-canonical fingerprint on
      a real source node (``language is not None``) is NOT skipped: it is a
      producer-side bare-hex leak and is recomputed in the canonical scheme
      (WI-lisog) — bare hex must never reach the output.
    - Symbols without a ``path`` or whose path can't be opened.
    - Symbols whose ``span`` is None or has end_line < start_line.
    - Synthesized boundary / external Symbols (their span typically is
      ``0-0`` and there's no source file to read).
    - Languages the helper can't fingerprint (returns None).
    """
    source_cache: dict[Path, bytes | None] = {}
    tree_cache: dict[Any, Any] = {}
    for sym in symbols:
        if sym.fingerprint is not None:
            # WI-lisog normalization: preserve an existing fingerprint ONLY if
            # it is already canonical (``hgfp2:``) OR it is an identity-derived
            # synthetic fingerprint on a ``language is None`` Class-B node (a
            # documented second shape — the central pass cannot source-
            # fingerprint a source-less synthetic). A non-canonical fingerprint
            # on a real source node (``language is not None``) is a
            # producer-side analyzer-internal-serialization leak — the bare
            # 16-hex ``sha256(source[start:end])[:16]`` still emitted by several
            # tree-sitter analyzers (cuda/glsl/fortran/… — WI-lisog facet c).
            # The line-531 precedence guard used to let those leaks survive to
            # the output; clear it instead so the recompute below replaces it
            # with the canonical hgfp2: form (or an honest None when the
            # language has no grammar). Bare hex must never reach the output.
            if (
                sym.fingerprint.startswith(_SCHEME_PREFIX)
                or getattr(sym, "language", None) is None
            ):
                continue
            sym.fingerprint = None
        if not getattr(sym, "path", None):
            continue
        span = getattr(sym, "span", None)
        if span is None:
            continue
        if span.start_line is None or span.end_line is None:  # pragma: no cover - Span dataclass enforces int
            continue
        if span.start_line <= 0 or span.end_line < span.start_line:
            continue
        # Resolve path: producers emit repo-relative paths after the
        # orchestrator's normalize_path pass.
        abs_path = Path(sym.path)
        if not abs_path.is_absolute():
            abs_path = (repo_root / sym.path).resolve()
        source = source_cache.get(abs_path)
        if source is None:
            if abs_path in source_cache:
                # Cached negative result.
                continue
            try:
                source = abs_path.read_bytes()
            except (OSError, ValueError):
                source_cache[abs_path] = None
                continue
            source_cache[abs_path] = source
        fp = _compute_fingerprint(sym.language, span, source, tree_cache, abs_path)
        if fp is not None:
            sym.fingerprint = fp


def compute_symbol_fingerprint(
    language: str,
    span: Span,
    source: bytes,
) -> str | None:
    """Compute a structural fingerprint for the source covered by *span*.

    *source* is the **whole file's** bytes; the span is located inside
    its full parse tree (see the module docstring for why context
    matters). Returns a string of the form ``hgfp2:<16-hex>`` when the
    language is supported and the located subtree parses cleanly;
    ``None`` otherwise (regex-only languages, grammars not in the
    language pack, spans over unparseable content).

    The orchestrator post-pass uses the cached-tree variant internally;
    this one-shot form parses *source* per call and exists for tests and
    ad-hoc consumers.
    """
    return _compute_fingerprint(language, span, source, {}, None)
