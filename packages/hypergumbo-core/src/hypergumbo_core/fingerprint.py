# SPDX-License-Identifier: AGPL-3.0-or-later
"""Symbol-level content fingerprinting (WI-fanun).

What this module does
---------------------
Compute a structural content fingerprint for the source code inside a
Symbol's span, expressed as a hex SHA-256 prefixed with the scheme tag
``hgfp1:``. The fingerprint reflects the symbol's source modulo
whitespace and comments — two symbols whose code text is identical
except for blank lines, indentation choices, and comments produce the
same fingerprint; renaming an identifier or changing a literal
produces a different one. This is the "Shape + identifiers + literals"
level documented in the WI-fanun design.

Why this exists
---------------
``Symbol.fingerprint`` is declared in the IR schema and seeded by a few
manifest-derived producers (``toml-v1`` for dependency / project nodes,
``json-v1`` for package / file nodes, ``wgsl-v1`` for the three WGSL
shader entry points). The field is null on 99.7% of source-code
Symbols, which makes the schema-level promise hollow and forecloses
consumer use-cases like symbol-level change detection, duplicate-code
detection, and per-symbol cache invalidation. WI-fanun fills the gap
without touching every analyzer: this module is the one place that
knows how to compute a fingerprint, and a single orchestrator
post-pass stamps every analyzer-produced Symbol that doesn't already
carry one.

How the structural walk works
-----------------------------
Python source is parsed with ``ast.parse`` and walked via
``ast.iter_child_nodes``. Each node contributes ``type(node).__name__``;
each leaf (Name / Constant / Attribute attr / FunctionDef / etc.)
additionally contributes the salient text (identifier name, literal
value) so renames and literal changes affect the hash. Comments don't
exist in the ``ast`` tree (they're stripped by the tokenizer) so the
filter is implicit.

Tree-sitter languages use the language pack's grammar and a pre-order
cursor walk. Internal nodes contribute their ``type``; leaves
(``child_count == 0``) contribute ``type:text``. ``comment``-typed
nodes (and the few language-specific variants — ``line_comment``,
``block_comment``, ``doc_comment``) are filtered before they
contribute to the hash, mirroring the Python-ast behavior.

Languages whose grammars aren't in the language pack — bash, ansible,
hcl, ini, gitignore, dockerfile-via-text, and the regex-only
analyzers — return ``None``. The orchestrator post-pass leaves those
Symbols' ``fingerprint`` as ``None`` (same shape as today), so there's
no regression for languages we can't honestly fingerprint.

Scheme tag
----------
The returned string is prefixed ``hgfp1:`` so consumers can detect
the version. Future normalization changes (e.g., switching to a
token-canonical walk that ignores rename) would bump to ``hgfp2:``.

Producer-side coexistence
-------------------------
The orchestrator post-pass at ``stamp_symbol_fingerprints`` skips any
Symbol whose ``fingerprint`` is already non-None, so the existing
toml-v1 / json-v1 / wgsl-v1 manifest-fingerprint producers continue to
own those nodes unchanged. The post-pass is purely additive on the
source-code Symbols that were previously null.
"""
from __future__ import annotations

import ast
import hashlib
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .ir import Span, Symbol


# Symbol fingerprint scheme tag. Stored in
# ``BehaviorMap.symbol_fingerprint_scheme`` for consumer-side
# version detection. The canonical definition lives in
# ``hypergumbo_core.schema`` (re-exported here for backwards
# compatibility with consumers that imported from this module
# pre-WI-fanun stabilization).
from .schema import SYMBOL_FINGERPRINT_SCHEME  # noqa: F401

_SCHEME_PREFIX = "hgfp1:"


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


def _python_ast_fingerprint(snippet: bytes) -> str | None:
    """Compute fingerprint via Python ``ast`` (skip comments inherently).

    Class methods occupy an indented span (``def foo(self):`` sits two
    levels in). Parsing that slice as a module raises IndentationError,
    so we retry with ``textwrap.dedent`` before giving up.
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


def _tree_sitter_fingerprint(
    grammar_name: str,
    snippet: bytes,
) -> str | None:
    """Compute fingerprint via tree-sitter pre-order walk."""
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
        tree = parser.parse(snippet)
    except Exception:  # pragma: no cover - tree-sitter rarely raises here
        return None
    parts: list[str] = []
    cursor = tree.walk()
    if not _walk_tree_sitter(cursor, snippet, parts):  # pragma: no cover - defensive
        return None
    if not parts:  # pragma: no cover - _walk_tree_sitter always appends root
        return None
    return _hash(parts)


def _walk_tree_sitter(cursor, source: bytes, parts: list[str]) -> bool:
    """Iterative pre-order walk producing tokens for the fingerprint.

    Returns False if the walk produced no tokens (defensive).
    """
    visited = 0
    while True:
        node = cursor.node
        if node is None:
            return False  # pragma: no cover - cursor.walk() always seeded
        node_type = node.type
        if node_type in _COMMENT_NODE_TYPES:
            # Skip the entire comment subtree.
            if not _advance_skip_subtree(cursor):  # pragma: no cover - reached root from comment
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
        visited += 1
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return visited > 0
    return visited > 0  # pragma: no cover - while True loop exits via return


def _advance_skip_subtree(cursor) -> bool:
    """Move past the current subtree without descending into it."""
    while not cursor.goto_next_sibling():
        if not cursor.goto_parent():  # pragma: no cover - reached root from comment
            return False
    return True


def _hash(parts: list[str]) -> str:
    payload = "|".join(parts).encode("utf-8", errors="replace")
    return _SCHEME_PREFIX + hashlib.sha256(payload).hexdigest()[:16]


def stamp_symbol_fingerprints(
    symbols: Iterable[Symbol],
    repo_root: Path,
) -> None:
    """Orchestrator post-pass: fill Symbol.fingerprint for source-code Symbols.

    Walks *symbols* once, reads each file at most once, and stamps the
    structural fingerprint on every Symbol whose ``fingerprint`` is None
    and whose span / path can be resolved against the on-disk source.

    Skips:
    - Symbols whose ``fingerprint`` is already populated (the manifest
      producers — toml-v1 dependency/project, json-v1 package/file,
      wgsl-v1 function — keep their existing values).
    - Symbols without a ``path`` or whose path can't be opened.
    - Symbols whose ``span`` is None or has end_line < start_line.
    - Synthesized boundary / external Symbols (their span typically is
      ``0-0`` and there's no source file to read).
    - Languages the helper can't fingerprint (returns None).
    """
    source_cache: dict[Path, bytes | None] = {}
    for sym in symbols:
        if sym.fingerprint is not None:
            continue
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
        fp = compute_symbol_fingerprint(sym.language, span, source)
        if fp is not None:
            sym.fingerprint = fp


def compute_symbol_fingerprint(
    language: str,
    span: Span,
    source: bytes,
) -> str | None:
    """Compute a structural fingerprint for the source covered by *span*.

    Returns a string of the form ``hgfp1:<16-hex>`` when the language is
    supported and the parse succeeds; ``None`` otherwise (regex-only
    languages, manifest formats, grammars not in the language pack,
    parser errors on partial-span snippets).

    The orchestrator post-pass calls this once per Symbol whose
    ``fingerprint`` field is None and stamps the result back onto the
    Symbol; producers that already populate ``fingerprint`` (toml-v1,
    json-v1, wgsl-v1) are not affected.
    """
    snippet = _slice_source(source, span)
    if not snippet:
        return None
    if language == "python":
        return _python_ast_fingerprint(snippet)
    grammar = _LANG_PACK_OVERRIDES.get(language, language)
    return _tree_sitter_fingerprint(grammar, snippet)
